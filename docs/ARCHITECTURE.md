# Architecture

## The two planes

```
                        ┌────────────────────────── Web app (React) ───────────────────────────┐
                        │ Home · Work rounds · Round · Studio · Review · Editor · Assistant · Guide │
                        └───────────────────────────────┬──────────────────────────────────────┘
                                                        │ REST (/api)
                        ┌───────────────────────────────┴──────────────────────────────────────┐
                        │ FastAPI · services · background jobs (thread pool, durable Job rows) │
            ┌───────────┴───────────────┐                        ┌──────────────────────────────┴──────────┐
            │     AI CONTROL PLANE      │                        │               DATA PLANE                 │
            │  heuristic mapper         │   MappingDocument      │  ingestion (immutable store)             │
            │  schema / mapping /       │ ───────────────────▶   │  profiling (tables, columns)             │
            │  adaptation / diagnosis / │   (declarative DSL)    │  fingerprint + drift                     │
            │  copilot agents           │                        │  transform engine (Polars, Thai ops)     │
            │  mapping memory           │                        │  quality rules · schema validation       │
            │  policy engine            │                        │  export (json / jsonl / csv)             │
            └───────────────────────────┘                        └─────────────────────────────────────────┘
                                                        │
                                   PostgreSQL / SQLite (versions, change sets, audit, LLM ledger)
                                   object storage / local disk (files, datasets, exports)
```

The control plane never touches a row of data directly: it reads *profiles* (headers, types, samples)
and emits a `MappingDocument`. The data plane never calls a model: it compiles the document to Polars
expressions and runs them. The policy engine sits between them and decides whether a human is needed.

## Import lifecycle

```
ImportRound: uploaded ─▶ profiled ─▶ ┬─ auto_approved ─▶ transformed ─▶ approved_data ─▶ published
                                     ├─ review ───────▶ approved ─────▶ transformed ─▶ …
                                     ├─ blocked        (wrong file / zero rows)
                                     └─ failed         (job exception; error kept on the round)
```

1. **Upload** — files are stored content-addressed (sha256). A duplicate upload is detected, never re-stored.
2. **Profile** — every sheet is read as a string grid (fastexcel); tables are detected (title rows, header
   offsets, multi-row headers, stacked and side-by-side tables, totals); columns are profiled (type, nulls,
   uniqueness, samples, hints such as `sku_like`, `thai_date`, `price_like`). openpyxl adds structure
   facts (hidden sheets, merged ranges, formulas without cached values) with bounded cost.
