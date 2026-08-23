# Catching silently-dropped fields

## Context

A six-document run extracted everything correctly except one field on one document:

```
CloudShield Order Form signed image.pdf   items=6  with burst=3
CloudShield Order Form signed text.pdf    items=6  with burst=0   <-- dropped
CloudShield Order Form.pdf                items=6  with burst=3
```

All three files contain **byte-identical extracted text**. Two runs found the three per-year burst thresholds; one found none.

It is not truncation. The stored response is well-formed JSON in which the model explicitly wrote `"burst": null` on every item, finishing at 2,252 characters against ~5,000 for the other two. It did not run out of room - it did less work.

The failure mode is what makes this worth fixing: **nothing looked wrong.** The document reported `Extracted`, its totals reconciled, and the report showed `Burst ... —` exactly as it would for a document with no burst clause. A field worth three contractual thresholds vanished silently.

Same field, same text, two successes out of three. Well below the assignment's >=95% accuracy target for that field.

## First: the pipeline runs from scratch

Stored results must never steer a later run. The database is an **output sink** - written to, and read back only to print the report for the run that just wrote it - never a cache consulted to decide what work to do.

One place violates that today:

```python
# src/services/document_processor.py
if self.repository.is_unchanged(source_file, content_hash):
    return {"source_file": source_file, "skipped": True, "reason": "unchanged"}
```

That looks up a previously stored hash and skips the LLM when it matches. It is the only read of stored state that changes a run's behaviour, and it has already caused real confusion: a run that appeared to do nothing ("it ran too fast, I guess we didn't call the LLM?"), and a whole tool - `reset_documents` - that exists mainly to work around it.

It also actively undermines the work below. A quality warning is only meaningful if the extraction actually ran; against a cached skip there is nothing to check, and a retry has nothing to retry.

**Remove it.** Delete the skip branch, and delete `is_unchanged` from `AbstractContractRepository` and both implementations rather than leaving an uncalled method behind. Every run extracts every document.

Two things deliberately kept:

- **The `content_hash` column stays.** It stops being a cache key and remains what it always should have been: audit data recording what the document contained at the moment it was extracted. Useful for answering "was this the same document last time?" after the fact, without ever deciding anything during a run.
- **Save still replaces.** `save()` already deletes any existing row for a source file before inserting, so re-running updates in place instead of accumulating duplicates. Nothing to change.

The cost is real and small: a full six-document run always costs about 12 cents instead of sometimes being free. That is the correct trade for a pipeline whose output must be reproducible from the documents alone.

`reset_documents` survives, but its purpose narrows - no longer "force a re-extraction" (every run does that now), just clearing out rows for documents that have left the folder, or starting from an empty database.

**Note on where the quality check reads from.** It compares the document's *text* against the *result of the extraction that just produced it* - both in memory, both from the current run. It does not consult stored data. The warning it produces is written to the database as output, like every other extracted field.

## Three fixes

### 1. Temperature 0

Temperature controls how faithfully the model samples from its own probability distribution: 0 always takes the most likely next token, higher values increasingly favour less likely ones. Extraction has one correct answer, so variety is pure downside.

The profiles currently use `0.1`. Dropping to `0` is a one-line change per profile.

**Worth being clear about how little this buys.** Temperature 0 is not truly deterministic - floating-point ordering, batching, and provider routing all introduce variance. And the failure here was not a poor word choice; the model skipped work. Temperature nudges token selection, it does not enforce diligence. This is the cheapest fix and the weakest. It is worth doing because it is free, not because it solves the problem.

### 2. Detect the drop, and say so

A cheap check catches exactly this: **if the document text mentions a field's subject but no extracted record carries it, the extraction probably dropped it.**

Verified against the current run - it flags the one real failure and nothing else:

```
ACME                      mentions=True  extracted=2  ok
CloudShield signed image  mentions=True  extracted=3  ok
CloudShield signed text   mentions=True  extracted=0  <-- MISS
CloudShield               mentions=True  extracted=3  ok
BrightOps                 mentions=True  extracted=2  ok
NovaFleet                 mentions=True  extracted=1  ok
```

Zero false positives across all six documents.

**Where it runs.** The check needs the source text, which the reporter does not have - it reads `StoredContract` from the database. So the check runs at extraction time in `DocumentProcessor`, which holds both the text and the result, and its output is stored.

**How it is stored.** `ExtractedContractData` gains `warnings: List[str]`, persisted to a `warnings` column on `processed_documents` (audit metadata about the extraction, alongside `status` and `error`, rather than contract data) and read back through `StoredContract`.

**A small class rather than an inline `if`.** `ExtractionQualityChecker.check(text, result) -> List[str]`, injected into `DocumentProcessor` per the project's convention. One check today; the shape leaves room for the obvious next ones (a payment-terms clause present but no `payment_terms`, a signature block present but no `signature_evidence`) without restructuring.

