# ADR 045 — Server-computed reports in assistant conversations

## Decision

The assistant can select a table, categorical bar chart, or chronological line chart, but cannot supply chart values, executable code, labels, units, or links. A read-only `build_report` tool computes a bounded report under the requesting principal's permissions. The final model response selects only a cited report evidence ID and one of that report's allowed views. Validation rejects invented references, unsupported views, extra fields, and reports accompanying unresolved clarification questions.

Supported reports:

- Work-round comparisons: 1–8 explicit work rounds; received, expected, ready, attention, processing and failed counts use the existing batch state resolver. Expected counts remain null when unset. Status counts are subsets of received.
- Work-round file lists: one round, optional status filter, up to 50 files; partial coverage is explicit and `complete` is false when truncated.
- Upload-date trends: 1–92 inclusive local calendar dates in a specified IANA timezone. Current statuses are grouped by original upload date, not completion date. Only customer-assigned nonduplicate source files are counted. Unassigned bulk files are explicitly excluded.
- Dataset grouping: one immutable dataset version, actual scalar field pointers from its target schema, up to three exact filters, at most 30 groups. Count, sum, average, minimum and maximum scan all records rather than profile samples. Null, boolean and nonfinite measures are excluded and disclosed. Declared monetary fields cannot aggregate mixed or missing currencies. Collections require a future explicit grain/join design; they are rejected rather than flattened implicitly.

Scope, units, exclusions, source navigation and generation time travel with every report. Ambiguous periods, rounds, measures or conventions must be clarified before a report is selected. Unsupported requests must be explained rather than silently approximated. This constrains numerical output; it does not guarantee that an LLM always interprets an ambiguous business request correctly.

## Presentation and persistence

React renders a native SVG chart and an accessible table, with light/dark theme variables, exact-value inspection, CSV export and source navigation. Chart coordinates use JavaScript numbers; the table retains decimal text. Values beyond the chart precision boundary are table-only. Charts are never stacked where measures overlap.

Reports are snapshots persisted in the existing conversation-turn reply JSON, including their query, so reopening a conversation does not silently substitute current data. Conversation-history retrieval includes report scope/query metadata; follow-up requests should recompute from live tools. No database migration or chart dependency is required.

## Validation

Tests cover exact decimal aggregation beyond sample limits, null and negative handling, currency/cardinality rejection, timezone boundaries and empty days, customer authorization, unknown expected counts, partial file coverage, persistence, and rejection of model-provided chart data. Existing assistant clarification and conversation tests remain applicable.
