"""
SQLite connection management and schema initialization.

SQLite is a full relational engine embedded in the process, so there is no
server to run - the whole database is one file (or ":memory:").
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

IN_MEMORY_PATH = ":memory:"

# Columns added to existing tables after the initial schema shipped.
# `CREATE TABLE IF NOT EXISTS` leaves an already-created table untouched, so
# new columns have to be added explicitly or an older database silently
# keeps the old shape and fails on INSERT. See _apply_additive_migrations.
ADDED_COLUMNS = {
    "sales_order_items": [
        ("term_months", "REAL"),
        ("price_period", "TEXT"),
        ("burst_raw_text", "TEXT"),
        ("burst_percentage", "REAL"),
        ("burst_basis", "TEXT"),
        ("burst_cap_units", "REAL"),
        ("burst_period", "TEXT"),
        ("burst_applies_to", "TEXT"),
    ],
    "purchase_order_items": [
        ("term_months", "REAL"),
        ("price_period", "TEXT"),
        ("burst_raw_text", "TEXT"),
        ("burst_percentage", "REAL"),
        ("burst_basis", "TEXT"),
        ("burst_cap_units", "REAL"),
        ("burst_period", "TEXT"),
        ("burst_applies_to", "TEXT"),
    ],
}


class Database:
    """
    Owns the SQLite connection and applies the schema.

    Holds a single long-lived connection for its lifetime rather than
    opening one per operation. That is required for ":memory:" to work at
    all: each new connection to ":memory:" gets its own fresh, empty
    database, so a connection-per-operation design would lose the schema
    (and all data) between calls.
    """

    def __init__(
        self,
        database_path: str,
        schema_path: Path = DEFAULT_SCHEMA_PATH,
    ):
        """
        Args:
            database_path: Path to the SQLite file, or ":memory:" for a
                transient in-memory database.
            schema_path: Path to the DDL file applied on startup.
        """
        self.database_path = database_path
        self.schema_path = Path(schema_path)

        if database_path != IN_MEMORY_PATH:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        self._connection = sqlite3.connect(database_path)
        # Return rows that can be addressed by column name, so callers read
        # row["amount"] rather than row[4].
        self._connection.row_factory = sqlite3.Row

        # SQLite disables foreign key enforcement by default, per connection.
        # Without this, every REFERENCES / ON DELETE CASCADE clause in
        # schema.sql is inert: orphaned rows are accepted and cascades never
        # fire. This single line is what makes the header -> items
        # relationship a real constraint rather than documentation.
        self._connection.execute("PRAGMA foreign_keys = ON")

        self._apply_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying SQLite connection."""
        return self._connection

    def _apply_schema(self) -> None:
        """Apply the DDL. All statements are CREATE ... IF NOT EXISTS, so
        this is safe to run against an existing database."""
        with open(self.schema_path, "r") as f:
            self._connection.executescript(f.read())
        self._apply_additive_migrations()
        self._connection.commit()

    def _apply_additive_migrations(self) -> None:
        """
        Add any columns missing from an existing database.

        `CREATE TABLE IF NOT EXISTS` is a no-op on a table that already
        exists, so a database created by an earlier version keeps its old
        columns and fails on INSERT. Every schema change so far has been
        purely additive, which `ALTER TABLE ... ADD COLUMN` handles without
        touching stored rows.

        Preserving the existing rows matters beyond convenience: dropping
        the database would also discard the content hashes that let unchanged
        documents skip extraction, forcing a full re-run against a
        rate-limited free tier.
        """
        for table, columns in ADDED_COLUMNS.items():
            existing = {
                row["name"]
                for row in self._connection.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue  # table doesn't exist yet; schema.sql just created it

            for column, column_type in columns:
                if column not in existing:
                    self._connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
                    )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """
        Run a unit of work in a transaction, committing on success and
        rolling back if the body raises.

        A document's header row and all its item rows are written inside one
        of these, so a failure partway through can't leave a half-written
        contract behind.

        Yields:
            sqlite3.Connection: The connection to execute statements on.
        """
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def close(self) -> None:
        """Close the connection. For ":memory:" this discards the database."""
        self._connection.close()
