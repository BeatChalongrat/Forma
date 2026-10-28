# ADR-008 — Policy gate between AI proposals and data

**Decision** `policies/approval.yaml` defines when a mapping may be auto-approved (previous human approval,
allowed drift kinds, per-risk confidence floors, error/warning budgets, row-count sanity), what goes to review
(with queue weights) and what is blocked (unrelated file, zero rows, missing identity). A human-approved
mapping that is reused verbatim skips field-confidence checks.

**Consequences** Automation rate is a tunable, auditable number, not a model property.
