# Architecture

## The two planes

```
                        ┌────────────────────────── Web app (React) ───────────────────────────┐
                        │ Overview · Imports · Round · Mapping Studio · Review · Data Editor    │
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
   approved formats (wrong-file signal).
4. **Mapping** — in order: *memory* (identical/moved structure → reuse the approved mapping verbatim),
   *heuristic draft* (synonyms + hints + value overlap, fully explainable), *AI* (schema agent → mapping
   agent, or adaptation agent when the format drifted) if an LLM is configured. The result is validated
   statically against the target schema and simulated on a sample.
5. **Policy** — `policies/approval.yaml`: confidence thresholds per risk level, allowed drift kinds,
   warnings budget, first-time-customer rule, row-count sanity, hard blocks. Produces
   `auto_approve | review | block` with reasons and a queue priority.
6. **Transform** — full run with the approved mapping; persistent overrides for the customer are
   re-applied; records are validated against the schema; a `DatasetVersion` (JSONL on disk) is created.
7. **Edit** — the Data Editor stages cell edits, bulk/copilot operations and deletions; the API previews
   impact (rows, cells, before/after) and schema validity; applying creates a new version + `ChangeSet`.
   *Persistent* edits also create `Override` rules for future rounds. Rollback = new version from parent.
8. **Change intelligence** — the dataset is diffed against the customer's previous published dataset:
   SKUs added/removed, changed fields, price moves (large ones flagged), promotion changes.
9. **Export** — JSON (nested), JSONL, or CSV flattened per the schema's `output_variants`; atomic write;
   artifact recorded with sha256.

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

## Scaling notes

* Reads are streaming-friendly (fastexcel/calamine → Polars). 100K-row files profile in ~1 s and
  transform in a few seconds; heavy per-row Python is limited to date/unit parsing and can be vectorised
  further if profiling shows need. 30M rows per round across 300 files is a parallelism question (several
  worker processes), not an engine redesign — the job runner interface is intentionally tiny so it can be
  swapped for Celery/arq without touching services.
* Datasets are JSONL on disk under `data/datasets/<customer>/<version>.jsonl`; swap `FileStore`/paths for
  S3/MinIO when moving off a single node (compose file included).
* Postgres is a one-line change (`FORMA_DATABASE_URL`); SQLite with WAL is fine for a single operator team.
