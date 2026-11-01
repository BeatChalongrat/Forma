# FORMA — System architecture

> **สรุปภาษาไทย** — เอกสารนี้อธิบายส่วนประกอบของระบบทั้งหมด: สองระนาบ (AI control plane / data plane),
> โมดูล backend แต่ละตัวทำอะไร, หน้า frontend, ฐานข้อมูลและ storage, งานเบื้องหลัง, ความปลอดภัย,
> การ deploy, การ scale และจุดที่ตั้งใจออกแบบให้ "ล้มแล้วไม่เสียหาย" อ่านคู่กับ [SYSTEM_FLOWS.md](SYSTEM_FLOWS.md)
> ที่อธิบายลำดับการทำงาน และ [adr/](adr/) ที่บันทึกเหตุผลของทุกการตัดสินใจ

This document is the long form of [ARCHITECTURE.md](ARCHITECTURE.md). It describes every component, how
they are wired, what they store, how they fail, and which decision record (ADR) binds each choice.

## 1. Purpose and constraints

FORMA receives spreadsheet exports from ~300 customers' ERPs every two weeks and produces one validated JSON
contract per delivery for a downstream system. The constraints that shaped the architecture:

| Constraint | Consequence |
|---|---|
| Layouts differ per customer and change without notice | Structural fingerprints, drift classification, mapping memory, an approved-format library |
| Thai and English headers and values, Buddhist-era dates, VAT conventions | Explicit, tested Thai operations in the engine; no implicit locale behaviour (ADR-007) |
| Output is a contract other systems depend on | Target schema is configuration; schema validation gates approval and export (ADR-001, ADR-026) |
| One person in operations, not an engineer, runs it | Verdicts, questions with evidence, spreadsheet editing, Thai/English UI, one-click actions through the policy |
| AI must help without being trusted blindly | AI emits a declarative DSL; the policy decides; a guard checks every output; a model failure routes to review (ADR-002, ADR-008, ADR-013) |
| Decisions must be auditable and reversible | Immutable sources, append-only versions, change sets, audit log, atomic writes (ADR-005, ADR-015) |
| 300 files × 100K rows in a night | Polars engine, vectorised rules, process/arq workers, measured benchmark (ADR-010) |
| One machine, no server | Ports published on 127.0.0.1 with no login (`FORMA_AUTH=dev`, R31); roles, customer scope and OIDC stay in the code for a later move; housekeeping and backups run by themselves (ADR-053) |

## 2. The two planes

```
┌──────────────────────────────── Web app (React 19, TypeScript) ───────────────────────────────┐
│ Home · Upload · Imports · Round (case) · Review · Mapping Studio · Data Editor · Customers      │
│ Settings · Assistant · System health · Feature guide · Ctrl K                                  │
└──────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                           │ REST /api (JSON) · SSE for assistant streaming · MCP
┌──────────────────────────────────────────┴────────────────────────────────────────────────────┐
│ FastAPI · services · job runner (thread | process | arq) · startup recovery                    │
│                                                                                                │
│  AI CONTROL PLANE                                 DATA PLANE                                   │
│  ────────────────                                 ──────────                                   │
│  heuristic mapper (synonyms, hints, overlap)      ingestion (immutable store, sniffing, readers)│
│  approved-format library (memory)                 OOXML fidelity checks                        │
│  schema / mapping / adaptation / diagnosis /      profiling (tables, columns, hints)            │
│  copilot agents · assistant tools · reports       fingerprint + drift                          │
│  LLM provider layer (retries, breaker, backups)   transform engine (DSL → Polars, Thai ops)     │
│  output guard                                     quality rules · schema validation            │
│  policy engine (+ per-customer overrides)         sandbox · field data checks · anomalies       │
│  verdict · learning · calibration · foresight     datasets · change sets · overrides · lineage  │
│                                                   export (json / jsonl / csv, atomic)           │
└──────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                           │
        PostgreSQL / SQLite (versions, change sets, audit, LLM ledger, corrections, chats, settings)
        object storage (local / S3 / MinIO): source files, dataset versions (JSONL), exports, sandbox details
        Redis (arq queue) · OpenTelemetry collector · Prometheus
```

The **control plane** never touches a row of data directly: it reads *profiles* (headers, types, samples)
and emits a `MappingDocument`. The **data plane** never calls a model: it compiles the document to Polars
expressions and runs them. The **policy engine** sits between them and decides whether a person is needed;
the **verdict** states what the evidence adds up to.

