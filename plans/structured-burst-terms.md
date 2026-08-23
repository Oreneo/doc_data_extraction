# Structured burst terms, scoping, and line-item arithmetic

## Context

`burst` is currently `Optional[str]` on `LineItem` — a sentence copied verbatim. Nothing parses a percentage, a cap, or a period, and nothing computes an allowance. Investigating CloudShield surfaced three defects, one of which is mine.

**Defect 1 — the evidence table in `canonical-contract-field-schema.md` is wrong.** It records all four documents as stating "one prose paragraph, whole-order scope". Re-reading the actual text, **not one of the four is whole-order**. Every document scopes its burst clause to a subset of line items, and CloudShield varies the threshold per year. The item-level model we chose is right; the justification recorded next to it is not, and it needs correcting so the next person doesn't inherit a false premise.

**Defect 2 — the prompt actively destroys the CloudShield data.** `extract_contract_fields.txt` says: *"If the document states one burst clause that applies to the whole order rather than to a specific product, copy that same text into the burst field of every item."* Applied to CloudShield, that flattens three distinct thresholds (38.89% / 28.21% / 19.05%) into one repeated paragraph, and attaches burst to Premium Support rows that have no burst entitlement at all.

**Defect 3 — `price × quantity ≠ total_amount` for CloudShield.** Year 1 is 180,000 units × $3.25 = $585,000, but the stated total is $7,020,000. The table has an `Order Term (Months) = 12.00` column and the price is *monthly per unit*; `LineItem` has nowhere to put that. The header-level reconciliation still passes (item totals do sum to $23,640,000), but any per-line arithmetic check would report a false failure today.

### What the documents actually say

| | ACME | CloudShield | BrightOps | NovaFleet |
|---|---|---|---|---|
| Percentage | 5% | 38.89% / 28.21% / 19.05% (per year) | 10% | 15% |
| Scoped to | "in connection with **SaaS subscription**" | Enterprise rows; redeemable against **CloudShield Sensor** | "licensed **analytics** usage" | "**monitored workloads**" |
| Basis quantity stated in doc? | table qty = 1 (a subscription count, not billable units) | **yes** — 180,000 / 195,000 / 210,000 | no | no |
| Cap | none | 280,000 Billable Units per year | none | none |
| Period | per consecutive 24 months | per subscription year (12 mo) | per contract year | annually |
| Other coupling | — | reduced by the §4 conversion right (10 Enterprise : 1 Code) | — | — |

## Scope of this change: extraction, not calculation

**Agreed scope: capture what each clause says, structurally. Do not compute allowances.**

An earlier draft proposed deriving an allowance in units (CloudShield: 180,000 × 38.89% → 70,002, capped at 280,000). That is dropped, because only 1 of the 4 documents states a quantified basis at all. ACME's `quantity` column reads `1` for the SaaS Subscription rows — one subscription, not one billable unit — so `5% × 1 = 0.05` would be a fabricated allowance wearing the costume of a fact. Rather than build a calculator whose main job is refusing to answer three-quarters of the time, we extract the percentage, the cap, and the basis *as stated*, and leave the arithmetic to whoever has the usage data.

Dropped with it: `basis_quantity` (its only consumer was the calculator) and the per-line arithmetic *check*. Every field the LLM no longer has to extract is one it can no longer get wrong, which matters on a free-tier model.

## Design

### 1. `BurstTerm` — a structured sub-model, with the raw text always kept

```python
class BurstTerm(BaseModel):
    raw_text: str                        # the clause, verbatim — always populated
    percentage: Optional[float] = None   # 38.89
    basis: Optional[str] = None          # "CloudShield Enterprise Billable Units"
    cap_units: Optional[float] = None    # 280000
    period: Optional[str] = None         # "per consecutive 24 months"
    applies_to: Optional[str] = None     # "CloudShield Sensor" — what it's redeemable against
```

