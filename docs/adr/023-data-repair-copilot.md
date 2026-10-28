# ADR-023 — Data repair copilot: conditions across collections, and answers from evidence

**Status** accepted · 2026-10

**Context** The copilot turned an instruction into edit operations, but conditions could only test root
fields, so "only SKUs with more than 100 in stock and not in a promotion" — stock and promotions are
collections — could not be expressed. Without an LLM it understood one sentence shape. Questions such as
"why did 120 SKUs not join?" had no answer at all.

**Decision**

1. **Derived figures for conditions.** A `where` may name `count(<collection>)` and `sum|min|max(<numeric
   collection field>)`, e.g. `sum(/inventory/*/qty)` or `count(/promotions)`. Names come from the schema
   (`datasets.derived_columns`) and are computed only when a condition uses them. They are data columns for
   the existing DSL comparison ops — no new operation, nothing executed.
2. **Rule-based planning** (`services/assistant.rule_based_plan`, no LLM needed) understands a percent price
   change up or down or a status change, scoped by category and/or brand, with stock and price comparisons
   and in/not in a promotion. An instruction that names no records is refused with a question rather than
   applied to everything. With an LLM, the copilot agent plans instead and is told the derived figures; its
   output still passes the guard and becomes pending operations.
3. **Questions** (`POST /api/datasets/{id}/ask`) are answered from evidence, deterministically:
   a named SKU → its lineage and cause (ADR-020); links that drop rows → the measured relationships
   (ADR-019) with the keys that fail and whether a key format (leading zeros, separators) explains them;
   anomalies → the anomaly report (ADR-022); duplicates/errors → the engine's findings.
4. Bulk edits on picked rows use the same operations with `identities` (see ADR-024).

**Consequences** The plan's example instruction works without an API key and shows its exact operation and
impact before anything is saved. Answers quote their evidence; when the evidence does not cover a
question, the copilot says so instead of guessing.