## 3. Backend components

Package `forma` under `backend/src/forma/`. Python 3.12, FastAPI, Polars, SQLAlchemy 2, Pydantic v2,
structlog; strict mypy; ruff.

### 3.1 Schema registry — `schema_registry/`

* Loads target schemas from `target_schema/*.json` (built-ins) and from the `target_schemas` table (registered
  in the web app). Each is a JSON Schema plus `x-forma`: `schema_id`, `version`, `identity_keys`, per-field
  `semantic`, `synonyms_en/th`, `risk` (low/medium/high/critical), `required`, `examples`, `output_variants`.
* `TargetSchema` exposes the field catalogue and the lookups the rest of the code must use: `semantic_field`,
  `semantic_pointers`, `identity_field`, `collection_for`, `risk_of`, `export_view`. **Rule 1: no code names
  a target field; it asks the registry by meaning.** (ADR-001, ADR-004, ADR-017)
* Refuses schema shapes the mapping catalogue cannot enumerate: `$ref`, `allOf/oneOf/if`, tuple schemas,
  nested object collections, boolean field schemas (ADR-043).

### 3.2 Mapping DSL — `mapping_dsl/`

* `MappingDocument`: `meta` (customer, target schema, created_by, confidence, evidence, fingerprints),
  `sources` (file/sheet/table selection, CSV options, unpivot), `joins`, `record` (root source), `fields`
  (target pointer → expression + confidence + evidence), `collections` (link column, item fields), `quality`
  rules.
* Expressions are a closed tree of operations (`Op`): column/const, string ops, casts, Thai ops
  (`parse_number_th`, `parse_date_th`, `vat_adjust`, `unit_normalize`, `pack_size`), arithmetic, conditionals,
  boolean predicates. A new transformation is a new `Op`, one compiler branch, tests and a doc row — never
  free-form code (ADR-002). Static validation checks pointers, types, arity and required fields before
  anything runs.

### 3.3 Ingestion — `ingestion/`

* `FileStore`: immutable storage keyed by sha256 and file name through the storage protocol. The same bytes
  under the same name are not stored twice, and a file already uploaded in another round is marked
  `duplicate_of` (ADR-005, ADR-012).
* Sniffing by content (not extension), safe names, size caps (ADR-013).
* Readers: fastexcel (calamine) for cell values, the streaming OOXML scan below for structure facts, CSV with
  BOM and strict decoding, TIS-620/cp874 handled. openpyxl is used only to read a cell back for lineage checks.
* `ooxml.py` (ADR-043): streams every worksheet row of an xlsx; records formula counts, missing caches, error
  cells, hidden rows/columns, external formulas and numeric-format transformations; restores pure zero masks
  and percentage masks; never recalculates formulas or executes macros. A selected sheet with missing formula
  caches or Excel errors fails transformation; a formula-bearing or hidden-data sheet forces review.

### 3.4 Profiling and fingerprints — `profiling/`, `fingerprint/`

* Table detection across the full sheet: title rows, header offsets, multi-row headers, stacked and
  side-by-side tables, totals rows, blank-row boundaries; column profiles with inferred type, null and
  unique ratios, samples and hints (`sku_like`, `thai_date`, `price_like`, `unit_like`, `percent`, …).
* Fingerprints: a strict hash (sheet names + ordered headers) and a loose hash (sorted normalised headers) per
  file, plus a shape per table. Types are compared by `compare`, not hashed. `compare`
  classifies drift as `identical · header_moved · columns_added · columns_renamed · columns_removed ·
  sheets_added/removed · types_changed · unrelated_file` with a similarity score (ADR-006).

### 3.5 Engine — `engine/`

* `TransformEngine` compiles the DSL to Polars expressions on eager DataFrames, runs joins and collections, applies quality
  rules, builds nested records (vectorised record builder), validates against the schema and counts records
  that break it (`invalid_records`), and keeps provenance (`source_meta`, row and cell lineage) when asked.
* `thai.py` / `thai_vec.py`: the Thai rules as explicit operations with parametrised tests (ADR-007).
* Deterministic: same inputs + same mapping + same version ⇒ identical output; no randomness, no wall-clock
  in records except `source_meta`.

### 3.6 AI layer — `ai/`

