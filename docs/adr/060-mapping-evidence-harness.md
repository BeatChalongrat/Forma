# ADR-060: mapping evidence and execution harness

Status: implemented; live provider quality must be measured separately from software tests.

## Context

The same model can map better when it can inspect the original workbook, try alternative readings and
receive execution feedback. It can also sound confident while choosing the wrong file or silently dropping
records. Model confidence and agreement with a heuristic draft it already saw are not independent evidence.

## Decisions

1. Bind explicit filename, sheet and fingerprint selectors together. Ambiguous files/sheet regexes do not
   silently choose the first match. Previously approved mappings can rebind renamed files using stored,
   unambiguous structural fingerprints; fresh model proposals cannot use that exception.
   Shadow comparisons can additionally bind one close structural match (similarity ≥ 0.8) with column/type
   drift to measure the old reading; that exception cannot enable production reuse.
2. Compare complete reading signatures: operations, constants, file/table selectors, joins, filters and
   deduplication. Reading the same price column and multiplying it by 1,000 is disagreement.
3. Inspect original cell ranges with addresses, sheet structure, evenly spaced samples including the tail,
   distinct-value distributions with truncation disclosed, and alternative TableSpec extractions. Inspect
   critical fields even when draft confidence is high. Review column choices use the same file scoping.
4. Mapping agents can run bounded Python experiments through the existing isolated runner (ADR-048).
   They receive only copies of the current round's profiled files, a manifest and the target contract. No
   application credentials, database, network or writable source objects are mounted. Python stdout is
   untrusted evidence; the final executable artifact remains validated Mapping DSL. Code, code hashes,
   input references, outcomes and timing are retained in the mapping's self-check history.
5. Successful sample execution is followed by full-file execution before accepting an agent candidate.
   Nonempty candidates with problems also receive a full check during repair. The heuristic's successful
   full execution supplies a coverage baseline. Repair must not improve its score by deleting critical
   field coverage or collection items. Unresolved differences require review, even when the decrease may
   ultimately be legitimate. Production proposals and saved human edits always execute on the full input.
6. Store an explicit verification report and coverage by target field/collection. Incomplete checks,
   execution errors, orphaned collection items, plausibility failures and unresolved self-check findings
   prevent automatic approval. Human review and final export gates remain in force.
7. Optional business invariants belong to the versioned target schema under `x-forma.checks`, never to an
   AI-authored quality-rule list. `sum_equals`, `equals` and `less_equal` can reconcile amounts/quantities
   using JSON pointers and a declared tolerance. Invalid checks fail closed. They run in the transformation
   engine, including final export, independently of the model.
8. Missing business constants can become typed clarification controls: boolean, number, text or enum,
   with a question, reason and a valid target pointer. The interface states that a constant applies to all
   records in this file. Row-varying facts must use mapping edits, not global constant answers. Answers
   create a new version, retain unrelated questions and run full validation. AI confidence/free-form prose
   cannot retire an unresolved VAT assumption. Group answers by customer, contract and reading signature.
9. Learned corrections must match the target contract. Reusing business constants for a new proposal
   additionally requires a compatible source format. Conflicting cross-customer header anchors remain
   suggestions, not automatically promoted aliases. Existing immutable DSL versions, approval history,
   drift checks and shadow comparison are the reusable adapter lifecycle (ADR-002/018/056).
10. Keep each runner serial; bound queued requests before spooling large input archives. An optional pool
    consists of separately isolated containers/sockets. Fail over only on an explicit busy response, never
    after an uncertain execution failure. Existing worker jobs still schedule mapping work. Each experiment
    has a deadline and input/output limits; mapping has separate inspection, repair and Python turn budgets.

## Configuration

`FORMA_MAPPING_LAB_ENABLED=true` enables experiments when a runner is configured.
`FORMA_MAPPING_LAB_TURNS=3` limits experiments per proposal (0 disables).
`FORMA_MAPPING_LAB_SOCKETS=[]` defaults to `FORMA_CODE_SANDBOX_SOCKET`; a JSON list routes to separate
runner containers. Each additional runner needs its own socket and private workspace/tmpfs, the same
network isolation and resource limits. Never raise process concurrency inside one runner/UID.
`SANDBOX_QUEUE_SLOTS=3` bounds admitted requests per runner, including the active request.
The production worker now mounts the existing sandbox socket volume as the API does.