`LineItem.burst` changes from `Optional[str]` to `Optional[BurstTerm]`. `raw_text` is mandatory whenever a burst exists, so we never lose the source sentence to a parsing failure — every structured field is a bonus on top of what we already store today, which makes this **strictly non-regressive**: worst case, we have exactly today's data.

Six fields, one mandatory. Deliberately small — this is the entire extraction surface a small model has to hit.

### 2. Scoping: stop replicating burst onto every item

The prompt instruction is rewritten around what the documents actually do:

> A burst clause usually names the product or usage it applies to (e.g. "in connection with SaaS subscription", "licensed analytics usage"). Attach the burst object **only to the line items matching that product**. Leave `burst` null on items the clause does not cover — for example a support or services line when the clause names only the platform. If a clause states different thresholds for different periods (e.g. "during Year 1, up to X%; during Year 2, up to Y%"), attach each threshold to the line item covering that period. Only when the clause genuinely covers the entire order with no product or period qualifier should the same burst appear on every item.

Expected outcome per document: ACME → SaaS Subscription rows only (2 of 5); CloudShield → Enterprise rows, a different percentage on each (3 of 6); BrightOps → Analytics Platform rows (2 of 4).

### 3. Ambiguous scope stays visible, and is never silently resolved

NovaFleet's "monitored workloads" maps cleanly to neither `Cloud Security Suite` nor `Advanced Threat Monitoring`. The document is genuinely ambiguous, and the failure mode to avoid is the model quietly picking one line while the report shows a confident, unmarked result.

Two mechanisms, both cheap:

1. **`applies_to` records the document's own phrase, verbatim** — `"monitored workloads"`, not a product name the model inferred. The reader sees the words the contract used and can judge the mapping themselves.
2. **When the clause's target doesn't clearly match one product, the prompt says to attach the burst to every line item it could plausibly cover, rather than choosing.** Burst appearing on several lines with a vague `applies_to` *is* the visible signal that the scope is unresolved — where a single confidently-placed row would hide it.

This deliberately prefers a visibly-unresolved answer over an invisibly-wrong one. It does mean NovaFleet may show burst on more lines than a lawyer would ultimately agree with; that is the intended trade.

### 4. Line-item term and price basis (extraction only)

Add to `LineItem`:

```python
term_months: Optional[float] = None    # CloudShield's "Order Term (Months)" = 12.00
price_period: Optional[str] = None     # "monthly" | "one_time"
```

These exist to *explain* why CloudShield's `price × quantity` (180,000 × $3.25 = $585,000) doesn't equal its `total_amount` ($7,020,000): the price is monthly and the term is 12 months. Both values are printed in the document; we simply had nowhere to put them.

`total_amount` remains authoritative and is never recomputed. Per the agreed scope, **no per-line arithmetic check is added** — these are recorded facts, not inputs to a validation. The existing header-level reconciliation (item totals vs. contract total) is unaffected and still passes.

### 5. Storage

New columns on `sales_order_items` and `purchase_order_items`: `burst_raw_text`, `burst_percentage`, `burst_basis`, `burst_cap_units`, `burst_period`, `burst_applies_to`, plus `term_months` and `price_period`. The existing `burst` TEXT column is superseded by `burst_raw_text`.

Flattened onto the items table rather than given its own `burst_terms` table: it is strictly 0-or-1 per line item, so a separate table would add a join for no gain in expressiveness.