3. **Fingerprint & drift** — the round's tables are hashed into a structural fingerprint and compared with the
   customer's last approved format (`identical · header_moved · columns_added · columns_renamed ·
   columns_removed · sheets_added/removed · types_changed · unrelated_file`), and with other customers'
   approved formats (wrong-file signal). Identical headers with a retyped column are `types_changed`, not
   `identical` (int/float count as one family; mixed/empty columns carry no type signal).
4. **Mapping** — in order: *memory* (identical/moved structure → reuse the approved mapping verbatim),
   *heuristic draft* (synonyms + hints + value overlap, fully explainable), *AI* (schema agent → mapping
   agent, or adaptation agent when the format drifted) if an LLM is configured. The result is validated
   statically against the target schema and simulated on a sample.
5. **Sandbox** — a new mapping for a customer with an approved one (drift re-proposal, AI adaptation,
   Studio edit) runs next to the approved mapping on the same files; rows, errors, invalid prices,
   duplicates, lost/gained records and every changed price are compared (ADR-018), and the customer's
   persistent overrides are checked for collisions (ADR-024). Each changed field is checked on the data
   against the last published round — same values, VAT gap, scaled or different (ADR-021).
   In the Studio, `POST /rounds/{id}/relationships` measures how every table of the round connects
   (cardinality, match rate, orphan rows) and suggests key pairs; the Relationships view draws them and turns
   an accepted connection into a collection link or a join of the mapping document (ADR-019).
6. **Policy** — `policies/approval.yaml`: confidence thresholds per risk level, allowed drift kinds,
   warnings budget, first-time-customer rule, row-count sanity, hard blocks. Produces
   `auto_approve | review | block` with reasons, a queue priority and a risk level (low / medium / high, with
   the factors and the records changed vs the approved mapping — ADR-019). A sandbox comparison that loses records or adds errors/invalid prices blocks; changed prices go to review. A `block` is not an ordinary review:
   approving it is an explicit override (`override_block`, approver role, mandatory reason, audited), and a
   Studio edit keeps the round's drift, so it cannot clear a wrong-file block.
6b. **Verdict** (ADR-036) — from the stored reports of a mapping version (and later of a dataset version) a
   deterministic rule set states one conclusion per file (`wrong_customer`, `rename_only`, `vat_shift`,
   `first_time`, `contract_broken`, `price_move`, …) with an outcome from a closed set, a confidence word,
   the one to three decisions only a person can make, the evidence behind it and what was folded away
   (a field whose rule is unchanged but whose values differ is a data change, not a mapping change). The
   policy decision is unchanged; `POST /rounds/{id}/verdict/accept` acts through the ordinary approval
   paths and records the rules that fired. Stored on the round's summary; recomputed when the rules move on.
6c. **Calibration** (ADR-037) — each decided mapping version gets an outcome from what a person did next
   (accepted unchanged, corrected, rejected; auto then edited or rolled back). Precision per verdict rule and
   per decision kind is reported, and consistent outcomes become proposals an admin applies through the
   settings service (`sandbox.trust_data_checks`, a confidence bar, a customer's row-count tolerance via
   `Customer.policy_overrides`). The policy still decides; its thresholds are now justified.
7. **Transform** — full run with the approved mapping; persistent overrides for the customer are
   re-applied; records are validated against the schema (the count of records that break it is kept on the
   version, ADR-026); a `DatasetVersion` (JSONL on disk) is created.
8. **Edit** — the Data Editor stages cell edits, pasted blocks, bulk edits on picked rows, copilot
   operations and deletions; the API previews impact (rows, cells, before/after) and schema validity —
   a save is refused only for errors it adds, so invalid records can be fixed one at a time (ADR-026);
   applying creates a new version + `ChangeSet`. *Persistent* edits also create `Override` rules for future
   rounds (ADR-024). The copilot plans conditions across collections and answers "why" questions from
   evidence (ADR-023). Every value traces back to its Excel cell, with the cause of a change (ADR-020).
   Rollback = new version from parent.
9. **Change intelligence** — the dataset is diffed against the customer's previous published dataset:
   SKUs added/removed, changed fields, price moves (large ones flagged), promotion changes; anomalies
   (price jumps and outliers, discounts, promotion dates, stock, removed SKUs, unusual size) with "% normal".
   Approving data with high-severity anomalies needs an acknowledgement (ADR-022). Data with records that
   break the target schema is never approved or exported, whatever is acknowledged (ADR-026).
10. **Bulk upload** (optional entry) — many files at once; each is matched to its customer by format (a
   customer code in the file name confirms or names it; disagreement waits for a person), becomes a one-file
   round, and — when the policy's publish checks all pass — is approved and exported as JSON without a
   person; one JSON per file, all of them in one zip (ADR-027).
10b. **Foresight** (ADR-039) — from each customer's round history: the expected next delivery (median
   cadence, due / overdue with a grace of a quarter cycle), a watchlist (layout drifted or a person needed in
   two of the last three rounds, or the last file blocked), and per-customer price-move baselines proposed
   through calibration from acknowledged rounds. Read-only, recomputed on each call, on Home and Customers.
11. **Export** — JSON (nested), JSONL, or CSV flattened per the schema's `output_variants`; atomic write;
   artifact recorded with sha256. Exporting publishes, so only an approved (or already published) dataset
   can be exported; a draft is refused with 409.

## Mapping memory and self-healing

```
new round ─▶ fingerprint ─▶ compare with approved ─▶ identical? ──yes──▶ reuse mapping ─▶ policy (auto)
                                                   └──no───▶ drift kind + diff
                                                              ├─ heuristic draft on the new structure
                                                              ├─ adaptation agent: approved mapping + diff + draft → adapted mapping
                                                              └─ policy → review (renamed/removed) · block (unrelated)
