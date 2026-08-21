"""
LLM configuration: named endpoint/model profiles loaded from a YAML file.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "llm_profiles.yaml"


class LLMProfile(BaseModel):
    """
    Resolved configuration for a single LLM endpoint/model, ready to hand
    to LLMService. Distinct from the raw YAML profile dict: by the time
    this is constructed, the API key has already been resolved from the
    environment.
    """

    name: str
    base_url: str
    model: str
    api_key: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 2048
    timeout: int = 60
    max_retries: int = 3
    retry_delay: float = 1.0


def load_llm_config(
    profile_name: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> LLMProfile:
    """
    Load and resolve an LLM profile.

    Profile selection precedence: `profile_name` argument > `LLM_PROFILE`
    environment variable > `active_profile` in the YAML file.

    Args:
        profile_name: Explicit profile name to load, overriding env/YAML defaults.
        config_path: Path to the profiles YAML file. Defaults to config/llm_profiles.yaml.

    Returns:
        LLMProfile: Resolved, validated profile with its API key filled in.
    """
    load_dotenv()

    path = config_path or DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        raw_config = yaml.safe_load(f)

    profiles: Dict[str, Any] = raw_config.get("profiles", {})
    selected_name = profile_name or os.getenv("LLM_PROFILE") or raw_config.get("active_profile")

    if selected_name not in profiles:
        raise ValueError(
            f"Unknown LLM profile '{selected_name}'. Available profiles: {', '.join(profiles)}"
        )

    profile = profiles[selected_name]

    api_key_env = profile.get("api_key_env")
    api_key = os.getenv(api_key_env) if api_key_env else None
    if api_key_env and not api_key:
        raise ValueError(
            f"LLM profile '{selected_name}' requires environment variable "
            f"'{api_key_env}' to be set, but it is empty or missing."
        )

    return LLMProfile(
        name=selected_name,
        base_url=profile["base_url"],
        model=profile["model"],
        api_key=api_key,
        temperature=profile.get("temperature", 0.1),
        max_tokens=profile.get("max_tokens", 2048),
        timeout=profile.get("timeout", 60),
        max_retries=profile.get("max_retries", 3),
        retry_delay=profile.get("retry_delay", 1.0),
    )
