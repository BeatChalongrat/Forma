# ADR-037 — Policy calibration from outcomes

**Status** accepted · 2026-10-30 · phase 1 implemented. Extends ADR-008 (policy), ADR-018 (sandbox),
ADR-036 (verdict) and ADR-038 (corrections).

## Context

`policies/approval.yaml` holds thresholds set when FORMA was built: field confidence bars, the drift kinds
that may pass, a row-count tolerance, "any changed price goes to a person". Nothing measured whether they
were right. In the demo corpus, three customers' renamed layouts went to review although every value was
identical to the previous published round, and were approved as they were each time; a customer whose
record count legitimately swings by 40 % went to review every round. The automation rate was counted on the
Ops page and never used.

## Decision

1. **Every decision gets an outcome.** `services/calibration.outcomes` joins each decided mapping version
   with what a person did next: `accepted_unchanged`, `corrected`, `rejected` for reviews (from the
   `corrections` table of ADR-038 and the version chain), `auto_ok`, `auto_then_edited`,
   `auto_then_rolled_back` for automatic approvals (from later human versions, dataset change sets and
   rollback audits). Each row carries the verdict rules that fired and the policy's reasons.
2. **A report says how often FORMA was right.** Precision per verdict rule, per decision kind (automatic vs
   review) and per customer, over the last six months. It is shown at the top of Settings › Approval policy.
3. **The system proposes, an admin applies.** `calibration.proposals` turns consistent outcomes into
   concrete, reversible changes, each with the evidence and the reviews it would have saved:
   * `trust_data_checks` — at least three renamed/moved layouts whose values were identical on the same SKUs
     were accepted without change and none corrected → turn on `sandbox.trust_data_checks`;
   * `lower_high_confidence` — reviews raised only by a high-risk field just under the bar were confirmed as
     read far more often than corrected → lower `auto_approve.min_field_confidence_high` by 0.05;
   * `row_change:<customer>` — a customer's record count moved beyond the tolerance twice or more and was
     always approved as it was → raise that customer's `max_row_change_ratio` to the observed swing + 10 %.
   Applying goes through the ordinary settings service (`config.update_policy`, validated, audited) or the
   customer's new `policy_overrides` (validated, audited as `policy.calibrate`). A proposal carries a digest;
   a stale one is refused. Nothing is ever applied without a person.
4. **A new policy switch, off by default.** `sandbox.trust_data_checks`: when every important changed field
   keeps last round's published values on at least three SKUs (`_data_identical`), the sandbox's changed
   prices and the drift are not reasons for review — the approved mapping broke on the moved layout, the
   proposal did not. Hard blocks (lost records, new errors, wrong file) are unchanged.
5. **Per-customer overrides.** `Customer.policy_overrides` (migration `f2a3b4c5d6e7`) is merged on top of the
   effective policy by `PolicyEngine.for_customer` wherever a mapping is evaluated; editable through
   `PATCH /api/customers/{id}` with the same validation as the global policy.

## Consequences

* The policy still decides (rule 6); what changes is that its thresholds are now justified by outcomes and
  the next report shows whether a change was right — an applied proposal that starts producing
  `auto_then_edited` outcomes is visible within the same report.
* Proposals need evidence (`MIN_EVIDENCE` = 3 matching outcomes, zero contradicting ones), so a quiet
  installation sees none; that is the intended behaviour, not a bug.
* Not in this phase: proposals about anomaly thresholds, dataset-stage decisions, or lowering the critical
  confidence bar (critical fields stay a person's call until real-file evidence exists, ADR-036 §F).

## Verification

`tests/test_calibration.py`: the data-identical rule; customer overrides on top of the policy; three
customers' renamed layouts accepted through the verdict produce the report, the `trust_data_checks`
proposal, a refused stale digest, an audited apply, and a fourth customer's renamed layout then approved
automatically with `checks.data_checks_trusted`; the per-customer row-change proposal from fabricated
outcomes, applied to `policy_overrides`, refused when invalid, withdrawn when an outcome contradicts it.
