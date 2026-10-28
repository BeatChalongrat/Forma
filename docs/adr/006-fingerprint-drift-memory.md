# ADR-006 — Structural fingerprints, drift classes, mapping memory

**Decision** A round's tables are reduced to (normalised sheet name, normalised headers, inferred types).
Strict hash ignores row counts and header positions; loose hash ignores sheets. Drift is classified by
header set diff with fuzzy rename pairing and a main-table similarity check; files whose strict hash equals
another customer's approved format are flagged as possible wrong files. Approved mappings are keyed by
(customer, fingerprint); identical structure ⇒ verbatim reuse ⇒ policy may auto-approve.

**Consequences** Unchanged formats cost zero review and zero tokens; renamed columns show the exact diff.
