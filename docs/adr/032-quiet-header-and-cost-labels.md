# ADR-032: Quiet header and honest operational cost labels

Status: accepted; extends ADR-028 and ADR-031.

Help and the assistant remain in the sidebar; the duplicated global header shortcuts are removed.
Page-specific assistant actions remain available. System health uses the same simple Server icon
in navigation and the feature catalogue.

LLMCall records contain token usage and locally calculated cost, not provider billing data.
Usage.cost_usd applies an indicative model price table or a default input/output rate for unknown
models. The Ops and Home labels explicitly say estimated cost; Ops explains the calculation,
fallback and UTC dates. Usage record counts are not described as exact provider request counts.
No price table, historical usage or billing integration is changed by this UI correction.

The readiness badge describes service connections, not end-to-end job progress. Queue health,
stalled-job alerts and provider billing reconciliation require separate operational work.

Validation: frontend build and lint, plus browser checks of navigation, Ops and Home in both themes.
