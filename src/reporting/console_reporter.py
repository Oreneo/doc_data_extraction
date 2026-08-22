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
    ("signature_evidence", "Signature evidence"),
    ("technical_account_manager", "Technical account manager"),
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

    def report(
        self,
        contracts: List[StoredContract],
        database_path: Optional[str] = None,
        only_paths: Optional[set] = None,
    ) -> str:
        """
        Render the full report and print it.

        Args:
            contracts: Records to render, as returned by the repository.
            database_path: Shown in the summary so it's obvious where the
                data was read from.
            only_paths: Restrict the report to these source paths (what the
                current run touched). None renders everything stored.

        Returns:
            str: The rendered report, also written to stdout.
        """
        output = self.render(contracts, database_path, only_paths)
        print(output)
        return output

    def render(
        self,
        contracts: List[StoredContract],
        database_path: Optional[str] = None,
        only_paths: Optional[set] = None,
    ) -> str:
        """
        Render the full report without printing it (useful for tests).

        Args:
            contracts: Records to render.
            database_path: Shown in the summary.
            only_paths: Restrict the report to these source paths. Documents
                filtered out are still counted in a "not shown" line, so the
                report scopes to this run without pretending the rest of the
                database doesn't exist.

        Returns:
            str: The rendered report.
        """
        hidden = []
        if only_paths is not None:
            shown = [c for c in contracts if c.source_file in only_paths]
            hidden = [c for c in contracts if c.source_file not in only_paths]
            contracts = shown

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

        lines.extend(self._render_summary(contracts, database_path, hidden))
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

        # Surfaced against the document itself, not just tallied in the
        # summary: a dropped field is invisible in the extracted values by
        # definition, so the warning is the only thing that shows it.
        for warning in contract.warnings:
            lines.extend(self._field_lines("⚠ Warning", warning))

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

        uniform_burst = self._uniform_burst(data)

        for line_number, item in enumerate(data.items, start=1):
            lines.append(
                " " * ITEM_INDENT
                + f"{line_number:<{COL_NUMBER}}"
                + f"{self._truncate(item.product_name, COL_PRODUCT - 1):<{COL_PRODUCT}}"
                + f"{self._format_quantity(item.quantity):>{COL_QUANTITY}}"
                + f"{self._format_money(item.price):>{COL_PRICE}}"
                + f"{self._format_money(item.total_amount):>{COL_TOTAL}}"
            )
            # When one clause covers the whole order it's printed once below
            # the table instead, to avoid repeating the same sentence on
            # every row.
            if item.burst and not uniform_burst:
                lines.extend(self._burst_detail_lines(item.burst))

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

    def _uniform_burst(self, data: ExtractedContractData):
        """
        The single burst term shared by every line item, or None if the
        items differ (including any item having no burst at all).

        A clause covering the whole order gets replicated onto every item,
        and repeating the same sentence on each row would bury the rest of
        the output - so that case is collapsed to one entry below the table.
        Anything else is genuinely per-item and is printed per row.
        """
        bursts = [item.burst for item in data.items]
        if not bursts or not all(bursts):
            return None

        first = bursts[0]
        return first if all(b == first for b in bursts) else None

    def _render_burst(self, data: ExtractedContractData) -> List[str]:
        """Burst summary printed below the line-item table."""
        if not any(item.burst for item in data.items):
            return ["", *self._field_lines("Burst", EMPTY)]

        uniform = self._uniform_burst(data)
        if uniform is not None:
            # Same headline/attribute shape as the per-item rendering, so the
            # two paths present a burst identically.
            indent = " " * (4 + LABEL_WIDTH + 1)
            return ["", *self._field_lines("Burst (all items)", self._burst_headline(uniform)),
                    *self._burst_attribute_lines(uniform, indent=indent)]

        # Per-item bursts were already printed inline against their rows.
        covered = sum(1 for item in data.items if item.burst)
        return ["", *self._field_lines(
            "Burst", f"per item - {covered} of {len(data.items)} line items (shown above)"
        )]

    def _burst_detail_lines(self, burst) -> List[str]:
        """The burst block printed under its line item's row."""
        prefix = " " * (ITEM_INDENT + COL_NUMBER)
        headline = self._burst_headline(burst)
        lines = self._wrapped(headline, prefix=prefix + "Burst: ")
        lines.extend(self._burst_attribute_lines(burst, indent=prefix + "       "))
        return lines

    def _burst_headline(self, burst) -> str:
        """`38.89% of <basis>`, degrading to the raw clause when nothing was parsed."""
        if burst.percentage is None:
            return burst.raw_text
        headline = f"{self._format_percentage(burst.percentage)}"
        if burst.basis:
            headline += f" of {burst.basis}"
        return headline

    def _burst_attribute_lines(self, burst, indent: str) -> List[str]:
        """
        Cap / period / applies-to, joined on one line. Only fields that are
        present are shown, so a sparsely-parsed burst degrades to nothing
        rather than a row of placeholders.

        `applies_to` is deliberately the document's own wording rather than a
        product name we inferred: where a clause names usage that doesn't map
        cleanly onto one line item (NovaFleet's "monitored workloads"), the
        reader needs to see the original phrase to judge the mapping.
        """
        parts = []
        if burst.cap_units is not None:
            parts.append(f"cap {self._format_quantity(burst.cap_units)}")
        if burst.period:
            parts.append(burst.period)
        if burst.applies_to:
            parts.append(f"applies to {burst.applies_to}")

        return self._wrapped(" · ".join(parts), prefix=indent) if parts else []

    # -- summary ------------------------------------------------------------

    def _render_summary(
        self,
        contracts: List[StoredContract],
        database_path: Optional[str],
        hidden: Optional[List[StoredContract]] = None,
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

        flagged = [c for c in contracts if c.warnings]
        if flagged:
            lines.extend(self._field_lines(
                "Quality warnings",
                f"{self._count(len(flagged), 'document')} with a possible dropped field",
                indent="  "))

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

        # Account for documents the database holds but this run didn't touch,
        # so scoping the report doesn't hide that they exist.
        if hidden:
            hidden_extracted = sum(1 for c in hidden if c.data is not None)
            hidden_failed = sum(1 for c in hidden if c.status == "failed")
            detail = []
            if hidden_extracted:
                detail.append(f"{hidden_extracted} extracted")
            if hidden_failed:
                detail.append(f"{hidden_failed} failed")
            suffix = f" ({', '.join(detail)})" if detail else ""
            lines.extend(self._field_lines(
                "Not shown",
                f"{self._count(len(hidden), 'other document')} stored{suffix}",
                indent="  ",
            ))

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
            # The assignment specifies this field as True/False, so render it
            # that way rather than as Yes/No.
            return "True" if value else "False"
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
    def _format_percentage(value: float) -> str:
        return f"{value:g}%"

    @staticmethod
    def _format_quantity(value: Optional[float]) -> str:
        if value is None:
            return EMPTY
        # Quantities are floats on the model but almost always whole numbers;
        # print 2 rather than 2.0 unless there's a real fraction. Grouped,
        # since unit counts reach the hundreds of thousands.
        return f"{int(value):,}" if float(value).is_integer() else f"{value:,g}"

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
