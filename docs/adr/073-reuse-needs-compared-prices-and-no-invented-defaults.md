# ADR-073: A reused mapping needs compared prices; the matcher does not make up values

Status: implemented, 2026-11-20; not yet run on real customer files

## Context

Two paths let a value through that nobody had confirmed.

1. **Reuse.** When a layout matches an approved one, every field is treated as approved (confidence 1.0). The
   only check on the values was the comparison with last round's published data on the same SKUs. With fewer
   than three SKUs in common, or with no published round, there was no comparison and no reason to stop: a
   file with new SKUs and prices without VAT, or in another unit, was approved by itself.
2. **Constants.** When no column fed a field, the matcher wrote `MAIN` for the warehouse, `standard` for the
   price list and `percent_off` for the promotion type. For the built-in contract the first two are its own
   declared defaults. For a contract that declares none, they were FORMA's guess. A "blocked?" column mapped
   every code it did not know to `active`.

## Decision

### Reuse

- The field review always gives the base price (`price.amount`) a check, when its risk is critical or high and
  it is not a constant. If the values cannot be paired by SKU, the typical price of this file is compared with
  the typical price of the whole published round:
  - five times higher or lower, with at least five prices on each side: `scaled`. The existing rules send it
    to review and name the factor.
  - otherwise `no_data` with the ratio, and the statement that this cannot rule out a VAT change.
  - nothing published: `no_data`, basis `none`.
- New policy setting `auto_approve.require_value_comparison_for_reuse`, default `true`. A reused mapping whose
  base price is `no_data` goes to review with the reason. With the setting off the round may pass, and
  `checks.unverified_amounts` still records the field.
- Only the base price is required. Promotion prices, discounts and stock are sparse or move every round;
  requiring them would send most rounds to review.

### Constants

- A field no column feeds takes the contract's declared default, and its evidence says so.
- With no default: a required field is mapped to an empty constant with confidence 0, so the contract refuses
  the records and the field becomes a question; an optional field is left out.
- A promotion type is still inferred from a column that is present (a discount percent column means
  `percent_off`). With no such column it is not guessed.
- In a "blocked?/deleted?" flag, a blank cell still means the flag is not set. A code the map does not know
  stays as written and fails the contract's enum, as it already did for ordinary status columns.

## Consequences

- The first file after a customer's mapping is approved goes to review if that customer has no approved or
  published dataset yet. `test_an_automatic_approval_can_be_spot_checked` now approves the first round's data
  before expecting an automatic second round.
- A customer who sends different SKUs every round is reviewed every round while the setting is on.
- The built-in contract behaves as before for warehouse, price list and status. A promotion sheet with neither
  a type nor a discount column now yields records the contract refuses, where it used to say `percent_off`.
- The accuracy gate on the synthetic corpus is unchanged.

## What this does not establish

- A typical-value comparison cannot detect a 7 % change. Only a comparison on the same SKUs, or a person, can.
- Mappings written by a model can still contain constants; the validator warns when a critical field is one.
- No real customer file has been through either rule.
