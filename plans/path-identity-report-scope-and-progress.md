# Path identity, report scope, and run progress

## Context

A single-document run (`python3 src/main.py "sample_docs/ACME Order From.pdf"`) printed a five-document report containing ACME twice, three stale failures, and a contract total of exactly double the real figure. Investigating turned up one genuine data-integrity bug, one design decision that has outlived its original justification, and one missing feature.

### Defect 1 — the same document can occupy two rows (data integrity)

```
id=1  extracted  11:43  '/Users/orenn/Dev/.../sample_docs/ACME Order From.pdf'   (absolute)
id=5  extracted  13:00  'sample_docs/ACME Order From.pdf'                        (relative)
       content_hash on both: 97693d382a6e...
```

`process_folder` builds absolute paths (the input folder is resolved against the project root by `load_pipeline_config`), while a path typed on the command line is stored exactly as typed. `processed_documents.source_file` is `UNIQUE`, but the constraint compares raw strings, so two spellings of one file are two documents as far as SQLite is concerned.

The consequences are not cosmetic:

- **Aggregates are wrong.** `SUM(amount)` over `sales_orders` reads 305,000.00 where the real figure is 152,500.00; line items read 10 instead of 5. Anything built on these tables inherits the error.
- **Idempotency silently fails.** `is_unchanged()` looks up by `source_file`, misses the differently-spelled row, and re-extracts — re-paying for an LLM call whose entire purpose was to be skipped. The identical `content_hash` proves the pipeline had everything it needed to know it was the same file.
- **It is invisible.** No error, no warning; just a quietly duplicated document.

The hash is the honest identity of a document's *content*; the path is the identity of its *location*. The bug is that we compare locations without first canonicalising them.

### Defect 2 — the report's scope no longer matches how the tool is used

`run()` prints `repository.fetch_all()` — every row in the database, regardless of what the run touched. That was a reasonable reading of "print them all out" when the only entry point processed a whole folder. With a single-document argument it is actively misleading: the user asked for one document and got five, three of them failures from an hour earlier, presented with no indication they were historical.

Stale rows are legitimately *in* the database and should stay there — the audit trail is the point. The problem is that the report presents "everything ever stored" as though it were "what just happened".

### Defect 3 — no progress output

`process_folder` prints one line, then nothing until every document is done. During that window the process is making LLM calls that take seconds each, retrying on failure with exponential backoff. A blank screen is indistinguishable from a hang — which is precisely what happened when an empty-content crash sent `call_llm` into a five-attempt retry loop and the run appeared dead for ten minutes.

## Design

### 1. Canonical paths, resolved at the boundary

`DocumentProcessor.process_file` resolves the incoming path to a canonical absolute form **once, at entry**, and uses that for hashing lookup, storage, and reporting:

```python
canonical = str(Path(file_path).resolve())
```

`Path.resolve()` gives an absolute path with `..`/`.` collapsed and symlinks followed, so `sample_docs/x.pdf`, `./sample_docs/x.pdf`, and the absolute form all converge on one string. Doing it at the boundary means every downstream consumer — repository, reporter, `is_unchanged` — sees one spelling and needs no knowledge of this problem.

The original path is not preserved. It carries no information the canonical form lacks, and keeping both would just reintroduce the question of which one is authoritative.

### 2. Repairing the rows already stored

Normalising future writes does nothing about the duplicate sitting in the database, and that row is not disposable: it represents an extraction that cost money.

A migration step in `Database`, alongside the existing additive column migration:

1. Read every `(id, source_file, processed_at)` from `processed_documents`.
2. Compute the canonical path for each.
3. Group by canonical path. Where a group has more than one row, **keep the most recently processed** and delete the rest — their header and item rows cascade away with them.
4. Rewrite each surviving row's `source_file` to its canonical form.

Keeping the newest is the defensible choice: a later extraction reflects the current code, prompt, and model, so it is the row a re-run would have produced anyway. The rows discarded are by construction duplicates of the same file — the ACME pair share a content hash, so nothing unique is lost.

This runs inside a transaction and reports what it did (`"merged 1 duplicate document path"`) rather than doing it silently — a migration that quietly deletes rows is a migration nobody trusts.

**Flagging the alternative:** deleting `data/extractions.db` also fixes it, and at ~$0.02 per document a full re-extraction costs about eight cents. I'm proposing the migration anyway because "delete your data to work around our bug" is a bad habit to build into a pipeline whose whole purpose is accumulating extractions, and because the same code will be needed the moment this runs against a folder someone cares about.

### 3. Report scopes to the run, with the database still visible

`run()` tracks the canonical paths it examined — including ones skipped as unchanged, since those were genuinely part of this run's scope — and passes them to the reporter as a filter.

The summary then closes with a line accounting for everything else:

```
  Database ................. data/extractions.db
  Not shown ................ 3 other documents stored (2 failed, 1 extracted)
```

This answers "what did this run do?" without pretending the rest of the database doesn't exist, and without requiring a flag for the common case. Running the folder with no arguments is unaffected: the run's scope *is* the folder, so the report looks exactly as it does today.

`ConsoleReporter.render` gains an optional `only_paths` argument. Passing `None` keeps the current whole-database behaviour, so the reporter stays usable for "show me everything" and the existing tests keep their meaning.

