# ADR-055 — Four files that went wrong quietly

**Status** accepted · 2026-11 · builds on ADR-002, ADR-007 and ADR-043

## Context

Running `manual_test_files/` through the pipeline found four files FORMA handled badly. Three changed the data and said
almost nothing; the fourth stopped the round with an internal error.

* A report printed page by page repeats its header. The later header scored better, so everything above it counted as
  a title: 6 of 12 products were gone, and the only sign was stock that matched no product.
* The mapper skipped a currency column on purpose and stamped the schema's default, so prices in dollars left as THB.
* `parse_date_th` swapped day and month one value at a time. In one file `10/31/2026` read correctly and `10/01/2026`
  became 10 January.
* A hidden cost sheet looked like a price list. With a price column in the main sheet too, `/prices` was mapped twice,
  which the validator allowed and the engine could not run.

## Decision

1. **A printed report is one table.** The first of several identical headers is the header, and blocks under one header
   are joined. A header printed again goes, with the blank and one-cell lines directly above it. `drop_repeated_headers`
   is on by default and runs before the blank-row stop, so the gap between pages does not end the table.
2. **One source per collection.** The mapper keeps the first and raises a question about the rest, because the engine
   has no rule for two price lists that disagree. `validate_mapping` refuses a document that maps a collection twice, so
   an agent's or a person's document fails with a reason, not inside the engine.
3. **A hidden sheet is not a source.** It is left out and a question says so. Hidden rows and columns of a sheet in use
   stay, with the finding they already raised.
4. **A currency column is read.** Words and symbols become codes, and anything else stays as written for the contract's
   pattern to stop. A weak header match does not overrule the default. With no such column the default stands, and a
   question is raised when a header names a currency or another sheet has a currency column saying something else.
5. **The order of day and month is decided once for a delivery.** A value past 12 settles it and a Buddhist year means
   day-first, because one column can hold nothing but `10/01/2026` while the next proves the order. Evidence for both
   orders, or for neither with a Christian-era year, reads day-first and raises a question.

## Consequences

* A first file whose dates could run either way gets one more question. That is the price of not guessing, and most
  files with a day past the 12th do not get it.
* A one-cell line directly above a repeated header goes even if it was meant as data. A row with one cell is not a
  record in these tables, so nothing it held could have been exported.
* An approved mapping keeps what it decided. A returning customer that starts writing month-first, or adds a currency
  column, is read with the old mapping: `columns_added` is allowed drift. Both need a check against the approved
  mapping, which this ADR does not add.
* `parse_date_th` still swaps a single value that only fits the other way, because approved mappings rely on it.
* The fix is in the mapper, so an AI proposal can still choose the wrong date order. The validator and the review catch
  a doubled collection, not a swapped date.

## How it was checked

`manual_test_files/` cases P03, P12, P18 and P22 now produce exactly their `expected.json`, and the 33 cases compared
went from 23 exact to 27. The accuracy gate holds. `test_quiet_failures.py` covers pages split by one to three blank
rows, a short first page, rows that only resemble a header, a hidden and a visible second price sheet, a document
mapping a collection twice, each way a currency can appear, and every date order.
