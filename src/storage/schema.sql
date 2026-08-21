-- Schema for extracted contract data.
--
-- Layout follows the assignment's Technical Overview diagram: separate
-- header/items table pairs per document type (SOs -> SOs Items, POs -> POs
-- Items), with a foreign key enforcing the 1-to-many relationship.
--
--   processed_documents (audit / idempotency)
--       |
--       +-- 0..1 -- sales_orders    --< sales_order_items
--       +-- 0..1 -- purchase_orders --< purchase_order_items
--
-- NOTE: SQLite only enforces the FOREIGN KEY clauses below when
-- `PRAGMA foreign_keys = ON` has been issued on the connection. Database
-- does that on connect; without it the ON DELETE CASCADE rules silently
-- do nothing. See src/storage/database.py.


-- One row per processed file. This is the idempotency key (source_file is
-- UNIQUE, content_hash detects changes) and the audit trail: unlike the
-- data tables below, it also records documents whose type has no table
-- pair (invoice/generic) and documents whose extraction failed.
CREATE TABLE IF NOT EXISTS processed_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file   TEXT    NOT NULL UNIQUE,
    content_hash  TEXT    NOT NULL,
    document_type TEXT    NOT NULL,   -- order_form | purchase_order | invoice | generic
    customer_key  TEXT,               -- NULL when the customer is unrecognized
    status        TEXT    NOT NULL,   -- extracted | failed | skipped_type
    error         TEXT,
    confidence    REAL,
    raw_response  TEXT,               -- unparsed LLM text, kept for audit/debugging
    processed_at  TEXT    NOT NULL    -- ISO 8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_processed_documents_type
    ON processed_documents (document_type);


-- Order Forms ("SOs Tables" in the assignment diagram).
CREATE TABLE IF NOT EXISTS sales_orders (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id               INTEGER NOT NULL UNIQUE
                              REFERENCES processed_documents (id) ON DELETE CASCADE,
    customer_key              TEXT,
    start_date                TEXT,     -- ISO 8601 (YYYY-MM-DD) so it sorts correctly
    end_date                  TEXT,
    amount                    REAL,
    payment_terms             TEXT,     -- "Net xx"
    billing_address           TEXT,
    customer_signature        INTEGER NOT NULL DEFAULT 0,   -- SQLite has no BOOLEAN; 0/1
    technical_account_manager TEXT
);

CREATE TABLE IF NOT EXISTS sales_order_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sales_order_id INTEGER NOT NULL
                   REFERENCES sales_orders (id) ON DELETE CASCADE,
    line_number    INTEGER NOT NULL,   -- preserves document order; SQL rows are unordered
    product_name   TEXT    NOT NULL,
    quantity       REAL,
    price          REAL,
    total_amount   REAL,
    burst          TEXT                -- per-item special term
);

CREATE INDEX IF NOT EXISTS idx_sales_order_items_parent
    ON sales_order_items (sales_order_id);


-- Purchase Orders ("POs Tables" in the assignment diagram). Column-identical
-- to the sales order pair above, because extraction uses one unified field
-- schema for both document types; only the destination differs.
CREATE TABLE IF NOT EXISTS purchase_orders (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id               INTEGER NOT NULL UNIQUE
                              REFERENCES processed_documents (id) ON DELETE CASCADE,
    customer_key              TEXT,
    start_date                TEXT,
    end_date                  TEXT,
    amount                    REAL,
    payment_terms             TEXT,
    billing_address           TEXT,
    customer_signature        INTEGER NOT NULL DEFAULT 0,
    technical_account_manager TEXT
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_id INTEGER NOT NULL
                      REFERENCES purchase_orders (id) ON DELETE CASCADE,
    line_number       INTEGER NOT NULL,
    product_name      TEXT    NOT NULL,
    quantity          REAL,
    price             REAL,
    total_amount      REAL,
    burst             TEXT
);

CREATE INDEX IF NOT EXISTS idx_purchase_order_items_parent
    ON purchase_order_items (purchase_order_id);
