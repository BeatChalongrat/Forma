# ADR-062 — Typed business decisions, effect checks and scoped approved memory

Status: accepted, 2026-11-03. Supersedes ADR-060's constant-only business-answer action and its unconditional heuristic VAT-assumption retention when deterministic row evidence exists.

## Problem and evidence

The international synthetic evaluation exposed four distinct decision operations: choosing a date grammar, supplying an unconstrained currency, defining a unit token, and choosing a conflicting source item. A string answer cannot safely become a constant in all four cases. A stale VAT assumption also offered to overwrite valid row-level flags. The warehouse fixture exposed a root/collection grain mismatch in duplicate detection. New schemas and ARQ retries occasionally failed through stale registry state and retained queue-result IDs.

## Decision

- Questions declare an action: `constant`, `date_format`, `value_map`, or `confirm`. The server derives `select_rows` decisions from conflicting collection identities and source provenance. The schema determines valid fields and value types; model suggestions cannot narrow an unconstrained contract enum. Source files remain immutable; answers produce new declarative mapping versions.
- Date answers change the parser; unit answers change only the selected token; a row choice filters only the unselected physical rows of this immutable delivery. Dates, identifiers and other fields are not filled by copying display labels into every record.
- For a source token embedded in a cell (for example a currency symbol in a price), reuse the candidate's explicit literal equality/contains predicate. Do not compare the whole price cell to the symbol or change the amount. Multiple distinct predicates for the same token require an editor decision instead of silently picking one.
- Preview and apply both execute the complete delivery. The gate rejects new validation errors, new invalid records, changed record identities/counts and changes to unrelated target fields. Already-existing unresolved errors remain visible and hold automation; partial answers may be saved without pretending the whole file is correct. This is an effect check, not proof of business truth.
- Confirming a source-backed boolean preserves its row expression. A heuristic-wide VAT assumption is retired automatically only when the final mapping uses `parse_bool` on an explicitly matching schema header and the executed outputs are boolean. Confidence prose is insufficient.
- Different root rows consumed by child collections are not automatically conflicting root values. Root mapped-field conflicts remain errors. Exact child duplicates may be collapsed; conflicting child values are retained for a reviewer instead of silently keeping the first.
- Answer metadata remains with the mapping. Only approval activates `Correction(kind=decision)` knowledge. Date, token and column interpretation rules are reusable; constants require an explicit “remember for this customer and format” choice. Physical source-row exceptions are never reusable. No migration is needed: the existing audited Correction JSON structure holds typed decision metadata.
- Recall requires the same customer, immutable target contract, compatible exact layout, operator requirements and matching before-expression. Contradictory approved rules cause abstention, not last-writer-wins. Retiring a decision invalidates verbatim format reuse depending on it. Knowledge is supplied to the mapping agent and checked again against its candidate; it does not grant authority to modify data outside the DSL.
- Workers refresh committed configuration at job start. ARQ IDs include the persisted attempt generation so a retained result cannot suppress an explicit retry; repeated submission of the same generation remains deduplicated.
- A single-table CSV's synthetic sheet name comes from its filename. A filename change alone is not layout drift; compare the column/type structure and rebind only an unambiguous source. This does not relax XLSX sheet or multi-source matching.
- Reused unit expressions are checked for previously unseen source tokens. A remembered token rule must not silently authorize a different token. Profile-derived checks are bounded by the profile's captured values, while full execution and contract checks still run for the delivery.
- Older unstructured notes require explicit reviewer acknowledgement after inspecting the output. The acknowledgement is audited, changes no record, and cannot replace a structured question's answer. Its selection is bound into the preview digest.
- Model and server questions about the same target/source token share one card and one audited answer; equivalent prompts are retired together. Different tokens remain separate decisions. A model's currency-symbol `value_map` question over a constant candidate is presented as an explicit delivery-wide currency question, not an executable token rule without a source expression. The UI warns that a constant answer applies to every record and directs mixed-row values to the mapping editor; remembering it still requires opt-in.
- Model responses are still untrusted, including those produced with structured output. Execution checks, policy, evidence and human decisions remain separate. No new agent framework, arbitrary production code execution, or model change is required.

## Vendor principles used (public documentation, not a claim to reproduce internal systems)

1. [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs): constrain response structure; semantic validation is still required.
2. [OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals): independent checks and explicit human review boundaries.
3. [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents): composable workflows, grounded tool feedback and bounded evaluation loops.
4. [LangChain memory concepts](https://docs.langchain.com/oss/python/concepts/memory): distinguish conversation state from persisted, scoped knowledge.
5. [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices): representative datasets, repeated runs and regression measurement.

## Validation and limits

New tests execute the actual transformation engine for per-row dates, currency contract validation, token-only unit changes and source-row conflict resolution. API tests cover effect checks, remembered versus delivery-only constants, approval activation, scope isolation, retirement, contradiction, worker refresh and ARQ retry generations. The frozen international candidates are replayed through the real preview/apply API without approval or export; fresh DeepSeek runs are stored separately from baseline results.

Passing synthetic tests does not establish real customer accuracy or equality to ChatGPT/Claude. The actual anonymized ERP exports, target contracts and business owners' answers are still needed for an acceptance pilot. Optional unmapped metadata and valid source facts should be informational; older unstructured model questions are deliberately not silently discarded just because a different question was answered.
