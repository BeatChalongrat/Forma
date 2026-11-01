# ADR-057 — What the first real AI run taught

**Status:** accepted · **Date:** 2026-11-02 · **Supersedes:** nothing; extends ADR-013, ADR-021, ADR-055, ADR-056

## Context

Until now every automated test ran with the mock provider, so the model's part of the pipeline had never been
exercised. The first run with a real model (`docs/REAL_AI_RUN_2026-11-02.md`: six ERP flavours, three deliveries
each, deepseek-flash through the live stack) showed that the model reads files well — the SAP info sheet, a status
legend, Odoo's tax column, a two-row header — and that the mistakes left were in the machinery around it:

1. In 2 of 6 new customers the model's answer failed `MappingDocument` three times (options written as `args`, a
   `const` without `value`, a `conditional` nested wrongly) and FORMA fell back to the heuristic draft. The prompt
   named the ops but not their arity or params, and the retry message repeated pydantic's union noise.
2. The model wrote `parse_date_th(..., day_first=false)`; the engine reads `dayfirst`. Nothing validated the
   param name, so US dates `10/1/2026` came out as **2026-01-10** on a critical field — while the model's own
   evidence said "month/day/year".
3. A price column that moved from the item sheet to the price sheet was classified `header_moved`, because
   drift compared the union of headers across sheets. The approved mapping was reused and produced twelve
   records without a price.
4. SAP's `31.12.9999` ("valid forever") was read as a Buddhist year and became `9456-12-31`.
5. `POST /rounds` returned before the row was committed: FastAPI runs a yield-dependency's exit code after the
   response is sent, and a client reading what it just created got 404 three times in twenty-five.
6. A null constant (`else null`) was dropped by `exclude_none` and the stored document no longer validated.

## Decision

- **The DSL tells the author its shape.** `OP_PARAMS` lists every literal option an op reads, beside `OP_ARITY`.
  The prompt lists each op as `name(args: n; params: …)`. `validate_mapping` reports `unknown_param` as an error,
  and `CallExpr` repairs a short list of known misspellings (`PARAM_ALIASES`: `day_first`, `assume`, `sep`,
  `precision`, …) before validation. DSL errors sent back to the model name the failing node and the branch that
  applies (`dsl_problems`), not every union branch.
- **Drift is per table.** A header that leaves one table and appears in another is `columns_moved`, which the
  memory path does not reuse and policy does not auto-approve; the adaptation agent gets to re-point it.
- **Years from 9000 are sentinels.** They are never Buddhist years and read as null (`OPEN_ENDED_YEAR`).
- **The API commits before it answers.** The API's session dependency (`request_session`) registers the session on
  the request; the http middleware commits it (or rolls back on a 4xx/5xx) before the response leaves. The
  dependency's own exit code stays as a no-op safety net, and `get_session` is unchanged for jobs and the CLI.
- **`ConstExpr.value` defaults to null**, so `{"op": "const"}` is the stored form of a null constant.

## Consequences

- A mapping written by a person in the editor with an unknown param is now refused instead of silently ignored;
  the error names the allowed params.
- `columns_moved` is a new drift kind in the policy's `allowed_drift` vocabulary and the web app's labels.
- Open-ended dates stop being values; a contract that wants "forever" as a date must say so explicitly.
- Every request pays one extra commit call inside the middleware; the dependency's later commit finds nothing to do.
- Not done here (recorded in the run report): a `choice` question type for "which column", fewer confirm-only
  questions, section rows in hand-made sheets, unit codes of SAP/Odoo/POS, a price table for deepseek.
