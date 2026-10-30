# ADR-044 — Reconcile durable jobs with published results before queue recovery

Status: implemented 2026-10-30.

A queued `process_batch_item` had no Redis entry, while its BatchItem already pointed at a published
round. The job's `bat_` identifier is a job group, not the upload's `bch_` identifier; resolving the upload
must go through `payload.item_id` and BatchItem.batch_id. The upload and its output were not missing.

On API/worker startup, compare queued bulk-file/pipeline jobs against durable evidence: published round,
the round's current published dataset, its corresponding mapping, and a JSON export for that exact
dataset/customer. Only complete matches may be reconciled to done. Record the existing IDs and reason in
`job.reconciled` audit events, preserve stage history, and use a conditional queued-to-done update so an
active worker's claim is not overwritten. Forced jobs are not reconciled.

Then recover stale running jobs and resubmit pending job IDs to every backend, including Redis. ARQ's
stable job IDs and the database's atomic claim prevent a duplicate delivery from executing the same job
twice. Recovery is performed at startup; this is not a continuous queue monitor or a transactional outbox.
API and ARQ startup run synchronous recovery in a thread to avoid nesting asyncio event loops.

Workers and the pipeline also check published-result evidence before processing a resumed delivery.
They reuse the recorded result without reprofiling, remapping, transforming, or publishing again.
Explicit forced pipeline runs retain their existing semantics. This does not claim global exactly-once
execution of different job IDs concurrently targeting the same unfinished round.

`tests/test_job_recovery.py` covers completed-job repair, stable output counts, duplicate deliveries,
audit idempotence, incomplete evidence, active/forced work, Redis resubmission and async startup.
Existing job, bulk-upload and pipeline tests cover normal processing and failure/retry behavior.
