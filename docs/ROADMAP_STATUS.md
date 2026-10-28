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
| 7 Production | scale, reliability, security, observability | **partial** — see below | |

## Phase 7 — what remains before 300 customers in production

* **Parallelism** — run files/rounds on several worker processes (replace the thread pool behind `enqueue`).
  The engine is already per-file; benchmark a full 300-file round and set the SLA from measurement.
* **Postgres + migrations** — `FORMA_DATABASE_URL` switches the DB; add Alembic before the first breaking change.
* **Object storage** — `FileStore` and dataset/export paths → S3/MinIO (compose service already present).
* **Auth & RBAC** — the API takes an `actor` string today; put it behind SSO (OIDC) and roles
  (importer / reviewer / approver / admin). Tenant isolation is by customer id in every query.
* **Observability** — structured logs exist; add OpenTelemetry tracing on jobs and agent calls, dashboards
  for review queue age, automation rate, cost per import.
* **Retention & DR** — lifecycle rules for stored files/datasets, backup of the DB + data dir.
* **Prompt-injection defence** — cell contents are already treated as data in prompts; add output
  scanning on agent results and cap sample sizes sent to models.
* **Eval on real files** — add the first real customer files to the eval index (see `docs/EVALUATION.md`).
