# Phase 7 review — production hardening & enterprise scale

Status as of 2026-10-28 (commits `7932e01 … b6a25d8`, one per step). Measurements were taken on a
development machine (Windows 11, 12 cores, 31 GB, Docker Desktop) unless stated otherwise.

## What was done

| Step | Delivered | Decision record |
|---|---|---|
| 1 Parallel execution & durable jobs | `FORMA_JOB_BACKEND=thread\|process\|arq` behind the unchanged `enqueue()`; atomic job claims; stale-job recovery; resumable stage ledger; per-file parallel profiling; `POST /api/rounds/batch-run` + `GET /api/jobs/batch/{id}`; 429 backpressure, 413 upload cap; streaming exports; `forma bench`; engine record builder vectorised; Polars `pack_size`/`unit_normalize` | ADR-010, `docs/PERFORMANCE.md` |
| 2 PostgreSQL & migrations | Alembic (`backend/alembic`, two revisions), `forma db …`, dev `create_all`+stamp, prod refuses on pending migration, JSONB, indexes, UTC datetimes, pagination; verified on real Postgres | ADR-011 |
| 3 Object storage | `Storage` protocol, `LocalStorage` + `S3Storage` (boto3/MinIO), keys in rows (migration converts paths), pre-signed downloads, object-first dataset versions; verified on MinIO | ADR-012 |
| 4 Auth, RBAC, tenancy, security | dev header mode + OIDC (JWKS, PKCE in SPA), roles importer⊂reviewer⊂approver⊂admin via `require_role`, actor recorded from the token, tenant `customers` claim, upload sniffing, safe names, CORS, LLM rate limits, sample caps, **LLM output guard** | ADR-013 |
| 5 Observability | OTel spans (jobs, stages, agent calls, engine runs), Prometheus `/metrics`, JSON logs + request ids into workers, `/health` `/ready`, web health banner, admin **Ops** page | ADR-014 |
| 6 Reliability, retention, DR | retries + circuit breaker (fallback → review, never auto-approve), retention policy + scheduler (audited, dry-run default), `forma backup|restore`, `docs/RUNBOOK.md`, failure-injection tests (killed worker process, storage failure, DB failure, LLM timeout) | ADR-015 |
| 7 Deployment | backend + web Dockerfiles, `docker-compose.prod.yml` (postgres, redis, minio, migrate, api, workers, web, otel/ops profiles), generated `.env.production.example` + drift check, `forma config show`, CI with eval/bench gates; the stack was brought up and smoke-tested end to end | ADR-016 |

## Measured numbers

**Quality gate** (`make eval`, unchanged from the Phase 6 baseline, now enforced by `forma eval --baseline`):
identity recall 1.0 · field accuracy 0.9962 · critical-field accuracy 1.0 · 30/30 variants ≥ 99 % · drift 9/9.

**Tests**: 291 passed, 3 skipped locally (the skipped ones are the real-Postgres and MinIO suites, which
pass when `FORMA_TEST_PG_URL` / `FORMA_TEST_S3_ENDPOINT` point at the compose services — they were run
here — and run in CI). Coverage 90.7 % (gate 80 % local, 85 % CI). ruff + mypy strict clean.

**Benchmark** (`forma bench --files 300 --rows 100000`, 30 M output rows, 3.5 GB of xlsx/csv):

| Backend | Workers | Wall-clock | Rows/s (transform) | Peak RSS | Failed |
|---|---|---|---|---|---|
| process | 8 | **17.0 min** | 46 865 | 7.1 GB | 0 |
| thread | 4 | 55.1 min | 12 695 | 2.4 GB | 0 |

Engine single-stream: ~16K rows/s per core after the record-builder change (−19 % transform time);
`pack_size` 4×, `unit_normalize` 2× faster vectorised; date parsing stays in Python (measured no gain).
Full tables and the profile in `docs/PERFORMANCE.md`, raw reports in `docs/bench/`.

**Production smoke test** (compose: Postgres 16, Redis 7, MinIO, 2 arq workers, nginx): `/ready` ok →
customer → upload (sniffed) → `run` executed by an arq worker → approve as reviewer → transform (173 rows)
→ export → `307` to a pre-signed MinIO URL → 173 lines fetched; importer denied approval (403); Ops page
reports `arq · s3 · postgresql · revision a1b2c3d4e5f6`. Two bugs found and fixed by this test (migrations
dir and relative paths inside an installed image; public pre-sign endpoint).

## Known limits

* **OIDC** is verified with locally generated RSA tokens and the SPA has the PKCE flow, but no real IdP
  (Keycloak/Entra) was available here. Expect to set `FORMA_AUTH_ROLE_CLAIM` (e.g. `realm_access.roles`)
  and the client id; the discovery document must be reachable from both the API and the browser.
* **Rate limiter** is per API process (sliding window in memory). Behind several API replicas the
  effective limit is N× the configured value; put a shared limiter behind the same `SlidingWindowLimiter`
  interface if that matters.
* **Thread backend + SQLite** is for dev and single-user installs; concurrent writers lock. The bench
  with `thread` ran while other work used the machine, so treat its numbers as conservative.
