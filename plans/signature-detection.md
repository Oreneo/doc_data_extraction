# Customer signature: definition, evidence, and output format

## Context

ACME's extraction reports `customer_signature` as true. The document is not signed. Reading the PDF geometry rather than its flattened text shows exactly what is there:

```
top=541   "Signature:"          "Signature:"
top=565   [rect]                [rect]              <- signature area
top=598   ────────              ────────            <- signature rule line
top=615   "Name: John Smith"    "Name: Bob Lee"
top=639   "Title: CFO"          "Title: CEO"
top=663   "Date: 12-13-2024"    "Date: 01-02-2025"
```

Between `Signature:` (541) and `Name:` (615) there are no words, no images, and no curves. The two images in the file are a 138x137 logo at `top=-6..131` — the letterhead, repeated on both pages, nowhere near the signature block. The signature lines are genuinely blank.

**The model was not wrong; our field definition was.** `config/field_schemas/base_fields.yaml` currently instructs:

> True if there is a signature indicator - an explicit "Signed" mark or checkmark, **or a filled-in name/title/date under a signature block**.

ACME's name/title/date are filled in, so the model followed that rule correctly and returned true. The bug is the clause in bold: it treats the *printed-name block that accompanies* a signature as though it were the signature.

### A second problem the same block exposes

The signature area has two columns, and **ACME is the vendor, not the customer**. The document says `To: Appsoft Inc.` and `Customer Billing Company Name: Appsoft Inc.`; the left column (x0≈77) is Acme, the right (x0≈311) is Appsoft. `customer_signature` should be read from Appsoft's column.

Nothing in the current field description says which party to read. It happens not to change the answer here — both columns are blank — but it is unspecified behaviour on a field the assignment defines as *customer* signature.

Worth flagging: pdfplumber flattens the two columns onto single lines, so the model sees `Signature: Signature:` and `Name: John Smith Name: Bob Lee` with no column structure. Asking it to distinguish parties from that text is asking it to infer layout that the text extraction has already destroyed.

### Evidence across the sample set

| Document | Signature area | Correct value |
|---|---|---|
| ACME | `Signature:` blank; name/title/date filled | **false** (currently true) |
| CloudShield | blank underscores, nothing filled | false |
| BrightOps | `Buyer Signature: ✔ Signed` | true |
| NovaFleet | `Buyer Signature: ✔ Signed` | true |

Only ACME is wrong, and only because of the clause above.

## Design

### 1. Redefine what counts as a signature

Rewrite the `customer_signature` description in `base_fields.yaml` around the distinction the current one blurs — **a signature is a mark, not a name field**:

- **true** only when there is an actual signature indicator on or above the signature line: a typed or written signature, `/s/ Name`, an explicit `Signed`, or a checkmark next to the signature label.
- **false** when the signature line is blank — **even if Name, Title, and Date beneath it are filled in.** Those fields identify who *would* sign and accompany a signature; they are not one. This sentence is the fix, stated explicitly because the previous wording said the opposite.
- **false** when there is no signature section at all.

And on party: read the **customer/buyer's** signature, not the vendor's. Where a signature block has two columns, identify the customer by cross-referencing the billing/"To:" name elsewhere in the document, and read that party's column.

This is a config change only — no code — which is exactly what the field-schema layer exists for.

### 2. Capture the evidence, not just the boolean

Add a `signature_evidence` field: a short phrase quoting or describing what the document actually shows — `"'✔ Signed' next to Buyer Signature"`, or `"signature line blank; Name/Title/Date filled"`.

This is the same principle as `BurstTerm.raw_text`: keep what the document said alongside the interpretation, so a wrong interpretation is visible rather than silent. Concretely, this bug would have been obvious on first read — the report would have shown `true` beside `name/title/date filled`, and the flawed reasoning would have been right there instead of requiring a dig into PDF geometry.

It is one nullable string: a new scalar in `base_fields.yaml`, a model field, one column per header table (handled by the existing additive migration), and a line in the report.

### 3. Output `True` / `False`, not `Yes` / `No`

The assignment specifies the field as `Customer signature - True/False`. `ConsoleReporter._format_value` currently renders `Yes`/`No`. One-line change to match the spec.

### 4. Rename the label

`Technical acct manager` becomes `Technical account manager` in the report. This widens the longest label from 22 to 25 characters, so `LABEL_WIDTH` (longest + 4) goes 26 -> 29, shifting every value column right by three. Values wrap to the report width already, so the layout absorbs it — but the existing width test needs to keep passing, which is the check that matters here.

## What we are NOT doing, and the limitation that leaves

**No image processing.** Per your call, and the sample set supports it: no document here contains a signature image.

The honest consequence: **a contract signed with a drawn or scanned signature would be reported as unsigned.** `extract_text()` returns nothing for image content, so the pipeline is structurally blind to it — this is a false negative, and the more dangerous direction of error for a contracts pipeline (a signed agreement recorded as unsigned).

If that case ever appears, the cheap fix needs no OCR and no image interpretation. The signature rule lines are addressable geometry — ACME's are at `top=598`, `x0=79..208` and `x0=313..442` — so it is enough to ask whether any image or curve overlaps the band just above one. That answers "is there ink where a signature goes?" without reading it. Noting the technique here so it is actionable later; not building it against a sample set where nothing exercises it.

A second known limitation, recorded for the same reason: two-column signature blocks are flattened by text extraction, so party attribution is inference rather than fact. Geometry-aware extraction of that block would fix it; the same call applies.

## Files

```
config/field_schemas/base_fields.yaml   # (modified) redefine customer_signature;
                                        #            add signature_evidence
src/models/extracted_data.py            # (modified) + signature_evidence
src/storage/schema.sql                  # (modified) + signature_evidence column
src/storage/database.py                 # (modified) register the new column for migration
src/storage/contract_repository.py      # (modified) read/write it
src/reporting/console_reporter.py       # (modified) True/False; label rename;
                                        #            render signature_evidence
tests/test_comprehensive.py             # (modified) tests
README.md                               # (modified) document the definition + limitation
```

No new dependencies, no new classes.

## Verification

- **The ACME case, end to end**: a stubbed payload shaped like ACME's signature block (blank signature, filled name/title/date) stores and renders `False` — the direct regression test for this bug.
- **The BrightOps case**: `✔ Signed` still yields `True`, so the stricter definition doesn't swing the error the other way.
- **Output format**: the report renders `True`/`False`, never `Yes`/`No`.
- **Evidence round trip**: `signature_evidence` survives save -> `fetch_all` -> report, and a null value renders as the empty marker rather than breaking the layout.
- **Migration**: a database predating the column gains it without losing rows (existing additive-migration test extended).
- **Label width**: the existing "no line exceeds the report width" test must still pass with the longer label — measured in display columns, since the box-drawing characters are multi-byte.
- **Live check**: re-extract ACME with `reset_documents ACME` and confirm `False`, then confirm BrightOps still reports `True`. About four cents.

The prompt change is the substance here, and it can only be judged against a real model — the stubbed tests confirm the plumbing, not that the LLM reads the new instruction the way we intend.