**Migration.** We currently have none — `CREATE TABLE IF NOT EXISTS` only — so an existing `data/extractions.db` would break on the new INSERT. Since every change here is *additive*, `Database` gains a small step that reads `PRAGMA table_info(<table>)` and issues `ALTER TABLE ... ADD COLUMN` for anything missing. ~15 lines, non-destructive, and it preserves stored extractions — which matters, because wiping the DB would also wipe the content hashes and force a full re-extraction against a rate-limited free tier. (The dropped `burst` column is left in place rather than removed; SQLite's `DROP COLUMN` is version-dependent and an unused column costs nothing.)

### 6. Reporting

`ConsoleReporter` currently collapses burst when every item's string is identical. With structured terms it shows, under each item that carries one:

```
    Line items (6)
      #  Product                              Qty         Price         Total
      1  CloudShield Enterprise           180,000          3.25  7,020,000.00
         Burst: 38.89% of CloudShield Enterprise Billable Units
                cap 280,000 · per subscription year · applies to CloudShield Sensor
      2  Premium Support                        1     22,916.67    275,000.00
      3  CloudShield Enterprise           195,000          3.25  7,605,000.00
         Burst: 28.21% of CloudShield Enterprise Billable Units
                cap 280,000 · per subscription year · applies to CloudShield Sensor
```

Only the sub-fields that are present are printed, so a sparse burst degrades to one line. When structured parsing found nothing, `raw_text` is shown as-is — the same information the report gives today.

The existing "collapse when uniform" behaviour is kept for the genuine whole-order case, comparing `raw_text`.

## Files

```
src/models/extracted_data.py          # (modified) BurstTerm; LineItem.burst retyped,
                                      #            + term_months, price_period
src/prompts/templates/extract_contract_fields.txt   # (modified) scoping + structured burst
src/storage/schema.sql                # (modified) new item columns
src/storage/database.py               # (modified) additive ALTER TABLE migration
src/storage/contract_repository.py    # (modified) read/write new columns
src/reporting/console_reporter.py     # (modified) render structured burst
tests/test_comprehensive.py           # (modified) new tests
plans/canonical-contract-field-schema.md   # (modified) correct the evidence table
README.md                             # (modified) document burst fields
```

No new dependencies, and no new classes — this is a data-shape change, so `ContractExtractor` and `main.py` are untouched.

## Verification

- **Structured round trip**: a CloudShield-shaped burst (38.89%, basis, cap 280,000, period, applies_to) survives save → `fetch_all` → report with every sub-field intact.
- **Non-regression on raw text**: a burst where *every* structured field is null still round-trips `raw_text` intact and renders — proving a total parsing failure degrades to exactly today's behaviour rather than losing data.
- **Scoping**: with a stubbed LLM returning a CloudShield-shaped payload, Premium Support items carry no burst while the three Enterprise items carry three *different* percentages — the case the current prompt flattens.
- **Ambiguous scope**: a NovaFleet-shaped payload with burst on multiple items and a vague `applies_to` renders that phrase verbatim on each, so the unresolved scope is legible in the output.
- **Migration**: build a database on the *old* schema with a row in it, open it with the new `Database`, and confirm the columns are added and the existing row survives with the new fields null. This is the test that protects stored content hashes from a destructive rebuild.
- **Reporting**: a fully-populated burst prints its sub-fields; a sparse one prints only what exists; a raw-text-only one prints the sentence.
- **Live pass** still needs `OPENROUTER_API_KEY`. CloudShield is the accuracy test that matters — pulling three percentages plus a cap out of one dense paragraph is the hardest extraction in the sample set and where a free-tier model is most likely to fail. Worth trying a stronger model before concluding the prompt is wrong.

## Out of scope, flagged

- **Computing allowances.** Dropped by agreement, per "Scope of this change" above.
- **Per-line arithmetic validation.** `term_months` / `price_period` are recorded but not used to check `total_amount`.
- **The assignment does not ask for any of this.** It says "Special terms | Burst - on the item level" — extraction only. `raw_text` alone satisfies it; the structured fields are added value.
- **CloudShield's §4 conversion right** (10 Enterprise workloads : 1 Code developer) reduces the burst basis. Modelling conversion state means tracking amendments over time — a different feature; `basis` records the caveat in text.
- **`Optional[BurstTerm]` is a breaking change** for anything consuming `burst` as a string. Only our own reporter and repository do today, both updated here.
