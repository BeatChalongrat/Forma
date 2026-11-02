# ADR-058 — The agent checks its own work

**Status:** accepted · **Date:** 2026-11-02 · **Extends:** ADR-013 (agents propose), ADR-021 (field review), ADR-029 (review questions), ADR-057

## Context

The first real AI run (`docs/REAL_AI_RUN_2026-11-02.md`) and its re-run left one gap that no prompt wording closes:
the model writes a mapping once and never sees what it produces. It reads five sample values per column and
answers blind; FORMA runs the mapping only after the model has gone. A mapping that yields zero records, or a
date column read month-first, or a status column whose values the samples never showed, all pass static
validation and reach a person. When a person tested the same files they ran the engine, looked at the output
next to the source rows, asked the file what a column held, and fixed the mapping — that loop is what made the
difference, not a better eye for spreadsheets.

Two smaller findings belong with it. A hand-made sheet puts section headings on their own row inside the data
("เครื่องดื่ม", "ของใช้"); the profiler took the first one for a second header row and the rest became products.
And the review card asked nine to thirteen questions of kind `confirm`, which have one answer, while the real
decisions ("which of these two columns is the category") could not be answered on the card at all.

## Decision

1. **A workbench beside the agent.** `ai/workbench.py` gives the mapping and adaptation agents two things,
   both implemented by FORMA, never by the model:
   * *inspect* — read-only questions about the file: the columns of a table, the distinct values of a column,
     a range of rows, rows matching a pattern. Answers are clipped like every sample (ADR-013) and are data,
     never instructions. The model asks by returning `{"inspect": [...], "mapping": null}`; at most
     `FORMA_LLM_INSPECT_ROUNDS` such turns (default 2), eight requests per turn.
   * *run* — the engine runs the model's mapping on the first `FORMA_LLM_SELF_CHECK_ROWS` rows (default 50)
     and reports record count, compile and join problems, schema errors, implausible values and three sample
     records. The model fixes the mapping or returns it unchanged; at most `FORMA_LLM_SELF_CHECK_ROUNDS`
     feedback rounds (default 2). When a later answer is worse than an earlier one, the best is kept.
   Every turn is recorded in the agent log (`self_check`: rounds, inspections, problems found and left) so the
   review card can say what the model checked. Rule 2 stands: the model still emits only a `MappingDocument`;
   FORMA executes.
2. **Plausibility is a deterministic check, not a model opinion.** `ai/plausibility.py` flags years outside
   1990–2100, a start after its end, prices at or below zero, an identity with almost no distinct values,
   required fields empty in most records, and records without a price while the others have one (a second price
   sheet taken for the only one) — by semantic through the schema registry, never by field name. The
   same check feeds the self-check report and every proposal's findings as warnings, so a mapping written by a
   person or reused from memory is held to it too.
3. **Section rows are a table shape.** `TableSpec.section_column`: rows with a single text cell under the
   header are headings; their text fills the named column down to the next heading and the rows leave the data.
   The profiler counts such rows (`TableCandidate.section_rows`) and sets the option on the detected spec when
   there are at least two — a lone one-cell line is a note and stays a data row (ADR-055) — and the
   resulting column carries the hint `section_heading`, so the mapper and the model see "section: เครื่องดื่ม,
   ของใช้" as an ordinary column. A second header row must fill at least half as many cells as the first.
4. **Questions with a choice.** A review question is `choice` when the field reads one source column and the
   same table has other columns of a compatible type; the options are column names and the answer re-points
   the expression. `confirm` remains only for critical fields with no alternative; other low-confidence fields
   stay visible in the field review without a question. The heuristic's "assumed VAT-inclusive" question is
   dropped when the AI set the VAT flag from evidence.

## Consequences

- A new customer costs more model calls (typically two to four instead of one) and the same number of
  engine runs on fifty rows; a reused mapping costs nothing new. The caps are settings.
- `inspect` and `self_check` appear in `agent_log`; the verdict evidence names them.
- Files whose profile gains a `section` column fingerprint differently from before; only hand-made sheets with
  heading rows are affected and none has an approved mapping yet.
- `choice` is a new question kind in the API, the verdict card and the review workspace; `answers` for it are
  column names and must be one of the offered options.
- `unit_normalize` knows SAP's German unit codes (ST, PAK, KAR, KI, FL, DO, BT) and Odoo's "Dozens"; the first run
  left them as questions.
- Not done here: letting the schema agent inspect; a model-written plausibility rule; a `choice` over values
  (not columns) for enum fields; a total row inside an unpivoted inventory source.
