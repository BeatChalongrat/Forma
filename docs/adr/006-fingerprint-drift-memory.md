# ADR-006 — Structural fingerprints, drift classes, mapping memory

**Decision** A round's tables are reduced to (normalised sheet name, normalised headers, inferred types).
Strict hash ignores row counts and header positions; loose hash ignores sheets. Drift is classified by
header set diff with fuzzy rename pairing and a main-table similarity check; files whose strict hash equals
another customer's approved format are flagged as possible wrong files. Approved mappings are keyed by
(customer, fingerprint); identical structure ⇒ verbatim reuse ⇒ policy may auto-approve.

**Amendment (2026-10)** Types are compared, not hashed: the strict hash covers sheets and ordered headers
only, and `compare` reports a column whose type family changed as `types_changed` even when the hashes are
equal (int/float are one family; mixed/empty columns carry no signal). Same headers holding different data
therefore never reuse a mapping verbatim.

**Consequences** Unchanged formats cost zero review and zero tokens; renamed columns show the exact diff.
