"""
Loads prompt templates from text files and renders them with variables.
"""

from pathlib import Path
from string import Template
from typing import Dict

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class PromptRepository:
    """
    Loads prompt templates by name from a directory of .txt files and
    renders them with $-placeholder substitution (string.Template).
    """

    def __init__(self, templates_dir: Path = DEFAULT_TEMPLATES_DIR):
        """
        Args:
            templates_dir: Directory containing `<name>.txt` template files.
        """
        self._templates_dir = Path(templates_dir)
        self._cache: Dict[str, Template] = {}

    def get(self, name: str) -> Template:
        """
        Load (and cache) the template registered under `name`.

        Args:
            name: Template name, without the .txt extension.

        Returns:
            Template: The parsed template.
        """
        if name not in self._cache:
            template_path = self._templates_dir / f"{name}.txt"
            if not template_path.is_file():
                raise FileNotFoundError(f"Prompt template not found: {template_path}")
            self._cache[name] = Template(template_path.read_text())

        return self._cache[name]

    def render(self, name: str, **kwargs) -> str:
        """
        Load the template registered under `name` and substitute `kwargs`
        into its $-placeholders.

        Args:
            name: Template name, without the .txt extension.
            **kwargs: Values for the template's placeholders.

        Returns:
            str: The rendered prompt text.
        """
        return self.get(name).substitute(**kwargs)
