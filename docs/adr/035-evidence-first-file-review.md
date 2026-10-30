# ADR-035: Evidence-first file review

Accepted 2026-10-29. Extends ADR-029 and retains all existing approval policies.

Review opens a full page with the customer, source filenames and mapping version. A persistent
section list puts source ownership and major data changes before individual questions. Previously
published data comparisons are labelled separately from mapping-rule changes. Missing baselines
are explicit. Original diagnostics remain available under technical details.

Each question fetches deterministic evidence on demand through an importer-role, customer-scoped
endpoint. The engine simulates at most 50 rows per source; up to five output examples are traced
through its provenance to immutable source cells. The source extractor's value is cross-checked
against the engine's extracted row, and failed cell verification is omitted, never guessed. An
incomplete trace is labelled. Source file, sheet, row, column and raw value are inspectable inline.
Examples and counts are explicitly samples, never presented as exhaustive file validation.

Fixed VAT flags are described as assumptions; price cells are context, not proof of VAT inclusion.
Other constants are labelled as rules rather than source values. Uncertain answers remain open.
An incorrect reading links to the exact field in Mapping Studio; there is no new automatic edit path.

Answer previews run the stored and proposed documents on the same bounded source sample and compare
by schema identity. The operator sees changed fields and before/after values, sample counts of changed,
lost and gained records, and errors. This replaces comparing a new 50-record preview with an old
three-record cached sample. Confirmation-only answers may change no values. Saving still requires
the preview digest, creates an immutable new mapping, records the actor and reevaluates the policy.
Unknown answers, source restrictions and unrelated open questions are not cleared by this workflow.

No migration, credentials, LLM calls or new external integration are required. Existing JSON preview
contracts are extended additively. Tests cover verified Excel cells, root and collection row provenance,
constant VAT context, missing sources, authorization, fresh comparison counts and immutable versions.

Check on the demo corpus: C902 round 4 traced the pack count `12` to the cell Products!G2 with the cell
value verified, while PCS/DZ/BOX units produced no pack count and were shown as missing rather than
guessed. The VAT answer preview ran without applying any edit; unknown answers stayed unresolved; the
"read incorrectly" link selected the pack-size field in the Mapping Studio; a 390 px viewport had no
document overflow. The workspace, evidence and lineage suites, strict mypy and the frontend build pass.
