# Testing strategy and quality gates

> **สรุปภาษาไทย** — ระบบมีด่านคุณภาพ 4 ชั้น: lint/type (ruff, mypy strict), unit + API tests (~500 ตัว,
> coverage ≥ 80% local / 85% CI), eval ความแม่นบน corpus สังเคราะห์ที่ห้ามตก, และ drift guards (migration,
> config example) + build ทั้งสอง image ใน CI ทุก feature ใหม่ต้องมี test ที่ล้มก่อนแก้และผ่านหลังแก้

## 1. Layers

| Layer | Tool | Gate | Where |
|---|---|---|---|
| Static | ruff (lint + format), mypy strict, oxlint, tsc strict | zero errors | `make lint typecheck`, CI |
| Unit | pytest | pass | `backend/tests/test_thai.py`, `test_engine.py`, `test_dsl.py`, `test_fingerprint.py`, `test_heuristics*.py`, `test_policy.py`, `test_verdict.py`, `test_healing.py`, `test_anomalies.py`, … |
| API / integration | pytest + FastAPI `TestClient` with `sync=true` jobs on an isolated SQLite + data dir (`isolated_env`) | pass, coverage ≥ 80 % locally, ≥ 85 % in CI | `test_api.py`, `test_batches.py`, `test_workspace.py`, `test_learning.py`, `test_calibration.py`, `test_foresight.py`, `test_schema_gate.py`, `test_sandbox.py`, … |
| Infrastructure | real Postgres and MinIO in CI (`FORMA_TEST_PG_URL`, `FORMA_TEST_S3_ENDPOINT`) | pass | `test_postgres.py`, `test_minio.py` |
| Migrations | upgrade/downgrade from scratch; models vs Alembic agree | pass | `test_migrations.py` |
| Failure injection | dead worker, storage error, DB error, LLM timeout, breaker | nothing partial visible; fallback routes to review | `test_failure_injection.py`, `test_job_recovery.py` |
| Accuracy | `forma eval` on the synthetic corpus vs `data/eval_baseline.json` | identity 1.00, field ≥ 0.996, critical 1.00, drift 9/9, no failed variants | `make eval-gate`, CI |
| Input fidelity | adversarial OOXML/CSV fixtures | pass | `test_ingestion_readiness.py`, `test_contract_readiness.py` |
| Configuration drift | `.env.production.example` regenerated and compared | identical | CI |
| Performance | `forma bench --files 6 --rows 2000` in CI; full 300 × 100K locally | reported; the full run is documented in PERFORMANCE.md | CI, `make bench` |
| Build | both Docker images, compose config | build | CI |
| Frontend | tsc, oxlint, vite build, `npm test` (navigation rules in `frontend/tests/`) | zero errors (warnings tracked) | `make check`, CI |

## 2. Conventions

* Tests live in `backend/tests/`; `conftest.py` isolates the database and data directory per test via
  environment variables and clears every `lru_cache` (`isolated_env`). Reuse it.
* API tests build workbooks with openpyxl in `tmp_path` (`tests/test_api.py::make_workbook`) and run jobs with
  `sync=true`; never depend on wall-clock ordering.
* Thai parsing is table-driven (`test_thai.py`); add a row when touching `engine/thai.py`.
* A fake provider (`_FakeProvider` with `complete_json`) exercises the real LLM branches without network;
  the guard and the fallback are tested with injected output.
* Every bug fix starts with a test that reproduces it (the regression test must fail without the fix).
* Every new policy rule, verdict rule, learning rule or proposal has a unit test from fabricated reports and
  at least one end-to-end API test.
* Coverage is a floor, not a target: untested branches in services are not accepted.

## 3. What the evaluation harness measures

| Mode | Command | Measures | Gate |
|---|---|---|---|
| Heuristic | `forma eval --corpus ../data/synthetic --baseline ../data/eval_baseline.json` | mapper + engine vs ground truth on 30 synthetic variants and 3 sequences | CI, must not drop |
| AI-refined | `forma eval --llm` | the draft refined by the configured provider; fallbacks counted | report (needs a key) |
| Correction replay | `forma eval --replay` | later rounds of a sequence scored without and with the alias learned from round 0 | report |
| Real files | `forma eval --corpus ../data/real --index` | the same metrics on reviewed real exports | pending files |

Details: [EVALUATION.md](EVALUATION.md), [adr/041](adr/041-evaluation-on-real-files-and-with-ai.md).

## 4. Manual / exploratory checks before a release

1. Seeded demo: Home lists C900–C902 sequences; C902 round 4 shows the wrong-customer verdict; a renamed-layout
   round shows the one-click accept; accepting it approves and audits.
2. Bulk upload of 5 mixed files: known customers publish, an unknown layout waits, assign continues it, the
   zip contains one JSON per file.
3. Review page: answer a VAT question, preview the impact, save, see the new version re-evaluated.
4. Data Editor: fix an invalid record, see the gate open; roll back; export.
5. Settings: paste a key, test it, add a backup, break the primary, see the backup take over; edit the policy;
   apply a calibration proposal; promote a learned alias.
6. Assistant: ask about a file, get cited evidence; ask for a work-round comparison chart; ask to accept a
   verdict and confirm the card; attach a picture and a file; have code make a chart and a document, pick part of it
   and change it, bring back the old version; tell it something to remember, then find it in a new chat.
7. A round's life: upload and watch the navy card across pages; cancel a round and reopen it; move one to another
   customer; replace one with a corrected file and check the zip; park a question and bring it back.
8. System health: storage used, last retention pass and last backup show real values. After changing Postgres or
   the image, restore a backup into an empty stack (RUNBOOK, ADR-054).
9. Both themes, 390 px width, Thai and English.

## 5. Definition of done (per change)

`make check` green · `make eval-gate` green · tests added for new behaviour · docs/ADR updated in the same
commit · `.env.production.example` regenerated when a setting changed · migration shipped when a model
changed · frontend `tsc` + `oxlint` + `npm test` + `build` green · conventional commit message.
