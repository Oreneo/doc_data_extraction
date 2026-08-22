"""
Renders resolved FieldDefinitions into the prompt fragments the contract
extraction template needs, so adding/editing a field in
config/field_schemas/*.yaml shows up in the prompt with no code change.
"""

from typing import List

from ..models.extracted_data import FieldDefinition

_JSON_PLACEHOLDER_BY_TYPE = {
    "string": '"<string>"',
    "date": '"mm-dd-yyyy"',
    "float": "0.0",
    "bool": "true",
}

# The `items` list's shape is fixed (it mirrors the LineItem model) rather
# than config-driven - no sample document varies the *shape* of a line
# item, only its column naming, which the LLM resolves from context.
_ITEMS_JSON_BLOCK = """    "items": [
        {
            "product_name": "<string>",
            "quantity": 0.0,
            "price": 0.0,
            "total_amount": 0.0,
            "term_months": "<number or null>",
            "price_period": "<monthly | one_time | null>",
            "burst": {
                "raw_text": "<the clause, verbatim>",
                "percentage": "<number or null>",
                "basis": "<string or null>",
                "cap_units": "<number or null>",
                "period": "<string or null>",
                "applies_to": "<string or null>"
            }
        }
    ]"""


class SchemaPromptBuilder:
    """
    Stateless helpers that turn a list of FieldDefinitions into prompt text.
    """

    @staticmethod
    def build_fields_description(fields: List[FieldDefinition]) -> str:
        """
        Args:
            fields: Resolved field definitions (base + customer overrides).

        Returns:
            str: One bullet per field with its type, required-ness, format
                hint, and description.
        """
        lines = []
        for field in fields:
            requirement = "required" if field.required else "optional"
            format_note = f", format: {field.format_hint}" if field.format_hint else ""
            lines.append(
                f"- {field.name} ({field.field_type}, {requirement}{format_note}): "
                f"{field.description.strip()}"
            )
        return "\n".join(lines)

    @staticmethod
    def build_json_example(fields: List[FieldDefinition]) -> str:
        """
        Args:
            fields: Resolved field definitions (base + customer overrides).

        Returns:
            str: A JSON object literal (as text) showing the expected
                response shape, including the fixed `items` array.
        """
        lines = ["{"]
        for field in fields:
            placeholder = _JSON_PLACEHOLDER_BY_TYPE.get(field.field_type, '"<string>"')
            lines.append(f'    "{field.name}": {placeholder},')
        lines.append(_ITEMS_JSON_BLOCK)
        lines.append("}")
        return "\n".join(lines)
