# FORMA — System flows (systems-analysis view)

> **สรุปภาษาไทย** — เอกสารนี้เขียนแบบ SA: context diagram, use case, ลำดับการทำงานทีละขั้น (process flow),
> sequence diagram ของแต่ละเส้นทาง และ state machine ของ round / mapping / dataset / batch item / job
> ทุกแผนภาพเป็น Mermaid (GitHub แสดงผลได้) แต่ละขั้นอ้าง ADR ที่เป็นเหตุผลของการออกแบบ

Diagrams are Mermaid; GitHub renders them. Each step names the module that implements it and the decision
record that explains why.

## 1. Context

```mermaid
flowchart LR
  C[Customers' ERPs<br/>xlsx / csv exports] -->|every ~14 days| U[(FORMA)]
  O[Operations team<br/>importer · reviewer · approver · admin] <-->|web app, assistant| U
  U -->|validated JSON / JSONL / CSV| D[Downstream systems<br/>catalogue, pricing, stock]
  U <-->|structured JSON only| L[LLM provider<br/>OpenAI · compatible gateway · Anthropic]
  U -->|traces, metrics, logs| M[Observability]
  I[Identity provider<br/>OIDC] -->|JWT, roles, customers| U
  X[External MCP clients] -->|read-only tools| U
```

## 2. Use cases

```mermaid
flowchart TB
  subgraph importer
    UC1[Upload one delivery]
    UC2[Upload many files at once]
    UC3[Choose a customer for an unknown layout]
    UC4[Resume stuck files]
    UC5[Ask the assistant]
  end
  subgraph reviewer
    UC6[Accept a verdict]
    UC7[Answer questions from evidence]
    UC8[Fix field matching in the Studio]
    UC9[Edit data, bulk edit, copilot]
    UC10[Approve data, acknowledge anomalies]
    UC11[Roll back a version]
  end
  subgraph approver
    UC12[Export / publish]
    UC13[Override a policy block with a reason]
    UC14[Register a target format]
    UC15[Forget a learned alias]
  end
  subgraph admin
    UC16[Connect AI, backup keys, model]
    UC17[Edit the approval policy]
    UC18[Apply a calibration proposal]
    UC19[Promote an alias for everyone]
    UC20[Retry failed jobs, read the audit log]
  end
```

Roles nest: importer ⊂ reviewer ⊂ approver ⊂ admin. A tenant-scoped principal sees only its customers.

## 3. Master process flow: one delivery, upload to export

```mermaid
flowchart TD
  A[Upload files to a round] --> B[Store content-addressed<br/>sha256, duplicate detection]
  B --> C[Profile each file<br/>tables, columns, hints, OOXML fidelity]
  C --> D[Fingerprint & drift<br/>vs this customer's approved format<br/>vs other customers' formats]
  D --> E{Mapping source}
  E -->|identical / moved layout| E1[Memory: reuse approved mapping verbatim]
  E -->|exact layout in history| E2[Approved-format library: reuse that version's rules]
  E -->|new or drifted layout| E3[Heuristic draft<br/>+ learned aliases & constants]
  E3 -->|AI configured| E4{Drifted from an approved mapping?}
  E4 -->|yes| E5[Adaptation agent]
  E4 -->|no| E6[Schema agent → mapping agent<br/>few-shot + what the operator taught]
  E5 --> F
  E6 --> F
  E3 -->|offline| F
  E1 --> F
  E2 --> F
  F[Static validation + simulation on a sample] --> G[Sandbox vs approved mapping<br/>lost / gained / errors / changed prices]
  G --> H[Field review: each changed field<br/>checked on last round's published data]
  H --> I[Field memory: unchanged fields inherit<br/>the confidence a person gave them]
  I --> J[Policy engine + customer overrides<br/>auto_approve / review / block, reasons, risk]
  J --> K[Verdict: conclusion, decisions, evidence]
  K -->|auto_approve| L[Approved by policy]
  K -->|review| M[Review task → reviewer accepts verdict,<br/>answers questions, or edits in the Studio]
  K -->|block| N[Blocked: wrong file or zero rows<br/>approver override with reason, or reject]
  M --> L
  N -->|override| L
  L --> O[Learning: diff approved vs proposed<br/>→ aliases, constants, accepted]
  L --> P[Transform: engine + overrides + schema validation<br/>→ dataset version v1]
  P --> Q[Change report & anomalies vs last round]
  Q --> R[Verdict, data stage]
  R -->|invalid records| S[Fix in the Data Editor<br/>each save refused only for errors it adds]
  R -->|high anomalies| T[Acknowledge or edit]
  R -->|clean / publishable| U[Approve data]
  S --> U
  T --> U
  U --> V[Export JSON / JSONL / CSV<br/>atomic, sha256, exact contract fields]
  V --> W[Calibration: outcome recorded;<br/>precision per rule; proposals]
```

