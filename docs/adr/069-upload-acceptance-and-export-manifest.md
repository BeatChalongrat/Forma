# 069 — Upload acceptance and export receipts

Date: 2026-11-04

An upload must not disappear because another file has the same name and length. Nor should a successful
business transaction depend on a later attempt to create a Job. A partial ZIP must state what it omits.

## Decision

- The browser keeps all supported selected files. The server deduplicates by sanitized filename and
  SHA-256 **within a work round**; identical bytes under different names may represent different customers.
- Initial bulk upload accepts an optional `upload_key`. The browser keeps it across retries of the same
  first chunk/options. An actor-scoped receipt and payload digest are persisted in the upload transaction.
  A PostgreSQL advisory lock (SQLite write transaction in tests) serializes concurrent replay. A changed
  payload with the same key returns 409. This is request idempotency, not resumable byte transfer.
- `stage_job(session, ...)` writes durable work in the caller's transaction. Create/append/assign/resume,
  move/replace/file actions and mapping approval stage work before committing the business change.
  `deliver_jobs` only notifies the executor after commit. Notification failure leaves accepted work in
  the existing outbox; its periodic recovery retries delivery. Queue capacity rejection rolls back the
  associated transaction. Legacy attached files with no active job can be resumed; old queued jobs are
  not duplicated just because they have waited a long time.
- Every nonempty batch ZIP contains `_forma/manifest.json`. It records all received files, omitted reasons,
  expected/received/exported counts, missing deliveries, current source hashes, round/mapping/dataset/export
  versions, output row counts and hashes. Only the latest approved dataset of a published round is eligible.
  Export bytes are checked against their stored SHA-256. Metadata lives in its own folder so a source named
  `manifest.csv` can still export `manifest.json`. Zero ready files still returns 409.

## Limits

Storage blobs themselves are outside the database transaction. A rolled-back request can leave a
content-addressed blob for retention cleanup; it cannot leave an accepted file row without its job.
The retry key is held in the mounted upload page, not retained across closing/reloading the browser.
ZIP creation still buffers output in memory. The manifest is an inventory, not a claim that model
interpretations are correct. Explicitly cancelling active execution remains a separate lifecycle action.

## Evidence

`manual_test_files/upload_flow_v1/REPORT.md` records the isolated fake-provider journey, browser retry,
real Redis outage/recovery and targeted regression results. No paid inference was used.