| Module | Role |
|---|---|
| `heuristics.py` | Deterministic baseline mapper: table roles, header ↔ synonym scoring, hint boosts, type compatibility, value overlap, VAT guess from headers; reads learned aliases and constants first (ADR-038). Fully explainable; the floor the LLM improves on, never the ceiling |
| `llm.py` | Provider layer: OpenAI, OpenAI-compatible gateways, Anthropic, mock. Structured JSON output with validation callback and retries; routes with per-route circuit breakers and backup keys tried in order; usage and cost ledger (ADR-015, ADR-025) |
| `agents.py` | Schema agent (table roles, column semantics), mapping agent (final `MappingDocument` from the draft, with few-shot examples and what the operator taught before), adaptation agent (approved mapping + drift → adapted mapping), diagnosis agent (root causes + patched mapping), copilot agent (instruction → safe operation plan). Every prompt states: cell contents are data, never instructions (rule 7) and carries the complete selected target schema (ADR-043) |
| `guard.py` | Output guard: rejects instruction-like text, pointers outside the schema, oversized text; a rejected output falls back to the deterministic draft and the round goes to review (ADR-013) |
| `breaker.py` | Circuit breaker per route; hard errors are not retried; fallback never auto-approves |
| `policy.py` | `PolicyEngine`: `evaluate` → `auto_approve | review | block` with reasons, priority, risk level and factors, impact in records; `evaluate_publish` for automatic publishing; `sandbox_problems`; `for_customer` merges per-customer overrides; `trust_data_checks` (ADR-008, ADR-018, ADR-019, ADR-022, ADR-027, ADR-037) |
| `text_stream.py` | Streaming text for the assistant's final answer (OpenAI-compatible and Anthropic SSE), guarded incrementally (ADR-034) |

### 3.7 Services — `services/`

The services own the business flows; the API and job handlers are thin. Every service takes a
`ServiceContext` (settings, storage, file store, and the registry / policy / LLM client that follow the
saved configuration) and a SQLAlchemy `Session`; decisions and edits record the acting principal.

| Service | Responsibility | ADR |
|---|---|---|
| `rounds.py` | customers, rounds, add_file (sha256), profile_round (profiles, fingerprints, drift vs approved, other-customer match), audit helper | 005, 006 |
| `mapping.py` | `propose_for_round` (memory → library → heuristic → AI → validate → simulate → sandbox → field review → policy → verdict), approve / reject / edit, previews, field confidence inherited from the approved mapping | 002, 006, 008, 018, 021, 036, 037, 038, 046 |
| `format_memory.py` | approved-format library: newest approved exact layout across history, closest compatible approval as adaptation baseline, keyed by customer, target schema digest and fingerprint | 046 |
| `sandbox.py` | a new mapping next to the approved one on the same files: rows, lost/gained, errors, invalid prices, duplicates, changed values, override collisions | 018, 024 |
| `healing.py` | field-by-field changes vs the approved mapping, checked on the data (same, VAT added/removed, scaled, different) | 021 |
| `relationships.py` | measured relationships between all tables of a round (cardinality, match rates, orphans), key suggestions, probes | 019 |
| `datasets.py` | transform_round (engine + overrides + validation), change sets and impact preview, persistent overrides, rollback, change report, anomaly report, approve (anomaly acknowledgement, contract gate), export, lineage | 005, 020, 022, 024, 026 |
| `lineage.py`, `anomalies.py` | cell-level trace with cause of change; anomalies within a round, vs last round, vs usual size | 020, 022 |
| `batches.py`, `publishing.py` | bulk upload: identify customer by layout and file-name code, attach to a round, item states, stuck detection and resume; policy-decided automatic approval + export; zip of all JSON | 027, 033, 034 |
| `verdict.py`, `verdict_flow.py` | one conclusion per file (rules, suppression, confidence, decisions, evidence, digest); stored on the round, recomputed when stale; accept through the ordinary approval paths | 036 |
| `learning.py` | corrections learned from approvals (aliases, constants, accepted), `Learned` for the mapper, suggestions and promotion, stats | 038 |
| `calibration.py` | outcomes per decision, precision per rule, proposals (trust data checks, confidence bar, per-customer row change and price-move thresholds), apply | 037, 039 |
| `foresight.py` | cadence and expected deliveries, watchlist | 039 |
| `review_questions.py`, `review_evidence.py`, `review_explanations.py` | questions from uncertain fields, answers as new versions, evidence traced to cells, bilingual explanations of policy reasons | 029, 035, 042 |
| `assistant.py` | data-repair copilot: instruction → operations, "why" answers from evidence | 023 |
| `workspace_assistant.py`, `workspace_tools.py`, `assistant_navigation.py`, `assistant_reports.py`, `conversations.py` | the workspace assistant: bounded tool loop over ~20 read-only tenant-scoped tools, citations, server-built navigation and confirmable actions, server-computed reports, clarification questions, persistent conversations with leases and streaming | 029, 030, 031, 034, 040, 042, 045 |
| `code_sandbox.py`, `attachments.py` | the assistant's code runs: inputs checked against access, results stored in the chat; files in a chat | 048, 049 |
| `review_queue.py` | how long a question has waited, questions parked for the customer, idle rounds | 053 |
| `round_lifecycle.py` | cancel, reopen, move and replace a round without deleting it; closed rounds leave queues, history, cadence and the zip | 052 |
| `memory.py` | topics kept across chats, the rule that a message reading others' words saves nothing unasked, search of earlier chats | 051 |
| `artifacts.py` | works beside the chat: flows, files, versions, previews made in the sandbox, restore, staleness | 050 |
| `config.py` | runtime configuration edited in the web app: AI connection and backups, policy, formats, defaults, personal preferences; encrypted secrets; audited; revision bump | 017, 025 |
| `context.py` | `ServiceContext`; rebuilds registry, policy and LLM client when the configuration revision changes | 017 |
| `retention.py`, `backup.py` | retention policy (dry-run default), each pass recorded, housekeeping for System health, backup and restore | 015, 053 |

