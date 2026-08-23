# Canonical, customer-aware contract field schema

## Context

The current extractors (`src/extractors/base_extractor.py`, `purchase_order_extractor.py`, `order_form_extractor.py`) each hardcode a generic e-commerce field list (`customer_name`, `vendor_name`, `order_number`, ...) that has nothing to do with the assignment's actual required fields: start/end date (`mm-dd-yyyy`), amount, payment terms (`Net xx`), billing address, a boolean customer signature, a list of line items (product/quantity/price/total), and two special terms — burst (item-level) and technical account manager. The assignment also states the *same* field list applies to both Order Forms and Purchase Orders, and explicitly asks for extraction "based on the customized definitions per customer and document type" with an adaptability KPI of ≥2 customer variations per document type.

I read all four `sample_docs` PDFs to ground this in real variation rather than guessing:

| | ACME (Order Form) | CloudShield (Order Form) | BrightOps (Purchase Order) | NovaFleet (Purchase Order) |
|---|---|---|---|---|
| Dates | "January 1, 2025" | "3/1/2025" | "03-01-2025" (already `mm-dd-yyyy`) | "02-01-2025" (already `mm-dd-yyyy`) |
| Payment terms | prose: "within thirty (30) days" → must derive `Net 30` | prose: "forty-five (45) days" → must derive `Net 45` | literal `Net 30` | literal `Net 45` |
| Items | flat table (Name/Start/End/Rate/Qty/Total) | multi-year subscription table, different column names | flat table matching the spec almost exactly | flat table matching the spec almost exactly |
| Burst clause | ~~one prose paragraph, whole-order scope~~ **CORRECTED**: 5%, scoped to "SaaS subscription" rows only | ~~one prose paragraph, whole-order scope~~ **CORRECTED**: 38.89% / 28.21% / 19.05% *per year*, cap 280,000 units, redeemable against a different product (Sensor) | ~~still one whole-order sentence~~ **CORRECTED**: labeled "Burst (Item Level)", 10%, scoped to "licensed analytics usage" | ~~whole-order sentence~~ **CORRECTED**: 15%, scoped to "monitored workloads" (ambiguous mapping) |
| TAM | **not present** — instead "Customer Support Manager and Product Architect" | **not present at all** | literally labeled "Technical Account Manager" | literally labeled "Technical Account Manager" |
| Signature | filled Name/Title/Date, no literal "Signed" text → True | blank underscores, nothing filled → False | "✔ Signed" → True | "✔ Signed" → True |

> **Correction (see `plans/structured-burst-terms.md`).** The burst row above originally recorded all four documents as stating one whole-order clause. That was wrong: re-reading the source text, **none** of the four is whole-order — every one scopes its burst to a subset of line items, and CloudShield varies the threshold per year. The item-level model chosen below is right; this justification for it was not. The prompt instruction derived from the original reading ("copy that same text into the burst field of every item") actively flattened CloudShield's three distinct thresholds, and has since been replaced.

This confirms real per-customer variation in wording, date format, and which special terms even exist — exactly what the assignment's adaptability KPI is testing. Per clarification from the project owner: **burst is modeled on each line item** (not as one document-level field), with the extraction instructed to copy the same burst text onto every item when the source document states it applies to the whole order (as all 4 samples currently do) rather than to one specific product.

Goal: replace the mismatched field lists with the assignment's real schema, built so field *descriptions* can be overridden per customer (config, not code), and normalize LLM output (dates, `Net xx`, boolean signature) so it reliably matches the spec's required formats.

## Design

**1. Unify the schema, delete the duplicate extractor classes.** Since the assignment defines one field list for both document types, `PurchaseOrderExtractor`/`OrderFormExtractor`/`BaseExtractor`'s generic-field logic (and their `_resolve_duplicates` alias dicts — ~150 lines total) are replaced by a single `ContractExtractor`. `document_type` remains a label threaded through (matches the assignment diagram's separate SOs/POs tables downstream) but no longer forks extraction behavior.

**2. Data model** (extend `src/models/extracted_data.py`, reusing its existing `FieldDefinition` shape rather than inventing a parallel one):
```python
class LineItem(BaseModel):
    product_name: str
    quantity: float
    price: float
    total_amount: float
    burst: Optional[str] = None   # per-item; replicated across items when the doc states one blanket clause

class ExtractedContractData(BaseModel):
    document_type: str
    customer_key: Optional[str]
    start_date: Optional[str]       # normalized to mm-dd-yyyy
    end_date: Optional[str]         # normalized to mm-dd-yyyy
    amount: Optional[float]
    payment_terms: Optional[str]    # normalized to "Net xx"
    billing_address: Optional[str]
    customer_signature: bool
    items: List[LineItem]
    technical_account_manager: Optional[str]
    confidence: float
    raw_response: Optional[str]     # unparsed LLM text, kept for debugging/audit
```
`start_date`/`end_date` get a pydantic `field_validator` that reparses whatever the LLM returned (via `dateutil.parser`) and reformats to `mm-dd-yyyy` — a safety net since LLM format compliance isn't 100%. `payment_terms` gets a light validator: if the raw value isn't already `Net \d+`, regex for `(\d+)\s*days?` and reformat; otherwise pass through. New dependency: `python-dateutil`.

