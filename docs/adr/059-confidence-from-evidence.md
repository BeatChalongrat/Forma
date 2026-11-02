# ADR-059 — Confidence from evidence, not from the model's word

**Status:** accepted · **Date:** 2026-11-02 · **Extends:** ADR-013 (agents propose), ADR-037 (calibration), ADR-038 (learning), ADR-058 (the agent checks its own work)

## Context

After ADR-058 the mapping agent could look at the file and see its mapping run. Two measured facts from the
second real run (`docs/REAL_AI_RUN_2026-11-02.md` §7) and a survey of how mature products do the same job
(`reports/เจ้าใหญ่ทำ AI schema mapping ยังไง.md`) said what to do next:

- The model used its right to look at the file once in twelve opportunities, and the misses that remained
  (a second price sheet taken for the only one, a status column whose values the samples never showed) were
  misses of *not looking*. Every working system in the survey forces the look instead of offering it, and
  the largest measured gains in schema matching come from values and from anchors — header → field pairs
  already approved for similar files — not from more model rounds or a bigger model.
- The policy read the number the model wrote as its confidence. Verbalized confidence is poorly calibrated
  (close to random as a ranking of right and wrong); what is calibrated is agreement between independent
  readers, deterministic checks, and thresholds set from what people did afterwards.

FORMA already had two readers of every file (the heuristic matcher and the model), a record of every
approved mapping across customers, a self-check, plausibility rules and a calibration report. They were not
wired to each other.

## Decision

1. **The model sees the column before it maps.** A column card carries the ten most frequent values with
   their counts, the share of empty cells, the number of distinct values and the range (`ColumnProfile.top_values`,
   `agents._column_card`), not five values in file order. Before the model's first turn FORMA runs
   `Workbench.ground`: the full distinct values of every column a low-confidence draft field reads, and of
   the unused columns of the same table that could hold the field instead (`ai/fit.compatible`), riskier fields
   first, at most eight columns. The answers arrive as `forma_looked_at_the_file_for_you`; the model does not
   have to decide to look. Inspect turns remain for what grounding did not cover.
2. **Anchors from other customers.** `learning.anchors_for` reads the newest approved mapping of every other
   customer for the same contract (and their operators' alias corrections) into (header, field) pairs with the
   customers that approved each. For the headers of the new file: a pair approved by at least
   `learning.share_after_customers` customers (policy, default 2) is an alias for this customer too — it wins
   in the matcher with evidence that says so; a pair fewer approved reaches the model as
   `anchors_from_other_customers`. The promotion suggestion (ADR-038) counts customers the same way, whether
   their operator taught the pair or accepted it from an anchor.
3. **Two readers, one signal.** Where the matcher's draft and the model's mapping read a field the same way
   (`mapping._agreement`, via the field diff of ADR-021) is agreement; where both mapped it differently is
   disagreement; a field only one of them has is `model_only` or `dropped`. The agreement is stored on the
   version (`policy.agreement`) and feeds the next two decisions.
4. **Confidence is bounded by evidence** (`mapping._ground_confidence`, policy section `confidence`): an agreed
   field is at least `agree_floor` (0.90); a disputed field is at most `disagree_cap` (0.70) and is asked on
   the card as a `choice` with the matcher's column offered first, however sure the model was, unless a
   person already approved that exact reading; a field a plausibility warning names is at most
   `plausibility_cap` (0.75); a mapping whose self-check left problems is at most
   `unresolved_self_check_cap` (0.80). Every bound leaves a line in the field's evidence. Fields the matcher
   had mapped and the model dropped are a review reason of their own, not a confidence question. The model's
   number still stands where FORMA has nothing to say (fields only the model mapped).
5. **Plausibility against the customer's own history.** Every simulation stores the share of records in which
   each field holds a value (`plausibility.fill_shares`). The next proposal — and the self-check run — is held
   to the approved round's shares: a field filled in 90 % of records before and in at most 50 % now, or a
   mapping that keeps less than half the rows per source row the approved one kept, is a warning the model
   sees and the card shows (`check_history`, `keep_ratio_problem`). Items that belong to no record (a total row
   in an unpivoted source) reach the self-check report too.
6. **Thresholds follow outcomes.** Calibration reports, per signal, how often a person corrected a field the
   matcher and the model agreed on, disagreed on, or only the model mapped (`calibration.per_signal`), and
   proposes moving `confidence.agree_floor` up when agreed fields are corrected in at most 2 % of twenty or more
   cases, down when in 10 % or more (`confidence_proposals`). An admin applies it like every proposal (ADR-037).
7. **Spot checks of automation.** A deterministic share of automatic approvals (`audit.sample_share`, default 5 %,
   by hash of the round id) gets a review task of kind `audit`. The approval stands and the round goes on;
   a person looks and confirms (`POST /review/{id}/confirm`) or corrects in the editor, and calibration counts
   confirmed against corrected as the honest accuracy of the automatic path.
8. **The same question across files, asked once.** Open review tasks whose questions coincide — a `choice`
   over the same columns for the same field, the same yes/no — form a group
   (`review_questions.groups`, `GET /review/question-groups`). One answer applies to every member
   (`POST /review/question-groups/answer`); each file still gets its own new version, previewed and re-decided
   by the policy. Groups sit above the queue: they teach the most per click.

9. **What the fourth real run added** (`docs/REAL_AI_RUN_2026-11-02.md` §9). Agreement compares *which columns*
   a field reads, not the expression text: the model wraps a column in `trim` or names its source differently
   and still agrees with the matcher. The target schema's `enum_hint` for the sell unit now lists every canonical
   unit the engine knows (BOTTLE, BAG, CAN, ROLL, PAIR included) and the op list tells the model the
   `unit_normalize` table already knows ST/PAK/KAR/KI/FL/DO/BT and the Thai and English words, so it stops
   remapping known codes to PCS. A model answer with a second JSON object or prose after the first complete
   object is parsed, not refused (`llm._parse_json` takes the first object). A failed agent reports what it
   spent, not the client's lifetime total.

## Consequences

- A new customer costs the same model calls as before plus a larger first prompt (top values and grounding
  answers; a few thousand tokens). Grounding and anchors run before the first call, deterministic and cheap.
- `policy` gains three sections (`confidence`, `learning`, `audit`) and `MappingVersion.policy` three keys
  (`agreement`, `confidence_basis`, `simulation.fill`). `ReviewTask.kind` gains `audit`. No migration: all are
  JSON columns and an existing kind column.
- Profiles stored before this change carry no `top_values`; their cards show the samples until the file is
  profiled again. Fingerprints are unchanged.
- The matcher's alias from an anchor overrides synonym scoring, as a learned alias always did (ADR-038). A
  wrong anchor approved by two customers would spread; the card's evidence names it, and a correction by the
  third customer becomes their own alias, which outranks nothing for others until two of them agree.
- Not done here: `choice` over enum values; anchors by ERP flavour rather than by header overlap; a review queue
  ordered by learning value beyond the groups; retraining anything — FORMA learns by remembering, not by
  fine-tuning. These remain in ADR-058's and this ADR's "not done" lists on purpose: the survey found no
  evidence that a second model critic, more self-check rounds or a bigger model would pay.
