# 066 — Value-driven evidence, candidate experiments and bounded semantic review

Date: 2026-11-04. Status: implemented in working tree; **tests/model runs/deployment pending by user request**.

## Audit of existing behavior

- `profiling/profile.py` already reported inferred types, exact null/distinct counts for profiled rows, frequent values and date hints. Type/range inference only examined the first 400 non-null values; every positive float was tagged `price_like`.
- `ai/agents.py` sent the target JSON Schema, field catalogue, heuristic draft, source metadata and scoped knowledge. Profiles omitted file IDs and explicit profile/column truncation; frequent values replaced distinct samples.
- `ai/workbench.py` already exposed raw cells, metadata, spaced samples, distinct values, literal search and alternative table extraction. The sandbox could inspect formulas/notes. There was no structured composite-join experiment or multiple-mapping comparison action.
- `services/relationships.py` already measured links for the UI. The mapper could not directly request the same kind of evidence; the new tool is independent of service/database state and reports its exact normalization and bounds.
- The agent already ran mapping candidates and self-corrected from errors. `reconciliation.py` checked coverage loss/full execution. `contract_checks.py` already checked pinned-contract sum/equality/range rules. These mechanisms are retained, not duplicated with another LLM judge.
- `decision_memory.py` already scoped approved rules by customer, contract, layout, requirements and exact expression, with contradiction abstention. `review_evidence.py` already verified effects of human answers. These remain authoritative.

## Changes

1. `profiling/value_evidence.py` provides pure observations: spread samples, numeric and integer shares, zeros/negative values, zero-prefixed identifiers, ambiguous separators, paired numeric ratios and composite key candidates. Type inference includes the tail. Decimal values are no longer labeled as prices. Facts explicitly disclose sampling; no product field names are hardcoded.
2. Profile serialization remains backward-compatible through optional default fields. Table digest includes file identity, total rows, profile completeness, column truncation and bounded observations. Old profiles remain usable; reprofile is necessary to populate new facts.
3. Read-only `column_profile`, `relationships` and `join` actions expose structured evidence. Joins accept 1–3 key columns and report multiplicity, unmatched rows and output expansion. They use up to 50,000 rows per side, report incomplete scope, and preserve zeros. A ratio is not a unit rule; a matching key is not a business conclusion.
4. `ai/hypotheses.py` executes 2–3 alternative mappings in one optional turn. It returns evidence and never selects a winner. The orchestrator validates each DSL, shares the existing task deadline and forces a final mapping which receives the existing full verification. Exploratory reports are not used as an approval shortcut. Inspection/candidate evidence is retained in `self_check.history`.
5. `engine/semantic_checks.py` checks actual aligned source/output identifiers before dedupe hides collisions. Removing leading zeros raises a semantic warning exposed to self-check and verification. It does not invent a mandatory normalization rule: explicit business decisions still belong to reviewers. Other source semantics remain checked through contract-owned rules and source inspection; this is not a universal semantic proof.
6. Human questions include deterministic source samples from the stored profile, resolving source renames. The prompt asks for the precise remaining business fact and forbids guessing from numeric shape or elimination. Stored failed/incomplete verification is excluded from recalled decisions/constants. New decision metadata retains a compact verification snapshot and reuse scope. Legacy approvals lacking snapshots retain compatibility and must not be described as newly verified.
7. New regression tests and `manual_test_files/value_inference_v1` preparation/runner code are staged for the next evaluation. No generation/test/model run happened in this change.

## Boundaries for a future framework / agents

| Responsibility | Inputs | Outputs | Authority |
|---|---|---|---|
| Evidence discovery | Authorized files, profile, selected inspection request | Observations with scope and provenance | Read-only; no guesses recorded as facts |
| Mapping proposer | Contract, operator requirements, evidence, scoped knowledge | Validated DSL + unresolved business questions | Propose only; cannot change contract/policy |
| Candidate executor | Validated alternative DSLs and same source snapshot | Execution reports, losses, errors | Deterministic; cannot choose business meaning |
| Semantic verifier | Source/output pairs, pinned contract checks | Findings and completeness | Cannot waive failures based on model prose |
| Human decision service | Typed question, exact scope, operator answer | New proposal + effect verification | Existing preview/apply path only |
| Memory service | Approved decision and scope | Applicable rule or abstention | No cross-customer convention leakage |

These are code/module boundaries, **not newly deployed independent agents**. A future framework should call the same interfaces and share one budget, evidence snapshot and cancellation path. More agents can introduce conflicting assumptions and extra cost; do not claim that splitting roles itself raises accuracy.

## Required validation later

- Run prepared `test_value_inference.py` plus profiling/agents/workbench/knowledge/approval regressions and the full backend suite before deployment.
- Confirm upper bounds on runtime/prompt sizes with wide/large workbooks; relationship discovery covers first 12 columns and 2,000 spread rows and explicitly says so.
- Compare the same frozen fixture suite on a reproducible pre-change runtime and post-change runtime, same DeepSeek model and configuration. Dirty HEAD alone is not a baseline.
- Score correct outputs, unsupported auto-approval, exact question meaning, unnecessary questions, post-answer side effects, scoped memory reuse, latency and cost separately.
- The prepared cases are known problem families with fresh values/layouts, not independent blind generalization. Add independent families and real ERP files for that claim.

## Before/after claim allowed now

Before: architecture and gaps observed from code. After: the mechanisms above are written and statically reviewable. **No new accuracy %, performance improvement, passing test count or production readiness claim is supported yet.** Previous relationships_v1 results describe the previous tested runtime, not this untested working tree.
