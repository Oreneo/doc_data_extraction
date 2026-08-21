"""
Pipeline configuration: which folder documents are read from.

Mirrors llm_config.py / storage_config.py (YAML file + environment
override) so every kind of configuration is resolved the same way.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "pipeline.yaml"


class PipelineConfig(BaseModel):
    """
    Resolved pipeline configuration.
    """

    input_folder: str


def load_pipeline_config(
    input_folder: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> PipelineConfig:
    """
    Load and resolve the pipeline configuration.

    Folder selection precedence: `input_folder` argument > `INPUT_FOLDER`
    environment variable > `input_folder` in the YAML file.

    Relative paths are resolved against the project root, so the same
    folder is scanned regardless of the working directory the pipeline is
    invoked from.

    Args:
        input_folder: Explicit folder, overriding env/YAML defaults.
        config_path: Path to the pipeline YAML file. Defaults to config/pipeline.yaml.

    Returns:
        PipelineConfig: Resolved, validated pipeline configuration.
    """
    load_dotenv()

    path = config_path or DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        raw_config = yaml.safe_load(f) or {}

    selected = input_folder or os.getenv("INPUT_FOLDER") or raw_config.get("input_folder")

    if not selected:
        raise ValueError(
            f"No input folder configured. Set 'input_folder' in {path}, "
            f"or the INPUT_FOLDER environment variable."
        )

    if not os.path.isabs(selected):
        selected = str(PROJECT_ROOT / selected)

    return PipelineConfig(input_folder=selected)
