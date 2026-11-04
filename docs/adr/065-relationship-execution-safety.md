# ADR-065: Relationship execution, source-cell validation and bounded AI calls

Status: accepted, 2026-11-04. Refines ADR-043 and ADR-060.

Complex-workbook evaluation exposed four distinct concerns: typed date filters were converted to
numbers, an unused uncached formula blocked an otherwise usable sheet, equivalent renamed columns
generated disagreements, and a provider could keep an HTTP connection alive indefinitely.

## Decisions

- Ordered DSL comparisons preserve Date/Datetime types. Numeric strings retain the existing number
  parser. Mixed date/text operands require explicit parsing instead of implicit guessing.
- Source validation follows columns used by expressions, joins, collection links, filters and
  fill-down. Invalid formula caches/errors/external references in these columns block execution
  with sheet and cell coordinates. Unpivot and root attribute passthrough validate all selected
  columns. Standalone readers remain strict by default. Validation covers the full column, not
  merely the execution sample, conservatively including cells outside a selected row range.
- FORMA still does not recalculate arbitrary Excel formulas. A model can inspect formulas in the
  isolated sandbox and propose equivalent declarative expressions over available inputs. Missing
  external data requires a repaired source or a scoped human decision; no invented quantity.
- `CollectionMapping.aggregate` explicitly groups and sums additive rows after field expressions
  and unit conversion, before duplicate handling. Every mapped field must be accounted for;
  identity fields must be grouped; only numeric fields may be summed. Any null input makes the
  corresponding total unknown and generates an error. All contributing row references survive.
- Cell signatures resolve renamed aliases back to their source names. Record/collection structure
  is checked separately. Only narrowly identified heuristic discovery questions may be retired
  after clean execution with populated source-backed fields in the matching source; no business
  assumption is retired merely because the model is confident.
- Provider HTTP calls use a wall-clock deadline, including keepalive reads, plus a context-local
  total task budget across calls/retries/repairs. Sandbox execution uses the remaining budget.
  This bounds AI I/O, not unrelated CPU work or database transactions. Invalid response shapes,
  truncation and empty content become actionable failures, never accepted partial mappings.
- PostgreSQL workers publish live stage messages. SQLite retains stage-boundary progress because
  publishing on a second connection during the mapping transaction would lock its single writer.
- A failed/incomplete recorded full-file verification cannot be overridden by human approval.
  Legacy versions without a verification snapshot retain the prior compatibility path; they are
  not evidence of successful verification. Review UI displays failures and repair navigation.
- Missing operands are `request_source` decisions, with file-repair navigation, not an input that
  replaces the final calculated field. The backend rejects constant answers over arithmetic even
  for old saved proposals. Root-record conflicts with one varying source produce source-row choices;
  selecting one excludes only the competing rows in this immutable delivery. These choices cannot
  become reusable customer memory. Conflicting N:1 lookup keys are execution errors, not warnings
  that could silently authorize whichever row happened to come first.
- Aggregated provenance retains one representative row per output item for compatibility and an
  aligned `item_groups` list with every contributing row. Evidence and lineage views expand groups.

## Validation

Regression tests cover temporal/numeric comparisons, invalid used vs unused formula cells,
renamed evidence, scoped question retirement, additive identical lots, null totals, aggregation
validation, malformed provider payloads, keepalive cancellation and non-overridable verification.
Real-stack reruns use unchanged neutral contracts and workbooks in `relationships_v1`, fresh
customers and separate before/after-human snapshots. Expected answers are not model inputs.

The suite is synthetic and small. A passing schema or high field score is not a claim that every
business interpretation is correct, and safe stops/questions are reported separately from errors.
