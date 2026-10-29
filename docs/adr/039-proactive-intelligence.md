# ADR-039 — Proactive intelligence: what FORMA expects before a file arrives

**Status** accepted · 2026-10-30 · phase 1 implemented. Extends ADR-022 (anomalies), ADR-027 (bulk
upload), ADR-036 (verdict) and ADR-037 (calibration).

## Context

FORMA only knew about a file once it was uploaded. Nobody was told that C104, who sends every fourteen
days, had not sent anything this round; nobody was warned that C110's layout had changed in each of the
last two rounds; and the anomaly thresholds (`max_price_change_pct` 30 %) were the same for a customer
whose prices move 10–15 % every round as for one whose prices never move, so the former was held for a
person every time.

## Decision

1. **Expected deliveries.** `services/foresight.cadence` takes a customer's rounds (one delivery per
   calendar day), the median gap between them, and states the expected next date; a customer is *due* on
   that date and *overdue* after a grace of a quarter of the cycle (at least two days). Home shows "Not
   received yet" with the reason in words ("C104 usually sends every 14 days; the last file came on … —
   18 days overdue"); the Customers list shows the next expected date. Two rounds are needed before a
   cadence is claimed; a customer with one round is *unknown*, never "overdue".
2. **Watchlist.** From the last three rounds' summaries: a layout that drifted in two of them, a last file
   that was blocked, or a person needed in two of them puts the customer on the list with those reasons
   ("expect a check next time"). It is a heads-up, not a decision: nothing in the pipeline changes.
3. **Per-customer anomaly baselines, through calibration.** Each data-stage verdict stores a compact
   `anomaly_summary` on the round (counts by kind, the price-jump percentages). When a customer's price
   jumps were acknowledged and approved in two or more rounds, `calibration.proposals` proposes raising that
   customer's `anomaly.max_price_change_pct` to the observed 90th percentile plus five points (capped at
   80 %) as a `policy_overrides` entry; an admin applies it (ADR-037). The global threshold is untouched.
4. **Read-only and recomputed.** `GET /api/foresight` derives everything from the database on each call;
   it is tenant-scoped like every other read. The assistant can answer "who has not sent this round?" from
   the same view.

## Consequences

* Operators see tomorrow's work today: who is late, who will need a look, and why.
* A customer whose prices legitimately move is no longer held for a person every round once an admin
  accepts the evidence; the next calibration report shows whether that was right.
* Cadence is a median over calendar days, so a customer who sends on a fixed weekday is predicted to the
  day and a customer with irregular sends gets a wide grace; neither is treated as a contract.
* Not in this phase: notifications outside the app, holiday calendars, and learning a customer's anomaly
  baseline for stock or discounts.

## Verification

`tests/test_foresight.py`: cadence from round dates (due, overdue, ahead, unknown, same-day uploads);
expected deliveries and the watchlist through the API, with the Thai wording and tenant scoping;
`tests/test_calibration.py`: the per-customer anomaly proposal from stored summaries.
