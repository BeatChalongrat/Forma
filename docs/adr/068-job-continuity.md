# ADR-068 — Durable job ownership, cancellation and shared provider admission

The production runner uses PostgreSQL as its job ledger and Redis/ARQ as a delivery mechanism.
Redis delivery is at least once. This change does not claim exactly-once HTTP requests or billing.

## Ownership and recovery

* Each claim has a fresh lease token, heartbeat and expiry. Heartbeats renew at one third of
  `job_lease_s` (default 90 seconds). Execution has a separate 1,800-second limit.
* Startup and a periodic recovery loop reclaim expired running jobs. Recovery uses row locks and
  compare-and-set; exhausted attempts become visible failed jobs. Queued waiting time is not execution time.
* SQLAlchemy business commits inside a job check its current token, expiry and active source round.
  Expired, cancelled or replaced attempts cannot commit. Stage receipts commit in the same transaction
  as the corresponding output, removing the crash gap before the former separate `mark()` transaction.
* PostgreSQL claims also serialize jobs for the same round (or batch item). SQLite uses a write lock.
* Completed stages of the same job survive retry even when its original request was `force=True`.
  A fresh forced job still deliberately runs the stages again.

## Queue reconciliation

Job rows are the durable outbox. A dispatch timestamp is claimed atomically, then submission happens
after commit. A broker outage leaves the accepted row queued. Periodic reconciliation repairs lost
ARQ queue memberships and retained results for unclaimed jobs. Delivery IDs are stable per attempt;
the DB claim remains authoritative. Concurrent upload admission checks and inserts share a transaction.

## Stop and retry

ARQ launches one child process per active job. The parent monitors cancellation, imposes a deadline,
terminates then kills an uncooperative child, and expires only that child's own lease token.
Duplicate deliveries cannot expire the real owner's lease. Development thread/process backends use
cooperative checks and commit fencing; the hard process-kill guarantee applies to ARQ.

API lifecycle actions first commit a stop request so an active child releases its source transaction;
the lifecycle transaction rechecks and revokes jobs again alongside the source change. If replacement
itself fails, the original source is preserved and its stopped job can be continued. Job cancellation
keeps source files and committed stages. The admin can continue a stopped job, but cannot retry work
for a superseded, moved or cancelled source round. The Ops page exposes active heartbeats, stop and
continue controls; the round page treats stopped jobs as stopped rather than successful.

The stop transaction touches the job first and commits immediately. Source status updates use skip-locked
and are repeated by the parent after the child exits; they cannot hold up cancellation behind a long-running
mapping transaction. Business publication and lifecycle mutations lock round then job. Read-only nested
sessions do not acquire publication locks; flushed/bulk writes remain tracked through savepoint rollback.

## AI coordination

Structured and streaming provider HTTP requests share Redis concurrency and rolling-minute admission
by hashed credential and endpoint host. Retries acquire capacity too. A 429 installs a shared cooldown
using Retry-After. Redis coordination failure does not bypass limits. Local development has a process-local
equivalent; `auto` selects Redis for ARQ. Slots expire after bounded request time, so worker death releases
capacity. Shared defaults are four calls and 60 requests/minute and must be tuned to the actual provider quota.

Existing graph attempt reservations and task deadlines remain in force. Waiting for capacity consumes
the task deadline; exhaustion goes to review/failure rather than permitting unlimited retries. Remote
providers may continue a request after local cancellation: unknown usage cannot be made zero by killing
the client. Fencing guarantees database publication safety, not refunds or exactly-once external effects.

## Checkpoint concurrency fixes found during real-stack testing

Fresh PostgreSQL checkpoint schema setup runs before jobs are accepted. Otherwise the saver's concurrent
index migration can wait on the mapping transaction that called setup. The PostgreSQL attempt ledger
has its own connection; it must not issue SQL on a connection simultaneously used by the saver's background
pipeline writes. SQLite already used separate checkpoint and ledger files.

## Validation and operational limits

See `manual_test_files/stability_v1` for the isolated PostgreSQL/Redis/ARQ stack, deterministic local HTTP
model, 300-upload runner and fault probes. Test credentials cannot call a paid provider. The mock returns
the supplied deterministic draft; these tests assess continuity, boundedness and publication safety,
not mapping intelligence or real ERP throughput. Worker restart policy must match production
(`unless-stopped`); a supervisor is necessary if the worker process itself exits during a Redis outage.

This does not add multi-host high availability for PostgreSQL/Redis/storage, backup restoration tests,
or resource limits for giant workbooks (the separately proposed items 6–7). Orphaned storage objects can
remain after a rolled-back business transaction; they are not published through a committed dataset.
