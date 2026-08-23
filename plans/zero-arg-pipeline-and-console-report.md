# Zero-argument pipeline run with a console report

## Context

`python -m src.main` currently requires a path argument and prints the raw `model_dump()` dict of whatever it processed — a wall of Python `repr` that's unreadable for four documents and useless for reviewing extraction quality. The DB is written but never read back.

The target is a single command with no arguments that does the whole assignment loop:

```
python -m src.main
    │
    ├─ 1. iterate every PDF in the input folder
    ├─ 2. extract → persist to SQLite   (the pipeline, unchanged)
    └─ 3. read back from SQLite → pretty-print every field to console
```

The ordering matters and is the point of the request: **step 3 reads from the database, not from the objects step 2 held in memory.** That makes the printed output a genuine verification that persistence worked — a field that failed to store shows up as missing on screen. It also means a re-run with zero LLM calls (everything unchanged and skipped) still prints the full report, because the report's source is the DB rather than the current run's results.

The storage layer today is write-only (`is_unchanged`, `save`). Reading is the substantive new capability.

## Design

### 1. The input folder becomes configuration, not an argument

`config/pipeline.yaml`:

```yaml
# Folder scanned for documents to process. Relative paths resolve against
# the project root. Override with INPUT_FOLDER in the environment.
input_folder: sample_docs
```

loaded by `src/config/pipeline_config.py` → `PipelineConfig(input_folder: str)`, following the exact precedence already used by `load_llm_config` and `load_storage_config`: **argument > `INPUT_FOLDER` env var > YAML**. Same shape, same relative-path-against-project-root resolution, so all three config loaders stay consistent.

This is what makes "no arguments" work without hardcoding a path in Python — the folder is still changeable, just not on the command line.

### 2. Reading contracts back out

`AbstractContractRepository` gains one method:

```python
def fetch_all(self) -> List[StoredContract]
```

with a new read model (`src/models/stored_contract.py`) that pairs the audit-row metadata with the extracted data:

```python
class StoredContract(BaseModel):
    source_file: str
    processed_at: str
    status: str                              # extracted | failed | skipped_type
    error: Optional[str] = None
    data: Optional[ExtractedContractData] = None   # None for failed/unmapped rows
```

`SqliteContractRepository.fetch_all()` selects from `processed_documents` ordered by `document_type, source_file`, and for each row with a mapped table pair, loads its header and items via `TABLES_BY_DOCUMENT_TYPE`.

Two notes on the implementation:

- **The date conversion is free.** The DB stores ISO (`2025-01-01`); `ExtractedContractData`'s existing `field_validator` reparses whatever it's given and reformats to `mm-dd-yyyy`, so simply constructing the model from the row gives back the assignment's format with no extra code. Verified: `ExtractedContractData(start_date="2025-01-01")` → `01-01-2025`.
- **This is deliberately N+1 queries** (one per document for its header, one for its items). With a folder of documents that's trivially fast, and it keeps the code readable versus a multi-way join that has to de-duplicate header columns across item rows. If the folder ever grows to thousands, this is the spot to revisit — noted so the choice is explicit.

`NullContractRepository.fetch_all()` returns `[]`, keeping the no-database path working.

### 3. `ConsoleReporter` — a class, not print statements in main

`src/reporting/console_reporter.py`. Takes the `List[StoredContract]` and writes the report; no knowledge of SQL, no knowledge of the pipeline. Injected into the run like everything else, and unit-testable by capturing its output as a string.

Formatting is hand-rolled with stdlib only — no new dependency. The nested header/items shape is simple enough to format directly, and `rich` would be the only dependency in the project that exists purely for cosmetics. (Say the word if you'd rather have `rich`: it would give colored output and auto-sized tables, and it's contained to this one class.)

Proposed output:

```
════════════════════════════════════════════════════════════════════════════
  DOCUMENT DATA EXTRACTION — 4 documents
════════════════════════════════════════════════════════════════════════════

ORDER FORMS (2)
────────────────────────────────────────────────────────────────────────────

  ACME Order From.pdf                                          customer: acme

    Start date .............. 01-01-2025
    End date ................ 12-31-2025
    Amount .................. 162,000.00
    Payment terms ........... Net 30
    Billing address ......... 42 King George Street, London, UK
    Customer signature ...... Yes
    Technical acct manager .. Customer Support Manager assigned for the term

    Line items (2)
      #  Product                            Qty        Price        Total
      1  Platform - Enterprise                1    72,000.00    72,000.00
      2  Support - Premium                    2    45,000.00    90,000.00
                                                           ──────────────
                                                    Total     162,000.00

    Burst (all items) ....... 10% burst at no additional cost

  CloudShield Order Form.pdf                             customer: cloudshield
    ...

PURCHASE ORDERS (2)
────────────────────────────────────────────────────────────────────────────
    ...

════════════════════════════════════════════════════════════════════════════
  SUMMARY
════════════════════════════════════════════════════════════════════════════
  Documents ............... 4  (2 order forms, 2 purchase orders)
  Extracted ............... 4       Failed: 0
  Total contract value .... 648,000.00
  Line items .............. 8
  Reconciliation .......... 4/4 documents: line items match header total ✓

  Database ................ data/extractions.db
```

