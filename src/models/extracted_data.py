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


class BurstTerm(BaseModel):
    """
    A "burst" special term: an allowance to exceed the purchased quantity at
    no additional cost.

    `raw_text` is always populated; every other field is a best-effort
    structured reading of that same sentence. That ordering is deliberate -
    if structured parsing fails entirely, the clause itself is still
    captured, so this is never worse than storing the sentence alone.

    Deliberately does NOT carry a computed allowance. Only one of the sample
    documents (CloudShield) states the quantity its percentage applies to;
    for the rest, multiplying the percentage by the line item's `quantity`
    would invent a number the contract never states.
    """
    raw_text: str
    percentage: Optional[float] = None    # 38.89 for "up to 38.89%"
    basis: Optional[str] = None           # what the percentage is of
    cap_units: Optional[float] = None     # absolute ceiling, if the clause sets one
    period: Optional[str] = None          # "per consecutive 24 months"
    applies_to: Optional[str] = None      # product/usage it is redeemable against,
                                          # in the document's own words


class LineItem(BaseModel):
    """
    A single line item within a document's items list.
    """
    product_name: str
    quantity: float
    price: float
    total_amount: float
    # Term and price basis, where the document states them. These exist to
    # explain totals that aren't simply price x quantity: CloudShield prices
    # per unit per *month* over a 12-month term, so 180,000 x $3.25 is
    # $585,000 while the stated total is $7,020,000. total_amount remains
    # authoritative and is never recomputed from these.
    term_months: Optional[float] = None
    price_period: Optional[str] = None     # "monthly" | "one_time"
    # Per-item burst term, attached only to the items the clause actually
    # covers (see extract_contract_fields.txt).
    burst: Optional[BurstTerm] = None


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