## Reproducible evaluation

`forma benchmark MANIFEST REPORT [--llm]` evaluates arbitrary supported target schemas, without reading
production customer memory. Each case names source files, schema, expected records and cold/warm/drift
split. Warm/drift cases must provide an explicit approved mapping; cold cases reject family leakage from
the declared training set. Gold files are read only after generation. A supplied `baseline_output` is scored
by the same identities and fields. Input/schema/gold/manifest hashes and prompt version accompany traces.
Whole collection items are compared as multisets to catch swapped values across items; exact-record scores
also preserve array order. Field accuracy alone must not be used as a release gate.
Without `--llm`, the command executes a supplied mapping or the deterministic mapper. It is not an AI score.

Examples: `backend/tests/fixtures/harness/manifest.json` checks a custom dispatch contract and reordered
rows/columns. `manifest-live.json` challenges new headings with a note above the table. These small fixtures
verify the mechanism; they do not measure representative customer accuracy. Use held-out ERP families,
reviewed golden labels, declared model/settings/budgets and a separate ambiguous-fact set before making
comparative claims. Do not tune on the held-out results and continue calling them cold.

## Amendments after the mock file set (2026-11-02)

- Plausibility findings are a warning class of their own in the verification report (`warnings`, beside
  `problems`). A new or edited mapping with any such warning still goes to review; a mapping reused
  verbatim from an approval keeps the policy's warning budget, because a person accepted those values once
  already and the warnings stay on the card. Incomplete checks, execution errors and orphaned items block
  automatic approval in every case.
- The Compare tab binds the approved mapping to renamed files the way the shadow comparison does.
- Contract checks are recomputed after persistent overrides and after data edits; a broken invariant is a
  `contract_check` error finding on the new version.
- The benchmark scores a null and a missing key alike, and its exact-record score ignores the passthrough
  `attributes` bag when the answer key has none.
- The table extractor drops subtotal rows that carry a count beside the label ("รวมหมวด … 5 รายการ"),
  and the matcher does not read a promotion's dates on the product row as the price's effective dates.
- The reading signature compares what a field reads — the file its source binds to in the round, the
  sheet, the range, unpivot, filter and fill-down, and the expression — not how a selector is spelled (file
  pattern versus the only file, header row spelled out versus "auto", CSV options, descriptions). Root
  filters, dedupe and joins select records, and a collection's link, filter and item dedupe select items;
  each forms its own signature, and a difference there is one mapping-level review reason, not a contested
  question on every field. Two readings agree when they read the same cells with the same constants and the
  same value-changing ops (arithmetic, VAT, lookups, conditionals, cutting or rewriting identifiers); a
  wrapper that only normalises (trim, whitespace, case, cast, Thai number or date parsing, unit and pack
  normalisation, value maps) is not a different reading and is listed under ``agreement.ops`` instead. The first live runs with the
  signature as written marked every field contested (the model had added a dedupe key) and put 7–12
  confirm questions back on the card.

## Boundaries

Arbitrary generated Python is not promoted into a production parser. Exploration must end in the reviewed
DSL, or a specific unsupported-layout question. This preserves deterministic replay and auditability.
The runner does not keep a persistent interpreter across turns. HTTP disconnects do not instantly cancel
running code; the enforced execution deadline terminates it. A production-sized multi-runner load test and
an independent same-model vendor comparison require their own workloads; these changes do not prove
superiority, calibrated confidence probabilities, or correctness for every Excel format.

Design references: [OpenAI Code Interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter),
[OpenAI shell tools](https://developers.openai.com/api/docs/guides/tools-shell),
[Anthropic advanced tool use](https://www.anthropic.com/research/advanced-tool-use),
[Anthropic agent evaluations](https://www.anthropic.com/research/demystifying-evals-for-ai-agents).
These public patterns motivate controlled tools and execution feedback; they do not describe private internals.
