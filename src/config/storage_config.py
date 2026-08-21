"""
Storage configuration: where the local SQLite database lives.

Mirrors llm_config.py's shape (YAML file + environment override) so both
kinds of configuration are resolved the same way.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "storage.yaml"

# SQLite's magic path for a database that lives only in RAM. It must be
# passed through untouched rather than resolved as a filesystem path.
IN_MEMORY_PATH = ":memory:"


class StorageConfig(BaseModel):
    """
    Resolved storage configuration, ready to hand to Database.
    """

    database_path: str


def load_storage_config(
    database_path: Optional[str] = None,
    config_path: Optional[Path] = None,
) -> StorageConfig:
    """
    Load and resolve the storage configuration.

    Path selection precedence: `database_path` argument > `DATABASE_PATH`
    environment variable > `database_path` in the YAML file.

    Relative paths are resolved against the project root so that the
    database lands in the same place regardless of the working directory
    the pipeline is invoked from. ":memory:" is passed through as-is.

    Args:
        database_path: Explicit database path, overriding env/YAML defaults.
        config_path: Path to the storage YAML file. Defaults to config/storage.yaml.

    Returns:
        StorageConfig: Resolved, validated storage configuration.
    """
    load_dotenv()

    path = config_path or DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        raw_config = yaml.safe_load(f) or {}

    selected = database_path or os.getenv("DATABASE_PATH") or raw_config.get("database_path")

    if not selected:
        raise ValueError(
            f"No database path configured. Set 'database_path' in {path}, "
            f"or the DATABASE_PATH environment variable."
        )

    if selected != IN_MEMORY_PATH and not os.path.isabs(selected):
        selected = str(PROJECT_ROOT / selected)

    return StorageConfig(database_path=selected)
