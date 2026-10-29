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

## Three modes (ADR-041)

| Command | What it scores |
|---|---|
| `forma eval` | the deterministic mapper alone (the CI gate, `--baseline data/eval_baseline.json`) |
| `forma eval --llm` | the heuristic draft refined by the configured AI provider's mapping agent; the draft is the fallback when the model fails (`mode`, `llm_fallbacks` in the summary); refused when no provider is configured |
| `forma eval --replay` | correction replay: round 0 of every sequence is scored, each wrong root-level field is corrected to the column whose values match the truth, the correction becomes a learned alias (ADR-038), and the later rounds are scored without and with it (`mean_field_accuracy` vs `mean_field_accuracy_with_learning`, `questions` vs `questions_with_learning`) |

On the synthetic corpus the heuristic is near the ceiling, so the AI and replay deltas are small by
construction; on real files they are the evidence that the AI path and the learning loop pay off.

## Real customer files

Put each delivery under `data/real/<customer>_<delivery>/` with `files/` and a reviewed
`expected_output.jsonl` (approve in the UI, export JSONL, check it), then from `backend/`:

```
forma eval --corpus ../data/real --index      # builds index.json and missing manifests, then scores
forma eval --corpus ../data/real --llm
forma eval --corpus ../data/real --replay     # sequences declared with "sequence" / "round_no" in manifest.json
```

Everything in `data/real/` except its README is git-ignored; the harness never uploads anything. Once the
first files are there, write `data/real/eval_baseline.json` the same way and gate on it.