The report grows a line, in the same spirit as the existing reconciliation line:

```
  Extracted ................... 6       Failed: 0
  Quality warnings ............ 1 document with a possible dropped field
```

and on the document itself:

```
  ⚠ Warning ................... document text mentions burst terms but none
                                were extracted - the model may have skipped them
```

### 3. Retry once when a warning fires

A detected miss is worth one more call: about 2 cents, and it would very likely have fixed this one.

**The non-obvious part: at temperature 0 a plain retry is useless.** Same prompt plus same near-deterministic sampling produces the same wrong answer, and we would pay for a second identical failure. Fix 1 makes fix 3 pointless unless the retry changes something.

So the retry re-renders the prompt with a corrective note appended:

```
A first extraction attempt of this document returned no burst terms, but the
document text does mention them. Re-read the document carefully and make sure
every burst clause is captured and attached to the line items it covers.
```

This is a genuinely different prompt, so it produces a genuinely different response, and it points at the specific thing that went missing rather than vaguely asking for more effort.

Rules:

- **At most one retry per document.** Two attempts, then accept the result and keep the warning. No loops, bounded cost.
- **Retry only on a warning**, never on success - the common path stays one call.
- **Keep the better result.** If the retry still returns nothing, keep the first result and its warning rather than assuming the second is better.
- **The warning survives a successful retry**, recorded as resolved, so the report shows the retry happened. Silently papering over a first-attempt failure would hide exactly the accuracy signal this is built to expose.

Progress output makes it visible:

```
        calling model ...
        model responded in 23.9s
        quality check: burst terms mentioned but not extracted - retrying
        calling model (retry) ...
        model responded in 19.2s
        stored 6 line items (3 with burst terms)
```

## Files

```
src/services/document_processor.py       # (modified) REMOVE the unchanged-skip
src/storage/contract_repository.py       # (modified) REMOVE is_unchanged (ABC + impls)
src/reporting/progress_reporter.py       # (modified) drop document_skipped
config/llm_profiles.yaml                 # (modified) temperature 0.1 -> 0
src/services/quality_checker.py          # (new) ExtractionQualityChecker
src/models/extracted_data.py             # (modified) + warnings
src/storage/schema.sql                   # (modified) + warnings column
src/storage/database.py                  # (modified) register for migration
src/storage/contract_repository.py       # (modified) read/write warnings
src/models/stored_contract.py            # (modified) surface warnings
src/services/document_processor.py       # (modified) run checks, retry once
src/extractors/contract_extractor.py     # (modified) accept a corrective note
src/prompts/templates/extract_contract_fields.txt  # (modified) $retry_note slot
src/reporting/console_reporter.py        # (modified) render warnings
src/reporting/progress_reporter.py       # (modified) announce check + retry
tests/test_comprehensive.py              # (modified) tests
README.md                                # (modified) document the behaviour
```

No new dependencies.

## Verification

- **No run reads stored state to decide what to do**: processing the same document twice calls the LLM twice. A stub that raises on a second invocation must now *fail*, which is the inverse of the test that previously guarded the skip - the clearest proof the caching is gone.
- **Re-running does not duplicate rows**: the same document processed twice leaves one row, updated in place.
- **The check catches the real case**: CloudShield's text with an extraction carrying no burst produces a warning; the same text with burst terms produces none.
- **No false positives**: replayed against all six documents' stored results, only the known miss flags. This is the test that matters - a noisy check gets ignored, which is worse than no check.
- **A document with no burst clause at all** produces no warning, however it extracts. The check must key on what the document says, not on absence alone.
- **Retry fires once and only on a warning**: a stubbed client that returns burst-less JSON first and populated JSON second yields a good result in two calls; a stub that always returns burst-less JSON is called exactly twice, never more.
- **The successful path costs one call.** A stub that raises on a second invocation must pass, proving no retry on clean extractions.
- **The retry prompt differs from the first**, and contains the corrective note - the check that fix 3 is not neutered by fix 1.
- **Warnings round-trip** save -> `fetch_all` -> report, and a document with none renders unchanged.
- **Migration**: a database predating the `warnings` column gains it without losing rows.
- **Live re-run**: `reset_documents CloudShield` and re-run all three variants. Expect 3/3 with burst. Roughly 6 cents.

## Out of scope

- **Self-consistency voting** (extract twice, compare, reconcile). Doubles cost on every document to protect against an occasional miss; the targeted retry gets most of the benefit for a fraction of the spend.
- **Checks for other fields.** `ExtractionQualityChecker` is shaped to hold them, but each needs its own false-positive validation against real documents, and only burst has a demonstrated failure.
- **Making warnings fail the extraction.** A warning is a flag for a human, not an error - the data is still worth storing.
