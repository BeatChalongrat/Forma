# FORMA — System flows (systems-analysis view)

> **สรุปภาษาไทย** — เอกสารนี้เขียนแบบ SA: context diagram, use case, ลำดับการทำงานทีละขั้น (process flow),
> sequence diagram ของแต่ละเส้นทาง และ state machine ของ round / mapping / dataset / batch item / job / คำถาม
> รวมถึงการปิดรอบ (ยกเลิก ย้าย แทนไฟล์) คำถามที่รอลูกค้าตอบ ผู้ช่วย (รันโค้ด ไฟล์ในแชต ผลงาน ความจำ)
> และงานดูแลระบบ (ล้างข้อมูลเก่า สำรองข้อมูล) ทั้งหมดเขียนตามการใช้งานจริง คือคนเดียวบนเครื่องเดียว
> ทุกแผนภาพเป็น Mermaid (GitHub แสดงผลได้) แต่ละขั้นอ้าง ADR ที่เป็นเหตุผลของการออกแบบ

Diagrams are Mermaid; GitHub renders them. Each step names the module that implements it and the decision
record that explains why. FORMA has one user on one machine, so the diagrams say *person* where the code has four
roles (§2).

## 1. Context

```mermaid
flowchart LR
  C[Customers' ERPs<br/>xlsx / csv exports] -->|sent every ~14 days| P[One person<br/>uploads, reviews, approves, runs it]
  P <-->|web app on this machine, assistant| U[(FORMA)]
  U -->|validated JSON / JSONL / CSV| D[Downstream systems<br/>catalogue, pricing, stock]
  U <-->|structured JSON, pictures for models that read them| L[LLM provider<br/>OpenAI · compatible gateway · Anthropic]
  X[External MCP clients] -->|read-only tools| U
```

