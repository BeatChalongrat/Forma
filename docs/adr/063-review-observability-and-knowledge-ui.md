# ADR-063 — Review history, knowledge inspection and schema-driven value editing

Status: accepted, 2026-11-03.

## Decision

Expose the existing backend's approval and learning boundaries in the UI, rather than treating a chat answer or a confidence score as proof of correctness.

- Review and round pages have a decision-history entry point. Events are ordered by timestamp and ID, paginated and filterable. Mapping inspection shows version/parent, recorded human decisions and their before/after expressions, approval actor, whole-delivery answer checks, linked knowledge and paginated agent logs. Existing dataset version/diff/lineage views remain the output inspection tools.
- Answer application persists the complete-delivery effect-check result with actor, time, original/new mapping IDs and preview digest. Each new structured decision records its mapping and answer time. `review.answers` records the transition in the audit log. A successful effect check is not a claim that all business facts are correct: remaining errors are displayed separately.
- New mapping runs record `learning.use` events with correction, mapping, round and retrieval method. Learned hints are distinguished from decision recall and approved-format reuse. Old aggregate counters are retained; missing historical events are not reconstructed. Logs are recorded activities and results, not hidden model thoughts.
- Settings offers searchable, paginated active/retired knowledge with customer scope, source question/answer, target contract, requirements, layout fingerprint, conflicting active decisions and usage events. Retirement affects future reuse and preserves previous outputs. Revisions go through the existing mapping editor, immutable version creation and approval, rather than directly overwriting approved knowledge. Personal assistant memories remain separately managed at `/memory`.
- Audit browsing supports actor/action/entity and UTC date filters. Round/mapping/knowledge APIs enforce the existing customer scope; organization-wide audit requires an unrestricted administrator. Diagnostic payloads mask common credential keys and API-key strings. Source/customer data still requires the same access controls.
- Assistant answers allow inspecting their persisted retrieval snapshots. Snapshots can be partial or stale and are labelled accordingly. This adds observability to existing retrieval; it does not introduce a vector database or change retrieval semantics.
- Table, paste, bulk-set and nested JSON edits use an explicit number grammar. Ambiguous separators, non-finite values, unsafe/non-integral integers, unknown booleans and impossible dates are rejected locally. The UI never silently rounds an integer or guesses an unknown boolean as false. JSON pointer escaping preserves literal `/`, `~` and empty keys without mutating object prototypes.
- Nested record editing follows the target field catalogue, supports objects/arrays and nullable/enum values, and stages edits for the existing full-schema validation and append-only dataset version save. The browser is not a substitute for server validation, including complex schema constraints.

## Entry points

1. Review a file → **Decision & AI history**; or round page → **Decision and AI history** → inspect round.
2. Settings → **What FORMA learned** → filter → select field → inspect original decision, compare expressions or open mapping for a revised version.
3. Settings → **Audit log** → select dates/actor/action/entity → page through full events.
4. Assistant response → **Inspect evidence snapshots**.
5. Dataset grid → select number format → open a record/collection → edit typed values or nested JSON → review pending changes → save a new version.

## Validation

API regression tests cover answer-check persistence, original-version isolation, usage links, log pagination, scoped access, conflicting/retired knowledge, audit filters, stable pagination and credential masking. Frontend tests cover explicit locale parsing, dates, booleans, container types, pointer escaping and prototype-safe overlays. Existing backend, corpus, frontend and production-build checks remain required. No provider/API key, source fixture, mapping approval or customer output is changed by opening an inspection view.

## Limits

This release cannot retroactively supply checks or per-use timestamps that old versions never stored. Retiring knowledge does not rewrite an already approved mapping or export. A new rule still needs a reviewed version and approval. Accuracy on real ERP files requires a separate acceptance pilot; UI observability does not raise the measured mapping score by itself.
