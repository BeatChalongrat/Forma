# ADR-067: Durable mapping workflow with three bounded roles

Status: implemented; live comparison recorded in `manual_test_files/workflow_v1/REPORT.md`.

Unknown/changed ERP layouts need evidence gathering, executable mapping and independent semantic
review. More agents alone do not establish accuracy. Keep the same configured model, engine,
source tools, sandbox and deterministic approval policy; compare the orchestration separately.

## Execution

LangGraph runs Evidence Analyst → Mapping Builder → full deterministic execution → Semantic Reviewer.
The analyst can inspect sheets, cells, values and relationships but cannot write a mapping. The
builder retains the existing candidate experiments, DSL validation, Python sandbox and self-check.
The reviewer sees source observations, the contract, executable mapping and execution results,
without the builder's confidence or explanation. A source-backed error can request one repair;
missing business facts become existing typed clarification cards. All roles use the configured
primary DeepSeek model; this graph does not silently switch to a backup model.

The approved-known-format fast path remains deterministic and does not add three LLM calls.
Neither an agent nor a reviewer verdict approves data. Existing policy, full verification and the
operator's versioned preview/apply/approve flow remain authoritative.

## Persistence and limits

- Official PostgresSaver in production; SqliteSaver in development. Setup creates checkpoint tables
  and a separate attempt/usage ledger. PostgreSQL setup is serialized with an advisory lock.
- A snapshot hashes source IDs/hashes, target contract, requirements, recalled knowledge, previous
  mapping, model/endpoint, prompt/workflow version and limits. Changed inputs produce a new workflow.
- A per-workflow PostgreSQL advisory lock prevents simultaneous execution of the same snapshot.
- Reserve each provider attempt before sending it, including retries. Persist successful response
  usage before structured validation. Unknown usage after a transport failure is flagged, not zeroed.
- Default 18 provider attempts, 600 seconds from first reserved attempt, one reviewer repair.
  The existing task-wide timeout also bounds execution. The elapsed deadline survives restarts.
- Checkpoints are written between graph nodes/inspection turns. A crash inside the builder can
  repeat that unfinished node; completed nodes are not repeated and the attempt budget is retained.
- Completed snapshots can be reused without another provider call. Credentials, sessions and
  dataframes are not serialized. Evidence can contain customer data: normal DB backup/access and
  retention controls apply. Checkpoint retention cleanup is not implemented in this change.

Human decisions use the existing FORMA question preview/apply service and create a new mapping
version. They do **not** use LangGraph interrupt/resume; this avoids a second decision API and
preserves current audit, validation, memory scope and UI behavior. Recalled knowledge is evidence,
not permission to bypass the current source checks.

The first live comparison found a missing handoff: the builder's sandbox had read formula text,
but the reviewer could not see that receipt and asked an unnecessary question about cached values.
Version `2026.11.04.2` forwards tool inspection receipts and executed sandbox code/output (including
input hashes), plus scoped recalled operator decisions. These are untrusted evidence to check,
not the builder's conclusion. Reviewer confirmation cards must endorse the current mapping;
they cannot pretend to select alternative expressions that the action cannot apply. The initial
run is retained and affected cases are retested separately rather than replacing the evidence.

## Observability and failure handling

`mapping.agent_log` and `policy.workflow` store role handoffs, source references, execution evidence,
review outcome, repairs, elapsed time, attempt count and unaccounted attempts. `mapping_trace`
exposes workflow evidence to existing inspection views. These are decision summaries, not private
chain-of-thought. Source references are checked for valid tables/columns/targets; a valid reference
alone does not prove the model's interpretation is true.

Invalid handoffs, exhausted budgets, incomplete execution and unresolved semantic repairs fail
closed into review. Review questions cannot hide a known mapping error. A same-model reviewer is
not an independent statistical oracle and may share the builder's mistakes.

## Rollout and validation

`FORMA_MAPPING_WORKFLOW=graph` enables the new workflow; `legacy` retains the old orchestration for
controlled comparison/rollback. Settings also expose `FORMA_MAPPING_WORKFLOW_CALLS` and
`FORMA_MAPPING_WORKFLOW_REPAIRS`. API and worker must use the same mode and application image.

Tests cover checkpoints, restart, shared retry budgets, durable deadlines, role boundaries,
evidence references, question validation, failed execution gates and upload/profile/propose API
integration with a fake provider. A no-provider PostgreSQL smoke test checks the deployed storage.
The sequential live comparison uses identical existing synthetic inputs and DeepSeek model in both
arms. Oracle answers are read only after freezing the proposal. It is a regression comparison, not
a new blind holdout and not proof of superiority over ChatGPT or Claude.

References: [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence),
[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
