"""
Data models for document extraction results
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator

from ..utils.normalization import normalize_date, normalize_payment_terms


class FieldDefinition(BaseModel):
    """
    Definition of a scalar field to be extracted, as loaded from
    config/field_schemas/*.yaml. `description` is sent to the LLM verbatim
    as the extraction instruction for this field, so it's the natural place
    to fold in per-customer wording hints rather than a separate alias list.
    """
    name: str
    description: str
    required: bool = False
    field_type: str = "string"  # "string" | "date" | "float" | "bool"
    format_hint: Optional[str] = None


class LineItem(BaseModel):
    """
    A single line item within a document's items list.
    """
    product_name: str
    quantity: float
    price: float
    total_amount: float
    # Per-item burst term. When a document states one burst clause for the
    # whole order rather than a specific product, the same text is expected
    # to be replicated onto every item (see extract_contract_fields.txt).
    burst: Optional[str] = None


class ExtractedContractData(BaseModel):
    """
    Canonical extraction result for Order Forms / Purchase Orders, per the
    assignment's field spec.
    """
    document_type: str
    customer_key: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    amount: Optional[float] = None
    payment_terms: Optional[str] = None
    billing_address: Optional[str] = None
    customer_signature: bool = False
    items: List[LineItem] = []
    technical_account_manager: Optional[str] = None
    confidence: float = 0.0
    error: Optional[str] = None
    raw_response: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _normalize_dates(cls, value: Optional[str]) -> Optional[str]:
        return normalize_date(value)

    @field_validator("payment_terms")
    @classmethod
    def _normalize_payment_terms(cls, value: Optional[str]) -> Optional[str]:
        return normalize_payment_terms(value)


class ExtractedData(BaseModel):
    """
    Generic container for extraction results that don't follow the
    ExtractedContractData schema (e.g. future document types without a
    dedicated model yet).
    """
    document_type: str
    extracted_data: Dict[str, Any]
    confidence: float = 0.0
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        arbitrary_types_allowed = True
