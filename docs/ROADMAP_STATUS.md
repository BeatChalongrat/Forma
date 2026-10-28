# Roadmap status (blueprint phases → what exists)

| Phase | Blueprint goal | Status | Where |
|---|---|---|---|
| 0 Foundation | target schema, DSL, ground truth, architecture | **done** | `target_schema/`, `mapping_dsl/`, `synth/`, `docs/` |
| 1 Core engine | multi-sheet ingest, profiler, joins, transform, export | **done** | `ingestion/`, `profiling/`, `engine/`, `export/` |
| 2 AI mapping | agents, confidence/evidence, validation, risk policy | **done** (LLM optional; heuristic floor ≥99% on corpus) | `ai/` |
| 3 Web app & review | dashboard, import workspace, Mapping Studio, review queue, bulk approval | **done** | `frontend/` |
| 4 Editor & versioning | spreadsheet editor, one-time/persistent/mapping edits, impact preview, versions, rollback | **done** | `services/datasets.py`, `DataEditor.tsx` |
| 5 Self-healing | drift detection, mapping memory, adaptation agent, feedback | **done** (adaptation needs an LLM key) | `fingerprint/`, `services/mapping.py` |
| 6 Advanced | NL data editing (copilot), root-cause diagnosis, change intelligence, lineage | **done** | `agents.py`, `routes.py` |
| 7 Production | scale, reliability, security, observability, deployment | **done** — see below | ADR-010 … ADR-016 |
| 7+ Operability | any target format without code (semantic mapping, exact export shape), settings in the web app (AI key, policy, formats, customers) | **done** | ADR-017 |

## Phase 7 — production hardening (done)

| Item | Status | Where / evidence |
|---|---|---|
| Parallel execution: thread / process / arq (Redis) job backends behind `enqueue()` | done | `jobs/runner.py`, `forma worker`, ADR-010 |
| Durable, idempotent, resumable jobs; stale-job recovery; retry from UI | done | `jobs/handlers.py` stages, `recover_stale`, `POST /api/jobs/{id}/retry`, `tests/test_jobs.py` |
| Per-file parallel profiling; `POST /api/rounds/batch-run` + batch progress | done | `services/rounds.py`, `GET /api/jobs/batch/{id}` |
| Backpressure (429 + Retry-After), upload size cap (413) | done | `FORMA_QUEUE_MAX`, `FORMA_MAX_UPLOAD_MB` |
| 300-file × 100K-row benchmark, numbers for thread and process backends | done | `forma bench`, `docs/PERFORMANCE.md`, `docs/bench/` |
| Engine hot path: vectorised record builder + Polars unit rules | done (dates kept in Python, measured) | `engine/transform.py`, `engine/thai_vec.py` |
| Streaming exports (JSON/JSONL/CSV) | done | `export/exporter.py` |
| Postgres + Alembic; JSONB; indexes; UTC datetimes; pagination | done (verified on real Postgres) | `backend/alembic/`, `forma db`, ADR-011, `tests/test_postgres.py` |
| Object storage protocol: local / S3 / MinIO; keys in rows; pre-signed downloads | done (verified on MinIO) | `forma/storage/`, ADR-012, `tests/test_minio.py` |
| Auth: dev header mode + OIDC (PKCE in the SPA); RBAC importer/reviewer/approver/admin; tenant scoping | done | `forma/auth/`, ADR-013, `tests/test_auth.py` |
| Security pass: upload sniffing, safe names, CORS, LLM rate limits, sample caps, **LLM output guard** | done | `ingestion/sniff.py`, `ai/guard.py`, `tests/test_guard_sniff.py` |
| OpenTelemetry traces, Prometheus `/metrics`, JSON logs, request ids, `/health` + `/ready`, Ops page | done | `core/telemetry.py`, `core/metrics.py`, `pages/Ops.tsx`, ADR-014 |
| Retries + circuit breaker (fallback never auto-approves), atomic dataset versions | done | `ai/breaker.py`, `services/datasets.py::_new_version`, ADR-015 |
| Retention policy (dry-run default, audited, scheduled) | done | `policies/retention.yaml`, `forma retention run` |
| Backup / restore + runbook (RPO/RTO, procedures) | done | `forma backup|restore`, `docs/RUNBOOK.md` |
| Failure-injection tests (dead worker, storage, DB, LLM timeout) | done | `tests/test_failure_injection.py` |
| Production compose, Dockerfiles, generated `.env.production.example`, CI with eval/bench gates | done (stack smoke-tested) | `docker-compose.prod.yml`, `.github/workflows/ci.yml`, ADR-016 |

## What remains (next phases)

* **Eval on real customer files** — the corpus is synthetic; add the first real ERP exports (anonymised) to
  `data/eval/` and extend `forma eval` to them before tuning synonyms further (`docs/EVALUATION.md`).
* **Real target contract** — `target_schema/product.v1.json` is still the draft; swap in the downstream
  schema and run the schema diff + a full re-eval.
* **OIDC against a real IdP** — the token path is unit-tested with RSA keys and the SPA has the PKCE flow;
  it has not been exercised against Keycloak/Entra yet (`docs/PHASE7_HANDOFF.md`).
* **Multi-node arq in anger** — the compose stack runs two workers on one host; a multi-host run needs a
  shared S3 (not MinIO-on-the-same-box) and Postgres connection pooling (pgbouncer) sized to workers.
* **Engine next levers** — schema validation per column, nested structs from Polars (see PERFORMANCE.md).
