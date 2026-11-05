# ADR-070: Transaction integrity and recovery gates

Status: accepted, 2026-11-05

## Context

The release audit reproduced five failures: a successful HTTP response after a failed commit,
two edits from the same dataset head creating duplicate version numbers, persistent corrections
losing their selection predicate, publishing a cancelled import, and restore accepting damaged bytes.
The recovery test also relied on a large workbook to make a worker slow enough to kill.

## Decisions

- Request middleware commits before returning success. A commit failure rolls back and returns 503
  with a request ID; dependency cleanup never retries the commit. New dataset/export objects are
  removed on rollback using transaction-owned unique keys only after a fresh connection proves they
  have no durable database reference. An ambiguous commit acknowledgement or unavailable reference
  check must not destroy committed data; an unverified orphan can be cleaned up later.
- Dataset mutations and lifecycle transitions share a round lock. Only the current head can be
  edited, rolled back, approved or exported. A stale writer gets 409 and the current dataset ID;
  the editor keeps the draft and links to the latest version. The database also enforces unique
  `(round_id, version)`. The migration refuses existing duplicates rather than rewriting history.
- Persistent assignments preserve both explicit identities and their predicate. New rules record
  the target schema reference and document hash. Unsupported persistent operations are rejected
  before any data changes; they remain available as one-time changes. Legacy unscoped rules retain
  their previous behavior and should be reviewed before knowledge promotion.
- Closed rounds cannot create, edit, approve, roll back or export new results. Existing immutable
  history remains readable. Rollback creates a new draft and updates the current round summary.
- Backup includes a checksum of the database dump. Restore validates paths, dump and all object
  checksums before publishing database references. Missing files or conflicting target objects fail
  the operation. PostgreSQL restore uses one transaction and stops on SQL errors.
- Allowed knowledge pins its source round and transitive evidence. Retention rechecks the preview
  under the same knowledge lock, protecting knowledge approved after preview. Withdrawal is explicit.
- Dataset browsing builds a disposable on-disk index per immutable version and pages results with
  bounded Python memory. Numeric sorting retains numeric semantics. Substring search still scans
  this index; it is not a distributed search engine. Concurrent cold S3 downloads own separate temp files.
  On Windows, simultaneous atomic replacements can still fail with access denied. A losing download
  can reuse an already-published immutable key; it must propagate the error when no completed file exists.
- Counts in the overview use database aggregates and name the unit (import deliveries). The short
  attention list is separate from its total. Upload modes share a back link that preserves the origin.
- Operational alerts use durable job and housekeeping state. Prometheus rules and receiver setup
  are separate; delivering notifications requires the deployment's Alertmanager configuration.
- The web proxy resolves the API through Docker's embedded DNS at runtime, using a shared upstream
  zone and a short cache lifetime. Container replacement can change its IP address; a static upstream
  left the UI returning 502 after an otherwise successful restart. The image pins nginx 1.27.5,
  which supports open-source upstream `resolve` (available since 1.27.3). Configuration validation
  runs against the built image in CI. See the [nginx upstream documentation](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#resolve).

## Verification and consequences

Regression tests exercise commit faults, concurrent writers, cancellation races, persistent selections,
corrupt/missing/traversal backup contents, retention approval races, migration conflicts and 100,000-row
browsing. The worker recovery test pauses a real spawned worker at handler entry, kills it, proves that
a valid lease is not stolen, expires the dead lease, and checks one recovered output at attempt two.
It tests recovery rather than relying on a workbook-size timing race. Large-workbook/load tests remain
separate requirements.

The isolated repro smoke runs PostgreSQL, Redis/ARQ and MinIO with mock-only network restrictions.
It reviews, edits concurrently, approves, downloads, restarts services and compares download hashes,
then restores to a fresh database and bucket. A synthetic LangGraph checkpoint and attempt ledger are
also checked after restore. This proves system behavior for the fixture; it does not measure real-model
accuracy, unknown ERP formats, identity-provider integration, cross-host failover or external alert delivery.

Historical backups without a database checksum require an explicit legacy recovery procedure; the
default verified restore now refuses them. Do not disable verification as a routine workaround.
