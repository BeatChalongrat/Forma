# FORMA — AI-Native Data Transformation & Operations Platform

FORMA turns the Excel/CSV exports that ~300 customers send from their own ERPs every two weeks into one
standard, validated JSON contract — and lets an operations team review, correct and publish the result
without writing code.

```
 customer files (xlsx / csv, any layout)
        │
        ▼
 ┌───────────────┐   ┌─────────────────────┐   ┌──────────────────┐   ┌──────────────┐
 │  Profile      │──▶│  Mapping intelligence│──▶│  Policy gate     │──▶│  Transform   │──▶ JSON / JSONL / CSV
 │  sheets,      │   │  memory → heuristic  │   │  auto-approve    │   │  deterministic│
 │  tables,      │   │  → AI agents         │   │  review / block  │   │  Polars engine│
 │  fingerprint  │   │  (declarative DSL)   │   │                  │   │  + overrides  │
 └───────────────┘   └─────────────────────┘   └──────────────────┘   └──────────────┘
                              ▲                         │
                              └── Mapping Studio · Review Center · Data Editor (humans) ◀┘
```

**What makes it hold up in production**

* **The target contract is configuration.** `target_schema/product.v1.json` is a JSON Schema with an `x-forma`
  extension (semantics, Thai/English synonyms, risk levels, identity keys). Swap in the real downstream
  contract when it arrives — mapping validation, the UI, AI prompts and exports all read from it.
* **AI proposes, the engine executes.** LLMs only ever emit a declarative Mapping DSL (whitelisted ops).
  No generated code runs. The same inputs always produce the same output.
* **Thai business reality is first-class.** Buddhist-era dates, Thai month names, Thai digits, ฿/comma
  numbers, VAT 7% inclusive vs exclusive, ชิ้น/แพ็ค/ลัง with pack sizes, TIS-620/cp874 CSVs.
* **It learns from every correction.** Approve a mapping you corrected and FORMA keeps the lesson — the
  column alias or the confirmed constant — for that customer's next file, with evidence saying where it came
  from; an alias several customers taught becomes everyone's once an admin promotes it. Corrections per
  approval, by month, are on the Settings page (ADR-038).
* **The policy learns where it is too strict.** Every decision gets an outcome; Settings › Approval policy
  shows how often FORMA was right per rule and proposes the threshold changes the evidence supports — an
  admin applies them, audited, and the next report shows whether they were right (ADR-037).
* **Memory + drift detection from day one.** Every file is fingerprinted. A returning customer with an
  unchanged format is approved automatically; a renamed column goes to review with the diff; a file that
  matches another customer's format is blocked as a probable wrong file.
* **Nothing is overwritten.** Source files are immutable, mappings and datasets are versioned, every edit is
  a change set with an impact preview, and rollback is a new version — never history rewriting.
* **Measured, not promised.** A 30-variant synthetic ERP corpus (8 ERP archetypes × 16 perturbations, with
  ground truth) drives `make eval`: the deterministic mapper alone reaches ≥99% field accuracy and 100%
  critical-field accuracy on it, offline, before any LLM is involved.

---

## Quick start (Windows PowerShell, macOS, Linux)

