# Burst first-attempt miss rate

> **Status: OUT OF SCOPE — not being built.**
>
> The problem is real, measured, and already mitigated: the quality check
> catches every occurrence and the retry has recovered it 6 times out of 6, so
> **no data is lost today**. What remains is a cost and elegance issue, not a
> correctness one.
>
> This document is kept because the analysis took several runs to get right -
> including a wrong hypothesis that had to be discarded - and that reasoning is
> worth preserving whether or not the fix is built.

## The measurement

Across **15 CloudShield extractions** (three byte-identical documents, five
runs), burst terms were dropped on the first attempt **7 times - about 47%**.

The failure lands on a **different file each run**, so it is not a property of
one document:

| Run | Dropped burst on first attempt |
|---|---|
| 1 | signed text |
| 2 | signed image |
| 3 | signed image |
| 4 | signed text **and** original |
| 5 | signed image **and** signed text |

It is a property of the document *class*: ~10,000 characters, six line items,
three distinct per-year burst thresholds. The three short purchase orders and
ACME have never dropped burst.

**The retry recovers it every time: 6/6.** So the user-visible outcome is
correct; roughly half of the complex documents just cost two LLM calls instead
of one.

## A hypothesis that turned out to be wrong

The first theory was **prompt position** - the burst rules sit at 20-34% of the
prompt, with ~10,000 characters of contract text after them, so perhaps they
were too far from the point of use.

**This does not survive arithmetic.** The whole prompt is ~17,000 characters,
roughly 4,000 tokens, against a 1M-token context window. "Lost in the middle"
is a long-context phenomenon; 4K tokens is nothing for this model. The theory
sounded plausible and was never checked against the numbers.

Recording it because the correction matters more than the original guess: it
would have led to reordering the prompt, which would have changed nothing.

## What the evidence actually points at

| | Response size |
|---|---|
| Successful CloudShield extraction | ~5,000-5,400 chars |
| **Failed** first attempt | **2,252 chars** |
| Available budget | 8,192 tokens (~32,000 chars) |

The failed response:

- stopped at roughly **16% of the available budget** - so not truncation,
- was **well-formed JSON** - so not a parsing or formatting failure,
- contained `"burst": null` six times - so not a misunderstanding of the schema.

It did not run out of room, and it did not misread the instructions. It took
the **cheap path**. Six nulls cost about 30 tokens; three burst objects with
verbatim `raw_text` cost about 700.

This also explains why the retry is so reliable. The retry supplies **no new
rules** - all seven burst rules are already in the first prompt. It supplies a
**correction signal**: "you already failed at this." That makes skipping no
longer the cheap option, which is a different mechanism from re-rolling the
dice, and is why it works 6/6 rather than ~53% of the time.

## The proposed fix

**Make the cheap path unavailable** by requiring a commitment before the items
are written:

```json
{
    "burst_clauses_found": <number of distinct burst clauses in the document>,
    ...
    "items": [ ... ]
}
```

Writing `"burst_clauses_found": 3` and then six nulls is self-contradictory in
a way that six nulls alone is not. The field costs one integer.

It also gives the quality checker a sharper signal: a stated count with no burst
objects is an **unambiguous** drop, where today's check has to infer one from
the presence of the word "burst" in the document text.

Secondary lever, if that is not enough: require `raw_text` to be quoted
**verbatim from the document** for every clause found, making the expensive
output the only compliant one.

## How it would have to be measured

Not assumed. A harness (`scripts/measure_burst.py`, outside the test suite)
runs the three CloudShield documents N times and reports the first-attempt miss
rate.

- **Baseline**: 5 runs x 3 documents = 15 extractions. Expect ~7/15, matching
  what has already been observed.
- **After**: the same 15.
- **Cost**: ~30 LLM calls, roughly 60 cents.

Report as a **fraction with its denominator** - 7/15 and 2/15 - never as a bare
percentage. "47% down to 13%" implies a precision that a sample of 15 does not
support.

## Files it would touch

```
src/prompts/templates/extract_contract_fields.txt   # burst_clauses_found + verbatim rule
src/models/extracted_data.py                        # + burst_clauses_found
src/services/quality_checker.py                     # count-vs-extracted check
src/storage/schema.sql, database.py,
  contract_repository.py                            # persist it
scripts/measure_burst.py                            # the before/after harness
tests/test_comprehensive.py                         # count-mismatch check
```

## Why it is reasonable to skip

- **Nothing is lost.** The check catches it and the retry fixes it, every time
  so far.
- **The cost is trivial** - one extra call on roughly half the complex
  documents, a fraction of a cent each.
- **The fix is speculative.** The economization theory fits the evidence, but it
  is a theory; the change might not move the number, and finding out costs a
  paid measurement run.
- **The failure is already visible** in the report, marked
  `(resolved on retry)`, rather than silent.

The honest position: this is a known, measured, mitigated weakness with a
documented hypothesis and a testable plan - which is a better place to be than
having quietly fixed it without ever knowing the rate.
