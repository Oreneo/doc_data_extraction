"""
Loads and merges the scalar field-definition schema (base + optional
per-customer overrides) used to build the extraction prompt, and matches
raw document text against configured customer keywords.
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from ..models.extracted_data import FieldDefinition

DEFAULT_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "config" / "field_schemas"


class FieldDefinitionRegistry:
    """
    Resolves the list of scalar FieldDefinitions to extract for a given
    customer, merging config/field_schemas/base_fields.yaml with an
    optional config/field_schemas/customers/<key>.yaml override, and
    identifies which customer profile (if any) a document belongs to.
    """

    def __init__(self, schemas_dir: Path = DEFAULT_SCHEMAS_DIR):
        """
        Args:
            schemas_dir: Directory containing base_fields.yaml and a
                customers/ subdirectory of per-customer override files.
        """
        self._schemas_dir = Path(schemas_dir)
        self._base_fields: Optional[List[FieldDefinition]] = None
        self._customer_profiles: Optional[Dict[str, dict]] = None

    def _load_base_fields(self) -> List[FieldDefinition]:
        if self._base_fields is None:
            path = self._schemas_dir / "base_fields.yaml"
            with open(path, "r") as f:
                raw = yaml.safe_load(f)
            self._base_fields = [FieldDefinition(**field) for field in raw.get("fields", [])]
        return self._base_fields

    def _load_customer_profiles(self) -> Dict[str, dict]:
        if self._customer_profiles is None:
            profiles: Dict[str, dict] = {}
            customers_dir = self._schemas_dir / "customers"
            if customers_dir.is_dir():
                for path in sorted(customers_dir.glob("*.yaml")):
                    with open(path, "r") as f:
                        raw = yaml.safe_load(f) or {}
                    profiles[path.stem] = {
                        "match_keywords": raw.get("match_keywords", []) or [],
                        "overrides": raw.get("overrides", []) or [],
                    }
            self._customer_profiles = profiles
        return self._customer_profiles

    def identify_customer(self, text: str) -> Optional[str]:
        """
        Match raw document text against each customer profile's
        match_keywords (case-insensitive substring search) - a cheap,
        no-LLM-call counterpart to DocumentProcessor's document-type
        keyword heuristic.

        Args:
            text: Raw extracted document text.

        Returns:
            Optional[str]: The matched customer key (e.g. "acme"), or None
                if no profile's keywords appear in the text.
        """
        lower_text = text.lower()
        for customer_key, profile in self._load_customer_profiles().items():
            for keyword in profile["match_keywords"]:
                if keyword.lower() in lower_text:
                    return customer_key
        return None

    def get_fields(self, customer_key: Optional[str] = None) -> List[FieldDefinition]:
        """
        Resolve the merged list of scalar FieldDefinitions for a customer,
        falling back to the base definitions alone when `customer_key` is
        None or has no matching profile.

        Args:
            customer_key: Customer profile key (e.g. "acme"), or None.

        Returns:
            List[FieldDefinition]: Base fields with any matching customer
                overrides applied.
        """
        fields = list(self._load_base_fields())

        profile = self._load_customer_profiles().get(customer_key) if customer_key else None
        if not profile:
            return fields

        overrides_by_name = {override["name"]: override for override in profile["overrides"]}
        return [
            field.model_copy(update=overrides_by_name[field.name])
            if field.name in overrides_by_name
            else field
            for field in fields
        ]
