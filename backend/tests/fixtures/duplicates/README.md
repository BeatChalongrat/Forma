# Source identity regression fixture

`erp.xlsx` is the original synthetic ERP workbook used to reproduce the duplicate/lot bug. No customer data or API credentials are included.

- Products: 4 source rows, 3 distinct SKUs; `0001` repeats exactly.
- Stock Lots: `0001 / BKK / L001 = 7` repeats, `L002 = 7` is a separate lot, `CNX / L003 = 3`. Correct warehouse totals: BKK 14 and CNX 3. `0002` has two lots of 2 (total 4); `0003` has zero stock.
- Price Lists: `0001` has competing 100/125 prices for the same list/date, a separate wholesale price of 80 and a later retail price of 110. `0002` repeats the same price row.

The test provider is mocked. Tests intentionally supply/confirm the business grain and additive rule; they evaluate execution, questions and gates, not a live model's ability to infer those rules.

`test_source_identity_api.py` exercises upload, mock mapping, refusal to override unresolved keys, answer preview/apply, price selection, approval and JSON download. Three additional CSV API cases cover conflicting quantities, missing lot IDs and separate bins. Engine tests rename the input columns and target schema, preserve equal-quantity lots and zero, test missing inputs, and find conflicts beyond the preview window.

Before the change, projecting away lot IDs kept BKK 7; blindly summing returned BKK 21. After the change, a confirmed source grain removes only the actual repeated source record before summing to 14. Unresolved identities/conflicts block mapping approval and dataset approval/export, including with anomaly acknowledgement.

Run from `backend` with `FORMA_TEST_BLOCK_EXTERNAL=1`, `POLARS_MAX_THREADS=2`:

```text
python -m pytest tests/test_source_identity.py tests/test_source_identity_api.py --no-cov
```

All database/storage state belongs to temporary fixtures. Do not run these tests against a dev or production service.
