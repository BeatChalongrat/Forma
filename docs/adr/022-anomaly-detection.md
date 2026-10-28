# ADR-022 — Change intelligence & anomaly detection, with an approval gate

**Status** accepted · 2026-10

**Context** The change report listed SKUs added/removed and price moves ≥ 20 % against the previous
published round. Nobody reviews 100,000 rows every two weeks, so the report must say which few records
look wrong, and data that looks wrong must not be published without a person saying so.

**Decision**

1. `services/anomalies.py` detects, deterministically and by field meaning (ADR-017):
   - within the round: prices ≤ 0, prices far from their category (median ± `outlier_mad` robust
     deviations, categories of ≥ 8 SKUs), discounts above `max_discount_pct`, discount amounts or special
     prices not below the price, promotions ending before they start, negative stock;
   - vs the previous published round: price moves ≥ `max_price_change_pct` (high at twice that), stock
     falling ≥ `max_stock_drop_pct`, more than `max_removed_pct` of SKUs gone;
   - vs the customer's usual size: record count beyond `row_count_tolerance_pct` of the average of the last
     `history_rounds` published rounds.
2. The report gives counts by severity and kind, the share of records with no anomaly ("% normal"), a
   headline and a "check first" list ordered by severity then magnitude. It is part of
   `GET /api/datasets/{id}/changes-report` (also for a first round, where only in-round rules apply).
3. Thresholds are the policy's `anomaly` section (`approval.yaml`, editable in Settings, validated).
4. **Gate:** with `anomaly.block_approval_on_high`, approving a dataset with high-severity anomalies returns
   409 until the request carries `acknowledge_anomalies: true`; the acknowledgement and its comment are in
   the audit log. Export already requires approval, so unusual data cannot be published silently.

**Consequences** Reviewers start from the handful of records that matter. The report reads the dataset and
the previous one in full, like the change report it extends; it is computed on request, not stored.
