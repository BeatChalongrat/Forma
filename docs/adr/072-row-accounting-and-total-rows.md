# ADR-072: Row accounting and the file's own totals

Status: implemented, 2026-11-20; not yet run on real customer files

## Context

Every wrong value recorded so far passed the target schema: a date read month-first, year 9999 read as a
Buddhist year, a price column that changed meaning under the same header. Verification checked that fields
were filled and that a newer candidate did not fill fewer than an older one. Nothing counted the rows a
source lost on the way to the output, and a total row was dropped without being read.

## Decision

- The engine keeps a ledger in `stats["accounting"]` and changes no record.
  - Per source: grid rows, rows dropped by reason (`page_break`, `after_blank_stop`, `blank`,
    `section_heading`, `total_row`), rows after unpivot, rows removed by the source filter.
  - Root: `rows_in + join_added - join_dropped - filtered - duplicate_identity - null_identity = rows_out`.
    If that does not hold, the run gets an `accounting` error finding. It is an engine defect, not a
    property of the file.
  - Per collection: source rows, filtered, folded by an aggregate, exact duplicates, items, orphans.
- A dropped total row is compared with the rows that were kept, column by column. It may be a subtotal
  (rows since the previous total), a running or grand total, or a count of rows; a row listed twice is
  counted once. Numbers are read with `parse_number_th`. The tolerance is half a unit of the last decimal
  the total shows.
  - One agreeing column confirms the row. The other columns may be averages or percentages.
  - A row with numbers that agree with nothing is contradicted.
  - A column where under 90% of the filled cells are numbers is not checked.
- Rows cut by `stop_at_blank_rows` are counted. A row at least half as full as a typical data row, and not
  labelled as a total, is counted as data-like.
- A contradicted total and data-like rows after the stop become `source_review` warnings. The policy already
  treats that kind as a fact about the file: confidence and reuse of an approved mapping do not clear it,
  so the round goes to review. Records are still produced; a person decides.
- `verification()` carries a summary under `row_accounting`. Both reasons have Thai and English explanations.

## What this does not establish

- A matching total shows the table was read whole. It does not show that a column means what the mapping says.
- The check runs on source columns, not on output fields. A mapping that reads the right rows into the wrong
  field is not caught here.
- A total row that follows the blank-row stop is not compared.
- Filters written into a mapping are counted, not questioned.
- On the synthetic corpus (30 variants) every root ledger balanced, two of three total rows were confirmed,
  one had no numeric column to check and none was contradicted. That corpus has three total rows; the rate
  of false warnings on real exports is unknown.

## Operations

No migration. The ledger is stored inside the simulation statistics that mapping versions already keep.
A customer whose file always carries a total that is not a sum will go to review every round; there is no
per-customer switch for this check yet.
