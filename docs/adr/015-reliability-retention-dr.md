# ADR-015 — Reliability, retention and disaster recovery

**Status** accepted (Phase 7).

## Decision

1. **Retries are explicit and bounded.** `core/retry.py` implements exponential backoff with jitter.
   LLM provider calls retry transient errors only (timeouts, connection errors, 429/5xx;
   `FORMA_LLM_TRANSPORT_RETRIES`, `FORMA_LLM_BACKOFF_S`); S3 uses boto3's standard retry mode
   (5 attempts); local disk does not retry.
2. **Circuit breaker, then the heuristic.** `ai/breaker.py` opens after `FORMA_LLM_BREAKER_FAILURES`
   consecutive provider failures for `FORMA_LLM_BREAKER_RESET_S`, then allows one probe. While open,
   calls fail fast as `LLMError`; the mapping/adaptation agents return the deterministic draft and
   `PolicyEngine.evaluate(llm_fallback=True)` always routes the round to review — a fallback can never be
   auto-approved. The state is a Prometheus gauge and on the Ops page.
3. **Nothing partial is ever visible.** Dataset versions are written object-first, DB row last, object
   deleted on failure (ADR-012). Exports are written to a temp file and published with one atomic put.
   A worker that dies mid-transform leaves no object and no row; `recover_stale` re-queues the job.
   `tests/test_failure_injection.py` kills a real worker process, fails storage writes, breaks the DB
   commit and times out the LLM, and asserts these invariants plus a readable error on the round.
4. **Retention is policy, dry-run by default.** `policies/retention.yaml` declares how long source files
   outlive their round, how many dataset versions to keep per round (approved/published versions and
   rollback parents are always kept), export/job/LLM-ledger ages and the S3 cache age. `forma retention run`
   plans; `--apply` deletes; `FORMA_RETENTION_INTERVAL_H` schedules it inside the API/worker process. Every
   deletion writes an audit row; purged rows stay (`status=purged`) so history remains explainable.
5. **Backup = DB dump + storage manifest.** `forma backup` writes a directory (or tar.gz) with the
   database dump (SQLite online backup API / `pg_dump`), `manifest.json` (every key with size and sha256,
   schema revision, masked config) and optionally the objects. `forma restore` replays into an empty
   database and re-uploads objects, reporting any key the manifest lists but the backup lacks.
   Targets: **RPO ≤ 24 h** (nightly backup; S3 versioning/replication lowers it to minutes for objects),
   **RTO ≤ 1 h** (restore procedure in `docs/RUNBOOK.md`).

## Why

Customers send money-relevant data every two weeks; the failure modes that matter are "we lost a
file", "we published half a dataset" and "the model is down at 02:00". Each has one explicit answer here
rather than a best-effort behaviour.

## Consequences

* A provider outage degrades automation (more reviews), never correctness; operators see it on the Ops
  page and in `forma_llm_breaker_open`.
* Retention needs the same storage the API uses (`FORMA_STORAGE`), and `--apply` is deliberately not the
  default anywhere.
* Backups contain customer data; the backup directory must inherit the data directory's access rules.
