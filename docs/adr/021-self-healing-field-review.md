# ADR-021 — Self-healing review: per-field changes, checked on the data

**Status** accepted · 2026-10

**Context** When a customer's format drifts, FORMA re-proposes a mapping (heuristics or the adaptation
agent) and the sandbox compares it with the approved mapping on the new files. But a renamed column is
exactly the case where the approved mapping cannot read the new files, and "NetPrice ≈ SellingPrice"
must not be accepted because the names look alike. Reviewers also had to read the whole mapping to find
the two fields that changed.

**Decision**

1. Every proposal for a customer with an approved mapping (except a verbatim memory reuse) gets a
   **field review** (`services/healing.py`): each target field is `unchanged`, `changed` (other expression
   or other table), `added` or `removed`, with the columns read before and after.
2. Each changed or added field is **checked on the data**: the new mapping's records (up to 5,000) are
   matched by identity with the customer's last *published* dataset and the field's values compared.
   ≥ 90 % equal → `same`; a steady ratio of 1.07 or 1/1.07 → `vat_added` / `vat_removed` (the Thai VAT rate
   is the named constant `engine.thai.VAT_RATE`); another steady ratio → `scaled` (unit, pack, currency);
   otherwise `different`; fewer than 3 shared SKUs → `no_data`.
3. **The policy decides** (rule 6): a `vat_*`, `scaled` or `different` check on a critical or high field adds
   a review reason (`data check: …`) and a high-risk factor (ADR-019), so it can never auto-approve.
4. The review is stored with the proposal (`policy.field_review`) and served by
   `GET /api/mappings/{id}/field-review` (computed on demand for older versions). The Studio opens a drifted
   proposal on **What changed**: fields that need a decision first (risky checks, removed fields,
   critical/high fields), then safe changes; unchanged fields are accepted with the approved mapping.
   "Use approved" puts the approved expression back (or removes an added field); saving creates a new
   version that goes through validation, simulation, sandbox and policy again.

**Consequences** A changed ERP export is repaired from the approved mapping instead of from scratch, and the
reviewer decides only the fields whose data says something changed. The check needs one published round
of the customer; until then it reports `no_data` and the usual confidence rules apply.
