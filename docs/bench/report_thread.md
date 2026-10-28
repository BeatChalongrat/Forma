# forma bench — 300 files × 100,000 rows (thread backend, 4 workers)

Run at 2026-10-28T05:24:19.391188+00:00 on 12 CPUs / 31.2 GB RAM.

| Metric | Value |
|---|---|
| Wall-clock (profile→propose→approve→transform) | 3305.2 s |
| Phase 1 profile+propose | 941.1 s |
| Phase 2 transform | 2363.2 s |
| Rows transformed | 30,000,000 |
| Rows/s (transform phase) | 12694.8 |
| Rows/s (end to end) | 9076.6 |
| Files/min | 5.4 |
| Peak RSS (api + workers) | 2414.0 MB |
| Failed jobs | 0 |
| Corpus generation (one-off, cached) | 0.0 s |

| Stage | n | p50 ms | p95 ms | mean ms | max ms |
|---|---|---|---|---|---|
| profile | 300 | 7326.6 | 11632.2 | 7744.7 | 13130.8 |
| propose | 300 | 4454.7 | 6150.7 | 4579.5 | 8285.3 |
| transform | 300 | 33579.8 | 41246.4 | 31334.5 | 45205.8 |
