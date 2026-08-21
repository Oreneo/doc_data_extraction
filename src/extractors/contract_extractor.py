"""
Extractor for the canonical Order Form / Purchase Order contract field
schema (see plans/canonical-contract-field-schema.md).
"""

from typing import Optional

from pydantic import ValidationError

from ..config.field_registry import FieldDefinitionRegistry
from ..models.extracted_data import ExtractedContractData, LineItem
from ..prompts.prompt_repository import PromptRepository
from ..prompts.schema_prompt_builder import SchemaPromptBuilder
from ..services.llm_service import LLMService

PROMPT_TEMPLATE_NAME = "extract_contract_fields"


class ContractExtractor:
    """
    Extracts the canonical contract field set from a document's text,
    resolving field definitions (and any customer-specific overrides) via
    the injected FieldDefinitionRegistry before building the prompt.
    """

    def __init__(
        self,
        llm_service: LLMService,
        field_registry: FieldDefinitionRegistry,
        prompt_repository: PromptRepository,
    ):
        """
        Args:
            llm_service: Service for making LLM calls.
            field_registry: Source of the (base + customer-merged) field schema.
            prompt_repository: Source of prompt templates.
        """
        self.llm_service = llm_service
        self.field_registry = field_registry
        self.prompt_repository = prompt_repository

    def extract(
        self,
        text: str,
        document_type: str,
        customer_key: Optional[str] = None,
    ) -> ExtractedContractData:
        """
        Extract contract fields from document text.

        Args:
            text: Raw extracted document text.
            document_type: Document type label (e.g. "purchase_order", "order_form").
            customer_key: Matched customer profile key, if any.

        Returns:
            ExtractedContractData: The extraction result. On failure, the
                returned object has confidence=0.0 and `error` set.
        """
        fields = self.field_registry.get_fields(customer_key)

        prompt = self.prompt_repository.render(
            PROMPT_TEMPLATE_NAME,
            fields_description=SchemaPromptBuilder.build_fields_description(fields),
            json_example=SchemaPromptBuilder.build_json_example(fields),
            document_text=text,
        )

        result = self.llm_service.complete_json(prompt)

        if not result["success"]:
            return ExtractedContractData(
                document_type=document_type,
                customer_key=customer_key,
                confidence=0.0,
                error=result["error"],
                raw_response=result.get("raw_response"),
            )

        data = result["data"]

        try:
            items = [LineItem(**item) for item in (data.get("items") or [])]
            return ExtractedContractData(
                document_type=document_type,
                customer_key=customer_key,
                start_date=data.get("start_date"),
                end_date=data.get("end_date"),
                amount=data.get("amount"),
                payment_terms=data.get("payment_terms"),
                billing_address=data.get("billing_address"),
                customer_signature=bool(data.get("customer_signature", False)),
                items=items,
                technical_account_manager=data.get("technical_account_manager"),
                confidence=0.9,
                raw_response=result.get("raw_response"),
            )
        except (TypeError, ValidationError) as e:
            return ExtractedContractData(
                document_type=document_type,
                customer_key=customer_key,
                confidence=0.0,
                error=f"Failed to validate extracted data: {e}",
                raw_response=result.get("raw_response"),
            )
