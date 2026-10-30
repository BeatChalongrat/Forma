# ADR-046 — Approved mapping library across ERP layouts

## Decision

Choose mappings by customer, the resolved target schema (`id@version`), and the round's
structural fingerprint (including the existing type-change checks). Prefer the newest
approved exact layout across history, not just the customer's current approved version.
If there is no exact layout, the closest compatible approval is an adaptation baseline
(newest approval breaks similarity ties);
if there is no compatible target contract, create a new proposal.

An approved mapping that becomes `superseded` remains a library entry. Superseded drafts
without `approved_at`, rejected mappings and other customers' approvals are excluded.
New proposals and human edits record a target-schema document digest. Legacy approvals
without a digest remain usable under their pinned schema version, since schema registration
is append-only. An out-of-band mutation of a schema with a recorded digest prevents reuse.

The library reuses transformation rules, never old JSON output. Each upload still runs
validation, simulation and the existing approval/publishing checks on the new input.
Mapping versions continue increasing even when their parent is an older layout.

Bulk customer identification considers historical layouts and ranks distinct customers
(two layouts of one customer must not be treated as ambiguous customer identities).
Identification is not permission to reuse a mapping: reuse is decided after the target
contract and the full round's files are known. Batch and round screens show the actual
selection, the saved mapping version and a link to inspect it.

## Verification

Regression coverage includes A → B → A with changed values, schema version switching,
target digest mismatch, newer operator corrections, exclusion of unapproved/rejected
history, customer boundaries and invalid data in a recognised format. No schema migration
or external AI call is required to build the library from existing approval history.
