"""
Export the current database to tests/ground_truth.yaml.

Run this ONCE against a run whose values a human has verified against the
source PDFs, then keep the result under version control. It is a capture
tool, not part of the test suite: re-running it against an unverified run
would quietly promote whatever that run produced into "truth".
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from src.config.storage_config import load_storage_config

TABLES = (("sales_orders", "sales_order_items", "sales_order_id"),
          ("purchase_orders", "purchase_order_items", "purchase_order_id"))

# Burst attachment that the source document leaves genuinely ambiguous, so
# it must not be asserted. NovaFleet's clause covers "monitored workloads",
# which maps to no single product line - across runs the model has attached
# it to 1 of 4 items and to all 4, both defensible readings. Freezing either
# would make the test fail about half the time, and a test people learn to
# ignore is worse than no test.
AMBIGUOUS_BURST_SCOPE = ("NovaFleet",)


def core_product_name(name):
    """
    The stable part of a product name.

    The model sometimes appends a period qualifier - "CloudShield Enterprise"
    on one run, "CloudShield Enterprise (Year 1)" on another, both correct.
    Ground truth stores the core, and comparison is 'actual contains expected'.
    """
    return name.split(" (")[0].strip()


def main():
    db = load_storage_config().database_path
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    documents = {}
    for header_table, items_table, fk in TABLES:
        for h in conn.execute(f"""SELECT h.*, p.source_file, p.document_type, p.customer_key
                                  FROM {header_table} h
                                  JOIN processed_documents p ON p.id = h.document_id"""):
            name = os.path.basename(h["source_file"])
            items = list(conn.execute(
                f"SELECT * FROM {items_table} WHERE {fk}=? ORDER BY line_number", (h["id"],)))

            ambiguous = any(k in name for k in AMBIGUOUS_BURST_SCOPE)

            documents[name] = {
                "document_type": h["document_type"],
                "customer_key": h["customer_key"],
                "start_date": h["start_date"],
                "end_date": h["end_date"],
                "amount": h["amount"],
                "payment_terms": h["payment_terms"],
                "billing_address": h["billing_address"],
                "customer_signature": bool(h["customer_signature"]),
                "signature_evidence": "present" if h["signature_evidence"] else "absent",
                "technical_account_manager":
                    "present" if h["technical_account_manager"] else "absent",
                "item_count": len(items),
                "burst_item_count":
                    "ambiguous" if ambiguous
                    else sum(1 for i in items if i["burst_raw_text"]),
                "items": [{
                    "product_name": core_product_name(i["product_name"]),
                    "quantity": i["quantity"],
                    "price": i["price"],
                    "total_amount": i["total_amount"],
                    "burst_percentage": i["burst_percentage"],
                    "burst_cap_units": i["burst_cap_units"],
                } for i in items],
            }

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "tests", "ground_truth.yaml")
    header = (
        "# Ground truth: the correct extraction for every sample document.\n"
        "#\n"
        "# Captured from a run whose values were verified by hand against the\n"
        "# source PDFs, then committed. Regenerate with\n"
        "# scripts/export_ground_truth.py only after re-verifying.\n"
        "#\n"
        "# How each field is compared (see tests/test_accuracy.py):\n"
        "#   exact     - document_type, customer_key, dates, amount,\n"
        "#               payment_terms, customer_signature, item_count,\n"
        "#               quantity, price, total_amount, burst_percentage,\n"
        "#               burst_cap_units\n"
        "#   contains  - billing_address, product_name (the model varies\n"
        "#               punctuation and appends period qualifiers)\n"
        "#   presence  - signature_evidence, technical_account_manager\n"
        "#               (free text; reworded every run, all correct)\n"
        "#   ambiguous - skipped entirely; the document does not determine it\n"
        "#\n"
        "# Only exact/contains fields count toward the accuracy denominator.\n\n")
    with open(out, "w") as f:
        f.write(header)
        yaml.safe_dump(documents, f, sort_keys=True, allow_unicode=True, width=100)
    print(f"wrote {out} ({len(documents)} documents)")


if __name__ == "__main__":
    main()