### Step-by-step with the technique and the reason

| # | Step | Module | Technique | Why this technique |
|---|---|---|---|---|
| 1 | Store the file | `ingestion/store.py` | content-addressed sha256, immutable object, storage key on the row | Re-uploads are detected, nothing is ever overwritten, every later version can point at exactly these bytes (ADR-005, ADR-012) |
| 2 | Sniff and read | `ingestion/sniff.py`, readers, `ooxml.py` | content sniffing, strict CSV decoding with BOMs, full-sheet OOXML streaming with bounded XML | Extensions lie, CSV encodings vary (TIS-620), and silent data loss (uncached formulas, error cells, zero padding) must surface as facts, not vanish (ADR-003, ADR-043) |
| 3 | Detect tables and profile columns | `profiling/` | blank-row block detection over the whole sheet, bounded header window, type inference, hints | ERP exports have titles, offsets and several tables per sheet; profiling must be descriptive so the mapper and the AI reason over facts, not raw cells |
| 4 | Fingerprint and classify drift | `fingerprint/` | strict + loose structural hashes, similarity, drift kinds; compare with the customer's approved format and with other customers' formats | Memory needs an exact key; drift needs a vocabulary a policy can reason about; a file identical to another customer's format is the strongest wrong-file signal (ADR-006) |
| 5 | Pick the mapping source | `services/mapping.py`, `format_memory.py` | memory (identical) → approved library (exact layout anywhere in history, newest first; closest compatible as adaptation baseline) → heuristic → AI | Reuse what a person approved before anything is guessed; a customer that alternates between two ERP layouts should not be re-asked (ADR-046) |
| 6 | Heuristic draft | `ai/heuristics.py` | synonym scoring (exact, containment, fuzzy), hint boosts, type compatibility, uniqueness for keys, value overlap, VAT guess from headers; learned aliases win outright; learned constants replace guesses | Deterministic, free, explainable, 0.996 on the corpus; it is the floor and the fallback, so the system never depends on a model being up (ADR-002, ADR-038) |
| 7 | AI refinement | `ai/agents.py`, `ai/llm.py`, `ai/guard.py` | structured JSON output against a schema, validation callback (DSL + static validation), retries, backup routes, output guard | The model adds judgement (nested promotions, ambiguous columns) but can only move inside the DSL; invalid or injected output is rejected and the draft is used; the full target schema is in the prompt so the model maps to the real contract (ADR-013, ADR-025, ADR-043) |
| 8 | Validate and simulate | `mapping_dsl/validate.py`, `simulate` | static validation; engine run on a bounded sample | Catch bad pointers and arity before data; give the policy rows, errors and warnings to reason about |
| 9 | Sandbox | `services/sandbox.py` | run the new and the approved mapping on the same real files; compare by schema identity | A new mapping must prove it does not lose records or break prices before it replaces the approved one; thresholds belong to the policy (ADR-018) |
| 10 | Field review | `services/healing.py` | per changed field: equal share, steady ratio (×1.07 = VAT), scaled, different, on the same SKUs as last round's published data | A header that looks similar may mean something else; the data says whether it does (ADR-021) |
| 11 | Field memory | `mapping.py::_inherit_confidence` | unchanged fields take confidence 1.0 from the approved mapping | A person already approved exactly this mapping of this field; asking again is noise (ADR-037) |
| 12 | Policy | `ai/policy.py` | thresholds per risk, allowed drift, warnings budget, first-time rule, row sanity, hard blocks, sandbox limits, meaning doubts, trust-data-checks, per-customer overrides | Automation is a configuration decision owned by the team, with reasons, never a model's opinion (ADR-008, ADR-037) |
| 13 | Verdict | `services/verdict.py` | ordered hypothesis rules over the stored reports; suppression of same-rule-different-data, repeated patterns, no-data checks; confidence word; digest | A reviewer reads one sentence and decides one to three things; the evidence is one click away, not in the way (ADR-036) |
| 14 | Review | `review_questions.py`, `review_evidence.py`, `ReviewWorkspace` | questions from fields under the confidence bar; answers become a new version with evidence; evidence traced to cells through engine provenance; preview on the same sample | Decisions are bounded and auditable; the person sees the actual cell, never a guess (ADR-029, ADR-035) |
| 15 | Learning | `services/learning.py` | diff approved vs proposed (nearest system/ai ancestor) → aliases, constants, accepted; uses counted; suggestions ≥ 3 customers; admin promotes | A correction is made once per customer and once for everyone; the evidence on every learned field says where it came from (ADR-038) |
| 16 | Transform | `engine/transform.py`, `services/datasets.py` | Polars lazy compilation, vectorised records, persistent overrides re-applied, schema validation with invalid count, object-first write | Deterministic, fast, and nothing partial is visible (ADR-002, ADR-015, ADR-024, ADR-026) |
| 17 | Change intelligence | `datasets.py::change_report`, `anomalies.py` | diff vs the last published dataset; robust outliers (median/MAD); per-kind anomalies with severity; "% normal" | Reviewers need what changed and what looks wrong, not the whole table; high anomalies need an acknowledgement (ADR-022) |
| 18 | Edit | `datasets.py::apply_changes`, `assistant.py` | staged operations, impact preview, schema check that refuses only added errors, change set → new version; copilot plans, never applies | Fixes are reviewable and reversible; invalid records can be fixed one at a time (ADR-023, ADR-024, ADR-026) |
| 19 | Approve and export | `datasets.py::approve_dataset/export_dataset`, `publishing.py` | contract gate (no invalid records), anomaly acknowledgement, approver role for export, atomic artifact with sha256, exact `export_view` fields | The downstream never receives a record that breaks the contract or a field it did not ask for (ADR-017, ADR-026) |
| 20 | Calibration | `services/calibration.py` | outcomes per decision from corrections, version chain, change sets, rollbacks; precision per rule; proposals with evidence; admin applies | Thresholds are justified by what happened, and the next report shows whether a change was right (ADR-037) |

