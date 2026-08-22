"""
Persistence for extracted contract data.

The abstract repository is what DocumentProcessor depends on, so the
extraction layer never imports sqlite3 and a different backing store stays
a swap of one class.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional

from ..models.extracted_data import BurstTerm, ExtractedContractData, LineItem
from ..models.stored_contract import StoredContract
from ..utils.normalization import to_iso_date
from .database import Database

# Routes a document type to its header/items table pair, per the assignment
# diagram's split (SOs / POs). Adding a third document type is an entry here
# plus its DDL - not a branch in the save path.
TABLES_BY_DOCUMENT_TYPE = {
    "order_form": ("sales_orders", "sales_order_items", "sales_order_id"),
    "purchase_order": ("purchase_orders", "purchase_order_items", "purchase_order_id"),
}

STATUS_EXTRACTED = "extracted"
STATUS_FAILED = "failed"
# Recognized and processed, but this document type has no header/items
# tables of its own (e.g. invoice/generic). The audit row is still written
# so the run's report can account for the file.
STATUS_SKIPPED_TYPE = "skipped_type"


class AbstractContractRepository(ABC):
    """
    Interface for persisting extraction results.
    """

    @abstractmethod
    def save(
        self,
        result: ExtractedContractData,
        source_file: str,
        content_hash: str,
    ) -> Optional[int]:
        """
        Persist an extraction result, replacing any previous record for the
        same source file.

        Args:
            result: The extraction result to store.
            source_file: Path of the source document.
            content_hash: Hash of the document's extracted text.

        Returns:
            Optional[int]: The processed_documents row id, or None if the
                implementation does not persist.
        """

    @abstractmethod
    def fetch_all(self) -> List[StoredContract]:
        """
        Load every stored document, including ones that failed extraction.

        Returns:
            List[StoredContract]: All stored records, grouped by document
                type and ordered by source file.
        """

    @abstractmethod
    def delete(self, source_file: str) -> bool:
        """
        Remove a stored document and everything beneath it.

        Every run re-extracts every document, so this is not needed to force
        a refresh - it is for clearing out records of documents that have
        left the input folder.

        Args:
            source_file: Canonical path of the document to remove.

        Returns:
            bool: True if a row was removed, False if none matched.
        """

    @abstractmethod
    def delete_all(self) -> int:
        """
        Remove every stored document.

        Returns:
            int: How many documents were removed.
        """


class NullContractRepository(AbstractContractRepository):
    """
    Does nothing. Lets the extraction pipeline run without a database (and
    without `if repository is not None` checks scattered through
    DocumentProcessor).
    """

    def save(
        self,
        result: ExtractedContractData,
        source_file: str,
        content_hash: str,
    ) -> Optional[int]:
        return None

    def fetch_all(self) -> List[StoredContract]:
        return []

    def delete(self, source_file: str) -> bool:
        return False

    def delete_all(self) -> int:
        return 0


class SqliteContractRepository(AbstractContractRepository):
    """
    Persists extraction results into the SQLite schema described in
    schema.sql: an audit row in processed_documents, plus a header row and
    its line items in the table pair for the document's type.
    """

    def __init__(self, database: Database):
        """
        Args:
            database: Connection/schema owner to write through.
        """
        self.database = database

    def save(
        self,
        result: ExtractedContractData,
        source_file: str,
        content_hash: str,
    ) -> Optional[int]:
        """
        Write the result in a single transaction, so a document is never
        left half-stored.
        """
        with self.database.transaction() as conn:
            # Replace rather than update: deleting the audit row cascades to
            # the header and its items, so a re-extraction can't leave stale
            # line items from a previous run behind. This depends on
            # PRAGMA foreign_keys = ON (see Database).
            conn.execute("DELETE FROM processed_documents WHERE source_file = ?", (source_file,))

            document_id = self._insert_audit_row(conn, result, source_file, content_hash)

            tables = TABLES_BY_DOCUMENT_TYPE.get(result.document_type)
            if result.error is None and tables is not None:
                self._insert_contract(conn, document_id, result, tables)

            return document_id

    def fetch_all(self) -> List[StoredContract]:
        """
        Load every stored document with its header fields and line items.

        Deliberately issues a couple of small queries per document rather
        than one wide join: a join across header and items repeats every
        header column on each item row and has to be de-duplicated in
        Python. Document counts here are folder-sized, so the clearer code
        wins; this is the place to revisit if that ever stops being true.
        """
        document_rows = self.database.connection.execute(
            """
            SELECT * FROM processed_documents
            ORDER BY document_type, source_file
            """
        ).fetchall()

        return [self._load_stored_contract(row) for row in document_rows]

    def delete(self, source_file: str) -> bool:
        """
        Remove one document. Its header and line-item rows go with it via
        ON DELETE CASCADE (which requires PRAGMA foreign_keys = ON - see
        Database), so no manual cleanup of child tables is needed.
        """
        with self.database.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM processed_documents WHERE source_file = ?", (source_file,)
            )
            return cursor.rowcount > 0

    def delete_all(self) -> int:
        """Remove every document, cascading to headers and line items."""
        with self.database.transaction() as conn:
            cursor = conn.execute("DELETE FROM processed_documents")
            return cursor.rowcount

    def _load_stored_contract(self, document_row) -> StoredContract:
        stored = StoredContract(
            source_file=document_row["source_file"],
            document_type=document_row["document_type"],
            processed_at=document_row["processed_at"],
            status=document_row["status"],
            error=document_row["error"],
            warnings=(document_row["warnings"] or "").splitlines(),
        )

        tables = TABLES_BY_DOCUMENT_TYPE.get(document_row["document_type"])
        if document_row["status"] != STATUS_EXTRACTED or tables is None:
            return stored

        header_table, items_table, foreign_key = tables

        header = self.database.connection.execute(
            f"SELECT * FROM {header_table} WHERE document_id = ?",
            (document_row["id"],),
        ).fetchone()

        if header is None:
            return stored

        item_rows = self.database.connection.execute(
            f"SELECT * FROM {items_table} WHERE {foreign_key} = ? ORDER BY line_number",
            (header["id"],),
        ).fetchall()

        # Dates come back out as ISO (that's how they're stored), but
        # ExtractedContractData's validator reparses and reformats them to
        # the assignment's mm-dd-yyyy - so no conversion is needed here.
        stored.data = ExtractedContractData(
            document_type=document_row["document_type"],
            customer_key=header["customer_key"],
            start_date=header["start_date"],
            end_date=header["end_date"],
            amount=header["amount"],
            payment_terms=header["payment_terms"],
            billing_address=header["billing_address"],
            customer_signature=bool(header["customer_signature"]),
            signature_evidence=header["signature_evidence"],
            technical_account_manager=header["technical_account_manager"],
            confidence=document_row["confidence"] or 0.0,
            items=[self._load_line_item(item) for item in item_rows],
        )
        return stored

    @staticmethod
    def _load_line_item(row) -> LineItem:
        # burst_raw_text is populated for every stored burst, so its absence
        # is what distinguishes "no burst term" from "burst with no
        # structured fields parsed".
        burst = (
            BurstTerm(
                raw_text=row["burst_raw_text"],
                percentage=row["burst_percentage"],
                basis=row["burst_basis"],
                cap_units=row["burst_cap_units"],
                period=row["burst_period"],
                applies_to=row["burst_applies_to"],
            )
            if row["burst_raw_text"]
            else None
        )

        return LineItem(
            product_name=row["product_name"],
            quantity=row["quantity"],
            price=row["price"],
            total_amount=row["total_amount"],
            term_months=row["term_months"],
            price_period=row["price_period"],
            burst=burst,
        )

    def _status_for(self, result: ExtractedContractData) -> str:
        if result.error is not None:
            return STATUS_FAILED
        if result.document_type not in TABLES_BY_DOCUMENT_TYPE:
            return STATUS_SKIPPED_TYPE
        return STATUS_EXTRACTED

    def _insert_audit_row(
        self,
        conn,
        result: ExtractedContractData,
        source_file: str,
        content_hash: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO processed_documents (
                source_file, content_hash, document_type, customer_key,
                status, error, confidence, raw_response, warnings, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_file,
                content_hash,
                result.document_type,
                result.customer_key,
                self._status_for(result),
                result.error,
                result.confidence,
                result.raw_response,
                "\n".join(result.warnings) if result.warnings else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return cursor.lastrowid

    def _insert_contract(
        self,
        conn,
        document_id: int,
        result: ExtractedContractData,
        tables: tuple,
    ) -> None:
        # Table/column names are interpolated because SQL placeholders can
        # only bind values, not identifiers. This is not an injection risk:
        # `tables` comes from the hardcoded TABLES_BY_DOCUMENT_TYPE mapping
        # above, never from document content. Every *value* is still bound.
        header_table, items_table, foreign_key = tables

        cursor = conn.execute(
            f"""
            INSERT INTO {header_table} (
                document_id, customer_key, start_date, end_date, amount,
                payment_terms, billing_address, customer_signature,
                signature_evidence, technical_account_manager
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                result.customer_key,
                to_iso_date(result.start_date),
                to_iso_date(result.end_date),
                result.amount,
                result.payment_terms,
                result.billing_address,
                int(result.customer_signature),
                result.signature_evidence,
                result.technical_account_manager,
            ),
        )
        header_id = cursor.lastrowid

        conn.executemany(
            f"""
            INSERT INTO {items_table} (
                {foreign_key}, line_number, product_name, quantity, price,
                total_amount, term_months, price_period,
                burst_raw_text, burst_percentage, burst_basis,
                burst_cap_units, burst_period, burst_applies_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    header_id,
                    line_number,
                    item.product_name,
                    item.quantity,
                    item.price,
                    item.total_amount,
                    item.term_months,
                    item.price_period,
                    item.burst.raw_text if item.burst else None,
                    item.burst.percentage if item.burst else None,
                    item.burst.basis if item.burst else None,
                    item.burst.cap_units if item.burst else None,
                    item.burst.period if item.burst else None,
                    item.burst.applies_to if item.burst else None,
                )
                for line_number, item in enumerate(result.items, start=1)
            ],
        )
