# FORMA — AI-native data transformation and operations platform

FORMA turns the Excel/CSV exports that ~300 customers send from their own ERPs every two weeks into one
standard, validated JSON contract, and lets an operations team review, correct and publish the result
without writing code. It reads any layout (Thai or English, multi-sheet, CSV in any encoding), decides what
each file means, states a verdict with evidence, learns from every correction, and keeps every step
auditable and reversible.

> **สรุปภาษาไทย** — FORMA รับไฟล์ Excel/CSV จาก ERP ของลูกค้าได้ทุกรูปแบบ แปลงเป็น JSON ตาม contract ที่กำหนด
> ตัดสินให้ว่าไฟล์นี้คืออะไร ควรทำอะไรต่อ พร้อมหลักฐาน และเรียนรู้จากการแก้ของทีม ops ทุกครั้ง
> ทุกการตัดสินใจผ่าน policy ที่ตั้งค่าได้ ไม่มีโค้ดที่ AI สร้างแล้วรันเอง ทุกอย่างย้อนกลับได้และมี audit log
> เริ่มใช้งานที่ [Quick start](#quick-start) เอกสารสำหรับทีมอยู่ที่ [docs/TEAM_LEAD_HANDBOOK.md](docs/TEAM_LEAD_HANDBOOK.md)

---

## Contents

1. [What FORMA does](#what-forma-does)
2. [How a file flows through](#how-a-file-flows-through)
3. [What makes it hold up](#what-makes-it-hold-up)
4. [Quick start](#quick-start)
5. [Using the web app](#using-the-web-app)
6. [Configuration](#configuration)
7. [Production deployment](#production-deployment)
8. [Command line](#command-line)
9. [Repository layout](#repository-layout)
10. [Quality gates](#quality-gates)
11. [Documentation map](#documentation-map)
12. [Status and known limits](#status-and-known-limits)

---

## What FORMA does

| Problem in the operations team | What FORMA does about it |
|---|---|
| 300 customers, 300 layouts, Thai and English headers, several sheets per file | Profiles every sheet, detects tables (titles, offsets, stacked and side-by-side tables, totals rows), classifies columns, and maps them to the target contract by meaning |
| A column renamed by the customer breaks the import | Fingerprints every file; a returning layout reuses the approved mapping, a drifted layout is re-proposed and checked field by field on the data, a wrong file is blocked |
| Thai business reality: Buddhist-era dates, ฿ and comma numbers, VAT 7 % in or out, ชิ้น/แพ็ค/ลัง | Explicit, unit-tested transformation operations; nothing is implicit locale behaviour |
| Reviewers drown in evidence | Every file gets a **verdict**: one sentence, a confidence word, the one to three decisions only a person can make, and a one-click accept when the evidence is clear |
| The same correction is made every round | Every approval is diffed against FORMA's proposal; the correction is learned for that customer and suggested for everyone when several customers teach the same thing |
| Policy thresholds are set once and never questioned | Every decision gets an outcome; precision per rule is reported and the policy proposes its own reversible changes for an admin to apply |
| Nobody notices a customer who did not send | Expected deliveries from each customer's cadence; a watchlist of customers likely to need a check |
| "Did the AI change my data?" | No generated code touches your data. The AI emits a declarative mapping document that a deterministic engine runs. The policy decides, a person confirms, the audit log records. The assistant can run code to answer a question, but only on copies, in a sandbox with no network, and the result goes to the chat |

## How a file flows through

```
 upload (one round, or many files at once)
   │
   ▼
 profile ──▶ fingerprint & drift ──▶ mapping (memory → approved library → heuristic → AI) ──▶ validate + simulate
   │                                                                                          │
   │                                           sandbox vs the approved mapping  ◀─────────────┘
   │                                           field-by-field data check
   ▼                                                      │
 policy: auto_approve | review | block  ◀─────────────────┘
   │
   ├─ verdict (one conclusion, evidence, decisions, one-click accept)
   ├─ on approval: learning (what did this approval teach?)
   │
   ▼
 transform (Polars engine + Thai ops + persistent overrides + schema validation) ──▶ dataset version
   │
   ├─ change report & anomalies vs last round ──▶ verdict (data stage)
   ├─ edit (spreadsheet, bulk, copilot) → new version, impact preview, lineage to the Excel cell
   ▼
 approve ──▶ export JSON / JSONL / CSV (atomic, sha256)

 afterwards: calibration reads what people did next (was the decision right?)
```

Each stage is described in detail in [docs/SYSTEM_FLOWS.md](docs/SYSTEM_FLOWS.md); the components behind
them in [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md).

## What makes it hold up

* **The target contract is configuration.** A JSON Schema with an `x-forma` extension (semantics,
  Thai/English synonyms, risk levels, identity keys) is the contract. Register the real downstream schema in
  **Settings → Target formats**; mapping, validation, AI prompts and exports follow it. Code never names a
  target field: it finds fields by meaning through the schema registry (ADR-001, ADR-017).
* **AI proposes, the engine executes.** Models only emit a declarative Mapping DSL of whitelisted operations.
  No generated code runs. The same inputs and the same mapping always produce the same output (ADR-002).
* **Policy decides, not the model.** `policies/approval.yaml` turns confidence, drift, sandbox deltas, data
  checks and anomalies into `auto_approve | review | block` with reasons. A block is an explicit override,
  never a casual approval (ADR-008).
* **Nothing is overwritten.** Source files are immutable, mappings and datasets are versioned, every edit is a
  change set with an impact preview, rollback is a new version (ADR-005).
* **Nothing partial is visible.** Dataset versions are written object-first, row last; LLM output passes a
  guard; a model failure routes to review, never to auto-approval (ADR-013, ADR-015).
* **It explains itself.** Every field mapping carries evidence; every value traces to its Excel cell with the
  cause of a change; every decision carries the rules that fired; every verdict lists what it folded away.
* **It gets better with use.** Corrections become column aliases and conventions; outcomes calibrate the
  policy; evaluation modes measure the heuristic, the AI and the learning loop side by side.
* **Measured, not promised.** A 30-variant synthetic ERP corpus with ground truth gates every change
  (identity 1.00, field accuracy 0.996, critical 1.00, drift 9/9 — no model involved); 500+ tests, 85 %
  coverage in CI; a 300-file night benchmarked at 17 minutes on one 12-core node.

---

## Quick start

Requirements: Python 3.12 and [`uv`](https://docs.astral.sh/uv/), Node 20+, no database (SQLite by default),
no AI key (the deterministic mapper runs offline).

```powershell
# Windows PowerShell
.\scripts\dev.ps1 install      # backend venv + frontend deps
.\scripts\dev.ps1 synth        # generate the synthetic ERP corpus (30 variants, 3 returning-customer sequences)
.\scripts\dev.ps1 seed         # load it as demo customers and run the pipeline
.\scripts\dev.ps1 api          # http://127.0.0.1:8000   (API docs: /api/docs)
.\scripts\dev.ps1 web          # http://localhost:5173   (new terminal)
```

```bash
# macOS / Linux / WSL
make install && make synth && make seed
make api      # terminal 1
make web      # terminal 2
```

Open http://localhost:5173. Home lists what needs a person, with FORMA's conclusion on each row. The demo
includes three returning-customer sequences (C900–C902) that exercise memory → drift → wrong file.

### Enable the AI agents (optional)

Everything works offline. To add AI refinement, drift adaptation, diagnosis, the copilot and the assistant,
open **Settings → AI**, pick the provider, paste the key (stored encrypted, applied without a restart, *Test
connection* checks it), choose a model, and add backup keys if you want fail-over. Or set it server-wide:

```
FORMA_LLM_PROVIDER=openai            # openai | openai_compatible | anthropic
FORMA_LLM_MODEL=gpt-4.1-mini
FORMA_LLM_API_KEY=sk-...
# FORMA_LLM_BASE_URL=https://your-gateway/v1   # any /chat/completions-compatible gateway
```

Token usage and estimated cost are recorded per call in the `llm_calls` ledger. Home shows the total, Ops the
recent days, and Prometheus the cost per round.

### Use the real downstream format

**Settings → Target formats → New format**: paste or load the JSON Schema the downstream team gave you, tag
each field with an `x-forma.semantic` from the list beside the editor (`sku`, `price.amount`, `stock.qty`,
`promo.type`, …), add Thai/English synonyms for header matching, *Check* it (validity, mapping findings,
diff to the previous version), then *Register*. Make it the default or assign it per customer. Schemas the
mapping catalogue cannot enumerate (references, conditionals, tuple schemas) are refused with a reason
(ADR-043).

---

## Using the web app

| Page | What you do there |
|---|---|
| **Home** | Upload files; see what needs you with FORMA's conclusion per file; expected deliveries and the watchlist; work-round dashboard; statistics |
| **Upload files** | Drop many files at once. Each is matched to its customer by layout (and the customer code in the file name), becomes its own round, and is published automatically when every check passes. Unknown layouts wait for you to pick a customer. One JSON per file, all in one zip |
| **Imports** | Work rounds (named, dated, reusable) and the history of every file with filters that survive the browser's Back button |
| **Round** | The case: the verdict card with its one-click accept, the three stages, files, mapping versions, datasets, the assistant in context |
| **Review** | Questions and issues: the verdict first, then each uncertain field as a question answered from evidence traced to the source cell; preview the impact before saving |
| **Mapping Studio** | Field view (drag a column onto a field), *What changed* vs the approved mapping with the data check, *Compare* (sandbox), *Relationships* (how the sheets connect, measured on the data), DSL on demand |
| **Data Editor** | Spreadsheet editing (paste, ranges, bulk on picked rows), copilot in Thai or English, lineage to the Excel cell, changes and anomalies vs last round, records that break the format, versions and rollback |
| **Customers** | Every customer, its approved formats, overrides, next expected delivery |
| **Settings** | AI connection and backup keys; target formats; approval policy with the calibration report and proposals; what FORMA learned; DSL reference; audit log; system |
| **Assistant** | Ask about files, customers, features, with cited evidence; server-computed reports (tables and charts); Python in an isolated sandbox for analysis, charts, flows and Word, Excel, PowerPoint or PDF files to download; images, documents, slides, emails, archives and data files in the chat; what it makes opens beside the chat with its versions kept; memory across chats that you can read and delete, and search of earlier chats; clarification questions; a confirmable "accept the verdict" action |
| **System health** (admin) | Queue, workers, failed jobs with retry, breaker state, spend, slowest rounds |

Every feature is findable with **Ctrl K** or the **Feature guide**, in English or Thai.

---

## Configuration

Everything that switches production behaviour on is an environment variable; the full list with a description
of each is generated from the settings model and drift-checked in CI: [`.env.production.example`](.env.production.example).

| Area | Variables | Dev default |
|---|---|---|
| Database | `FORMA_DATABASE_URL`, `FORMA_ALEMBIC_DIR` | SQLite under `data/`; Postgres + Alembic in production (`FORMA_ENV=prod` makes migrations mandatory) |
| Storage | `FORMA_STORAGE`, `FORMA_S3_*` | local disk; S3/MinIO with pre-signed downloads |
| Jobs | `FORMA_JOB_BACKEND` (thread, process, arq), `FORMA_WORKERS`, `FORMA_REDIS_URL`, `FORMA_QUEUE_MAX`, `FORMA_JOB_STALE_S` | threads in-process |
| Auth | `FORMA_AUTH` (dev, oidc), `FORMA_AUTH_*` (issuer, audience, JWKS, claims) | dev header mode; the API refuses `dev` in production |
| AI | `FORMA_LLM_*` (provider, model, key, retries, breaker, rate limit, sample caps) | mock (offline). The web app's Settings override these, encrypted with `FORMA_SECRET_KEY` |
| Limits | `FORMA_MAX_UPLOAD_MB`, `FORMA_SANDBOX_MAX_ROWS`, `FORMA_CHUNK_ROWS` | sized for 100K-row files |
| Observability | `FORMA_OTEL_ENDPOINT`, `FORMA_LOG_FORMAT`, `FORMA_LOG_LEVEL` | plain logs; Prometheus at `/metrics` always |
| Retention | `FORMA_RETENTION_INTERVAL_H`, `FORMA_RETENTION_APPLY` | dry-run |

Settings edited in the web app (AI, policy, formats, default format, personal preferences) are validated,
audited without secrets and applied to every API and worker process within `FORMA_CONFIG_REFRESH_S` seconds.

---

## Production deployment

```bash
cp .env.production.example .env.production        # every variable explained; fill in the secrets
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build --scale worker=4
open http://localhost/            # web (set FORMA_WEB_PORT to change)
curl http://localhost/ready       # db · storage · queue
```

`FORMA_AUTH=dev` has no login, so anyone who can reach the published ports acts with the default role. Use it only
on a machine other devices cannot reach (R31 in [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md)).

The stack is nginx (SPA + `/api` proxy) → FastAPI (N replicas) → Redis/arq workers (N nodes) → PostgreSQL
(JSONB) + S3/MinIO. On start, API and workers reconcile queued jobs against durable results, recover stale
jobs and resubmit pending ones (ADR-044). Operations are in [docs/RUNBOOK.md](docs/RUNBOOK.md): worker
crash, stuck round, wrong file published, provider outage, schema rollout, backup and restore, retention.

---

## Command line

All commands run from `backend/` through the venv (`.venv/Scripts/forma.exe` on Windows, `.venv/bin/forma`
elsewhere) or via `make <target>` / `.\scripts\dev.ps1 <target>` from the repository root. `dev.ps1` has no `check`
target: on Windows run its `lint` and `test` targets plus `ruff format --check .` from `backend/`.

| Command | What it does |
|---|---|
| `forma serve` / `forma worker` | the API / a job worker (`FORMA_JOB_BACKEND=arq` needs Redis) |
| `forma profile <file>` | sheets, tables, columns, types, fingerprint of one file |
| `forma propose <file> [--llm]` | propose a mapping YAML for a file |
| `forma transform <file> <mapping.yaml> --fmt csv` | run a mapping, write output |
| `forma eval [--baseline …] [--llm] [--replay] [--index]` | accuracy vs ground truth: heuristic (CI gate), AI-refined, correction replay; index a folder of real files |
| `forma bench --files 300 --rows 100000` | end-to-end throughput benchmark |
| `forma db upgrade / revision -m "…" / current` | Alembic migrations |
| `forma retention run [--apply]` | retention policy, dry-run by default |
| `forma backup / forma restore <path>` | DB dump + storage manifest (+ objects) |
| `forma config show / config example [--check]` | effective config (masked) / regenerate or check `.env.production.example` |
| `make check` | ruff + mypy (strict) + pytest with coverage |
| `make eval-gate` | eval and compare with `data/eval_baseline.json` |
| `make reset` / `make seed` | wipe runtime data / rebuild the demo |

---

## Repository layout

```
backend/                  FastAPI + Polars service (Python 3.12, uv, strict mypy)
  src/forma/
    schema_registry/      target JSON Schema + x-forma catalogue (configuration, not code)
    mapping_dsl/          declarative Mapping DSL (Pydantic) + static validator
    ingestion/            immutable file store, sniffing, fastexcel and csv readers, OOXML fidelity checks
    profiling/            table detection, column profiles
    fingerprint/          structural fingerprints, drift classification
    engine/               Thai operations, DSL → Polars compiler, transform engine, vectorised rules
    export/               JSON / JSONL / CSV, atomic publish
    ai/                   heuristic mapper, provider layer (retries, breaker, backup routes), agents, output guard, policy engine
    services/             rounds, mapping lifecycle, sandbox, healing, datasets/edits/lineage, anomalies,
                          batches, publishing, verdict, learning, calibration, foresight, format library,
                          assistant + tools + reports, review questions/evidence/explanations, config
    jobs/                 runner (thread | process | arq), idempotent handlers, startup recovery
    api/                  REST API (/api/docs), serializers, auth dependencies, MCP endpoint
    auth/                 dev header mode, OIDC/JWKS, roles, tenant scoping
    storage/              local and S3 object storage behind one protocol
    core/                 settings, errors, hashing, metrics, telemetry
    synth/                synthetic ERP corpus generator + database seeder
    eval/                 evaluation harness, correction replay
  alembic/                migrations (every model change ships one)
  tests/                  ~500 tests: unit, API, failure injection, Postgres, MinIO, migrations, eval
frontend/                 React 19 + TypeScript + Vite + Tailwind v4; no UI library
  src/pages/              Overview, Upload, Imports, Round, Review, MappingStudio, RelationshipMap, DataEditor,
                          Customers, Settings, Ops, Guide, AuthCallback
  src/components/         Shell, CommandPalette, VerdictCard, ReviewWorkspace, Assistant*, Foresight, …
  src/lib/                api.ts (typed client mirroring api/serialize.py), features.ts, preferences, auth
target_schema/            product.v1.json — a DRAFT contract; register the real one in Settings
policies/                 approval.yaml (auto-approve / review / block, sandbox, anomaly, publish, verdict)
data/                     runtime data (git-ignored) · synthetic/ corpus with ground truth (committed) · real/ (local only)
docs/                     architecture, flows, user stories, handbook, DSL, data model, Thai rules, evaluation,
                          performance, runbook, ADRs 001–053
```

---

## Quality gates

Before any commit: `make check` (ruff, ruff format, mypy strict, pytest with 80 % coverage locally / 85 % in
CI) and `make eval-gate` (accuracy on the synthetic corpus must not drop). CI additionally runs the Postgres
and MinIO integration tests, a migration drift guard (models vs Alembic), a configuration drift guard
(`.env.production.example`), a short benchmark, the frontend lint and build, both Docker images and the
compose config. Details: [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md).

---

## Documentation map

| Audience | Start here |
|---|---|
| Team lead / product | [docs/TEAM_LEAD_HANDBOOK.md](docs/TEAM_LEAD_HANDBOOK.md) — index of everything below, KPIs, risks, release process |
| Everyone | [docs/GLOSSARY.md](docs/GLOSSARY.md) — the vocabulary (round, mapping version, drift, sandbox, verdict, …) |
| Architects / engineers | [docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) — components, planes, deployment, data, security, scaling |
| Analysts / QA | [docs/SYSTEM_FLOWS.md](docs/SYSTEM_FLOWS.md) — context, use cases, process flows, sequences, state machines |
| Product / QA | [docs/USER_STORIES.md](docs/USER_STORIES.md) — how the user works through a round, then every story and edge case, with the technique used at each step and why |
| Engineers | [docs/API_OVERVIEW.md](docs/API_OVERVIEW.md) · [docs/MAPPING_DSL.md](docs/MAPPING_DSL.md) · [docs/DATA_MODEL.md](docs/DATA_MODEL.md) · [docs/THAI_CONTEXT_RULES.md](docs/THAI_CONTEXT_RULES.md) |
| Security / compliance | [docs/RBAC_AND_SECURITY.md](docs/RBAC_AND_SECURITY.md) |
| QA | [docs/TESTING_STRATEGY.md](docs/TESTING_STRATEGY.md) · [docs/EVALUATION.md](docs/EVALUATION.md) |
| Operations | [docs/RUNBOOK.md](docs/RUNBOOK.md) · [docs/PERFORMANCE.md](docs/PERFORMANCE.md) |
| New team members | [docs/ONBOARDING.md](docs/ONBOARDING.md) |
| Decisions | [docs/adr/](docs/adr/) — 46 architecture decision records, binding unless superseded |
| Status | [docs/ROADMAP_STATUS.md](docs/ROADMAP_STATUS.md) · [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md) · [docs/KPIS_AND_METRICS.md](docs/KPIS_AND_METRICS.md) |

---

## Status and known limits

Built and verified on the synthetic corpus and a local production-like stack: ingestion, profiling, mapping
(heuristic, AI, memory, approved-format library), sandbox, policy, verdict, transform, editing, lineage,
anomalies, bulk upload with automatic publishing, learning from corrections, policy calibration, proactive
expectations, the assistant with grounded reports and confirmable actions, workers, Postgres, S3, OIDC,
observability, retention, backup and restore.

What is not yet established — and is said so wherever a number appears:

* **Accuracy on real customer files.** Every figure comes from the synthetic corpus. The harness for real
  files exists (`data/real/`, `forma eval --index|--llm|--replay`); the files and a baseline do not.
* **The real downstream contract.** `target_schema/product.v1.json` is a draft example.
* **OIDC against a real identity provider**, multi-host arq with pgbouncer, and a provider-enabled evaluation
  baseline in CI.
* **Excel semantics FORMA does not interpret:** formulas are read as cached values, never recalculated;
  macros, images, comments, colours, pivots and external connections are not business rules (ADR-043).

See [docs/RISK_REGISTER.md](docs/RISK_REGISTER.md) for the full list with owners and mitigations.
