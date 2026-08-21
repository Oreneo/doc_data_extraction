"""
Renders stored contract data as a human-readable console report.

Formats only - it takes already-loaded StoredContract objects and knows
nothing about SQL or the extraction pipeline.
"""

import textwrap
from typing import List, Optional

from ..models.extracted_data import ExtractedContractData
from ..models.stored_contract import StoredContract

WIDTH = 78

# Placeholder for a field the extraction didn't find, so an empty value is
# visibly "not present" rather than a blank that looks like a formatting bug.
EMPTY = "—"

# Tolerance when comparing a header amount against the sum of its line
# items: these are floats, and a cent of rounding is not a discrepancy.
RECONCILIATION_TOLERANCE = 0.01

# Section heading and singular noun for each document type. The plural is
# derived, so a single document doesn't read "1 order forms".
DOCUMENT_TYPES = {
    "order_form": ("ORDER FORMS", "order form"),
    "purchase_order": ("PURCHASE ORDERS", "purchase order"),
    "invoice": ("INVOICES", "invoice"),
    "generic": ("OTHER DOCUMENTS", "other document"),
}

FIELD_LABELS = [
    ("start_date", "Start date"),
    ("end_date", "End date"),
    ("amount", "Amount"),
    ("payment_terms", "Payment terms"),
    ("billing_address", "Billing address"),
    ("customer_signature", "Customer signature"),
    ("technical_account_manager", "Technical acct manager"),
]

# Width of the dotted leader column, wide enough for the longest label plus
# a few dots.
LABEL_WIDTH = max(len(label) for _, label in FIELD_LABELS) + 4

# Line-item table columns. These sum to less than WIDTH so the table never
# wraps; the totals rule below is aligned from the same numbers.
ITEM_INDENT = 6
COL_NUMBER = 3
COL_PRODUCT = 34
COL_QUANTITY = 6
COL_PRICE = 14
COL_TOTAL = 14