### 4. Progress reporting

A `ProgressReporter` class in `src/reporting/`, injected into `DocumentProcessor`, with a `NullProgressReporter` default — the same null-object pattern already used for `AbstractContractRepository`, so the processor gains no `if verbose:` branches and the test suite stays silent without special-casing.

Target output:

```
Processing 4 documents in sample_docs/
  profile: openrouter_paid · model: anthropic/claude-sonnet-5

  [1/4] ACME Order From.pdf
        text extracted (2 pages, 2,180 chars)
        order_form · customer: acme
        calling model ... done in 4.2s
        stored 5 line items
  [2/4] CloudShield Order Form.pdf
        text extracted (6 pages, 7,240 chars)
        order_form · customer: cloudshield
        calling model ... retry 1/3 after 429 (waiting 6s)
        calling model ... done in 11.8s
        stored 6 line items
  [3/4] Purchase Order – BrightOps Analytics Ltd.pdf
        unchanged since last run — skipped
```

Four properties that matter:

- **The slow step is announced before it starts, not after.** `calling model ...` prints before the call, so the blank period is labelled rather than mysterious.
- **Retries are visible.** The current behaviour dumps a raw provider JSON blob per attempt via a bare `print` in `LLMService`; that moves behind the progress reporter and becomes one legible line. The raw error still reaches the stored `error` field and the failure summary, so nothing is lost for debugging.
- **Every line is flushed.** Python block-buffers stdout when it is not a terminal, which is why a redirected run looked dead earlier. `flush=True` on progress output makes `> log.txt` and `tail -f` behave.
- **Skipped documents say so**, making the hash-skip optimisation legible instead of looking like documents were dropped.

`LLMService` needs a way to report retries without importing the reporter. It takes an optional `on_retry` callback (default `None`, preserving current behaviour) that `ContractExtractor` wires to the progress reporter — keeping the service prompt-agnostic and free of presentation concerns, consistent with how it was deliberately kept prompt-free earlier.

### 5. Error rendering (small, included because it is in the way)

A failed document currently renders ~15 lines of raw provider JSON in both the per-document block and the failure summary, which is what made the earlier report so hard to read. The reporter gains a short summariser: recognised shapes (`Error code: NNN` with a `message`) render as `HTTP 429 — Provider returned error (rate-limited upstream)`; anything unrecognised falls back to the first line of the raw text. The full text stays in the database.

## Files

```
src/services/document_processor.py   # (modified) canonical paths; progress reporting;
                                     #            return processed paths from folder/multi runs
src/storage/database.py              # (modified) duplicate-path migration
src/reporting/progress_reporter.py   # (new) ProgressReporter, NullProgressReporter
src/reporting/console_reporter.py    # (modified) only_paths filter; "not shown" line;
                                     #            error summarisation
src/reporting/__init__.py            # (modified) exports
src/services/llm_service.py          # (modified) on_retry callback replaces bare print
src/extractors/contract_extractor.py # (modified) pass on_retry through
src/main.py                          # (modified) wire ProgressReporter; scope the report
tests/test_comprehensive.py          # (modified) new tests
README.md                            # (modified) document the progress output
```

No new dependencies.

## Verification

- **Path canonicalisation**: processing the same document via a relative path, an absolute path, and a `./`-prefixed path produces exactly one row; the second and third runs report as *skipped*, proving idempotency now holds across spellings. This is the direct regression test for the bug.
- **Aggregate correctness**: after the above, `SUM(amount)` equals the single document's amount, not a multiple of it.
- **Duplicate migration**: build a database containing the exact observed defect — one absolute row and one relative row for the same file, different `processed_at` — run `Database`, and assert one row survives, that it is the newer one, that its path is canonical, and that the older row's item rows were cascaded away rather than orphaned.
- **Migration is conservative**: two genuinely different documents are never merged, and a database with no duplicates is left byte-identical.
- **Report scoping**: with five documents stored and a run touching one, the report shows that one and the footer accounts for the other four; a run with `only_paths=None` still renders everything (the existing behaviour the current tests cover).
- **Progress output**: rendered to a captured stream and asserted on content — document counter, skip notice, retry notice — not on exact spacing. Plus an assertion that `NullProgressReporter` emits nothing, so the suite stays quiet.
- **Error summarisation**: a raw 401/429 provider blob renders as a single legible line, and the untouched original is still what lands in the database.
- **Live re-run**: with the migration applied and `max_tokens: 8192`, re-running the folder on the paid profile should clear all three stale failures — CloudShield's truncation and the two 401s both have resolved causes. Roughly $0.08 against the $10 credit balance.

## Out of scope

- **A `--all` / `--since` flag for the report.** The run-scoped default plus the "not shown" footer covers the observed need; flags can follow if a real one appears.
- **Retrying failures selectively.** Failed documents are already retried on the next run by design (`is_unchanged` returns `False` for them), which is why the stale rows will clear on their own.
- **Structured logging / log levels.** Progress output is for a human watching a terminal. If this ever runs unattended, that is a different feature.
- **Content-hash-based identity** (treating two paths with the same hash as one document regardless of location). Tempting, but it would silently merge a genuine copy of a contract filed in two places, which is a real scenario in document pipelines. Path stays the identity; canonicalisation just makes it honest.
