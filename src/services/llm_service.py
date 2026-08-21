"""
LLM Service for interacting with an OpenAI-compatible chat completions API
"""

import json
import re
import time
from typing import Any, Dict, Optional

from openai import OpenAI

from ..config.llm_config import LLMProfile

# Matches a whole response wrapped in a markdown code fence, optionally tagged
# with a language (```json). Smaller models frequently add one despite being
# told to return only JSON, so strip it before parsing rather than failing.
_CODE_FENCE_PATTERN = re.compile(
    r"\A```[a-zA-Z0-9_-]*\s*\n(?P<body>.*?)\n?```\s*\Z",
    re.DOTALL,
)


def _strip_code_fence(text: str) -> str:
    """
    Remove a surrounding markdown code fence from an LLM response, if present.

    Args:
        text (str): Raw LLM response text.

    Returns:
        str: The fence's contents, or the original text if it wasn't fenced.
    """
    match = _CODE_FENCE_PATTERN.match(text.strip())
    return match.group("body") if match else text


class LLMService:
    """
    Service for handling LLM interactions via any OpenAI-compatible API
    (OpenRouter, a local Ollama/LM Studio/vLLM server, etc.), as selected
    by the injected LLMProfile. Prompt-agnostic: callers build their own
    prompts (see PromptRepository / SchemaPromptBuilder) and pass the
    finished text in.
    """

    def __init__(
        self,
        config: LLMProfile,
        client: Optional[OpenAI] = None,
    ):
        """
        Initialize the LLM service

        Args:
            config (LLMProfile): Resolved endpoint/model configuration.
            client (OpenAI, optional): Pre-built client, mainly for tests. If not
                provided, one is built from `config`.
        """
        self.config = config

        # Some OpenAI-compatible local servers reject an empty/missing api_key,
        # so fall back to a placeholder when the profile doesn't need a real one.
        self.client = client or OpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url,
        )

        self.max_retries = config.max_retries
        self.retry_delay = config.retry_delay

    def call_llm(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Call the LLM with a prompt and return the response

        Args:
            prompt (str): The prompt to send to the LLM
            model (str, optional): Model to use. Defaults to the configured profile's model.
            temperature (float, optional): Sampling temperature. Defaults to the profile's.

        Returns:
            Dict[str, Any]: The LLM response
        """
        model = model or self.config.model
        temperature = temperature if temperature is not None else self.config.temperature

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "user", "content": prompt}
                    ],
                    temperature=temperature,
                    max_tokens=self.config.max_tokens,
                    timeout=self.config.timeout,
                )

                return {
                    "success": True,
                    "response": response.choices[0].message.content.strip(),
                    "model": model
                }

            except Exception as e:
                print(f"LLM API call failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))  # Exponential backoff
                else:
                    return {
                        "success": False,
                        "error": str(e),
                        "response": None
                    }

        return {
            "success": False,
            "error": "Max retries exceeded",
            "response": None
        }

    def complete_json(self, prompt: str, model: Optional[str] = None) -> Dict[str, Any]:
        """
        Call the LLM with a prompt expected to produce a JSON response, and
        parse that response.

        Args:
            prompt (str): The (already-rendered) prompt to send to the LLM.
            model (str, optional): Model to use. Defaults to the configured profile's model.

        Returns:
            Dict[str, Any]: {"success": True, "data": <parsed JSON>, "raw_response": <text>}
                on success, or {"success": False, "error": <str>, "raw_response": <text or None>}.
        """
        result = self.call_llm(prompt, model)

        if not result["success"]:
            return {
                "success": False,
                "error": result["error"],
                "raw_response": None,
            }

        try:
            data = json.loads(_strip_code_fence(result["response"]))
            return {
                "success": True,
                "data": data,
                "raw_response": result["response"],
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Failed to parse JSON: {e}",
                "raw_response": result["response"],
            }