## 4. Bulk upload and automatic publishing

```mermaid
sequenceDiagram
  actor Op as Importer
  participant UI as Web app
  participant API as API /batches
  participant Q as Job queue
  participant W as Worker
  participant P as Policy
  Op->>UI: drop N files (work round name, date, expected count)
  UI->>API: POST /batches (chunks of ≤10 files)
  API->>API: store each file (sha256), create BatchItems, dedupe by name+sha
  API->>Q: enqueue process_batch_item × N
  loop each item
    W->>W: identify: profile, fingerprint, match approved formats across all customers (exact / same columns / similar)<br/>file-name code confirms, names, or contradicts
    alt known customer
      W->>W: attach: create round, add file, reuse profile
      W->>W: run_pipeline: propose → policy → (auto) transform → publish decision
      W->>P: evaluate_publish(invalid records, errors, high anomalies)
      alt publish
        W->>W: approve_dataset + export JSON
      else review
        W->>W: round held for a person (needs_data_check / needs_review)
      end
    else unknown layout
      W->>W: item = needs_customer (candidates listed)
      Op->>UI: choose a customer or create one
      UI->>API: POST /batches/{id}/assign
      API->>Q: enqueue for those items
    end
  end
  Op->>UI: Download all JSON (zip, one file per input)
```

Stuck items (queue full or down at upload time) are detected and resumed (`POST /batches/{id}/resume`); on
startup, queued jobs whose results already exist are reconciled instead of re-run (ADR-027, ADR-044).

## 5. Review and verdict

```mermaid
sequenceDiagram
  actor R as Reviewer
  participant UI as Review page
  participant API as API
  participant V as verdict_flow
  participant M as mapping service
  R->>UI: open a file from the queue
  UI->>API: GET /mappings/{id}/questions · GET /rounds/{id}/verdict
  API->>V: current(round) — stored, or recomputed when rules/status moved
  V-->>UI: conclusion, outcome, confidence, must_decide, evidence, suppressed, digest
  alt outcome = accept
    R->>UI: Accept and continue
    UI->>API: POST /rounds/{id}/verdict/accept {digest}
    API->>M: approve_mapping (block refused; audit verdict.accept with rules_fired)
  else outcome = answer
    R->>UI: answer the 1–3 decisions (yes/no, number, confirm as read)
    UI->>API: POST /rounds/{id}/verdict/accept {digest, decisions}
    API->>M: answer_document → apply_answers → new proposed version → policy again
  else fix_mapping / assign_customer / reject_file
    UI-->>R: link to the Studio, the customer choice, or reject
  end
```

A stale digest (the file changed since the verdict was shown) is refused with 409 (ADR-036).

## 6. Learning and calibration loop

```mermaid
flowchart LR
  A[Proposal<br/>system / ai] -->|person edits, answers| B[Human version]
  B -->|approve| C[learning.record_from_approval<br/>diff vs proposal]
  A -->|approve as is| C
  C --> D[(corrections<br/>alias · constant · accepted · promoted)]
  D -->|learned_for customer| E[Next proposal reads aliases & constants first]
  D -->|≥3 customers same header| F[Suggestion → admin promotes]
  E --> A
  A -->|decision + rules fired| G[calibration.outcomes<br/>accepted / corrected / rejected / auto then edited]
  G --> H[Report: precision per rule and decision]
  G --> I[Proposals: trust data checks · confidence bar ·<br/>per-customer row change · price moves]
  I -->|admin applies| J[(policy / customer overrides)]
  J --> A
```

## 7. Assistant with grounded evidence and confirmable actions

