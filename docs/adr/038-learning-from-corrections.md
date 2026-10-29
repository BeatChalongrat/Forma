# ADR-038 — Learning from corrections: every approval teaches FORMA

**Status** accepted · 2026-10-30 · phase 1 implemented. Extends ADR-006 (memory), ADR-021 (self-healing),
ADR-036 (verdict). ADR-037 (policy calibration) builds on the same records.

## Context

Mapping memory (ADR-006) reused an approved mapping only when the next file's structure was identical, and
the mapping agent saw a few approved mappings of *other* customers with overlapping headers. Everything
else a person decided — re-pointing a field to another column in the Studio, answering "VAT included: no",
confirming a pack size — was kept as a new mapping version and never read again. A customer whose export
names the price column `col_17` was asked about it every time the layout moved, and the fifth customer to
use `Net Amt` for the price got the same low-confidence guess as the first. The system did not get better
with use, and nothing showed whether it did.

## Decision

1. **An approval is compared with what FORMA proposed.** `approve_mapping` walks the version chain from the
   approved version to the nearest ancestor FORMA created (`created_by` in `system`, `ai`) and diffs them
   field by field (`healing.field_changes`). The result is written to a new `corrections` table
   (`services/learning.py`, migration `e1f2a3b4c5d6`):
   * `alias` — the field now reads another column: `(customer, target pointer, normalized header)`;
   * `constant` — a person confirmed a fixed value (VAT inclusion, pack size …);
   * `accepted` — approved exactly as proposed (nothing to learn, but ADR-037 needs to know);
   * `promoted` — an alias an admin made everyone's (`customer_id` null).
   Rows are append-only; "forget" retires a row (`status = retired`) and is audited.
2. **The mapper reads what was learned before it guesses.** `learning.learned_for(customer, schema)` returns
   the customer's confirmed aliases and constants plus the promoted ones as a `Learned` object keyed by
   target pointer (rule 1: the schema's synonyms stay in the schema; this is a runtime layer on top).
   `heuristics.match_columns` lets a taught header win outright (score 1.0, evidence
   `learned: you mapped 'col_17' here on 2026-10-30`); `propose_mapping` replaces a guessed constant with the
   confirmed one and drops its question. The AI agents receive the same facts as
   `what_the_operator_taught_before` (data in the prompt, never instructions). The proposal's policy report
   records `learning.used`, the verdict shows "N fields mapped from what you taught FORMA before", and the
   rows' `uses` count how often each learned fact was applied.
3. **Aliases several customers taught become suggestions.** `learning.suggestions` lists headers mapped to the
   same field by at least three customers that are not yet synonyms of the schema; an admin promotes one
   (`POST /api/learning/promote`) and every customer's next proposal reads it. Nothing is promoted
   automatically: the admin decides, the audit log records it.
4. **The trend is visible.** `GET /api/learning` returns, per month, mappings approved, accepted as proposed,
   corrections made, corrections per approval and learned fields used; Settings › *What FORMA learned* shows
   it with the learned rows (forget per row) and the suggestions; Home shows "Learned from you".
   Corrections per approval falling month over month is the measurable claim that FORMA learns.

## Consequences

* A correction is made once per customer, not once per round; a convention several customers share is
  made once for everyone. The evidence on every learned field says where it came from, so a wrong lesson is
  visible and can be forgotten.
* Learning is customer-scoped and tenant-scoped: a tenant's principal sees only its customers' rows and
  cannot promote.
* The records (`alias`, `constant`, `accepted`, plus `verdict.accept` audits from ADR-036) are the dataset
  ADR-037 will use to measure how often FORMA was right and propose policy thresholds from it.
* Not in this phase: learning from dataset edits (`ChangeSet`s already become overrides, ADR-024), learning
  table roles or joins, and promotion without an admin.

## Verification

`tests/test_learning.py`: an edited then approved mapping is learned and used on the customer's next
(drifted) round with evidence, counted as used, shown in the verdict and not inherited by another customer;
a confirmed constant is reused and its question dropped; an approval as proposed is recorded; three
customers teaching the same header produce a suggestion, promotion makes a fourth customer's first round
read it, forgetting retires it; a tenant-scoped principal sees nothing of other customers and cannot promote.
`tests/test_migrations.py` compares the model with the migration. Eval on the synthetic corpus is unchanged
(no corrections exist there).
