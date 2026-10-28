# ADR-020 — Explainable lineage: every value back to its Excel cell, and why it changed

**Status** accepted · 2026-10

**Context** Lineage showed the source file, the root row index of the extracted table and the mapping
expression per field, and manual edits only from a change set's first 25 changes. A reviewer asking
"why is A001's price 29,000?" still had to open the workbook and guess the sheet, row and column, and could
not tell an ERP change from a mapping change or a manual edit.

**Decision**

1. **The engine names its source rows.** With `provenance` on, `source_meta.lineage` holds the root row,
   the source row of every collection item (`items: {"/inventory": [rows…]}`) and of every joined lookup
   (`joined: {source: row}`). Exports drop `source_meta`; the records' content is unchanged.
2. **Positions come from the immutable file.** `extract_table(..., row_numbers=True, positions=…)` returns
   the same table plus the raw grid row of each data row and header → raw column; without those arguments
   the output is identical to before. Lineage adds the sheet's used-range origin (openpyxl, read-only,
   because the reader drops leading empty rows/columns) to give the Excel row and column letter, and reads
   the cell back to mark it `verified`. Unpivoted values map to the column they came from.
3. **`GET /api/datasets/{id}/trace/{identity}?pointer=…`** returns, for every value of the record: the
   source cells (file, sheet, row, column, header, raw text), the mapping expression and what it yields on
   those cells (`mapped`), active persistent overrides that apply, the record's history across the round's
   dataset versions (every change with who, when, kind and note — not a sample), and the final value.
4. **Why it changed.** With `pointer`, the trace compares with the customer's previous published round and
   classifies the cause with evidence: `manual` (an edit in this round's versions), `source_changed` (the
   source cell holds another value — both cells quoted), `mapping_changed` (same cell, different
   expression), `previous_edit` (last round's value was a manual edit), `new_record`, `unchanged`,
   `no_baseline`.
5. In the Data Editor, **T** (or "Trace this value") on a cell opens the trace at that field; anomalies and
   price moves open it too.

**Consequences** Every published value can be explained down to a cell without opening Excel or reading
code. A trace re-reads the round's sources and walks the version chain (bounded at 50 versions), so it is
an on-demand call, not a list view. When a workbook's dimension tag disagrees with its first used cell the
position is marked unverified rather than guessed.
