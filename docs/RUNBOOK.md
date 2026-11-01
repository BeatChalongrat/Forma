# FORMA operations runbook

Who this is for: the on-call operator of a FORMA production deployment (compose or equivalent).
Everything here uses the `forma` CLI inside the API/worker container (`docker compose exec api forma …`)
or the Ops page (`/ops`, admin role). Health endpoints: `/health` (liveness), `/ready` (dependencies),
`/metrics` (Prometheus).

## Service objectives

| Objective | Target | How it is met |
|---|---|---|
| RPO (data loss on total loss of the node) | ≤ 24 h | nightly `forma backup` (DB dump + manifest + objects). With S3 versioning + cross-region replication the object RPO drops to minutes; the DB RPO follows your Postgres backup/WAL shipping. |
| RTO (time to serve again) | ≤ 1 h | documented restore below; images are stateless, state is DB + bucket. |
| 300-file night | profile+propose ≤ 10 min, transform ≤ 15 min | `docs/PERFORMANCE.md`; 8 worker processes on a 12-core node. |

## Daily checks (5 minutes)

1. Ops page: queue depth ≈ 0 outside import windows, no failed jobs, breaker closed, spend as expected.
2. `/ready` is `ok`; `forma_review_oldest_age_seconds` under 2 days.
3. Last backup younger than 24 h (`ls data/backups` or the bucket's backup prefix).

## Procedures

### Worker crashed / machine rebooted

Symptoms: jobs stuck in `running`, `forma_jobs_queue{status="running"}` flat, the round shows no progress.

1. Restart the worker (`docker compose restart worker`). On start it re-queues every `running` job older
   than `FORMA_JOB_STALE_S` (default 30 min) and re-submits `queued` rows (ADR-010).
2. Do not wait for the window if you know the worker is dead:
   `forma shell` is not needed — call the API: `POST /api/jobs/{id}/retry` only works for `failed` jobs;
   for a stuck `running` job run `docker compose exec api python -c "from forma.jobs import runner; print(runner.recover_stale(0))"`.
3. Nothing partial can be visible: dataset versions are written object-first, row last (ADR-015). The
   re-run skips stages the job already recorded (`profile`, `propose`) and redoes the transform.
4. After the third attempt the job is marked `failed` with "worker died"; look at the worker logs
   (`request_id` on the job row links to the originating HTTP call) before retrying from the Ops page.

### Round stuck in `review` / nothing moves

1. Check the Review Center: a `blocked` task means policy stopped it (wrong file, zero rows) — the reason
   is on the task. Resolve with the customer; reject the mapping; upload the right file in a new round.
2. If the task is `drift`, open Mapping Studio, fix the re-pointed columns, Save → a new version is
   proposed; approve it. The approved mapping becomes the memory for the next round.
3. If the round is `failed`, the error is on the round summary and the job; most causes are an
   unreadable file (open it in Excel, re-save as xlsx) or a provider outage (see below).

### Wrong file published — roll back and re-export

1. In the Data Editor open the published dataset version. **Rollback** creates a new version whose content
   equals the parent (history is never rewritten). Approve it.
2. Export again: a new `ExportArtifact` with a new sha256 is created; the previous artifact stays in the
   audit trail. Tell the downstream consumer which sha256 to take.
3. If the wrong *source file* was uploaded: reject the mapping (the round becomes `rejected`), start a
   new round with the correct file. Policy will flag a file whose structure matches another customer's
   format (`unrelated_file`/`matches_other_customer`) before any data is produced.
4. Audit: `GET /api/audit?entity_id=<dataset id>` lists every edit, rollback and export with the actor.

### LLM provider outage

Symptoms: `forma_llm_breaker_open` = 1, Ops page shows "circuit breaker OPEN", rounds land in review with
"AI mapping unavailable (provider failure or rejected output); heuristic draft used".

1. Nothing to do for correctness: the deterministic mapper produced the draft and policy refuses to
   auto-approve a fallback. Reviewers approve as usual (returning customers with identical structure are
   still approved from memory without any model call).
2. The breaker probes the provider every `FORMA_LLM_BREAKER_RESET_S` (120 s). When the provider is back the
   gauge drops to 0. To force a reset restart the API.
3. If the outage is a quota/rate limit (`HTTP 429`), lower `FORMA_LLM_RATE_LIMIT_PER_MIN` or add a
   backup key in Settings → AI (another provider, model or gateway). Backups are tried in order with their
   own breakers, so the next round uses them at once (ADR-025); "Test all keys" shows which key answers.
   The mapping DSL is provider-agnostic.

### The assistant cannot run code

Symptoms: Settings → AI shows the sandbox as not available; the composer says the sandbox is off; the assistant says
it cannot run code. Everything else keeps working.

1. `docker compose ps sandbox`. If it is not healthy: `docker compose restart sandbox`, then the API once, so both
   see the same socket.
2. The service has no network by design (ADR-048). Do not add one to debug it. To look inside, use
   `docker compose exec sandbox python -c "import pandas; print(pandas.__version__)"`.
3. A run that ends "stopped by a limit" ran out of memory or processes. Limits are in `code_sandbox/runner.py` and the
   service definition. `FORMA_CODE_SANDBOX_TIMEOUT_S` sets the time limit and `FORMA_CODE_SANDBOX_MAX_INPUT_MB` the data
   one run can receive.
4. A new library goes in `deploy/sandbox/requirements.txt`, followed by a rebuild of the image.
5. LibreOffice failing with "no valid pipe path found" means the service lost its `/tmp` mount.

### Storage outage (S3/MinIO)

`/ready` reports `storage: ok=false`; uploads fail with 5xx; transforms fail with the boto3 error on the job.

1. Fix the bucket/credentials (`forma config show` prints the effective, masked config).
2. Failed jobs are retried from the Ops page; no partial version exists (ADR-015).
3. Workers cache objects under `<data>/cache/`; a cold worker re-downloads on demand.

### Schema version rollout (new target contract)

1. Drop the new JSON Schema into `target_schema/` (new `version`) or `POST /api/schemas` (approver role).
   `GET /api/schemas/{a}/diff/{b}` lists added/removed/changed fields.
2. Nothing existing changes: mappings and datasets reference their schema `ref`. New rounds use the
   round's `target_schema`; set it when creating the round (`RoundIn.target_schema`).
3. Migrate customers one at a time: run a round against the new ref; for returning customers the
   approved mapping is reused when structure is identical, and the validator reports fields that no longer
   exist in the new contract. Approve, then switch the customer's default.
4. Database schema changes (not the target contract) always go through Alembic: `forma db revision -m …`
   in the PR, `forma db upgrade` before starting the new image (the API refuses to start otherwise).

### Database migration failed mid-way

Postgres runs each migration in a transaction: a failed `forma db upgrade` leaves the database at the
previous revision. Fix the migration, re-run. On SQLite (dev) delete the DB and `make seed`.

## Backup and restore

```bash
# the compose stack's `backup-nightly` service dumps the database every 24 hours and keeps 14 days
forma backup --out /backups --archive            # DB dump + manifest.json + objects/ → forma-backup-<stamp>.tar.gz
forma backup --out /backups --no-objects         # when the bucket is versioned/replicated: manifest only

# restore into an EMPTY database (no `forma db upgrade` first: the dump carries schema + alembic_version)
forma restore /backups/forma-backup-20261028T020000Z.tar.gz
forma db upgrade                                 # only if the running code is newer than the backup's revision
```

`manifest.json` lists every object key with size and sha256 and the schema revision; `forma restore`
reports keys that are in the manifest but missing from the backup and the bucket, so a partial restore is
visible, never silent.

## Retention

```bash
forma retention run                # dry run: prints what would be deleted (policies/retention.yaml)
forma retention run --apply        # deletes; every deletion is an audit row (retention.purge_*)
```

Scheduled: `FORMA_RETENTION_INTERVAL_H=24` (and `FORMA_RETENTION_APPLY=true` to actually delete) runs it
inside the API/worker process. The first pass comes a minute after start, and a pass is skipped when one ran in
the last half interval. Approved/published versions, rollback parents and open rounds are never touched; a round
with no activity for 60 days is flagged on the home page and left alone. System health shows storage used, the
last pass and what it did, and the age of the last backup, amber past a day (ADR-053).

## Who can reach the stack

The web port and MinIO are published on 127.0.0.1, so only the machine running the stack can open them. FORMA runs
on one machine with `FORMA_AUTH=dev`, which has no login, and this is what makes that safe. Set `FORMA_BIND_HOST`
only behind OIDC or a VPN, never with `FORMA_AUTH=dev`.

On one machine a backup shares the machine's fate. Copy the `backups` volume somewhere else from time to time, and
keep the code on a remote.

## Capacity

* One worker process ≈ 1 GB RAM at 100K-row files; `FORMA_WORKERS` ≤ cores − 2.
* SQLite is single-node and locks under concurrent writers; use Postgres with more than one worker node.
* `FORMA_QUEUE_MAX` bounds in-flight jobs; the API answers 429 + `Retry-After` beyond it — clients that
  batch-run hundreds of rounds must honour it.
