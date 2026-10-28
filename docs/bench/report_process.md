# forma bench — 300 files × 100,000 rows (process backend, 8 workers)

Run at 2026-10-28T03:59:55.079693+00:00 on 12 CPUs / 31.2 GB RAM.

| Metric | Value |
|---|---|
| Wall-clock (profile→propose→approve→transform) | 1021.0 s |
| Phase 1 profile+propose | 379.9 s |
| Phase 2 transform | 640.1 s |
| Rows transformed | 30,000,000 |
| Rows/s (transform phase) | 46865.1 |
| Rows/s (end to end) | 29382.0 |
| Files/min | 17.6 |
| Peak RSS (api + workers) | 7137.6 MB |
| Failed jobs | 0 |
| Corpus generation (one-off, cached) | 0.0 s |

| Stage | n | p50 ms | p95 ms | mean ms | max ms |
|---|---|---|---|---|---|
| profile | 300 | 5565.6 | 8055.7 | 5950.2 | 17440.2 |
| propose | 300 | 3614.2 | 5410.0 | 3898.2 | 12109.6 |
| transform | 300 | 15960.1 | 21392.7 | 16417.3 | 28493.6 |
