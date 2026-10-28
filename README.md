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

Then open http://localhost:5173 — the Overview shows the review queue, the Imports list shows the demo
rounds (including the returning-customer sequences C900–C902 that exercise memory → drift → wrong file),
and **New import** accepts your own files.

### Enable the AI agents (optional)

Everything works offline with the deterministic mapper. To add LLM refinement, diagnosis, self-healing
adaptation and the natural-language copilot, create `backend/.env`:

```
FORMA_LLM_PROVIDER=openai            # openai | openai_compatible | anthropic
FORMA_LLM_MODEL=gpt-4.1-mini
FORMA_LLM_API_KEY=sk-...
# FORMA_LLM_BASE_URL=https://your-gateway/v1   # any /chat/completions-compatible gateway
```

Token usage and cost per round are recorded in the `llm_calls` ledger and shown on the Overview.

### Useful commands

| Command | What it does |
|---|---|
| `forma profile <file>` | sheets, tables, columns, types, fingerprint of one file |
| `forma propose <file> [--llm]` | propose a mapping YAML for a file |
| `forma transform <file> <mapping.yaml> --fmt csv` | run a mapping, write output |
| `forma eval` | accuracy of mapper + engine vs corpus ground truth |
| `make check` | ruff + mypy + pytest with coverage |
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
* [docs/ROADMAP_STATUS.md](docs/ROADMAP_STATUS.md) — what is built per blueprint phase, what is next
* [docs/adr/](docs/adr/) — architecture decision records