### 3.8 Jobs — `jobs/`

* `runner.py`: `enqueue(kind, payload)` and `@handler(kind)`; backends `thread`, `process`, `arq` (Redis)
  behind one interface; jobs are durable rows claimed atomically; bounded queue (429 + Retry-After).
* Handlers (`profile_round`, `propose_mapping`, `run_pipeline`, `process_batch_item`, `transform_round`,
  `export_dataset`) are idempotent: stages are recorded with `JobProgress.mark` and skipped on re-run.
* `recovery.py` (ADR-044): on API and worker start, queued jobs are reconciled against durable evidence
  (published round, dataset, mapping, export) before stale jobs are recovered and pending jobs resubmitted.

### 3.9 API — `api/`

* `routes.py` (core), `config_routes.py` (settings), `batch_routes.py` (bulk upload), `workspace_routes.py`
  (preferences, history, questions, verdict, learning, calibration, foresight, assistant, MCP). ~110
  endpoints. Writes declare a role with `require_role`; most reads need an authenticated principal
  (`current_principal`); customer-owned data is scoped to the principal's customers, with the exceptions listed
  in [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).
  See [API_OVERVIEW.md](API_OVERVIEW.md) and `/api/docs`.
* `serialize.py` is the single place that shapes rows into JSON; `frontend/src/lib/api.ts` mirrors it.
* `POST /api/mcp`: the assistant's read-only tools as a stateless Streamable HTTP JSON-RPC endpoint for
  external clients, under the same authentication (ADR-029).

### 3.10 Auth — `auth/`

Modes `dev` (headers `X-Forma-User`, `X-Forma-Roles`, `X-Forma-Customers`; refused when `FORMA_ENV=prod`
unless explicitly allowed for a local smoke test) and `oidc` (JWT via JWKS, PKCE in the SPA). Roles
`importer ⊂ reviewer ⊂ approver ⊂ admin`; an optional customers claim scopes customer-owned data to a
tenant's customers (ADR-013). Details and the endpoint matrix: [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).

## 4. Frontend

React 19, TypeScript strict, Vite, Tailwind v4, TanStack Query and Virtual; no UI library (primitives in
`components/ui.tsx`, tokens in `index.css`, aCommerce palette and bundled fonts, light and neutral dark
themes). Pages are self-contained; API types live in `lib/api.ts`.