```
Approved mappings are keyed by `(customer, fingerprint)`; the memory also provides few-shot examples
from other customers whose headers overlap (header Jaccard ≥ 0.3) to the mapping agent.

**Learning from corrections (ADR-038).** Every approval is diffed against the version FORMA proposed: a field
re-pointed to another column becomes a column alias for that customer, a confirmed constant becomes a
convention, an approval as proposed is recorded too (`corrections` table). The heuristic draft reads them
before it guesses (a taught header wins, with evidence saying so), the agents get them as data, and an alias
three customers taught independently is suggested as a synonym for everyone — promoted by an admin, never
automatically. Settings › *What FORMA learned* shows corrections per approval by month.

## Target formats and runtime settings (ADR-017)

The mapper, change report, copilot and editor find fields by `x-forma.semantic`, never by name, so a new
contract maps without code changes. Records keep provenance (`source_meta`) and unmapped columns
(`attributes`) inside FORMA; exports and validation use `TargetSchema.export_view`, which drops them unless
the contract declares them. Formats registered in **Settings** live in the `target_schemas` table (files in
`target_schema/` are the built-ins); the AI provider/key (encrypted), the approval policy and the default
format live in `app_settings`. Each write is audited and bumps a revision; every API/worker process
rebuilds its registry, policy and LLM client when it sees a new one (`FORMA_CONFIG_REFRESH_S`).
A round's format: the request's, else the customer's (`default_target_schema`), else the default.

## Where the intelligence lives

| Concern | Deterministic | LLM (optional) |
|---|---|---|
| Table/header detection | ✓ profiler | – |
| Column → target field | ✓ synonyms, hints, type compatibility, key uniqueness | refines ambiguous columns, nested promo structures |
| Table roles & links | ✓ field coverage scoring, value overlap | confirms joins / composite keys |
| VAT / units / dates | ✓ explicit ops with Thai rules | states assumptions in evidence |
| Drift | ✓ fingerprint diff | adapts an approved mapping to the diff |
| Failures | ✓ rule-based causes | root-cause narrative + patched mapping |
| Data edits | ✓ operations with impact preview | natural language → operations (never applied directly) |

## Production shape (Phase 7)

```
 browser ──▶ nginx (web image: SPA + /api proxy) ──▶ API (FastAPI, N replicas)
                                                       │ enqueue()            ▲ /ready /metrics /api/ops
                                                       ▼                      │
                                              Redis (arq queue) ──▶ workers (forma worker × N) ── OTLP ──▶ collector
                                                       │                      │
                                              PostgreSQL (JSONB, Alembic) ◀───┘──▶ S3 / MinIO (files, datasets, exports)
```

* **Workers** (ADR-010). `FORMA_JOB_BACKEND=thread|process|arq`. Jobs are rows claimed atomically; handlers
  record finished stages and skip them on re-run; stale `running` jobs are re-queued on start; the queue is
  bounded (429). `forma bench` measures a 300-file night (`docs/PERFORMANCE.md`).
* **Storage** (ADR-012). `forma.storage.Storage` with `LocalStorage` (dev) and `S3Storage`; rows hold keys
  (`files/…`, `datasets/…`, `exports/…`), readers get a local path (cache on S3), downloads are pre-signed.
  Dataset versions are written object-first, row last — never a partial version (ADR-015).
* **Database** (ADR-011). SQLite in dev (`create_all` + stamp), Postgres in production with mandatory
  Alembic migrations; JSON → JSONB; every datetime is UTC-aware.
* **Auth & tenancy** (ADR-013). `FORMA_AUTH=dev` (header) or `oidc` (JWT via JWKS, PKCE in the SPA).
  Roles importer ⊂ reviewer ⊂ approver ⊂ admin enforced by one dependency; the authenticated actor is what
  the audit log records; an optional `customers` claim scopes everything to a tenant's customers.
* **Security** (ADR-013). Uploads are sniffed by content, names sanitised, sizes capped; LLM endpoints are
  rate-limited and samples capped; agent output passes a guard (instruction-like text or pointers outside
  the schema → rejected → heuristic draft → review).
* **Observability** (ADR-014). `span()` tracing (OTLP when configured), Prometheus `/metrics` with
  scrape-time gauges from the DB, JSON logs with request ids that follow a job into its worker, `/health`
  and `/ready`, an admin Ops page (queue, workers, failed jobs + retry, spend, slowest rounds).
* **Reliability** (ADR-015). Retries with backoff and a circuit breaker for the model (fallback never
  auto-approves); backup AI keys tried in order, each with its own breaker and model (ADR-025); retention policy with audited deletions, `forma backup|restore`, `docs/RUNBOOK.md`.
* **Deployment** (ADR-016). One backend image (`forma serve` / `forma worker`), one web image,
  `docker-compose.prod.yml`, `.env.production.example` generated from `Settings`, CI gates (lint, types,
  tests on SQLite + Postgres + MinIO, migration/config drift, eval baseline, short bench, image builds).

## Scaling notes

* Measured (docs/PERFORMANCE.md): 300 files × 100K rows = 30M rows in 17 min with 8 worker processes on a
  12-core node; ~16K rows/s per core single-stream. Memory ≈ 1 GB per worker at 100K rows.
* The thread backend is GIL-bound in record building and schema validation; use `process` on one node and
  `arq` across nodes. Postgres is required for more than one worker node (SQLite locks under concurrent
  writers).
* S3 adds one download per cold read per node; keys are immutable so the per-node cache never invalidates.
