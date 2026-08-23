"""
Accuracy against ground truth.

The assignment's headline KPI is >=95% correct field extraction. Nothing else
in the suite measures that: every other value assertion runs against stubbed
payloads, so they verify the plumbing, never that real extraction produces the
right answers. `Reconciliation 8/8` in the report only proves line items sum to
their header total - a document where every figure was misread consistently
would still reconcile.

`tests/ground_truth.yaml` holds the correct values, read off the PDFs by a
human. This scores extraction against them and reports a number.

Two runners:

  test_ground_truth_is_well_formed  - always on, free. Guards the file itself.
  test_accuracy_live                - opt-in (RUN_LIVE_ACCURACY=1). Calls the
                                      real model and prints the scorecard.

The live run is opt-in because it costs money and is non-deterministic; it must
never gate CI, but it is the only thing that produces a real KPI figure.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GROUND_TRUTH = os.path.join(HERE, "ground_truth.yaml")
SAMPLE_DOCS = os.path.join(ROOT, "sample_docs")

# Compared with ==. These have exactly one right answer.
EXACT_HEADER = ("document_type", "customer_key", "start_date", "end_date",
                "amount", "payment_terms", "customer_signature", "item_count")
EXACT_ITEM = ("quantity", "price", "total_amount", "burst_percentage",
              "burst_cap_units")

# Compared with "actual contains expected", after normalizing dashes and
# whitespace. The model varies punctuation and appends period qualifiers
# ("CloudShield Enterprise" vs "CloudShield Enterprise (Year 1)") - both
# correct, so equality would measure phrasing rather than accuracy.
CONTAINS_HEADER = ("billing_address",)

# Only checked for presence. Free text, reworded every run, all correct.
PRESENCE_HEADER = ("signature_evidence", "technical_account_manager")


def load_ground_truth():
    with open(GROUND_TRUTH) as f:
        return yaml.safe_load(f)


def _norm(value):
    """Fold dash variants and whitespace so punctuation isn't scored."""
    text = str(value or "")
    for dash in ("–", "—", "−"):
        text = text.replace(dash, "-")
    return " ".join(text.split()).lower()


def _same_date(expected, actual):
    """
    Compare dates by value, not by representation.

    Ground truth is exported from the database, which stores ISO
    (2025-07-01); the extraction API returns the assignment's mm-dd-yyyy
    (07-01-2025). Both denote the same day, so comparing the strings would
    score the storage format rather than the extraction.
    """
    from dateutil import parser

    if expected is None or actual is None:
        return expected == actual
    try:
        return parser.parse(str(expected)).date() == parser.parse(str(actual)).date()
    except (ValueError, OverflowError):
        return str(expected) == str(actual)


def score_document(expected, actual):
    """
    Compare one document against its ground truth.

    Args:
        expected: The ground-truth entry.
        actual: A dict of the extracted values, shaped like the export.

    Returns:
        tuple: (fields_correct, fields_checked, [failure descriptions])
    """
    correct = checked = 0
    failures = []

    def check(ok, name, exp, got):
        nonlocal correct, checked
        checked += 1
        if ok:
            correct += 1
        else:
            failures.append(f"{name}: expected {exp!r}, got {got!r}")

    for field in EXACT_HEADER:
        if field in ("start_date", "end_date"):
            ok = _same_date(expected.get(field), actual.get(field))
        else:
            ok = actual.get(field) == expected.get(field)
        check(ok, field, expected.get(field), actual.get(field))

    for field in CONTAINS_HEADER:
        check(_norm(expected.get(field)) in _norm(actual.get(field)), field,
              expected.get(field), actual.get(field))

    for field in PRESENCE_HEADER:
        want = expected.get(field) == "present"
        check(bool(actual.get(field)) == want, field,
              expected.get(field), actual.get(field))

    burst_ambiguous = expected.get("burst_item_count") == "ambiguous"

    # Burst attachment, unless the document leaves it ambiguous.
    if not burst_ambiguous:
        got = sum(1 for i in actual.get("items", []) if i.get("burst_percentage") is not None
                  or i.get("burst_raw_text"))
        check(got == expected["burst_item_count"], "burst_item_count",
              expected["burst_item_count"], got)

    for index, exp_item in enumerate(expected.get("items", [])):
        got_items = actual.get("items", [])
        if index >= len(got_items):
            checked += 1
            failures.append(f"items[{index}]: missing")
            continue
        got_item = got_items[index]

        check(_norm(exp_item["product_name"]) in _norm(got_item.get("product_name")),
              f"items[{index}].product_name",
              exp_item["product_name"], got_item.get("product_name"))

        for field in EXACT_ITEM:
            # A document that doesn't determine which items carry the burst
            # clause doesn't determine their percentages or caps either.
            if burst_ambiguous and field.startswith("burst_"):
                continue
            check(got_item.get(field) == exp_item.get(field),
                  f"items[{index}].{field}", exp_item.get(field), got_item.get(field))

    return correct, checked, failures