Everything runs in Docker on one machine: web, API, worker, PostgreSQL, Redis, MinIO, the code sandbox and the
nightly backup ([SYSTEM_ARCHITECTURE.md §7](SYSTEM_ARCHITECTURE.md#7-deployment)). Only that machine can open it
(R31). Two things in the code are not in this picture because this setup does not use them: an identity provider
(OIDC, ADR-013) and a trace collector (ADR-014). Both switch on by environment setting when FORMA moves to a server.

## 2. Use cases

```mermaid
flowchart TB
  P((One person))
  P --> receive
  P --> decide
  P --> data
  P --> assistant
  P --> care
  subgraph receive [Receive files]
    UC1[Upload one delivery]
    UC2[Upload many files at once]
    UC3[Choose a customer for an unknown layout]
    UC4[Resume stuck files]
    UC5[Cancel, move or replace a round]
  end
  subgraph decide [Decide]
    UC6[Accept a verdict]
    UC7[Answer questions from evidence]
    UC8[Park a question until the customer answers]
    UC9[Fix field matching in the Studio]
    UC10[Override a policy block with a reason]
  end
  subgraph data [Work on the data]
    UC11[Edit data, bulk edit, copilot]
    UC12[Approve data, acknowledge anomalies]
    UC13[Export / publish]
    UC14[Roll back a version]
  end
  subgraph assistant [Use the assistant]
    UC15[Ask, with cited evidence]
    UC16[Attach a picture or a file]
    UC17[Have code run: analysis, charts, Word, slides]
    UC18[See and change what it makes]
    UC19[Keep or forget what it remembers]
  end
  subgraph care [Configure and look after it]
    UC20[Register a target format]
    UC21[Connect AI, backup keys, model]
    UC22[Edit the approval policy]
    UC23[Apply a calibration proposal]
    UC24[Promote or forget a learned alias]
    UC25[Retry failed jobs, read the audit log]
    UC26[See storage, retention and the last backup]
  end
```

The code keeps four roles that nest, importer ⊂ reviewer ⊂ approver ⊂ admin, and this person holds all of them.
[USER_STORIES.md](USER_STORIES.md#เมื่อมีคนใช้เพิ่ม) says what they are for when someone joins.

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
  K -->|review| M[Review task → person accepts the verdict,<br/>answers, edits in the Studio,<br/>or parks it for the customer]
  K -->|block| N[Blocked: wrong file or zero rows<br/>override with a reason, reject, or move the round]
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

At any status a round can leave this path: cancelled, moved to another customer, or replaced by a corrected file
(§4.2).

### Step-by-step with the technique and the reason

| # | Step | Module | Technique | Why this technique |
|---|---|---|---|---|
| 1 | Store the file | `ingestion/store.py` | content-addressed sha256, immutable object, storage key on the row | Re-uploads are detected, nothing is ever overwritten, every later version can point at exactly these bytes (ADR-005, ADR-012) |
| 2 | Sniff and read | `ingestion/sniff.py`, readers, `ooxml.py` | content sniffing, strict CSV decoding with BOMs, full-sheet OOXML streaming with bounded XML | Extensions lie, CSV encodings vary (TIS-620), and silent data loss (uncached formulas, error cells, zero padding) must surface as facts, not vanish (ADR-003, ADR-043) |
| 3 | Detect tables and profile columns | `profiling/` | blank-row block detection over the whole sheet, bounded header window, a header printed again on each page dropped, type inference, hints, date order | ERP exports have titles, offsets and several tables per sheet; profiling must be descriptive so the mapper and the AI reason over facts, not raw cells |
| 4 | Fingerprint and classify drift | `fingerprint/` | strict + loose structural hashes, similarity, drift kinds; compare with the customer's approved format and with other customers' formats | Memory needs an exact key; drift needs a vocabulary a policy can reason about; a file identical to another customer's format is the strongest wrong-file signal (ADR-006) |
| 5 | Pick the mapping source | `services/mapping.py`, `format_memory.py` | memory (identical) → approved library (exact layout anywhere in history, newest first; closest compatible as adaptation baseline) → heuristic → AI | Reuse what a person approved before anything is guessed; a customer that alternates between two ERP layouts should not be re-asked (ADR-046) |
| 6 | Heuristic draft | `ai/heuristics.py` | synonym scoring (exact, containment, fuzzy), hint boosts, type compatibility, uniqueness for keys, value overlap, VAT guess from headers; learned aliases win outright; learned constants replace guesses | Deterministic, free, explainable, 0.996 on the corpus; it is the floor and the fallback, so the system never depends on a model being up (ADR-002, ADR-038) |
| 7 | AI refinement | `ai/agents.py`, `ai/llm.py`, `ai/guard.py` | structured JSON output against a schema, validation callback (DSL + static validation), retries, backup routes, output guard | The model adds judgement (nested promotions, ambiguous columns) but can only move inside the DSL; invalid or injected output is rejected and the draft is used; the full target schema is in the prompt so the model maps to the real contract (ADR-013, ADR-025, ADR-043) |
| 8 | Validate and simulate | `mapping_dsl/validate.py`, `simulate` | static validation; engine run on a bounded sample | Catch bad pointers and arity before data; give the policy rows, errors and warnings to reason about |
| 9 | Sandbox | `services/sandbox.py` | run the new and the approved mapping on the same real files; compare by schema identity | A new mapping must prove it does not lose records or break prices before it replaces the approved one; thresholds belong to the policy (ADR-018) |
| 10 | Field review | `services/healing.py` | per changed field, and per critical field of a reused mapping: equal share, steady ratio (×1.07 = VAT), scaled, different, on the same SKUs as last round's published data | A header that looks similar may mean something else, and a layout that did not move can still carry prices without VAT; the data says (ADR-021, ADR-056) |
| 11 | Field memory | `mapping.py::_inherit_confidence` | unchanged fields take confidence 1.0 from the approved mapping | A person already approved exactly this mapping of this field; asking again is noise (ADR-037) |
| 12 | Policy | `ai/policy.py` | thresholds per risk, allowed drift, warnings budget, first-time rule, row sanity, hard blocks, sandbox limits, meaning doubts, trust-data-checks, per-customer overrides | Automation is a configuration decision owned by the person running FORMA, with reasons, never a model's opinion (ADR-008, ADR-037) |
| 13 | Verdict | `services/verdict.py` | ordered hypothesis rules over the stored reports; suppression of same-rule-different-data, repeated patterns, no-data checks; confidence word; digest | A person reads one sentence and decides one to three things, with the evidence right under it (ADR-036, ADR-053) |
| 14 | Review | `review_questions.py`, `review_evidence.py`, `review_queue.py`, `ReviewWorkspace` | questions from fields under the confidence bar; answers become a new version with evidence; evidence traced to cells through engine provenance; preview on the same sample; a clock on every question, which can wait for the customer | Decisions are bounded and auditable; the person sees the actual cell, never a guess; an old question is visible and a parked one says whose turn it is (ADR-029, ADR-035, ADR-053) |
| 15 | Learning | `services/learning.py` | diff approved vs proposed (nearest system/ai ancestor) → aliases, constants, accepted; uses counted; suggestions ≥ 3 customers; a person promotes | A correction is made once per customer and once for everyone; the evidence on every learned field says where it came from (ADR-038) |
| 16 | Transform | `engine/transform.py`, `services/datasets.py` | DSL compiled to Polars expressions, vectorised records, persistent overrides re-applied, schema validation with invalid count, object-first write | Deterministic, fast, and nothing partial is visible (ADR-002, ADR-015, ADR-024, ADR-026) |
| 17 | Change intelligence | `datasets.py::change_report`, `anomalies.py` | diff vs the last published dataset; robust outliers (median/MAD); per-kind anomalies with severity; "% normal" | Reviewers need what changed and what looks wrong, not the whole table; high anomalies need an acknowledgement (ADR-022) |
| 18 | Edit | `datasets.py::apply_changes`, `assistant.py` | staged operations, impact preview, schema check that refuses only added errors, change set → new version; copilot plans, never applies | Fixes are reviewable and reversible; invalid records can be fixed one at a time (ADR-023, ADR-024, ADR-026) |
| 19 | Approve and export | `datasets.py::approve_dataset/export_dataset`, `publishing.py` | contract gate (no invalid records), anomaly acknowledgement, approver role for a manual export (a bulk round set to publish automatically exports on approval), atomic artifact with sha256, exact `export_view` fields | The downstream never receives a record that breaks the contract or a field it did not ask for (ADR-017, ADR-026) |
| 20 | Calibration | `services/calibration.py` | outcomes per decision from corrections, the version chain and change sets; precision per rule; proposals with evidence; a person applies | Thresholds are justified by what happened, and the next report shows whether a change was right (ADR-037) |

## 4. Bulk upload and automatic publishing

```mermaid
sequenceDiagram
  actor Op as Person
  participant UI as Web app
  participant API as API /batches
  participant Q as Job queue
  participant W as Worker
  participant P as Policy
  Op->>UI: drop N files (work round name, date, expected count)
  UI->>API: POST /batches (chunks of ≤10 files)
  UI-->>Op: send bar counts the files sent
  API->>API: store each file (sha256), create BatchItems, dedupe by name+sha
  API->>Q: enqueue process_batch_item × N
  UI-->>Op: navy card on every page: files finished, ready, waiting for you
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
  UI-->>Op: navy card: all ready, or what is waiting, with a link
  Op->>UI: Download all JSON (zip, one file per input)
```

Stuck items (queue full or down at upload time) are detected and resumed (`POST /batches/{id}/resume`); on
startup, queued jobs whose results already exist are reconciled instead of re-run (ADR-027, ADR-044).

The navy card (`components/UploadCard.tsx`) reads `GET /batches/{id}/summary` every 3 seconds and follows the work
round across pages. On the round's own page it goes once the work is done, because that page has it all; elsewhere it
stays until read. When every remaining file is stuck it says the queue has stopped and nothing is lost, checks every
10 seconds, and cannot be closed.

### 4.1 A new round, or a corrected file

```mermaid
flowchart TD
  A[A file arrives from a customer] --> B{Does it correct a file already uploaded?}
  B -->|no| C{One delivery or many files?}
  C -->|many files| D[Upload files<br/>each file becomes its own round]
  C -->|one delivery| E[Imports, new round<br/>the files combine into one round]
  B -->|yes| F{On the one-delivery page, and<br/>the old round is under 7 days old?}
  F -->|yes| G[Choose that round on the same page]
  F -->|no| H[Open the old round, Replace]
  G --> I[A new round takes its place, §4.2]
  H --> I
```

A customer's next delivery is always a new round. Earlier rounds stay as they are, and the approved format carries
over through mapping memory. Only a correction closes an older round. Bulk upload does not ask, because one upload
spans many customers (ADR-052). The same bytes under the same name in a work round are skipped; the same bytes
elsewhere are stored once and marked `duplicate_of`.

### 4.2 Closing a round: cancel, move, replace

```mermaid
flowchart TD
  A[A round should not count] --> B{What went wrong?}
  B -->|uploaded by mistake, not needed| C[Cancel, with a reason]
  B -->|it is another customer's file| D[Move to the right customer, with a reason]
  B -->|the customer sent a corrected file| E[Replace with the new file, with a reason]
  C --> F[Status cancelled]
  D --> G[Status moved]
  E --> H[Status superseded]
  F --> I[Open and waiting questions dismissed<br/>who, when, why and the old status kept<br/>files, versions and history stay]
  G --> I
  H --> I
  I --> J[Leaves the queues, the counts and the work round's zip]
  G --> K[New round of that customer<br/>same stored files, marked duplicate_of]
  H --> L[New round of the same customer<br/>the corrected files]
  K --> M[The work round's file row follows to the new round]
  L --> M
  M --> N[run_pipeline: the ordinary path]
  F -->|Reopen| O{Has retention removed its files?}
  O -->|no| P[Back to the status it had<br/>its questions open again]
  O -->|yes| Q[Refused: upload again as a new round]
```

Nothing is deleted. Cancel can be undone; move and replace cannot, because a successor took the old round's place and
the two pages link to each other. A moved round runs the ordinary pipeline for its new customer, so a first-time
customer still gets a review. An approved mapping stays approved when its round is cancelled: approval is a fact about
the layout, not the delivery. Replacing a published round withdraws its JSON from the zip before the new one exists,
and the counts say so (`services/round_lifecycle.py`, ADR-052).

## 5. Review and verdict

```mermaid
sequenceDiagram
  actor R as Person
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
    API->>M: approve_mapping (block refused, audit verdict.accept with rules_fired)
  else outcome = answer
    R->>UI: answer the 1–3 decisions (yes/no, number, confirm as read)
    UI->>API: POST /rounds/{id}/verdict/accept {digest, decisions}
    API->>M: answer_document → apply_answers → new proposed version → policy again
  else fix_mapping / assign_customer / reject_file
    UI-->>R: link to the Studio, the customer choice, or reject
  else the customer has to answer first
    R->>UI: Wait for the customer (a note and a date)
    UI->>API: POST /review/{id}/wait
    API-->>UI: leaves the queue, comes back on that day
  else it is another customer's file
    R->>UI: Move to the right customer (matched customer already chosen)
    UI->>API: POST /rounds/{id}/move
    API-->>UI: a new round of that customer runs
  end
```

A stale digest (the file changed since the verdict was shown) is refused with 409 (ADR-036). Accepting while
decisions are listed is refused with 409 too, so the card has to be answered first (ADR-047). The states of a question
are in §8.6, and moving a round is in §4.2.

## 6. Learning and calibration loop

```mermaid
flowchart LR
  A[Proposal<br/>system / ai] -->|person edits, answers| B[Human version]
  B -->|approve| C[learning.record_from_approval<br/>diff vs proposal]
  A -->|approve as is| C
  C --> D[(corrections<br/>alias · constant · accepted · promoted)]
  D -->|learned_for customer| E[Next proposal reads aliases & constants first]
  D -->|≥3 customers same header| F[Suggestion → a person promotes]
  E --> A
  A -->|decision + rules fired| G[calibration.outcomes<br/>accepted / corrected / rejected / auto then edited]
  G --> H[Report: precision per rule and decision]
  G --> I[Proposals: trust data checks · confidence bar ·<br/>per-customer row change · price moves]
  I -->|a person applies| J[(policy / customer overrides)]
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
  U->>UI: question, with files or a picked part of a work (optionally in a round's context)
  UI->>API: POST /assistant/conversations/{id}/messages/stream
  API->>A: persist question, read memory notes, take lease, start run
  loop ≤ 6 turns (≤ 12 with the sandbox on), ≤ 2 tool calls each
    A->>LLM: structured JSON: answer / citations / calls / clarifications / report choice
    LLM-->>A: validated reply (guard, cited ids must exist, action ids from cited evidence)
    alt a read-only tool
      A->>T: run_tool(name, args) → evidence (bounded, truncated marked, navigation + act choices built server-side)
    else run_python
      A->>A: run in the sandbox, §7.1
    else save_flow
      A->>A: keep a flow, §7.3
    else remember, search_chats, recent_chats
      A->>A: keep a note or read earlier chats, §7.4
    end
  end
  A->>LLM: stream final text from the selected evidence only
  A-->>UI: deltas (guarded), then actions / report / clarification card
  API->>API: settle_turn: files and flows become works, previews made
  API-->>UI: the works of this message
  alt act = verdict.accept
    U->>UI: Review & confirm
    UI->>API: POST /rounds/{id}/verdict/accept {digest}
  end
```

The model never supplies URLs, report values or action payloads; it selects server-built ids. Reports are computed
by the server under the user's permissions and persisted as snapshots (ADR-029, ADR-040, ADR-042, ADR-045). The code
it writes runs only in the sandbox, and the result stops at the chat (§7.1).

### 7.1 Running code in the sandbox

```mermaid
sequenceDiagram
  participant LLM as Provider
  participant A as workspace_assistant
  participant R as code_sandbox
  participant ST as Storage
  participant S as Sandbox container
  LLM-->>A: run_python (code, datasets, files)
  A->>R: run
  R->>R: check the arguments and each dataset and file against access
  R->>ST: open copies: datasets, source files, chat files, earlier outputs
  R->>S: code and inputs over one Unix socket
  S->>S: run as uid 20000, 60 s, 2.5 GB, no network, no secrets
  S-->>R: stdout, stderr, outputs/, look/
  R->>ST: keep outputs/ as files of this chat
  R->>R: audit assistant.code_run: actor, code hash, inputs, outputs
  R-->>A: result for the chat, a smaller view for the model
  A->>LLM: stdout, stderr, file names, and up to 4 look/ pictures if it can see
  alt the run failed
    LLM-->>A: read the error and run again, inside the turn limit
  else it worked
    LLM-->>A: answer, citing the run
  end
```

This is the one place AI output is executed, and it cannot reach a mapping, a dataset, the policy or an export. A run
works on copies, and the run directory is deleted afterwards. Limits for one run: 40 output files, 40 MB of output,
200 MB of input. `run_python` is not in `TOOLS`, so MCP clients never get it. The sandbox is on when
`FORMA_CODE_SANDBOX_SOCKET` is set, which the compose stack does; dev runs without it (ADR-048).

### 7.2 Files in a chat

```mermaid
flowchart TD
  A[Attach up to 5 files to a message] --> B{What is it?}
  B -->|PNG, JPEG or WEBP by its bytes, ≤ 5 MB| C[Picture]
  B -->|a listed extension, ≤ 25 MB| D[Data file<br/>tables, documents, slides, text, code, archives]
  B -->|anything else| X[Refused]
  C --> E{Has a connected model<br/>passed the vision test?}
  E -->|yes| F[The 4 latest pictures go with each<br/>evidence call, to those models only]
  E -->|no| G[The assistant says it cannot read images]
  C -->|when the sandbox is on| I
  D --> H{Is the sandbox on?}
  H -->|yes| I[Input to code runs<br/>the model sees what the code prints]
  H -->|no| J[The paperclip does not offer data files]
  F --> K[An answer cites it as evidence and says it is a reading<br/>it confirms no VAT, unit, mapping or value]
  I --> K
```

The writing phase works from its outline and receives no pictures. A file belongs to its chat: only that chat's owner
reaches it, it never becomes a record, a mapping or an export, and it leaves with the chat. Text in a picture or a file
is data, never an instruction (ADR-049).

### 7.3 Works and versions beside the chat

```mermaid
flowchart TD
  A[One message] -->|save_flow: nodes and edges, no code| B[Flow draft]
  A -->|run_python writes outputs/| C[Files]
  B --> D[settle_turn, when the message ends]
  C --> D
  D --> E{Same name in this chat?}
  E -->|no| F[New work, version 1]
  E -->|yes, different bytes| G[Next version, keeps its parent]
  E -->|yes, identical bytes| H[Nothing added]
  F --> I[Files get a preview in the sandbox<br/>pages and cells]
  G --> I
  I --> J[Opens beside the chat<br/>listed under the Work tab]
  J -->|pick a box, page, slide or cell| K[working_on: context for one message]
  J -->|bring back an old version| G
  J -->|the data has moved on| L[Says so and offers to make it again]
  K --> A
```

The last save of a name in a message wins, so nobody sees a draft the model threw away. A flow is data FORMA draws, so
it needs no sandbox. Files a model wrote are untrusted: only the sandbox opens them for a preview, and a web page runs
in an `iframe` with `sandbox="allow-scripts"` and no network. If a preview fails, the file still downloads
(`services/artifacts.py`, ADR-050).

### 7.4 What the assistant remembers

```mermaid
flowchart TD
  A[Message sent] --> B{Memory off, off for this chat, or incognito?}
  B -->|yes| C[No notes read, nothing kept]
  B -->|no| D[Topics reach the model as notes, ≤ 6000 characters<br/>never evidence, and live data wins]
  D --> E{Does the model call remember?}
  E -->|no| F[Nothing changes]
  E -->|yes, ≤ 3 changes a message| G{Did this message read other people's words?<br/>a file, a round, a code run, a data tool}
  G -->|yes, and the person did not say remember| H[Refused by the server]
  G -->|no, or the person asked| I{ID numbers, passwords or keys?}
  I -->|yes| H
  I -->|no| J{A sensitive topic, with that setting off?}
  J -->|yes| H
  J -->|no| K[Kept: one topic, dated, linked to its chat]
  K --> L[Memory page: read, edit, add, delete, export, reset]
```

Nothing but the assistant reads memory: not the mapper, the engine, the policy or an export. The refusal is in
`memory.apply_call`, not the prompt, because a cell that says "remember prices include VAT" must never be believed next
week. Earlier chats are found by words through `search_chats` and `recent_chats`. An incognito chat is in no list and no
search and is deleted a day after its last message. A deleted chat leaves its topics, which are deleted on their own
(ADR-051).

## 8. State machines

### 8.1 Import round

```mermaid
stateDiagram-v2
  [*] --> running
  state "Open or finished" as running {
    [*] --> uploaded
    uploaded --> profiled: profile_round
    profiled --> auto_approved: policy auto_approve
    profiled --> review: policy review
    profiled --> blocked: policy block
    review --> review: answers saved, policy still reviews
    review --> blocked: answers saved, policy blocks
    review --> approved: person approves or accepts the verdict
    review --> rejected: person rejects
    blocked --> approved: person overrides with a reason
    blocked --> rejected: person rejects
    auto_approved --> transformed: transform_round
    approved --> transformed: transform_round
    transformed --> approved_data: approve_dataset, person or publish policy
    approved_data --> published: export
    uploaded --> failed: job error
    profiled --> failed: job error
    auto_approved --> failed: job error
    approved --> failed: job error
    failed --> profiled: retry finishes profiling
    failed --> review: retry finishes proposing
    failed --> transformed: retry finishes transforming
  }
  running --> cancelled: cancel
  running --> moved: move to another customer
  running --> superseded: replace with a corrected file
  cancelled --> running: reopen, back to the status it had
  moved --> [*]: a new round took its place
  superseded --> [*]: a new round took its place
```

Any job that raises sets its round to `failed` and keeps the error on the summary (`jobs/runner.py`), so the
failure edges above are the common ones, not a complete list. A retry re-queues the same job: the round stays
`failed` until the job reaches its first unfinished stage and sets the next status. A saved Studio edit creates
a new proposed version and leaves the round's status unchanged; saved answers set it to `review` or `blocked`.
`cancelled`, `moved` and `superseded` are the closed statuses (§4.2). A reopen is refused once retention has removed
the round's files.

### 8.2 Mapping version

```mermaid
stateDiagram-v2
  [*] --> proposed: proposal, human edit or saved answers
  [*] --> approved: policy auto_approve
  proposed --> approved: approve_mapping
  proposed --> rejected: reject_mapping
  proposed --> superseded: a human edit or saved answers replace it
  approved --> rejected: reject_mapping
  approved --> superseded: another version of the customer is approved
```

`approve_mapping` accepts only proposed or approved versions. `reject_mapping` has no status check. A superseded
version that was once approved stays in the approved-format library (ADR-046); other superseded and rejected
versions are never reused.

### 8.3 Dataset version

```mermaid
stateDiagram-v2
  [*] --> draft: transform, change set or rollback
  draft --> approved: approve_dataset
  draft --> superseded: a change set creates a newer version
  approved --> published: export
  approved --> superseded: another version of the round is approved
  published --> superseded: another version of the round is approved
  draft --> purged: retention
  superseded --> purged: retention
```

A rollback creates a new draft and leaves the version it came from unchanged. Retention removes the JSONL object
of old draft and superseded versions and marks the row `purged`; approved and published versions and the parents
of kept versions are kept (`policies/retention.yaml`).

### 8.4 Bulk-upload item

```mermaid
stateDiagram-v2
  [*] --> open_item
  state "Open" as open_item {
    [*] --> processing: file stored
    [*] --> failed: unreadable at upload
    processing --> needs_customer: no format match or a contradiction
    needs_customer --> processing: person assigns a customer
    processing --> needs_review: mapping needs a person
    needs_review --> processing: mapping approved
    processing --> needs_data_check: publish decision is review
    needs_data_check --> processing: data approved
    processing --> ready: JSON exported
    processing --> failed: pipeline error
  }
  open_item --> closed: round cancelled
  closed --> open_item: round reopened
  open_item --> closed: retention, a file that never became a round
```

These are the states a file row shows (`batches.item_state`). They are derived from the stored item status
(`received`, `identified`, `needs_customer`, `attached`, `failed`, `purged`) and, once attached, from the round's
status. A moved or replaced round hands its row to the new round, so `closed` is only ever seen for a cancelled round
or a file retention removed. A closed file counts as done, and its JSON is not in the zip.

### 8.5 Job

```mermaid
stateDiagram-v2
  [*] --> queued
  queued --> running: claimed atomically
  running --> done: handler finished, stages recorded
  running --> failed: handler error
  running --> queued: stale worker, attempts left
  running --> failed: stale worker, attempt limit reached
  queued --> done: reconciled on startup, durable result exists
  failed --> queued: retry
```

### 8.6 Review question

```mermaid
stateDiagram-v2
  [*] --> open: policy review or block, a human edit, saved answers
  open --> waiting: wait, with a note and a date
  waiting --> open: brought back by hand
  waiting --> open: its day comes, actor clock
  open --> approved: mapping approved
  waiting --> approved: mapping approved
  open --> rejected: mapping rejected
  waiting --> rejected: mapping rejected
  open --> dismissed: round closed, or a new version replaces it
  waiting --> dismissed: round closed, or a new version replaces it
  dismissed --> open: cancelled round reopened
  approved --> [*]: retention, 365 days
  rejected --> [*]: retention, 365 days
  dismissed --> [*]: retention, 365 days, unless the round is cancelled
```

Waiting says whose turn it is and decides nothing about the file, and the policy never sees it. A waiting question
leaves the person's queue and every count. Reading the queue or the dashboard wakes what is due
(`review_queue.wake_due`), so there is no timer to fail. An open question older than 7 days is flagged on the home
page, and foresight lists it, along with customers whose answer is late. A question that was waiting when its round was
cancelled comes back as `open` if the round is reopened (ADR-053).

## 9. Error and recovery flows

| Situation | What happens | Where |
|---|---|---|
| Model fails, times out, or output rejected by the guard | heuristic draft used, `llm_fallback` → review, never auto-approve; breaker opens after N failures; backup routes tried | ADR-013, 015, 025 |
| Wrong file for a customer | drift `unrelated_file` → block; verdict names the likely owner when the layout matches another customer and the values differ | ADR-006, 036 |
| Zero rows | block | ADR-008 |
| Identity missing on some rows | review, with a high-priority reason (the `on_missing_identity` switch does not block) | ADR-008 |
| A report printed page by page | one table: the repeated headers and the title lines above them are dropped, and every row is kept | ADR-055 |
| A second price sheet, or a hidden one | the first source is used and a question says what was left out; a document mapping a collection twice is refused | ADR-055 |
| A currency or date order the file does not state plainly | a currency column is read and a date past the 12th settles the order; otherwise a question, never a quiet guess | ADR-055 |
| Prices with the VAT taken out, or in satang, in a file whose layout did not change | the reused mapping is still checked on last round's values: a steady factor on every SKU is a question, not a pass | ADR-056 |
| One SKU twice with different values | an engine error that holds the round; identical duplicates stay a warning | ADR-056 |
| A status or promotion kind FORMA has no word for | kept as written and refused by the contract's enum, so it reaches a person; never active or percent_off by default | ADR-056 |
| A column moved to another sheet | `columns_moved`: the approved mapping is not reused; the adaptation agent re-points it and a person reviews | ADR-057 |
| A model writes an option the engine does not know (`day_first`) | repaired when it is a known spelling, otherwise refused by the validator and sent back to the model — never ignored | ADR-057 |
| Records break the contract | approval and export refused; Data Editor shows them; a save is refused only for errors it adds | ADR-026 |
| Worker dies mid-job | stale recovery re-queues; stages already recorded are skipped; dataset objects are never partial | ADR-010, 015 |
| Queue was full or Redis down at upload time | bulk items detected as stuck; Resume re-enqueues; startup reconciliation marks jobs done when results exist | ADR-027, 044 |
| Storage outage | reads fail loudly; writes are object-first so a failed write leaves no row | ADR-012, 015 |
| Wrong data published | rollback creates a new version equal to the parent; re-export with a new sha256; audit lists every step | RUNBOOK |
| Provider key invalid or exhausted | Settings › AI test fails; backups take over in order; spend and breaker on Ops | ADR-025, 032 |
| Queue stopped while files wait | the navy card and the round page say so, and nothing is lost; Resume sends the files again | ADR-027, 044 |
| A file uploaded by mistake, or under the wrong customer | cancel the round, or move it with the same stored file; the verdict's wrong-file card opens the move with the customer chosen | ADR-052 |
| The customer corrects a delivery | replace the round; the zip and the counts show the new one | ADR-052 |
| A cancelled round is reopened after retention removed its files | refused, with what to do instead: upload again as a new round | ADR-052 |
| A question sits for too long | past 7 days it is flagged on the home page and in foresight; a customer's overdue answer is flagged the same way | ADR-053 |
| A round stops moving | after 60 days with no activity it is flagged on the home page and never closed by itself | ADR-053 |
| Sandbox not running | `run_python` is not offered, or the call says it could not be reached; the rest of the assistant keeps working | ADR-048 |
| A code run fails or times out | the model reads the error and tries again, inside the turn limit; nothing reaches a record | ADR-048 |
| A picture with no model that reads images | the assistant says it cannot read it, and no backup answers as if it had | ADR-049 |
| A preview of a work fails | the file stays downloadable, and the panel says so | ADR-050 |
| Retention off, or the backup is old | System health shows the last pass and the backup's age, amber past a day | ADR-053 |
| A backup fails or is cut off | it leaves nothing with a backup's name, older backups stay, and System health turns amber after a day | ADR-054 |
| The last backup holds no files | System health says database only, in amber | ADR-054 |
| Schema version rollout | register the new version; assign per customer or as default; diff view; the next round has no approved mapping under that version, so it is proposed like a first-time format, with no sandbox or field review | ADR-017, ADR-046, RUNBOOK |

## 10. Keeping the system tidy: retention and the nightly backup

```mermaid
flowchart TD
  A[API and worker start] --> B[Retention schedule<br/>FORMA_RETENTION_INTERVAL_H]
  B -->|first pass within a minute, then each interval| C{Did a pass run in the last half interval?}
  C -->|yes| B
  C -->|no| D[Plan: what is past its days<br/>policies/retention.yaml]
  D --> E{FORMA_RETENTION_APPLY}
  E -->|false: counts only| G[Audit row retention.run]
  E -->|true| F[Delete objects, rows become purged]
  F --> G
  G --> H[System health:<br/>last pass, storage used, last backup]
  I[backup-nightly container] -->|every 24 hours| J[forma backup: database and every file]
  J --> K[(backups volume, 14 days)]
  J --> L[Audit row backup.create]
  L --> H
```

| What | Kept for | Stays regardless |
|---|---|---|
| Source files | 90 days after the round was published, rejected, blocked or closed | files of an open round, and bytes another round or a file waiting for a customer still uses |
| Work-round files that never became a round | 90 days after upload | the row, as `purged`, and bytes something else uses |
| Dataset versions | the last 5 per round | approved and published versions, and the parents of kept versions |
| Exports | 180 days | the dataset version, which can be exported again |
| Resolved questions | 365 days | those of a cancelled round, because a reopen brings them back; the audit log keeps every decision |
| Job rows | 30 days | |
| AI call rows | 365 days | |
| S3 local cache | 7 days | |

Retention never closes work: a round nobody has touched for 60 days is flagged on the home page and left to the person.
`FORMA_RETENTION_APPLY` defaults to false, so a pass only counts what it would delete until it is switched on. The
compose bucket is versioned, so a delete keeps the bytes on disk until a lifecycle rule expires them (R37). Chat files
stay out of retention and leave with their chat. The nightly backup holds the database and every object, and a restore
has been proven, but it stays on this machine ([RUNBOOK.md](RUNBOOK.md#who-can-reach-the-stack), R36, ADR-015, ADR-054).
