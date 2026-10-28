# Evaluation

`make synth` renders 30 variants from 8 ERP archetypes (SAP-like, Express-TH, Odoo-like, Dynamics-like,
hand-made Excel, months-as-columns price grids, promotion-heavy, CSV vendor with mixed encodings) with
random perturbations (title rows, header offsets, merged titles, hidden sheets, totals rows, Thai digits,
currency symbols, leading zeros, duplicate rows, BE/mixed dates, blank rows, empty columns, NBSP, renamed
columns, multiple tables per sheet). Each variant has `manifest.json`, the rendered files, and
`expected_output.jsonl` written from the generator's own truth. Three *returning-customer sequences*
(round 1 → unchanged → renamed → wrong file) exercise memory, drift and wrong-file detection.

`make eval` runs profile → heuristic mapping → engine on every variant with **no LLM** and scores:

| Metric | Definition | Current |
|---|---|---|
| identity recall / precision | expected SKUs found / produced | 1.00 / 1.00 |
| field value accuracy | exact match (numeric tolerance 0.01) over 16 compared pointers | 0.996 mean, 30/30 variants ≥ 0.99 |
| critical accuracy | sku, prices, VAT flag, promo type/discount/dates, currency | 1.00 |
| drift classification | expected relation per sequence round | 9 / 9 |

Residual misses are information that the source genuinely lost (e.g. `N` meaning either inactive or
discontinued). Re-run after any change to the profiler, heuristics or engine; the number must not drop.

When real customer files arrive: put them under `data/real/<customer>/`, write `expected_output.jsonl` once
(approve in the UI, export JSONL, review), and add the folder to the eval index — the same harness scores
AI proposals (`forma propose --llm`) against them.