| Page | Components | Backend it talks to |
|---|---|---|
| Overview (Home) | Workflow, WorkRoundDashboard, Foresight, VerdictLine, NextStepCell | dashboard, rounds, uploads, foresight |
| Upload (bulk + work rounds) | BulkUpload, UploadPage; UploadCard follows a working round on every page | batches, jobs |
| Imports | WorkRounds, history filters | history, batches |
| Round (case) | RoundSteps + VerdictCard, RoundMenu (cancel, move, replace, reopen), files, mapping versions, datasets | rounds, verdict, jobs |
| Review | ReviewWorkspace (verdict, questions, evidence, preview, save), age and wait for the customer, ClarificationCard via assistant | review, mappings/questions, evidence, answers, verdict |
| MappingStudio, RelationshipMap | field view, what changed, compare, relationships, DSL | mappings, sandbox, field-review, relationships, previews |
| DataEditor | grid, bulk edits, copilot, trace, changes/anomalies, findings, versions | datasets/* |
| Customers | list with next expected, detail with formats, overrides, policy overrides | customers |
| Settings | AI (connection, models, backups), formats, policy (+ calibration), learning, DSL, audit, system | settings/*, calibration, learning, schemas, audit |
| Assistant | Assistant, AssistantResponse, AssistantReport, ChatFiles, ConfirmAction, ArtifactPanel, FlowCanvas, ChatHistory (chats, works, saved), ChatMenu | assistant/*, conversations, stream |
| Memory | what the assistant remembers: read, edit, add, delete, export, reset | assistant/memory |
| Ops | queue, workers, failed jobs, breaker, spend, storage used, last retention pass, last backup | ops, jobs |
| Guide, CommandPalette | feature catalogue (`lib/features.ts`) in English and Thai | — |

## 5. Data

### 5.1 Database (SQLite in dev, PostgreSQL in production)

| Table | Holds | Notes |
|---|---|---|
| `customers` | code, name, ERP, default target schema, `policy_overrides` | |
| `import_rounds` | one delivery: status, label, target schema, `summary` (drift, decision, verdict, publish, anomaly summary) | status machine in [SYSTEM_FLOWS.md](SYSTEM_FLOWS.md) |
| `source_files` | sha256, storage key, profile, fingerprint, drift | immutable |
| `batches`, `batch_items` | bulk uploads / work rounds and their files (match, state, round) | ADR-027, 033 |
| `mapping_versions` | document, status, confidence, policy report (decision, checks, sandbox, field review, verdict, learning), agent log, parent | append-only |
| `review_tasks` | queue rows: kind, priority, reasons, details (risk, verdict brief) | |
| `dataset_versions` | records key (JSONL object), row count, stats (`invalid_records`), findings, parent | append-only, object-first |
| `change_sets`, `overrides` | edits with impact; persistent rules re-applied every import | ADR-024 |
| `exports` | artifact key, format, sha256 | atomic publish |
| `jobs` | durable job rows with stages, attempts, request id | ADR-010, 044 |
| `audit_log` | actor, action, entity, details — every write, never contents of chats | |
| `llm_calls` | agent, model, tokens, estimated cost, latency, attempts | ADR-032 |
| `corrections` | alias / constant / accepted / promoted, per customer, uses | ADR-038 |
| `app_settings`, `target_schemas` | runtime configuration (encrypted secrets), registered formats | ADR-017 |
| `assistant_conversations`, `assistant_turns` | chats with owner and access scope, leases, turns with reply JSON | ADR-030 |
| `assistant_attachments` | files in a chat: uploads, what code runs produced, preview pages | ADR-048, ADR-049, ADR-050 |
| `assistant_artifacts`, `assistant_artifact_versions` | works the assistant made, with every version | ADR-050 |
| `assistant_memories` | what the assistant remembers about a person | ADR-051 |

Every datetime is UTC-aware; JSON columns are JSONB on Postgres; every model change ships an Alembic
migration and CI checks models against migrations (ADR-011).

### 5.2 Object storage

`forma.storage.Storage` with `LocalStorage` and `S3Storage`. Rows hold keys (`files/…`, `datasets/…`,
`exports/…`, `sandbox/…`), never paths; readers get a local path (cache on S3); on S3, export downloads are
pre-signed.
Dataset versions are written object-first, row last, so no partial version is ever visible (ADR-012, ADR-015).

### 5.3 Configuration

Environment variables (`core/settings.py`, every field documented, `.env.production.example` generated and
drift-checked) for infrastructure; `app_settings` for what is edited in the web app (AI, policy,
formats, defaults, preferences). A revision row tells every process when to reload (ADR-016, ADR-017).

## 6. Security

* AI never executes code in the data path; the DSL is whitelisted; the guard checks every model output; cell
  contents are data, never instructions, in every prompt and tool. The assistant's code sandbox is the one
  exception: a separate container with no network and no secrets, whose results reach a person, never a record
  (ADR-048).
* Uploads sniffed by content, names sanitised, sizes capped; formulas never recalculated, macros never run.
* Roles on every write, an authenticated principal on reads, tenant scoping on customer-owned data, AI endpoints
  rate-limited and samples capped. Known gaps are listed in [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).
* Secrets encrypted at rest with `FORMA_SECRET_KEY`; audit records never contain secrets or chat contents.
* The assistant and MCP tools are read-only; the only writable action a model can propose is server-built,
  tied to a verdict digest, and performed only after a person confirms (ADR-040).

Full treatment: [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).

## 7. Deployment

```
browser ──▶ nginx (web image: SPA + /api proxy, SSE unbuffered) ──▶ API (FastAPI, N replicas)
                                                                      │ enqueue()      ▲ /health /ready /metrics /api/ops
                                                                      ▼                │
                                                            Redis (arq) ──▶ workers (forma worker × N) ── OTLP ──▶ collector
                                                                      │                │
                                                            PostgreSQL (JSONB, Alembic) ◀┘──▶ S3 / MinIO
```

One backend image (`forma serve` / `forma worker`), one web image, a sandbox image for the assistant's code runs,
`docker-compose.prod.yml` with a `migrate` service, generated `.env.production.example`, CI building the images and
validating the compose file (ADR-016, ADR-048). The sandbox service has no network: the API reaches it through one
Unix socket on a shared volume. A `backup-nightly` service backs up the database and every file every 24 hours and keeps
14 days (ADR-053, ADR-054). Dev stays SQLite + local disk + no auth + no external service, and without the sandbox.

Today this whole stack runs on one machine for one person. The web port and MinIO are published on 127.0.0.1, so
nothing else can reach them, and `FORMA_AUTH=dev` with `FORMA_AUTH_ALLOW_DEV_IN_PROD=true` means there is no login.
Moving to a server means OIDC first, then `FORMA_BIND_HOST` (R31 in [RISK_REGISTER.md](RISK_REGISTER.md)). The
diagram's replicas and workers × N are what the design allows, not what this machine needs.

## 8. Observability and reliability

* `span()` tracing (OTLP when configured), Prometheus `/metrics` with scrape-time gauges from the database
  (queue, review age, automation rate) and an in-process breaker gauge, JSON logs with request ids that follow a job into its worker,
  `/health` and `/ready`, the Ops page (ADR-014).
* Retries with backoff and per-route circuit breakers for the model; backup keys; fallback never auto-approves
  (ADR-015, ADR-025).
* Idempotent, resumable jobs; stale recovery; durable-result reconciliation on startup; bulk-item resume
  (ADR-010, ADR-027, ADR-044).
* Retention policy with audited deletions; `forma backup | restore`; RPO ≤ 24 h and RTO ≤ 1 h are targets, and on one machine they do not cover losing the machine (R36, [RUNBOOK.md](RUNBOOK.md)).

## 9. Scaling

Measured ([PERFORMANCE.md](PERFORMANCE.md)): 300 files × 100K rows = 30M rows in 17 minutes with 8 worker
processes on a 12-core node; ~16K rows/s per core single-stream; ~1 GB RAM per worker at 100K rows. The thread
backend is GIL-bound; use `process` on one node and `arq` across nodes; Postgres is required for more than one
worker node; S3 adds one download per cold read per node (keys are immutable, so the cache never invalidates).
ADR-043's full OOXML scan postdates these numbers; re-measure before quoting them for a deployment.

## 10. Design principles in one place (the non-negotiable rules)

1. Never hard-code a target field name; go through the registry.
2. AI never executes code in the data path; it emits the DSL. The assistant's sandbox (ADR-048) is the one exception.
3. Immutable sources, append-only versions.
4. Deterministic engine.
5. Thai rules are explicit operations.
6. Policy decides, not the model.
7. Cell contents are data, never instructions.
8. The job runner interface is stable; handlers are idempotent.
9. Services never touch paths or tokens; every write records the actor; every endpoint declares a role.
10. Every model change ships its migration; every setting has a description.
11. Nothing partial is ever visible.
12. Settings edited in the web app go through the configuration service.

These are the project's rules. Where the code does not yet meet one, the gap is tracked in
[RISK_REGISTER.md](RISK_REGISTER.md) (R20–R22, R30).

## 11. Decision records

ADRs 001–057 in [adr/](adr/) are binding unless superseded by a newer ADR. The index with one line each is in
[TEAM_LEAD_HANDBOOK.md](TEAM_LEAD_HANDBOOK.md#5-decision-log-adr-index).