Requirements: Python 3.12 and [`uv`](https://docs.astral.sh/uv/) (`pip install uv` or `winget install astral-sh.uv`), Node 20+, no database needed (SQLite by default).

```powershell
# Windows
.\scripts\dev.ps1 install      # backend venv + frontend deps
.\scripts\dev.ps1 synth        # generate the synthetic ERP corpus (30 variants)
.\scripts\dev.ps1 seed         # load it as demo customers and run the pipeline
.\scripts\dev.ps1 api          # http://127.0.0.1:8000  (API docs: /api/docs)
.\scripts\dev.ps1 web          # http://localhost:5173  (new terminal)
```

```bash
# macOS / Linux / WSL
make install && make synth && make seed
make api      # terminal 1
make web      # terminal 2
```

Then open http://localhost:5173 — Home lists what needs a person (grouped by the next step), the Imports list
shows the demo rounds (including the returning-customer sequences C900–C902 that exercise memory → drift → wrong file),
and **Upload files** takes any number of files at once: each is matched to its customer by format and comes
out as its own JSON, published automatically when every check passes (ADR-027). Every round page shows its five steps
(files → mapping → create data → check & approve → export) with the next action; **Feature guide** and
**Ctrl K** find any feature by name, in English or Thai. **Review** opens with FORMA's verdict — one sentence, a confidence, the decisions only a person can make, and a
one-click accept when the evidence is clear (ADR-036) — and turns each uncertain field into a question answered from evidence traced to the source cell (ADR-035),
and the **Assistant** answers questions about files, customers and features from live, read-only tools — also exposed to
external clients at `POST /api/mcp` (ADR-029); conversations are kept in the database (ADR-030). The UI follows the aCommerce brand (Montserrat,
navy/blue/lime), bundled so it works offline.

### Enable the AI agents (optional)

Everything works offline with the deterministic mapper. To add LLM refinement, diagnosis, self-healing
adaptation and the natural-language copilot, open **Settings → AI**, pick the provider and paste the key
(stored encrypted, applied without a restart, *Test connection* checks it). Or set it for the whole server
in `backend/.env`:

```
FORMA_LLM_PROVIDER=openai            # openai | openai_compatible | anthropic
FORMA_LLM_MODEL=gpt-4.1-mini
FORMA_LLM_API_KEY=sk-...
# FORMA_LLM_BASE_URL=https://your-gateway/v1   # any /chat/completions-compatible gateway
```

Token usage and cost per round are recorded in the `llm_calls` ledger and shown on Home.

### Use the real downstream format

**Settings → Target formats → New format**: paste (or load) the JSON Schema the downstream team gave you,
tag each field with an `x-forma.semantic` from the list shown beside the editor (`sku`, `price.amount`,
`stock.qty`, `promo.type` …) and Thai/English synonyms, *Check* it (validity, mapping findings, diff to the
previous version), then *Register*. Make it the default, or assign it per customer (**Customers → Edit**).
Mapping, validation, AI prompts and exports follow it; exports carry exactly its fields (ADR-017).
The approval policy (**Settings → Approval policy**) and customer details are edited the same way —
validated, audited, no database edits.

### Run it in production mode (Postgres · Redis workers · MinIO · nginx)

```bash
cp .env.production.example .env.production        # every variable explained; fill in the secrets
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build --scale worker=4
open http://localhost/            # web (set FORMA_WEB_PORT to change)
curl http://localhost/ready       # db · storage · queue
```

What switches on in production is configuration, not code: `FORMA_ENV=prod` (migrations mandatory),
`FORMA_DATABASE_URL` (Postgres, JSONB), `FORMA_JOB_BACKEND=arq` + `FORMA_REDIS_URL` (workers on any
number of nodes), `FORMA_STORAGE=s3` (S3/MinIO, pre-signed downloads), `FORMA_AUTH=oidc` (bearer tokens,
PKCE login in the web app; roles importer/reviewer/approver/admin — required: the API refuses to start on
`FORMA_AUTH=dev` when `FORMA_ENV=prod`), `FORMA_OTEL_ENDPOINT` (traces),
`FORMA_LOG_FORMAT=json`. `forma config show` prints the effective configuration with secrets masked.
Operations: `docs/RUNBOOK.md` (worker crash, stuck round, wrong file published, provider outage, schema
rollout, backup/restore), the admin **Ops** page, `/metrics` for Prometheus.

### Useful commands

| Command | What it does |
|---|---|
| `forma profile <file>` | sheets, tables, columns, types, fingerprint of one file |
| `forma propose <file> [--llm]` | propose a mapping YAML for a file |
| `forma transform <file> <mapping.yaml> --fmt csv` | run a mapping, write output |
| `forma eval [--baseline data/eval_baseline.json]` | accuracy of mapper + engine vs corpus ground truth (CI gate) |
| `forma bench --files 300 --rows 100000` | end-to-end throughput benchmark (`docs/PERFORMANCE.md`) |
| `forma worker` | run a job worker (`FORMA_JOB_BACKEND=arq` needs Redis) |
| `forma db upgrade / revision -m "…" / current` | Alembic migrations |
| `forma retention run [--apply]` | retention policy, dry-run by default |
| `forma backup / forma restore <path>` | DB dump + storage manifest (+ objects) |
| `forma config show / config example` | effective config (masked) / regenerate `.env.production.example` |
| `make check` | ruff + mypy + pytest with coverage (≥ 85 %) |
| `make reset` | wipe runtime data (DB, files, datasets, exports) |

---

## Repository layout

```
backend/            FastAPI + Polars service (Python 3.12, uv)
  src/forma/
    schema_registry/  target JSON Schema + x-forma catalogue (config, not code)
    mapping_dsl/      declarative Mapping DSL (Pydantic) + static validator
    ingestion/        immutable file store, fastexcel/openpyxl/csv readers
    profiling/        table detection (titles, offsets, stacked/side-by-side), column profiles
    fingerprint/      structural fingerprints, drift classification
    engine/           Thai normalisation, DSL → Polars compiler, transform engine
    export/           JSON / JSONL / CSV (atomic publish)
    ai/               heuristic mapper, LLM provider layer, agents, policy engine
    services/         rounds, mapping lifecycle, datasets/edits/overrides/diff/lineage
    jobs/             background runner + handlers
    api/              REST API (/api/docs)
    synth/            synthetic ERP corpus generator + DB seeder
    eval/             evaluation harness
frontend/           React 19 + TypeScript + Vite + Tailwind v4
target_schema/      product.v1.json (DRAFT contract — replace with the real one)
policies/           approval.yaml (auto-approve / review / block thresholds)
data/               runtime data (git-ignored) + synthetic corpus
docs/               architecture, ADRs, data model, DSL reference, Thai rules, roadmap status
```

## Documentation

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — control plane / data plane, the import lifecycle, state machines
* [docs/MAPPING_DSL.md](docs/MAPPING_DSL.md) — the declarative mapping language and every operation
* [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — entities and versioning rules
* [docs/THAI_CONTEXT_RULES.md](docs/THAI_CONTEXT_RULES.md) — dates, numbers, VAT, units
* [docs/EVALUATION.md](docs/EVALUATION.md) — corpus, metrics, current results
* [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — measured throughput of a 300-file night, engine profile
* [docs/RUNBOOK.md](docs/RUNBOOK.md) — operations: RPO/RTO, incident procedures, backup/restore, retention
* [docs/ROADMAP_STATUS.md](docs/ROADMAP_STATUS.md) — what is built per blueprint phase, what is next
* [docs/PHASE7_HANDOFF.md](docs/PHASE7_HANDOFF.md) — Phase 7 handoff: what was done, numbers, limits, next steps
* [docs/adr/](docs/adr/) — architecture decision records
