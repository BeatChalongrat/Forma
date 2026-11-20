# ADR-075: One table that holds more than one thing

Status: implemented, 2026-11-20; tested on files written to imitate each export, not on files from a customer

## Context

`backend/tests/test_erp_export_formats.py` left three findings open after ADR-074. Each is a table that is
not one row per product with one meaning per cell:

1. **SAP "Unconverted" list.** The list is saved as printed. The lines of dashes above and below the header
   and at the end were read as rows with no product code.
2. **NetSuite saved search.** NetSuite calls the item number `Name` and gives every record an `Internal ID`.
   Both hold one code per row. FORMA took `Internal ID` for the product code and asked nothing.
3. **Oracle OTBI, one row per item and organization.** In the Excel view the item's own cells are written
   once and merged down its rows. The second row of each item had no code, and its stock was lost. The CSV
   export repeats the values instead, and FORMA took the organization for the product code.

## Decision

### Rules of a printed list

A line of delimited text whose cells hold nothing but `-`, `=`, `+` or `_` (three or more) is read as a blank
row. It keeps its place, so row numbers shown to a person do not move. A row with any other cell is data.

### Cells merged down the data

- New table option `fill_merged`. A value merged down several rows is read on each row. Only the cells the
  file records as merged are filled: `.xlsx` merge ranges, `ss:MergeDown` in XML Spreadsheet, `rowspan` in
  HTML. A blank cell that is not merged stays blank. `fill_down` is unchanged: it fills every blank below a
  value and is still the tool for a file that leaves cells blank without merging them.
- The profiler sets it when the file merges a cell down the data rows of a table. A merge in a title or
  header does not count.
- A reader of a table with `fill_merged` must supply the sheet's merges. Without them it raises: a caller
  that forgot would otherwise read other values than the engine.

### The product code

After the header match picks the code column, FORMA looks for rivals: other columns with one code per row
that no field has taken. A number a collection field names (a price, a quantity) is not a rival.

| Chosen header | Rivals | What FORMA does |
|---|---|---|
| a name of the code (`รหัสสินค้า`, `SKU`) | any | keeps it, asks nothing |
| only resembles one | one rival whose header is a name of the code | takes the rival, asks nothing |
| a system's record id (`Internal ID`, `Row ID`, `ลำดับ`) | exactly one | takes the rival at confidence 0.6 and asks |
| a record id, or only resembles a name of the code | otherwise | keeps it at confidence 0.6 and asks, naming the rivals |

0.6 is below the bar a critical field must clear, so the mapping cannot be approved by itself.

### Several rows per product

- **Stock on the product rows.** When no stock table was found and the product table has a quantity column,
  the inventory collection is fed from the product table. A file with a stock table keeps it as the only
  source.
- **The warehouse.** If no header names it, FORMA takes the column that tells a product's rows apart: with
  the code it identifies a row, alone it does not, and it has fewer values than the code. It is used at
  confidence 0.6 and the card asks. With no such column the contract's default applies (ADR-073).
- **Codes that repeat.** A column of codes with digits that is not one per row gets the hint `code_like`. It
  counts against the product name as `sku_like` does, so `Item Name` holding `A001` twice is not the name.
- **What another collection exports is not lost.** The engine refused a collection when rows gave the same
  item and differed in any other source column (`engine/source_identity.py`). Columns that another collection of the same source
  reads are now left out of that comparison: a price repeated on each warehouse row is the same price, and
  the warehouse leaves with the stock. A price that differs between those rows is still a conflict. A
  column no collection reads (a lot number) still needs a key.

### Two tables read as one

If the code's header appears a second time in the product table, a second table probably stands beside the
first, one blank column away. FORMA reads the table up to the second header, leaves the rest unread and
says so on the card. Before, the columns beside the second header became attributes of whichever product
shared the sheet row.

## Consequences

- The SAP, NetSuite and Oracle files of the test set are read with the right code, name, price and stock.
  Each still goes to a person: it is the customer's first file, and the questions above are open.
- A reused mapping is not affected. `fill_merged` is off in a mapping written before this ADR.
- A new file whose data has merged cells is profiled with them filled. Its column statistics differ from a
  profile made before this ADR; headers and types, which the fingerprint uses, do not.
- The accuracy gate on the synthetic corpus is unchanged (0.9962; critical 1.0; drift 9 of 9).

## What this does not establish

- No file exported from a real SAP, NetSuite or Oracle system has been read.
- The record-id headers are a short list. A system that names its id otherwise is not recognised; the rival
  rule still asks when the header only resembles a name of the code.
- The warehouse rule needs the column to be among the first eight of the table and the rows sampled to hold
  no blank in it (`composite_key_candidates`).
- A file that leaves repeated cells blank without merging them (a printed report) is not filled.
- Merges are not known for legacy `.xls` and `.xlsb`.
- Two tables side by side are recognised only by the repeated code header. The second table is reported,
  not read.