Three deliberate details:

- **Grouped by document type**, mirroring the SOs/POs table split — so the report reads like the database is structured.
- **Burst is collapsed when uniform.** Because a whole-order burst clause gets replicated onto every line item (the design decision from `canonical-contract-field-schema.md`), printing it per-row would repeat the same sentence 8 times. When every item's burst is identical it prints once as `Burst (all items)`; when they differ it prints per item. This makes the item-level model visible without making the output noisy.
- **The summary runs the reconciliation check** — comparing each header's `amount` against the sum of its line items. That's the self-checking property the relational split gives us, surfaced where it's actually seen rather than only available to someone who writes SQL. A mismatch is the cheapest available signal that the LLM misread a number, which is exactly what the ≥95% accuracy KPI needs.

Missing/null fields print as `—` rather than `None`, and failed documents print their error under a `FAILED` heading instead of an empty field block.

### 4. `main.py` becomes a pipeline runner

```python
def run() -> int:
    """Process every document in the configured folder, then report."""
    processor, repository = _build_pipeline()      # composition root
    processor.process_folder(pipeline_config.input_folder)
    ConsoleReporter().report(repository.fetch_all())
```

`_build_document_processor` is refactored slightly to also hand back the repository (the reporter needs it), which is a natural thing for a composition root to expose.

`process_document` / `process_multiple_documents` / `process_folder` stay as they are — they're the documented library API and the README references them.

**One decision to flag:** I'd keep an *optional* positional argument (`python -m src.main path/to/file.pdf`) as a debugging convenience. Running with no arguments does exactly what you asked; the override just avoids re-processing a whole folder when chasing one document. If you'd rather it be strictly zero-argument, that's a two-line deletion — say so and I'll drop it.

## Files

```
config/pipeline.yaml                  # (new) input_folder
src/config/pipeline_config.py         # (new) load_pipeline_config()
src/config/__init__.py                # (modified) export it
src/models/stored_contract.py         # (new) StoredContract read model
src/storage/contract_repository.py    # (modified) fetch_all() on ABC + both impls
src/reporting/__init__.py             # (new)
src/reporting/console_reporter.py     # (new) ConsoleReporter
src/main.py                           # (modified) run() with no arguments
test_comprehensive.py                 # (modified) fetch_all + reporter tests
README.md                             # (modified) usage + config
```

No new dependencies.

## Verification

- **`fetch_all` round trip**: save two contracts of different types to `:memory:`, fetch them back, assert every scalar field, item count, and line ordering survives — and that dates come back as `mm-dd-yyyy` despite being stored as ISO.
- **Failed and unmapped rows**: a `failed` document returns `StoredContract(status="failed", data=None)` with its error intact rather than being dropped or raising.
- **Empty database**: `fetch_all()` on a fresh DB returns `[]`, and the reporter renders a clean "no documents" message rather than a broken frame.
- **Reporter output**: render a known `StoredContract` list to a string and assert the field values, the collapsed-burst line, and the reconciliation count appear. Assert on *content*, not exact whitespace, so cosmetic tweaks don't break tests.
- **Reconciliation detection**: a contract whose items deliberately don't sum to its header amount must be counted as a mismatch, not silently pass.
- **Null repository**: `fetch_all()` returns `[]` without touching a database.
- **Full zero-arg run**: execute the real `run()` against `sample_docs/` with a stubbed LLM client and a temp DB, confirming the printed report is populated from the database — then run it a second time and confirm it prints the identical report with zero LLM calls, which is the proof that the report is sourced from SQLite rather than from the current run's in-memory results.

## Out of scope

- **Other output formats** (JSON/CSV export, writing the report to a file). Easy to add later — `ConsoleReporter` is one implementation of a shape that could sit behind a `Reporter` interface if a second format ever appears — but nothing needs it now.
- **Filtering/paging the report** (by customer, type, date). The folder is small enough to print whole; SQL is the right tool for anything selective, and the README already documents the queries.
- **A live accuracy pass.** Still needs `OPENROUTER_API_KEY`. Once it's set, this report is exactly the artifact to eyeball against the sample PDFs to score the ≥95% KPI.