class ConsoleReporter:
    """
    Renders a list of StoredContract records as a grouped, aligned report.
    """

    def __init__(self, width: int = WIDTH):
        """
        Args:
            width: Total line width used for rules, headings, and wrapping.
        """
        self.width = width

    def report(self, contracts: List[StoredContract], database_path: Optional[str] = None) -> str:
        """
        Render the full report and print it.

        Args:
            contracts: Records to render, as returned by the repository.
            database_path: Shown in the summary so it's obvious where the
                data was read from.

        Returns:
            str: The rendered report, also written to stdout.
        """
        output = self.render(contracts, database_path)
        print(output)
        return output

    def render(self, contracts: List[StoredContract], database_path: Optional[str] = None) -> str:
        """
        Render the full report without printing it (useful for tests).

        Args:
            contracts: Records to render.
            database_path: Shown in the summary.

        Returns:
            str: The rendered report.
        """
        if not contracts:
            return "\n".join([
                self._heavy_rule(),
                "  DOCUMENT DATA EXTRACTION",
                self._heavy_rule(),
                "",
                "  No documents have been processed yet.",
                "",
            ])

        lines = [
            self._heavy_rule(),
            f"  DOCUMENT DATA EXTRACTION — {self._count(len(contracts), 'document')}",
            self._heavy_rule(),
        ]

        for document_type in self._ordered_types(contracts):
            group = [c for c in contracts if c.document_type == document_type]
            heading, _ = self._labels_for(document_type)

            lines.append("")
            lines.append(f"{heading} ({len(group)})")
            lines.append(self._light_rule())

            for contract in group:
                lines.extend(self._render_contract(contract))

        lines.extend(self._render_summary(contracts, database_path))
        return "\n".join(lines)

    # -- document rendering -------------------------------------------------

    def _render_contract(self, contract: StoredContract) -> List[str]:
        name = self._file_name(contract.source_file)

        if contract.data is None:
            return [
                "",
                f"  {name}",
                f"    STATUS: {contract.status.upper()}",
                *self._field_lines("Error", contract.error or EMPTY),
            ]

        data = contract.data
        customer = data.customer_key or "unrecognized"

        lines = ["", self._pad_between(f"  {name}", f"customer: {customer}"), ""]

        for attribute, label in FIELD_LABELS:
            lines.extend(
                self._field_lines(label, self._format_value(attribute, getattr(data, attribute)))
            )

        lines.extend(self._render_items(data))
        return lines

    def _render_items(self, data: ExtractedContractData) -> List[str]:
        if not data.items:
            return ["", *self._field_lines("Line items", EMPTY)]

        lines = [
            "",
            f"    Line items ({len(data.items)})",
            " " * ITEM_INDENT
            + f"{'#':<{COL_NUMBER}}{'Product':<{COL_PRODUCT}}"
            + f"{'Qty':>{COL_QUANTITY}}{'Price':>{COL_PRICE}}{'Total':>{COL_TOTAL}}",
        ]

        for line_number, item in enumerate(data.items, start=1):
            lines.append(
                " " * ITEM_INDENT
                + f"{line_number:<{COL_NUMBER}}"
                + f"{self._truncate(item.product_name, COL_PRODUCT - 1):<{COL_PRODUCT}}"
                + f"{self._format_quantity(item.quantity):>{COL_QUANTITY}}"
                + f"{self._format_money(item.price):>{COL_PRICE}}"
                + f"{self._format_money(item.total_amount):>{COL_TOTAL}}"
            )

        # Align the rule and the total under the Total column, using the same
        # column widths as the rows above.
        before_total = " " * (ITEM_INDENT + COL_NUMBER + COL_PRODUCT + COL_QUANTITY + COL_PRICE)
        before_label = " " * (ITEM_INDENT + COL_NUMBER + COL_PRODUCT + COL_QUANTITY)
        items_total = sum(item.total_amount for item in data.items)

        lines.append(before_total + "─" * COL_TOTAL)
        lines.append(
            before_label
            + f"{'Total':>{COL_PRICE}}"
            + f"{self._format_money(items_total):>{COL_TOTAL}}"
        )

        lines.extend(self._render_burst(data))
        return lines

    def _render_burst(self, data: ExtractedContractData) -> List[str]:
        """
        Burst is stored per line item, but a document usually states one
        clause covering the whole order, which is then replicated onto every
        item. Printing that identical sentence once per row would bury the
        rest of the output, so a uniform value collapses to a single line.
        """
        bursts = [item.burst for item in data.items]

        if not any(bursts):
            return ["", *self._field_lines("Burst", EMPTY)]

        if len(set(bursts)) == 1:
            return ["", *self._field_lines("Burst (all items)", bursts[0])]

        lines = ["", "    Burst (per item)"]
        for line_number, burst in enumerate(bursts, start=1):
            lines.extend(
                self._wrapped(
                    burst or EMPTY,
                    prefix=" " * ITEM_INDENT + f"{line_number:<{COL_NUMBER}}",
                )
            )
        return lines

    # -- summary ------------------------------------------------------------

    def _render_summary(
        self,
        contracts: List[StoredContract],
        database_path: Optional[str],
    ) -> List[str]:
        extracted = [c for c in contracts if c.data is not None]
        failed = [c for c in contracts if c.status == "failed"]

        breakdown = ", ".join(
            self._count(
                sum(1 for c in contracts if c.document_type == document_type),
                self._labels_for(document_type)[1],
            )
            for document_type in self._ordered_types(contracts)
        )

        total_value = sum(c.data.amount or 0.0 for c in extracted)
        item_count = sum(len(c.data.items) for c in extracted)
        reconciled = sum(1 for c in extracted if self._reconciles(c.data))

        lines = [
            "",
            self._heavy_rule(),
            "  SUMMARY",
            self._heavy_rule(),
            *self._field_lines("Documents", f"{len(contracts)}  ({breakdown})", indent="  "),
            *self._field_lines(
                "Extracted", f"{len(extracted)}       Failed: {len(failed)}", indent="  "
            ),
            *self._field_lines(
                "Total contract value", self._format_money(total_value), indent="  "
            ),
            *self._field_lines("Line items", str(item_count), indent="  "),
        ]

        if extracted:
            mark = "✓" if reconciled == len(extracted) else "✗"
            lines.extend(
                self._field_lines(
                    "Reconciliation",
                    f"{reconciled}/{len(extracted)} line items match header total {mark}",
                    indent="  ",
                )
            )

        if failed:
            lines.append("")
            for contract in failed:
                lines.append(f"  FAILED: {self._file_name(contract.source_file)}")
                lines.extend(self._wrapped(contract.error or EMPTY, prefix=" " * 10))

        if database_path:
            lines.append("")
            lines.extend(self._field_lines("Database", database_path, indent="  "))

        lines.append("")
        return lines

    def _reconciles(self, data: ExtractedContractData) -> bool:
        """
        Whether a contract's line items sum to its stated total.

        A mismatch is the cheapest available signal that the LLM misread a
        number, which is why it's surfaced in the summary rather than left
        to whoever thinks to run the SQL.
        """
        if data.amount is None or not data.items:
            return True

        items_total = sum(item.total_amount for item in data.items)
        return abs(data.amount - items_total) <= RECONCILIATION_TOLERANCE

    # -- formatting helpers -------------------------------------------------

    def _field_lines(self, label: str, value: str, indent: str = "    ") -> List[str]:
        """
        One `label ..... value` line, wrapped to the report width with the
        continuation lines hanging under the value column.
        """
        leader = indent + (label + " ").ljust(LABEL_WIDTH, ".") + " "
        return self._wrapped(value, prefix=leader)

    def _wrapped(self, value: str, prefix: str) -> List[str]:
        """Wrap `value` after `prefix`, indenting continuation lines to line
        up under the first one."""
        available = max(self.width - len(prefix), 20)
        # break_on_hyphens=False keeps hyphenated product names and file
        # paths intact - splitting "Enterprise-Tier" or a path at a hyphen
        # reads as though the hyphen were inserted by the wrapper.
        chunks = textwrap.wrap(str(value), width=available, break_on_hyphens=False) or [EMPTY]
        hanging = " " * len(prefix)
        return [prefix + chunks[0]] + [hanging + chunk for chunk in chunks[1:]]

    def _ordered_types(self, contracts: List[StoredContract]) -> List[str]:
        """Document types present, in the order they appear in
        DOCUMENT_TYPES, with any unknown types after them."""
        present = {c.document_type for c in contracts}
        known = [t for t in DOCUMENT_TYPES if t in present]
        return known + sorted(present - set(known))

    @staticmethod
    def _labels_for(document_type: str) -> tuple:
        return DOCUMENT_TYPES.get(document_type, (document_type.upper(), document_type))

    @staticmethod
    def _count(number: int, singular: str) -> str:
        return f"{number} {singular}" if number == 1 else f"{number} {singular}s"

    @staticmethod
    def _file_name(source_file: str) -> str:
        return source_file.rsplit("/", 1)[-1]

    def _format_value(self, attribute: str, value) -> str:
        if attribute == "customer_signature":
            return "Yes" if value else "No"
        if value is None or value == "":
            return EMPTY
        if attribute == "amount":
            return self._format_money(value)
        # Values often arrive with embedded newlines (addresses especially);
        # collapse them so wrapping controls the layout.
        return " ".join(str(value).split())

    @staticmethod
    def _format_money(value: Optional[float]) -> str:
        return EMPTY if value is None else f"{value:,.2f}"

    @staticmethod
    def _format_quantity(value: Optional[float]) -> str:
        if value is None:
            return EMPTY
        # Quantities are floats on the model but almost always whole numbers;
        # print 2 rather than 2.0 unless there's a real fraction.
        return str(int(value)) if float(value).is_integer() else f"{value:g}"

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _pad_between(self, left: str, right: str) -> str:
        gap = self.width - len(left) - len(right)
        return left + " " * max(gap, 1) + right

    def _heavy_rule(self) -> str:
        return "═" * self.width

    def _light_rule(self) -> str:
        return "─" * self.width
