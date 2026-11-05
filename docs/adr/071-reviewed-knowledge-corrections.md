# ADR-071: Versioned corrections to interpretation knowledge

Date: 2026-11-05

## Context

Approved answers could be retired, but correcting one required opening the mapping editor. Usage events alone did not show indirect reuse across later rounds. A matching layout also did not rule out changed locale, currency or parsing conventions.

## Decision

- An approver can preview and revise a remembered interpretation from Settings → Learning. The preview executes both mappings against the complete delivery and checks record identities, schema errors and unrelated fields. Only the displayed examples are limited.
- Saving immediately retires the wrong answer and creates a proposed mapping version. It does not edit an approved document, a dataset or an exported artifact. The new answer becomes reusable after the ordinary approval gate. Failure to approve leaves the old answer retired.
- The preview digest includes the current mapping, policy, source identities, requirements, answer and reason. Revision locks the source round and refreshes state. Two stale browser tabs cannot both apply a revision.
- Retired answers are checked transitively before approving dependent proposals. Recalled answers keep their original provenance instead of minting independent correction records on every approval. A corrected or explicitly reopened branch records which answer it replaces.
- The impact view shows both direct and indirect dependent versions. Selecting current deliveries creates new review versions; it never starts paid model work or silently republishes results. Closed deliveries and missing source files cannot be reopened through this path. Repeated requests are idempotent, and changed heads require refreshing the selection.
- Country, locale and currency come only from explicitly named source columns. Date and number conventions use observed hints and representations. Changes veto verbatim reuse and recalled constants/decisions. File renaming, ordinary price changes and new unit tokens do not by themselves reject an entire format; new unit tokens still require their own decisions. A fixed unit is reconsidered when observed unit codes change.
- These observations are bounded profile evidence, not proof of business meaning or every row. Full execution, validation and human decisions remain required. Unknown headers do not establish a country or currency.
- Question controls show source filename, sheet, column and observed examples, with a distinction between direct evidence and context-only values. Canonical Thai labels wrap the original business question; custom wording is retained. An unanswered item never becomes approval.

## Operations

No schema migration is needed. Rebuild the API, workers and web application together. This change does not change worker count, RAM limits, provider keys or dev data. Existing stored verdicts refresh under the new verdict rules version.

Tests use mock models and isolated data. They cover wrong answers followed by revision, indirect withdrawal, conflicts, changed currency with identical headers, unknown unit codes, source-scoped effect checks, stale previews, concurrent apply and customer permissions. They establish workflow correctness, not real-model mapping accuracy.

## Limits

Historical exports remain immutable and may contain the earlier interpretation. They are identified for review, not retroactively corrected. Correcting a retired answer whose source no longer exists requires uploading the source again. A field whose current expression differs from the remembered rule must be reviewed explicitly rather than patched blindly. Very long dependency chains fail closed for review.