**3. `FieldDefinitionRegistry`** (`src/config/field_registry.py`) — loads and merges YAML field definitions for the *scalar* fields only (`start_date`, `end_date`, `amount`, `payment_terms`, `billing_address`, `customer_signature`, `technical_account_manager`); `items`/`LineItem` stays a fixed structural part of the model and prompt (no sample shows per-customer variation in the *shape* of a line item, only in column naming, which the LLM handles from context). Layering: `config/field_schemas/base_fields.yaml` → optional per-customer override in `config/field_schemas/customers/{key}.yaml`, merged by field name (customer entries override `description`/`format_hint`, both merge into one list). Real profiles are populated for `acme`, `cloudshield`, `brightops`, `novafleet` using the actual wording observed above (e.g. ACME's `technical_account_manager` description mentions "may appear as Customer Support Manager"; CloudShield's stays generic so it correctly resolves to `null`). Unknown customers fall back to `base_fields.yaml` alone — safe default for anything landing in the folder that isn't one of the four.

**4. Customer identification without an extra LLM call.** Each customer profile declares `match_keywords` (e.g. `["BrightOps"]`). `DocumentProcessor` gets a small keyword scan over the raw PDF text to pick a `customer_key`, exactly mirroring its existing `_identify_document_type` heuristic — cheap, consistent with the existing pattern, no second network round-trip.

**5. Prompt.** New `src/prompts/templates/extract_contract_fields.txt` with the nested JSON shape (items array of objects). A small `SchemaPromptBuilder` (`src/prompts/schema_prompt_builder.py`) turns the registry's resolved `List[FieldDefinition]` into the "fields to extract" description block, so adding a field in YAML shows up in the prompt with no code change. The template's static wrapper text explicitly instructs: `mm-dd-yyyy` dates, deriving `Net xx` from prose, signature True/False inference (blank lines vs. filled/checked), and replicating a whole-order burst clause onto every item.

**6. `LLMService`** (`src/services/llm_service.py`) — generalize `extract_structured_data`'s call+parse-JSON logic into a prompt-agnostic `complete_json(prompt, model=None) -> Dict[str, Any]`, since the new nested prompt no longer fits a flat field-list method. `call_llm` is unchanged. This also makes verification easier: tests can inject a fake `client` (already supported via the constructor from the last refactor) that returns canned JSON, exercising the full parse/normalize/validate path with no network call.

**7. `ContractExtractor`** (`src/extractors/contract_extractor.py`, replaces the three old extractor files) — constructor takes `llm_service: LLMService`, `field_registry: FieldDefinitionRegistry` (DI). `.extract(text, document_type, customer_key) -> ExtractedContractData` resolves fields via the registry, renders the prompt, calls `llm_service.complete_json`, and validates the result into `ExtractedContractData`.

**8. `DocumentProcessor`** — keeps `_identify_document_type` as-is, adds the customer keyword match, and delegates to one injected `ContractExtractor` instead of dynamically importing a per-type extractor class (this also removes the two-dots-relative dynamic-import fragility that was in the old per-type branches).

## Files

```
config/field_schemas/
  base_fields.yaml
  customers/acme.yaml, cloudshield.yaml, brightops.yaml, novafleet.yaml
src/
  models/extracted_data.py        # + LineItem, ExtractedContractData, date/payment_terms validators (modified)
  config/field_registry.py        # FieldDefinitionRegistry (new)
  prompts/
    schema_prompt_builder.py       # (new)
    templates/extract_contract_fields.txt  # (new)
  services/
    llm_service.py                 # + complete_json(), extract_structured_data removed (modified)
    document_processor.py          # customer keyword match, delegates to ContractExtractor (modified)
  extractors/
    contract_extractor.py          # (new)
    base_extractor.py, purchase_order_extractor.py, order_form_extractor.py   # deleted
    __init__.py                    # updated export (modified)
  utils/normalization.py           # normalize_date, normalize_payment_terms helpers (new)
  main.py                          # wires FieldDefinitionRegistry + ContractExtractor (modified)
requirements.txt                   # + python-dateutil (modified)
test_comprehensive.py, final_test.py  # update to instantiate ContractExtractor/FieldDefinitionRegistry (modified)
```

## Out of scope (flagging, not doing now)
- Coverage KPI (≥3 document types): only Order Form and Purchase Order have real schemas/samples; a third type (e.g. invoice) would need its own field set and no sample exists to validate against.
- Persisting to a relational DB (the assignment diagram's SOs/POs tables) — this plan produces validated `ExtractedContractData` objects per document; storage is a separate follow-up.
- `confidence` stays a simple placeholder, as it already was.

## Verification
- Build a fake `client` (injected via `LLMService`'s existing `client` param) that returns hand-written JSON matching what a correct extraction of each of the 4 sample PDFs should look like, and run it through `ContractExtractor` end-to-end — confirms prompt building, registry merging (including the "unknown customer" fallback), JSON parsing, and the date/payment-terms/signature normalization all work without needing a live API key.
- Unit-check `FieldDefinitionRegistry` returns the right merged field list for each of the 4 customer keys plus an unmatched one.
- Unit-check date normalization on the three formats actually seen ("January 1, 2025", "3/1/2025", "02-15-2025") and payment-terms derivation on "within thirty (30) days" → `Net 30`.
- Update `test_comprehensive.py`/`final_test.py` to build `ContractExtractor` via DI (same pattern as the `LLMService` fix from the last change) and run them.
- A real end-to-end run against OpenRouter needs an `OPENROUTER_API_KEY` in `.env` — everything above is validated with a stubbed client instead; a live pass can be run afterward once implementation lands.