```mermaid
sequenceDiagram
  actor U as User
  participant UI as Assistant
  participant API as API
  participant A as workspace_assistant
  participant T as tools (read-only, tenant-scoped)
  participant LLM as Provider
  U->>UI: question (optionally in a round's context)
  UI->>API: POST /assistant/conversations/{id}/messages/stream
  API->>A: persist question, take lease, start run
  loop ≤ 6 turns, ≤ 2 tool calls each
    A->>LLM: structured JSON: answer / citations / calls / clarifications / report choice
    LLM-->>A: validated reply (guard, cited ids must exist, action ids from cited evidence)
    A->>T: run_tool(name, args) → evidence (bounded, truncated marked, navigation + act choices built server-side)
  end
  A->>LLM: stream final text from the selected evidence only
  A-->>UI: deltas (guarded), then actions / report / clarification card
  alt act = verdict.accept
    U->>UI: Review & confirm
    UI->>API: POST /rounds/{id}/verdict/accept {digest}
  end
```

The model never supplies URLs, chart values, code or payloads; it selects server-built ids. Reports are
computed by the server under the user's permissions and persisted as snapshots (ADR-029, ADR-040, ADR-042,
ADR-045).

## 8. State machines

### 8.1 Import round

```mermaid
stateDiagram-v2
  [*] --> uploaded
  uploaded --> profiled: profile_round
  profiled --> auto_approved: policy auto_approve
  profiled --> review: policy review
  profiled --> blocked: policy block
  review --> approved: reviewer approves / accepts verdict
  review --> rejected: reviewer rejects
  blocked --> approved: approver overrides with reason
  blocked --> rejected
  auto_approved --> transformed: transform_round
  approved --> transformed: transform_round
  transformed --> approved_data: approve_dataset (contract gate, anomalies acknowledged)
  transformed --> review: human edit of the mapping → new proposed version
  approved_data --> published: export
  uploaded --> failed: job exception (error on the round)
  profiled --> failed
  approved --> failed
  failed --> uploaded: retry
```

### 8.2 Mapping version

```mermaid
stateDiagram-v2
  [*] --> proposed: propose / human edit / answers
  [*] --> approved: policy auto_approve
  proposed --> approved: approve (supersedes the customer's other approved versions)
  proposed --> rejected
  proposed --> superseded: a newer version replaces it
  approved --> superseded: a newer version is approved (stays in the approved-format library)
```

### 8.3 Dataset version

```mermaid
stateDiagram-v2
  [*] --> draft: transform / apply_changes / rollback
  draft --> approved: approve_dataset
  approved --> published: export
  draft --> superseded: a newer version
  approved --> superseded
  published --> superseded
```

### 8.4 Bulk-upload item

```mermaid
stateDiagram-v2
  [*] --> received
  received --> identified: layout matched
  received --> needs_customer: unknown layout
  needs_customer --> identified: person assigns
  identified --> attached: round created
  attached --> processing
  processing --> needs_review: mapping needs a person
  processing --> needs_data_check: publish decision = review
  processing --> ready: published, JSON available
  received --> failed
  processing --> failed
```

### 8.5 Job

```mermaid
stateDiagram-v2
  [*] --> queued
  queued --> running: claimed atomically
  running --> done: handler finished (stages recorded)
  running --> failed: exception, attempts exhausted
  running --> queued: stale recovery
  queued --> done: reconciled on startup (durable result exists)
  failed --> queued: retry
```

## 9. Error and recovery flows

| Situation | What happens | Where |
|---|---|---|
| Model fails, times out, or output rejected by the guard | heuristic draft used, `llm_fallback` → review, never auto-approve; breaker opens after N failures; backup routes tried | ADR-013, 015, 025 |
| Wrong file for a customer | drift `unrelated_file` → block; verdict names the likely owner when the layout matches another customer and the values differ | ADR-006, 036 |
| Zero rows, missing identity | block | ADR-008 |
| Records break the contract | approval and export refused; Data Editor shows them; a save is refused only for errors it adds | ADR-026 |
| Worker dies mid-job | stale recovery re-queues; stages already recorded are skipped; dataset objects are never partial | ADR-010, 015 |
| Queue was full or Redis down at upload time | bulk items detected as stuck; Resume re-enqueues; startup reconciliation marks jobs done when results exist | ADR-027, 044 |
| Storage outage | reads fail loudly; writes are object-first so a failed write leaves no row | ADR-012, 015 |
| Wrong data published | rollback creates a new version equal to the parent; re-export with a new sha256; audit lists every step | RUNBOOK |
| Provider key invalid or exhausted | Settings › AI test fails; backups take over in order; spend and breaker on Ops | ADR-025, 032 |
| Schema version rollout | register the new version; assign per customer or as default; diff view; sandbox and policy re-run on the next round | ADR-017, RUNBOOK |
