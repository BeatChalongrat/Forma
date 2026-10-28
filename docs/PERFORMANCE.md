# Performance

Measured with `forma bench` (ADR-010) on this development machine — numbers are real runs, not
estimates. Re-run with `cd backend && .venv/bin/forma bench --files 300 --rows 100000 --backend process --workers 8`
(raw reports in `docs/bench/`, the corpus and throw-away DB land in `data/bench/`, git-ignored).

Machine: Windows 11, 12 logical CPUs, 31 GB RAM, NVMe, Python 3.12, Polars 2.1, SQLite (WAL).
Workload: 300 rounds × one 100 000-row workbook each (8 distinct ERP archetypes, 3.5 GB of xlsx/csv),
30 000 000 output rows. The pipeline per round is profile → propose (heuristic mapper, no LLM) →
operator approval → transform (engine + schema validation + JSONL dataset version).

## 300 files × 100K rows — end to end

| Backend | Workers | Wall-clock | Phase 1 profile+propose | Phase 2 transform | Rows/s (transform) | Rows/s (end to end) | Files/min | Peak RSS (api + workers) | Failed |
|---|---|---|---|---|---|---|---|---|---|
| `process` | 8 | **1 021 s (17.0 min)** | 380 s | 640 s | 46 865 | 29 382 | 17.6 | 7.1 GB | 0 |
| `thread` | 4 | see `docs/bench/report_thread.md` | | | | | | | |

Per-stage latency under load (process backend, 8 concurrent rounds, 100K rows each):

| Stage | p50 | p95 | max |
|---|---|---|---|
| profile (fastexcel read, table detection, column profiles, fingerprint) | 5.6 s | 8.1 s | 17.4 s |
| propose (heuristic mapping + validation + 3 000-row simulation + policy) | 3.6 s | 5.4 s | 12.1 s |
| transform (full engine run, overrides, schema validation, JSONL write) | 16.0 s | 21.4 s | 28.5 s |

Reading the numbers:

* A **300-customer fortnight** (300 files, 100K rows each) completes in about 17 minutes on one
  12-core machine with 8 worker processes. The SLA for "all files of the night are profiled and proposed"
  is therefore **≤ 10 minutes**, and "everything approved is transformed" **≤ 15 minutes** after approval.
* Single-stream engine throughput is ~16 000 rows/s per core (30K rows in 1.9 s, see below); under
  8-way contention each transform takes ~2× longer (memory bandwidth, SQLite writes), which is why
  aggregate throughput is ~47K rows/s rather than 8 × 16K.
* Memory: ~900 MB per worker at 100K rows (profile + frame + records + validation). Size workers at
  roughly 1 GB RAM each; `FORMA_WORKERS` defaults to 4.
* The `thread` backend shares one GIL: profiling and Polars expressions run in parallel (they release
  the GIL) but record building and JSON-schema validation do not, so it scales to ~2 cores. Use it for
  dev and single-user installs; use `process` or `arq` for throughput.

## Engine: where the time goes (30 000-row files, single stream)

Profile of `TransformEngine.run` *before* Phase 7 (cProfile, express_th archetype):

| Part | Share |
|---|---|
| `_build_records` — flat frame → nested records (per-cell Python, pointer parsing, date conversion) | ~45 % |
| fastjsonschema validation of every record | ~22 % |
| Excel read (fastexcel/calamine) | ~8 % |
| Polars expressions incl. the per-row Thai ops (`map_elements`) | ~10 % |
| joins / dedupe / collections / findings | rest |

The per-row Python ops the roadmap worried about (`parse_date_th`, `unit_normalize`, `pack_size`) are
**not** the bottleneck on realistic files: a 100K-row file has a handful of date/unit columns, each
parsed in ~0.2 s. Record assembly was.

### What changed

1. **Record builder** (`engine/transform.py::_build_records`): dates are converted to ISO strings and
   collection link keys are computed as Polars columns *before* iterating; rows are walked as tuples with
   pre-split pointer paths instead of `iter_rows(named=True)` + per-cell `isinstance` chains. Output is
   byte-identical (the engine tests and `make eval` are unchanged).
2. **Vectorised Thai unit rules** (`engine/thai_vec.py`): `pack_size` and `unit_normalize` are Polars
   expressions; the Python functions remain the reference and `tests/test_thai_vec.py` asserts equality on
   the Thai test tables. `FORMA_VEC_OPS=0` forces the Python path (A/B switch).
3. **Dates stay in Python.** A Polars fast path for `parse_date_th` was built and measured slower than
   the regex-based Python parser (2.6 µs vs 2.0 µs per value), so it was removed — one implementation,
   one set of Thai rules (ADR-007).

### Before / after

Transform of 4 archetypes × 30 000 rows, single stream, best of 3 (same machine, same corpus):

| | before | after |
|---|---|---|
| `_build_records` + ops, total 4 files | 9.3 s | 7.5 s (−19 %) |
| rows/s (express_th) | 13 200 | 16 700 |

Per-op micro-benchmark, 100 000 values, Python `map_elements` vs vectorised expression:

| Op | Python | Vectorised | Speed-up |
|---|---|---|---|
| `pack_size` | 253 ms | 63 ms | 4.0× |
| `unit_normalize` | 198 ms | 97 ms | 2.0× |
| `parse_date_th` | 204 ms | 258 ms (removed) | — |

### Next levers (not done, measured as worth it)

* Schema validation at ~22 % could validate the *frame* once per column (types, enums, required) and
  only run fastjsonschema on rows that fail a cheap pre-check — expected −15 % of transform time.
* `_build_records` could emit nested structs straight from Polars (`pl.struct`) for root objects; the
  remaining Python cost is the dict allocation per record.
* Profiling reads every sheet fully to detect tables; a 100K-row sheet could be sampled (first 5K +
  tail) for detection and streamed for column stats.

## Streaming exports

Exports stream from the JSONL dataset (`iter_records` → `iter_export` → temp file → atomic rename):
memory is O(chunk), not O(dataset), and the sha256 is computed while writing. CSV makes two passes over
the file (column discovery, rows) so a 1M-row dataset is never held in memory.

## Memory and limits

* `FORMA_MAX_UPLOAD_MB` (200) bounds a single file; a 200 MB xlsx is ~2M rows and takes ~40 s to profile.
* `FORMA_QUEUE_MAX` (500) bounds queued + running jobs; above it the API answers 429 with `Retry-After`.
* The process pool holds `FORMA_WORKERS` interpreters resident (~150 MB idle each).
