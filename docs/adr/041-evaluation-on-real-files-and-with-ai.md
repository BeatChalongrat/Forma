# ADR-041 — Evaluation on real files, with the AI on, and with corrections replayed

**Status** accepted · 2026-10-30 · harness implemented; real files pending. Extends the evaluation of
ADR-002/ADR-006 (`docs/EVALUATION.md`), ADR-038 (learning) and ADR-036 (verdict).

## Context

Every accuracy figure FORMA quotes came from a synthetic corpus scored with the AI switched off. The AI
path (schema agent → mapping agent) had never been measured, the learning loop (ADR-038) had no number
that said it works, and there was no place or procedure for real customer exports. The harness
(`forma eval`) could only run one way.

## Decision

1. **Three modes of the same harness.** `forma eval` keeps its heuristic default and gains:
   * `--llm` — each variant's heuristic draft is refined by the configured provider's mapping agent
     (`ai.agents.mapping_agent`) and the refined mapping is scored; the draft remains the fallback when the
     model fails, and the summary reports `mode` and `llm_fallbacks`. The run is refused with exit 2 when no
     provider is configured, so a "heuristic" number is never mistaken for an AI number.
   * `--replay` — correction replay (`eval/replay.py`): on every returning-customer sequence, round 0 is
     scored and each root-level field it got wrong is corrected the way an operator would — pointed at the
     source column whose values match the ground truth — and the correction becomes a learned alias
     (`services.learning.Learned`); the later rounds are scored without and with it. The summary gives
     field accuracy and open questions both ways. Wrong-file rounds are skipped: nothing carries across
     customers.
   * `--index` — builds `index.json` (and missing `manifest.json`s) for a folder of hand-made variants.
2. **A place for real files.** `data/real/<customer>_<delivery>/{files/, expected_output.jsonl}` with the
   same layout as a synthetic variant; everything but the README is git-ignored. `expected_output.jsonl`
   is produced by approving in the UI, exporting JSONL and reviewing it. Sequences and expected relations
   are declared in `manifest.json` like the corpus.
3. **The baseline gate is unchanged.** CI still gates on the heuristic run of the synthetic corpus; the
   other modes are reports until real files exist, at which point `data/real` gets its own baseline.

## Consequences

* Three numbers can be put side by side on the same files: heuristic, with the AI, after corrections.
  On the synthetic corpus the heuristic is already near the ceiling, so the AI and replay deltas are
  small by construction; on real files they are the evidence.
* Real files never leave the machine that evaluates them; the harness never uploads.
* Open: the first 20–30 real exports with reviewed expected output, a baseline for `data/real`, and a
  `--llm` baseline once a provider key is available in CI.

## Verification

`tests/test_eval_modes.py`: the `--llm` mode scores the refined mapping and records usage with a fake
provider, falls back to the draft when the provider fails; replay derives a correction to the column whose
values match the truth and scores at least as well with learning, with no more open questions; an index is
built from a real-style folder and the folder evaluates.