* **Retention** marks purged versions (`status=purged`) and keeps rows; the Data Editor shows them as
  history but cannot open their rows (404 "purged by retention").
* **Backups** include customer data in plain files; the backup volume must be protected like the data
  directory. `pg_dump`/`psql` must be on the PATH of the container that runs `forma backup|restore`
  (the backend image installs `postgresql-client`).
* **arq** requires the API and workers to share Redis **and** the storage backend; the thread/process
  backends cannot span hosts.
* **Windows**: long backup paths use the `\\?\` prefix; `forma bench` with the process backend needs
  `FORMA_WORKERS` ≤ cores−2 to leave room for the API.
* Dependencies were installed at current versions (FastAPI 0.162, SQLAlchemy 2.1, Polars 2.1, pytest 9);
  `uv.lock` is not committed — pin before the first production build.

## Exact next steps

1. **Real IdP**: run `FORMA_AUTH=oidc` against the organisation's Keycloak/Entra tenant; add the redirect
   URI `https://<host>/auth/callback`; map groups to `importer/reviewer/approver/admin`; verify
   `GET /api/me` shows the right `effective_roles`. The API already refuses `FORMA_AUTH=dev` with
   `FORMA_ENV=prod`; a local smoke test of the prod stack without an IdP needs
   `FORMA_AUTH_ALLOW_DEV_IN_PROD=true`, which must never reach a real deployment.
2. **Real files into eval**: drop anonymised customer exports + expected JSON into `data/eval/<customer>/`
   and extend `forma eval` to iterate them (same report shape), then raise `data/eval_baseline.json`.
3. **Real target contract**: replace `target_schema/product.v1.json`, run `GET /api/schemas/product/diff/…`,
   re-run eval, update synonyms where accuracy drops.
4. **Pin dependencies**: `uv lock` in `backend/`, commit `uv.lock`, use `uv sync --frozen` in Dockerfile and CI.
5. **Shared rate limiter + metrics across replicas** when running >1 API replica (Redis-backed window; the
   DB-derived gauges already agree across replicas).
6. **Alerting** on `forma_jobs_queue{status="failed"}`, `forma_review_oldest_age_seconds`,
   `forma_llm_breaker_open`, `/ready` != 200 (Prometheus rules are not part of this repo).
7. **Engine**: column-wise schema pre-validation and `pl.struct` record emission (expected −15 % / −10 %
   transform time, see PERFORMANCE.md "next levers").

## สรุป (ภาษาไทย)

* งาน Phase 7 ครบทั้ง 7 ข้อ: worker แบบ thread/process/arq, งานรันซ้ำได้ไม่ซ้ำผล, Postgres + Alembic,
  S3/MinIO, OIDC + สิทธิ์ 4 ระดับ, tracing/metrics/logs, retention + backup/restore, compose + CI
* ตัวเลขจริงจากเครื่องนี้: 300 ไฟล์ × 100,000 แถว (30 ล้านแถว) ใช้ 17 นาทีด้วย process 8 ตัว,
  55 นาทีด้วย thread 4 ตัว, ไม่มีงานล้มเหลว
* ความแม่นยำ eval เท่าเดิม (field 0.9962, critical 1.0) และมี gate ใน CI ไม่ให้ตกลง
* เทส 291 ตัวผ่าน, coverage 90.7 %, ruff/mypy สะอาด; เทสจำลองความล้มเหลว (worker ตาย, storage พัง, DB หลุด,
  LLM timeout) ยืนยันว่าไม่มี dataset ครึ่งๆ กลางๆ โผล่ให้เห็น
* เปิด stack production ด้วย compose แล้วทดสอบจริงครบ flow: upload → worker → approve → transform → export →
  pre-signed download; เจอบั๊ก 2 จุดจากการรันจริงและแก้แล้ว
* ถ้า LLM ล่ม ระบบใช้ heuristic แทนและ policy ส่งเข้า review เสมอ ไม่ auto-approve
* ยังไม่ได้ทดสอบ OIDC กับ IdP จริง (ทดสอบด้วย token RSA ที่สร้างเอง) — ขั้นตอนถัดไปข้อ 1
* API ไม่ยอมเริ่มทำงานถ้า `FORMA_ENV=prod` แต่ยังใช้ `FORMA_AUTH=dev` (ทุกคำขอจะเป็น admin โดยไม่ต้องล็อกอิน);
  ถ้าจะ smoke test stack production ในเครื่องโดยไม่มี IdP ให้ตั้ง `FORMA_AUTH_ALLOW_DEV_IN_PROD=true` ชั่วคราวเท่านั้น
* ควร pin dependency (`uv lock`) ก่อน build production ครั้งแรก
* เอกสาร: ADR-010…016, PERFORMANCE.md, RUNBOOK.md, ROADMAP_STATUS.md อัปเดตแล้ว
* Dev loop เดิมยังใช้ได้เหมือนเดิม: SQLite + local disk + ไม่ต้องล็อกอิน ไม่ต้องมี service เพิ่ม