def test_ground_truth_is_well_formed():
    """
    The ground-truth file must cover every sample document and be internally
    consistent. A silently incomplete file would inflate any score computed
    from it.
    """
    print("Testing ground truth file...")

    truth = load_ground_truth()
    pdfs = {f for f in os.listdir(SAMPLE_DOCS) if f.endswith(".pdf")}

    assert set(truth) == pdfs, (
        f"ground truth and sample_docs disagree.\n"
        f"  missing from ground truth: {sorted(pdfs - set(truth))}\n"
        f"  no longer in sample_docs:  {sorted(set(truth) - pdfs)}")

    for name, entry in truth.items():
        assert entry["item_count"] == len(entry["items"]), \
            f"{name}: item_count {entry['item_count']} != {len(entry['items'])} items"
        assert entry["document_type"] in ("order_form", "purchase_order"), name
        # The stated total must equal the sum of the line items, or the
        # ground truth itself is wrong.
        total = sum(i["total_amount"] for i in entry["items"])
        assert abs(entry["amount"] - total) < 0.01, \
            f"{name}: amount {entry['amount']} != items total {total}"

    print(f"✓ ground truth covers {len(truth)} documents, all self-consistent")


def test_accuracy_live():
    """
    Score real extraction against ground truth. Opt-in: RUN_LIVE_ACCURACY=1.
    """
    if not os.environ.get("RUN_LIVE_ACCURACY"):
        print("- live accuracy run skipped (set RUN_LIVE_ACCURACY=1 to run)")
        return

    print("Running live accuracy scoring...")

    from src.config.field_registry import FieldDefinitionRegistry
    from src.config.llm_config import load_llm_config
    from src.extractors.contract_extractor import ContractExtractor
    from src.prompts.prompt_repository import PromptRepository
    from src.services.document_processor import DocumentProcessor
    from src.services.llm_service import LLMService

    truth = load_ground_truth()
    registry = FieldDefinitionRegistry()
    processor = DocumentProcessor(
        ContractExtractor(LLMService(load_llm_config()), registry, PromptRepository()),
        registry)

    total_correct = total_checked = 0
    print()
    for name in sorted(truth):
        result = processor.process_file(os.path.join(SAMPLE_DOCS, name))
        actual = {
            **result,
            "item_count": len(result.get("items", [])),
            "items": [{
                "product_name": i["product_name"], "quantity": i["quantity"],
                "price": i["price"], "total_amount": i["total_amount"],
                "burst_percentage": (i.get("burst") or {}).get("percentage"),
                "burst_cap_units": (i.get("burst") or {}).get("cap_units"),
                "burst_raw_text": (i.get("burst") or {}).get("raw_text"),
            } for i in result.get("items", [])],
        }
        correct, checked, failures = score_document(truth[name], actual)
        total_correct += correct
        total_checked += checked
        pct = 100.0 * correct / checked if checked else 0.0
        print(f"  {name[:44]:46s} {correct:>3}/{checked:<3} fields  {pct:5.1f}%")
        for failure in failures:
            print(f"       {failure}")

    pct = 100.0 * total_correct / total_checked if total_checked else 0.0
    print(f"\n  {'TOTAL':46s} {total_correct:>3}/{total_checked:<3} fields  {pct:5.1f}%")
    print("  (objectively checkable fields only - free text is presence-checked,")
    print("   and genuinely ambiguous fields are excluded)")

    assert pct >= 95.0, f"accuracy {pct:.1f}% is below the 95% target"
    print("✓ accuracy target met")


if __name__ == "__main__":
    test_ground_truth_is_well_formed()
    test_accuracy_live()
