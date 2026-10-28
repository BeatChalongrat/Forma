# ADR-019 — Trust levels and visual multi-sheet mapping

**Status** accepted · 2026-10

**Context** The policy already decided `auto_approve | review | block`, but a reviewer looking at the queue
could not tell a cosmetic review from one where most prices would change. And multi-sheet files were
mapped table by table: the Studio showed each source's columns, while the keys that connect the sheets
(product ↔ stock ↔ warehouse) lived in JSON, with no evidence of how well they actually match.

**Decision**

1. **Risk level next to every decision.** `PolicyEngine.evaluate` first decides as before, then classifies
   the risk the reviewer is taking: `high` when any risk factor is present (blocked by policy, an
   ambiguous critical field, validation or engine errors, join cardinality or duplicate-key findings, a
   destructive format change, or a sandbox comparison changing at least `review.high_impact_ratio` of the
   records — default 20 %, editable in Settings); otherwise `low` for an automatic approval and `medium`
   for a review. The decision itself is unchanged: the level explains it, the policy still decides.
2. **Impact in records.** When the sandbox (ADR-018) compared the mapping with the approved one,
   `policy.impact` holds `affected_records` (changed + lost + gained), `of_records`, `ratio` and
   `changed_prices`. Review tasks copy `risk_level`, `risk_factors` and `impact` into their details so the
   queue shows them without loading every mapping.
3. **Relationship discovery is measured, not guessed.** `POST /api/rounds/{id}/relationships` (optionally
   with an unsaved mapping document) loads every table of the round (`limit` rows each, default 20,000)
   and returns a graph: nodes are the mapping's sources plus the round's other non-noise tables (each
   with a ready-to-add `source_spec`); edges are the mapping's collection links and joins, and for each
   pair of tables not yet connected, the best primary-key → foreign-key pair found in the values
   (normalized trim + upper; a key column is ≥ 90 % unique, at least half of the referencing values must
   resolve; siblings hanging off the main table by the same key are skipped). Every edge carries
   cardinality, shared values, the match rate on both sides, orphan rows and warnings (a missing column,
   a repeating main key, rows dropped, joins that multiply rows). A mapping source that nothing reads is
   shown as unused.
4. **The Studio draws it itself.** A Relationships view renders the graph as SVG (no diagram library):
   solid lines are used links, dashed lines are discoveries, amber lines have problems. Clicking a line
   shows its numbers; dragging from one column's dot to another table's column proposes a connection,
   measured at once (`probe` in the request) so a wrong key pair shows "these columns do not connect"
   before it is added.
   Accepting one edits the mapping document only: a main-table edge becomes a collection link (many rows
   per record) or a many-to-one join (one lookup row per record); an edge between two other tables
   becomes a join into the referencing table. The DSL is unchanged (rule 2), and saving still creates a
   new mapping version through validation, simulation, sandbox and policy.
5. **Drag a column onto a field.** In the field view, source columns can be dragged onto target fields;
   the same helper backs the "use" button and places a field of a collection in that collection even
   before it is mapped (previously it landed on the record).

**Consequences** Reviewers triage by trust level with the reason in words; ops can connect a new sheet
without writing JSON and see whether the keys really match before saving. Discovery reads every table of
the round on each request (bounded by `limit`), so the Studio refreshes it only after edits settle.
