# ADR-056 — Six values that changed quietly

**Status** accepted · 2026-11 · builds on ADR-007, ADR-021, ADR-027 and ADR-055

## Context

A review of every line of the code (`docs/CODE_REVIEW_2026-11-01.md`) ran the returning-customer cases of
`manual_test_files/` through the pipeline and found six places where FORMA decided something on its own and said
nothing:

1. A file with the same layout as last time reused the approved mapping and skipped the data checks of ADR-021
   entirely (`mapping.py`, `source_kind == "memory"`). Prices with the VAT taken out of every row, or written in
   satang, were approved by the policy and marked publishable. The anomaly layer looks for moves of 30 % or more
   per record, so a steady 7 % passed it too.
2. Two rows with one SKU and two prices were a warning; the engine kept the first. The policy tolerates five
   warnings, so a returning customer's file passed with the second price gone.
3. A status FORMA had no word for became `active`, and a promotion kind it had no word for became `percent_off`,
   because the mapper's `map_values` carried a default. A blank did the same. `promo.type` is a critical field.
4. `189.-`, the Thai way of writing 189 baht exactly, read as nothing; `1.500,00` read as 1.5; `1500,50` as
   150050.
5. `publishing.latest_export` found the newest export of the *round*. After a person approved a corrected
   version, FORMA returned the export of the old one and never wrote the new one; the work round's zip shipped it.
6. `THAI_CONTEXT_RULES.md` promised `1x12 → CARTON`; the code returned `1X12`.

## Decision

1. **A reused mapping is checked on the data too.** `healing.review(check_unchanged=True)` runs the ADR-021 data
   check on every critical and high field the mapping did not touch, against the same SKUs of last round's
   published data. A steady factor on every SKU (`vat_added`, `vat_removed`, `scaled`) reaches the policy and the
   verdict as before, so the file goes to review with the VAT question or the scale doubt. Values that merely
   differ become `data_changed`, which nobody acts on here: a new price list is the anomaly layer's business, not
   a reason to doubt the mapping.
2. **Duplicates that disagree are an error.** `_dedupe` compares the rows of each repeated identity on every other
   column. Identical rows stay a warning; rows that differ raise an error naming the columns, so the policy holds
   the round and a person chooses. The first row is still the one kept, as before, so nothing downstream changes
   shape.
3. **No default on an enum field.** The status and promotion-kind maps keep an unknown word as written
   (`keep_unmapped`) and let the contract's enum refuse it, which puts the record in front of a person as a
   contract error. A blank takes the schema's own default when it has one; that is the contract speaking, not a
   guess. The inverted status map (`Blocked`, `LVORM`) keeps `active` for a blank, because there a blank means
   "not blocked".
4. **Four more written forms of a number.** `189.-` is 189; `200.00-` is −200 (SAP writes the sign last);
   `1.500,00` and `1.500.000` are European; `1500,50` is a decimal comma. `1.500` alone stays 1.5, because nothing
   in it says otherwise. The Python reference, the Polars op and the engine's type coercion share one rule.
5. **Publishing exports the version a person approved.** `latest_export` orders by dataset version and can be asked
   for one version; `auto_publish` asks for the version it is about to publish.
6. **A bare packing figure names its unit.** `1x12` is a `CARTON` and `12's` a `PACK`, in both the Python rule and
   its Polars twin, as the documentation already said.

## Consequences

* A returning customer whose prices all rise by the same factor, a real price increase as much as a changed
  unit, now gets a question. That is the price of catching the VAT shift, and it is one question.
* A file with a duplicated SKU at two prices now needs a person. Identical duplicates still pass.
* A file that spells a status its own way now shows contract errors on those records instead of exporting them as
  active. The fix is one `map_values` row in the Studio, which the next round learns (ADR-038).
* `1.500` on its own still reads as 1.5. A customer who writes thousands with a dot and no decimals needs the
  column told so in the mapping.

## How it was checked

`test_silent_values.py`: every number form through both implementations; the packing units; an unknown status
and an unknown promotion kind becoming contract errors while a blank takes the schema default; identical versus
conflicting duplicates; through the API, a reused mapping on prices with the VAT removed (verdict `vat_shift`),
in satang (`scaled`) and with an ordinary price change (passes); a conflicting duplicate holding a returning
customer; and publishing exporting the version a person approved. The manual cases R10, R11 and R12 now go to
review at the mapping stage; the accuracy gate holds.
