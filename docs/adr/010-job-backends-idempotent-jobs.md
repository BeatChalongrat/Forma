# ADR-010 — Pluggable job backends, atomic claims, resumable stages

**Status** accepted (Phase 7). Supersedes the "thread pool only" consequence of ADR-009; the
`enqueue()`/`@handler` interface of ADR-009 is unchanged.

## Decision

1. **One interface, three backends.** `jobs/runner.enqueue(kind, payload)` stays the only way services
   and the API start work. `FORMA_JOB_BACKEND` selects the executor:
   `thread` (default; pool inside the API process, zero services), `process` (`multiprocessing`
   spawn pool, `FORMA_WORKERS` processes, same machine) or `arq` (Redis queue; `forma worker` runs on
   any number of nodes). The API only enqueues; the arq worker executes `run_job(job_id)` in a thread.
2. **The database is the queue of record.** Every job is a durable `Job` row. A worker *claims* a row
   with one atomic `UPDATE … WHERE status='queued'`; a job submitted twice (restart, arq redelivery,
   stale recovery racing a slow worker) runs once. `attempts`, `worker`, `batch_id`, `request_id` are
   recorded on the row.
3. **Crash recovery is explicit.** `runner.start()` (API lifespan and `forma worker`) re-queues
   `running` jobs older than `FORMA_JOB_STALE_S` and re-submits `queued` rows whose in-memory
   submission was lost; after `FORMA_JOB_MAX_ATTEMPTS` a job is failed with a readable error and its
   round is marked `failed`. Failed jobs can be retried from the API/Ops page (`POST /api/jobs/{id}/retry`).
4. **Handlers are idempotent and resumable.** `JobProgress.mark(stage, …)` persists each finished stage
   in `job.result["stages"]` in its own transaction. A re-run of `run_pipeline` skips `profile` when all
   files are profiled, reuses the round's live proposal instead of proposing again, and does not create a
   second dataset version for the same mapping. `payload["force"]` disables the skips (the explicit
   `POST /rounds/{id}/transform` always forces a new version).
5. **Backpressure is a 429.** `FORMA_QUEUE_MAX` bounds `queued + running` jobs; `enqueue` raises
   `QueueFull` → HTTP 429 with `Retry-After`. Batches are checked as a whole before any row is created.
   Uploads above `FORMA_MAX_UPLOAD_MB` are rejected with 413 before they are stored.
6. **Parallelism at two levels.** `profile_round` profiles the files of one round concurrently
   (`FORMA_FILE_WORKERS` threads; fastexcel/Polars release the GIL); `POST /api/rounds/batch-run` fans
   out one `run_pipeline` job per round under a `batch_id`, aggregated by `GET /api/jobs/batch/{id}`.
7. **Measured, not assumed.** `forma bench --files N --rows R` builds a perf corpus (cached), loads N
   rounds into a throw-away database and runs profile → propose → approve → transform through the
   configured backend, reporting wall-clock, per-stage p50/p95, rows/s and peak RSS
   (`data/bench/report.{json,md}`, numbers in `docs/PERFORMANCE.md`). The CI runs it in short mode.

## Why

* Services never learned about threads, processes or Redis — swapping executors stays a config change.
* Durable rows + atomic claim is the simplest construction that is correct for every backend; a
  dedicated broker would duplicate state we already keep for the UI (progress, errors, lineage).
* Resumability belongs in the handlers (they know what "already done" means) rather than in the
  runner, which only knows job ids.

## Consequences

* SQLite works with the process backend (WAL + busy timeout) but is single-node; Postgres is required for
  more than one worker node (ADR-011).
* The thread backend is GIL-bound for the record-building part of the engine (see PERFORMANCE.md); use
  `process` or `arq` for throughput.
* Handlers must keep stage names stable (`profile`, `propose`, `transform`, `export`): they are part of the
  job ledger that the Ops page and the bench read.
