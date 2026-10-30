# FORMA — User stories, every case, and the technique behind each step

> **สรุปภาษาไทย** — เอกสารนี้รวบรวม user story ของทุกบทบาท (importer, reviewer, approver, admin) ครอบคลุม
> ทั้งเส้นทางปกติและกรณีขอบ (ไฟล์ผิด, format เปลี่ยน, AI ล่ม, ข้อมูลผิด contract, งานค้าง, สิทธิ์ไม่พอ ฯลฯ)
> ทุก story มี acceptance criteria และตารางว่าแต่ละขั้นตอน "ใช้เทคนิคอะไร เพราะอะไร" พร้อม ADR อ้างอิง
> ใช้เป็นฐานของ test plan และของการเล่าให้ทีม/ลูกค้าฟังว่าระบบคิดอย่างไร

Format: **As a** role, **I want** …, **so that** …. Each story has acceptance criteria (AC), the edge cases
it covers, and a table of the techniques used at each step with the reason. Roles nest: importer ⊂ reviewer
⊂ approver ⊂ admin. Tests that cover each story are named so QA can trace them.

## Personas

| Persona | Role | Goal | Pain today |
|---|---|---|---|
| **Nok**, import operator | importer | Get every customer's fortnightly files into the system with the least clicking | 300 files, many layouts, no idea which need a person |
| **Pim**, data reviewer | reviewer | Decide quickly and correctly what needs a human; never approve something wrong | Evidence everywhere, conclusion nowhere |
| **Ton**, team lead / approver | approver | Publish only correct data; justify every exception; register new contracts | Blocks and overrides must be explainable to downstream teams |
| **Dao**, platform admin | admin | Keep AI, policy and formats configured; know how often FORMA is right | Thresholds set once, never questioned; AI cost opaque |
| **Downstream team** | consumer of the JSON | Receive exactly the contract, every time | Partial, drifting or renamed fields |

---

## Epic A — Receiving files

### A1. Upload one delivery
**As** Nok **I want** to upload one or more files of a customer as one round **so that** FORMA reads them
and tells me what happens next.

AC: files are stored once (a re-upload of identical bytes is marked duplicate, not re-stored); every sheet is
read; the round shows its five steps and FORMA's conclusion; unreadable files fail loudly on the round, not
silently as empty data.

Edge cases: xlsx with a title block and the header on row 7 · CSV in TIS-620 · an xlsx whose formulas have no
cached values (transformation refused, review forced) · legacy .xls (review forced) · a file over the size cap
(413) · an encrypted workbook (unsupported, with guidance).

| Step | Technique | Why |
|---|---|---|
| Store | sha256 content addressing, storage key on the row | immutability and duplicate detection (ADR-005, ADR-012) |
| Sniff | content sniffing, strict CSV decoding with BOMs | extensions lie; a decode error must not become `?` characters (ADR-013, ADR-043) |
| Read | fastexcel for speed, openpyxl for structure facts, full-row OOXML scan | speed for 100K rows; hidden sheets, merged ranges and uncached formulas are facts the policy needs (ADR-003, ADR-043) |
| Profile | full-sheet blank-row block detection, bounded header window, type inference, hints | multiple tables per sheet and long preambles are normal in ERP exports |

Tests: `test_api.py::test_full_flow`, `test_ingestion_readiness.py`, `test_profiling.py`, `test_guard_sniff.py`.

### A2. Upload many files at once (bulk)
**As** Nok **I want** to drop all of tonight's files in one go **so that** each is matched to its customer
and comes out as its own JSON without me opening each one.

AC: each file becomes a batch item; a known layout is matched to its customer (exact, same columns moved,
similar ≥ 0.75 with a margin over the runner-up); a customer code in the file name confirms the match, names
the customer when the layout is unknown, or contradicts (then the file waits for a person); files pass through
mapping, data creation and publishing automatically when every check passes; one JSON per input file; all in
one zip.

Edge cases: two customers with the same ERP layout (ambiguous → person) · a customer with two historical
layouts (not ambiguous: one customer) · the queue is full at upload time (items marked stuck, Resume) · the
same file uploaded twice into a work round (skipped by name + sha256) · the browser loses a later chunk (round
and file list stay, retry).

| Step | Technique | Why |
|---|---|---|
| Identify | fingerprint vs every approved format in history, ranked per distinct customer; file-name code as a confirming signal | the layout is the strongest evidence, the name a cheap confirmation; history matters because customers alternate layouts (ADR-027, ADR-046) |
| Attach | one round per file, profile reused, row lock on the item | 1 file = 1 JSON; no double processing under concurrent workers |
| Publish | `PolicyEngine.evaluate_publish` (invalid records, engine errors, high anomalies) | automation is a policy decision with reasons, never implicit (ADR-027) |
| Resume / reconcile | stuck detection (no job younger than 120 s), startup reconciliation against durable results | the queue can be down; results that exist must not be produced twice (ADR-044) |

Tests: `test_batches.py`, `test_job_recovery.py`.

### A3. Choose a customer for an unknown layout
**As** Nok **I want** to assign the files FORMA could not place to an existing or new customer **so that**
processing continues.

AC: candidates with similarity are shown; assigning re-enqueues the items; a tenant-scoped user cannot assign
across customers (403).

Tests: `test_batches.py::…unknown…`, `test_auth.py`.

### A4. Work rounds
**As** Nok **I want** a named, dated round I can add files to later and compare against an expected count
**so that** I can see "received 280 of 300, 270 ready".

AC: rename, date and expected target editable (importer, all-customer access); dashboard on Home and the
round; status counts link to filtered files; unknown targets are labelled unknown, never "missing".

Tests: `test_batches.py::test_work_round_date_append_and_retry`, `test_workspace.py`.

---

## Epic B — Deciding what a file means

### B1. A returning customer with an unchanged layout
**As** Pim **I want** a file whose layout is identical to the approved one to pass without me **so that** I
only see what changed.

AC: the approved mapping is reused verbatim (`memory`), confidence 1.0, policy auto-approves, verdict `clean`.

| Technique | Why |
|---|---|
| strict/loose fingerprint equality → reuse | a person approved exactly these rules for exactly this layout (ADR-006) |

Tests: `test_api.py` sequences, `test_heuristics_eval.py` (drift 9/9).

### B2. A renamed or moved column
**As** Pim **I want** FORMA to adapt the mapping and prove on the data that the renamed column still means
the same **so that** I accept with one click instead of re-mapping.

AC: drift `columns_renamed`/`header_moved`/`columns_removed`; the new draft (or the AI adaptation) is checked
field by field against last round's published values on the same SKUs; when every important field keeps its
values, the verdict is `rename_only` → **Accept** with one click; with `sandbox.trust_data_checks` on, the
policy auto-approves it outright.

| Technique | Why |
|---|---|
| adaptation agent with the approved mapping + drift report + fresh draft | keep business decisions (VAT, value maps), re-point only what moved (ADR-021) |
| data check: equal share, steady ratio (×1.07 = VAT), scaled, different | the header is a hint; the values decide (ADR-021) |
| sandbox delta interpreted in the light of the data check | the delta describes the *approved* mapping breaking on the new layout, not a risk in the proposal (ADR-037) |

Tests: `test_healing.py`, `test_sandbox.py`, `test_verdict.py`, `test_calibration.py::…renamed…`.

### B3. A column that changed meaning
**As** Pim **I want** to be told when a renamed column no longer holds the same values **so that** I fix the
mapping instead of publishing wrong prices.

AC: data check `different` on a critical/high field → policy review with "meaning check" reason; verdict
`meaning_changed` names the field and the share of SKUs that kept their value; link to that field in the Studio.

Tests: `test_healing.py::test_data_check[different]`, `test_policy.py::…meaning_checks…`.

### B4. VAT included or excluded
**As** Pim **I want** FORMA to tell me when a price column moved by exactly 7 % **so that** I confirm the VAT
convention with one yes/no.

AC: data check `vat_added`/`vat_removed`; verdict `vat_shift` with one boolean decision and the Thai retail
default stated as a default, not a fact; the answer becomes a new mapping version with evidence.

| Technique | Why |
|---|---|
| steady-ratio detection with the named constant `VAT_RATE` | VAT is the most common silent price change in Thai price lists (ADR-007, ADR-021) |
| answer → `ConstExpr`, confidence 1.0, open question retired | the decision is bounded and auditable (ADR-029) |

Tests: `test_workspace.py::test_answer_preview_versions_and_policy`, `test_verdict.py::test_vat_shift…`.

### B5. A wrong file (another customer's export)
**As** Pim **I want** a file that belongs to another customer to be stopped and named **so that** it is never
published under the wrong customer.

AC: layout `unrelated_file` to this customer → block; when it matches another customer's approved format and
the values on the shared SKUs differ, the verdict is `wrong_customer`: "This file is probably C901's, not
C902's", outcome *move to the right customer*, no questions asked; approving a block needs an approver and a
reason (audited).

| Technique | Why |
|---|---|
| other-customer fingerprint match + identity-matched data checks with constants and identity keys excluded | the layout alone is weak (shared ERPs); layout + different values on the same SKUs is strong (ADR-036) |
| block as an explicit override path | a wrong file must never be an ordinary approval (ADR-008) |

Tests: `test_api.py::test_file_matching_another_customer…`, `test_verdict.py::test_wrong_file_is_named…`.

### B6. A first-time customer
**As** Pim **I want** a new customer's first file to come to me with the few fields FORMA is unsure about
**so that** I confirm them once.

AC: policy review ("first time"); verdict `first_time` lists ≤ 3 decisions; after approval the mapping is the
customer's memory and the format library entry.

Tests: `test_api.py::test_full_flow`, `test_verdict.py::test_first_time_and_clean`.

### B7. A new customer whose layout equals another customer's
**As** Pim **I want** to be told the layout is shared (same ERP), not accused of a wrong file.

AC: verdict `first_time_shared_layout`, outcome *answer*, sender confirmed in passing.

### B8. The AI is unavailable
**As** Pim **I want** FORMA to keep working without AI and to tell me it did **so that** nothing stops and
nothing is silently trusted.

AC: heuristic draft used; `llm_fallback` → review, never auto-approve; verdict `ai_unavailable`; breaker open
on Ops; backup keys tried in order first.

| Technique | Why |
|---|---|
| deterministic floor + circuit breaker + backup routes | availability without lowering the bar (ADR-015, ADR-025) |

Tests: `test_failure_injection.py`, `test_backup_keys.py`.

### B9. The model returns something unsafe
**As** Dao **I want** injected or malformed model output rejected **so that** a cell saying "ignore the rules"
cannot change a mapping.

AC: guard rejects instruction-like text and pointers outside the schema; the draft is used; the round goes to
review; `test_guard_sniff.py`, `test_workspace.py::test_assistant_rejects_injected_output`.

### B10. Edit the mapping by hand
**As** Pim **I want** to drag a column onto a field or connect two sheets in the Studio **so that** I fix a
mapping without writing JSON.

AC: every save is a new proposed version with parent; validation, simulation, sandbox and policy run again;
a wrong-file block cannot be cleared by an edit; relationships are measured on the data before a join is added.

Tests: `test_sandbox.py::test_an_edit_that_makes_data_worse_is_blocked`, `test_relationships.py`.

### B11. The mapping is learned from my correction
**As** Pim **I want** the column I re-pointed last round to be mapped right this round **so that** I never
correct the same thing twice.

AC: the approved version is diffed against FORMA's proposal; the alias is stored for the customer; the next
proposal maps it with evidence "learned: you mapped 'col_17' here on …"; a confirmed constant replaces the
guess and drops its question; another customer does not inherit it; the Settings page shows corrections per
approval falling.

Tests: `test_learning.py`.

### B12. An alias several customers taught becomes everyone's
**As** Dao **I want** to promote a header three customers mapped the same way **so that** the fourth customer
gets it on day one.

AC: suggestion listed when ≥ 3 distinct customers taught it and it is not already a synonym; promote (admin,
audited); forget retires it.

Tests: `test_learning.py::test_accepted_as_proposed_is_recorded_and_promotion_shares_an_alias`.

---

## Epic C — The data

### C1. Create the data and see what changed
**As** Pim **I want** the full dataset with a change report against last round **so that** I check what moved,
not every row.

AC: SKUs added/removed, changed fields, price moves (large ones flagged), promotion changes; anomalies within
the round, vs last round, vs usual size with "% normal"; data-stage verdict (`contract_broken`, `price_move`,
`anomalies`, `publishable`).

| Technique | Why |
|---|---|
| diff by schema identity; robust outliers (median/MAD) | means and standard deviations are dragged by the outliers themselves (ADR-022) |

Tests: `test_anomalies.py`, `test_datasets.py`.

### C2. Fix records that break the contract
**As** Pim **I want** to see the records that break the target schema and fix them one at a time **so that**
nothing invalid is ever exported.

AC: approval and export refused while `invalid_records` > 0 (409 with the errors); the Data Editor lists
them; a save is refused only for errors it *adds*; a deleted invalid record is a fix too.

Tests: `test_schema_gate.py`.

### C3. Edit like a spreadsheet
**As** Pim **I want** to paste from Excel, select ranges and bulk-edit picked rows **so that** fixes take
seconds.

AC: staged operations, impact preview (rows, cells, before/after), new version with a change set; persistent
edits become overrides for every future round and collide loudly with a new mapping.

Tests: `test_datasets.py`, `test_overrides.py`.

### C4. Ask the copilot
**As** Pim **I want** to type "ลดราคาหมวด X 5 % เฉพาะที่สต็อกเกิน 100" **so that** the system plans the
operations and shows me the impact before anything is applied.

AC: a plan, never an application; conditions across collections via derived figures; ambiguity → a
clarification in Thai; "why" questions answered from lineage and join evidence.

Tests: `test_assistant.py` (copilot), `test_lineage.py`.

### C5. Trace a value to its cell
**As** Pim **I want** to click a value and see file, sheet, row, column, raw cell and the rule that produced
it, plus why it differs from last round **so that** I trust what I publish.

Tests: `test_lineage.py`, `test_review_evidence.py`.

### C6. Approve and export
**As** Ton **I want** to approve data (acknowledging high anomalies explicitly) and export JSON/JSONL/CSV with
exactly the contract's fields **so that** the downstream gets what it asked for.

AC: export needs approved data (409 otherwise); atomic write; sha256 recorded; `export_view` drops internal
keys; a bulk round exports automatically after a manual approval.

Tests: `test_export.py`, `test_api.py::test_export_needs_approved_data…`.

### C7. Roll back
**As** Pim **I want** to roll back a dataset to its parent **so that** a wrong edit is undone without history
being rewritten.

AC: rollback = new version equal to the parent; audit lists it; re-export yields a new artifact.

---

## Epic D — The verdict and the review

### D1. Read one sentence, decide one thing
**As** Pim **I want** each file to open with FORMA's conclusion, confidence and the decisions only I can
make **so that** I am not reading 23 tables.

AC: verdict card on the Review page, the Round page, the queue rows, Home and batch rows; evidence lines link to
the page that proves them; "folded away" says what was hidden and why; the same stored reports always give
the same verdict (digest).

| Technique | Why |
|---|---|
| ordered hypothesis rules over stored reports; suppression (same rule + different data = data change) | deterministic, explainable, cheap; a model may narrate later, never decide (ADR-036) |
| confidence as a word, number kept for eval | words are honest at this stage; calibration measures them (ADR-036, ADR-037) |

Tests: `test_verdict.py`.

### D2. Accept with one click
**As** Pim **I want** to accept a verdict with one click **so that** clear cases take seconds.

AC: `POST /rounds/{id}/verdict/accept` goes through `approve_mapping`/`apply_answers`/`approve_dataset`;
a stale digest is refused; a block is never acceptable this way; `verdict.accept` is audited with the rules
that fired and the decisions that differed from the defaults.

### D3. Understand why a file is in review
**As** Pim **I want** policy reasons in plain Thai or English with counts in their real scope **so that** I
do not need to read raw diagnostics.

AC: deterministic bilingual explanations; raw diagnostics under a disclosure (ADR-042).

Tests: `test_review_assistant.py`.

---

## Epic E — Configuration and governance

### E1. Register the real target format
**As** Ton **I want** to paste the downstream JSON Schema, tag semantics and synonyms, check it, and register
it **so that** mapping, validation and export follow the real contract without a code change.

AC: validity, mapping findings and diff to the previous version before registering; default or per customer;
unsupported shapes refused with a reason; exports carry exactly the schema's fields.

Tests: `test_registry.py`, `test_contract_readiness.py`, `test_config.py`.

### E2. Connect AI with fail-over
**As** Dao **I want** to paste a key, pick a model, add backup keys and test every one **so that** a provider
outage does not stop the night.

AC: key stored encrypted; applied without restart; per-route breaker; backups tried in order; model discovery
admin-only and rate-limited; the saved key is never forwarded to a changed endpoint.

Tests: `test_backup_keys.py`, `test_model_discovery.py`, `test_config.py`.

### E3. Edit the policy and see whether it is right
**As** Dao **I want** to edit thresholds in the web app, see precision per rule, and apply the proposals the
evidence supports **so that** automation grows without lowering the bar.

AC: validated, audited policy edits; calibration report; proposals with evidence and the reviews they would
have saved; apply with a digest (stale refused); per-customer overrides; the next report shows the effect.

Tests: `test_calibration.py`, `test_policy.py`.

### E4. Know who is late and who will need a check
**As** Nok **I want** Home to list customers whose files are overdue and customers likely to need a review
**so that** I chase and prepare before the night.

AC: cadence from round dates (median gap, grace a quarter of the cycle), due/overdue with the reason;
watchlist from the last three rounds' drift and decisions; one round → unknown, never overdue.

Tests: `test_foresight.py`.

### E5. Ask the assistant and let it do the one safe thing
**As** Nok **I want** to ask "why is C902 waiting?" or "compare the last three work rounds" and get an answer
with evidence, a chart when it helps, and a confirmable action when the verdict says accept **so that** I act
from the conversation.

AC: read-only tools, citations to real evidence ids, server-built navigation and act actions, server-computed
reports (the model picks a view, never values), clarification questions before assuming, streaming with
Stop, persistent conversations scoped to my identity and access; the act runs only after I confirm, through
the ordinary endpoint.

Tests: `test_workspace.py`, `test_workspace_tools.py`, `test_assistant_navigation.py`, `test_assistant_reports.py`,
`test_conversations.py`, `test_chat_stream.py`.

### E6. Operate the system
**As** Dao **I want** queue, workers, failed jobs with retry, breaker state and spend on one page, and a
runbook for every incident **so that** the night is boring.

Tests: `test_jobs.py`, `test_failure_injection.py`, `test_postgres.py`, `test_minio.py`, `test_migrations.py`.

---

## Epic F — Security and tenancy

### F1. Roles
**As** Ton **I want** importers to upload, reviewers to approve mappings and data, approvers to export and
override blocks, admins to configure **so that** responsibility is explicit.

AC: every endpoint declares a role; a lower role gets 403; the actor recorded is the authenticated identity,
never a body field. Matrix: [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).

### F2. Tenant scope
**As** Ton **I want** a principal limited to certain customers to see nothing else **so that** a shared
deployment is safe.

AC: every list, read, write, assistant tool, learning and foresight view is scoped; bulk unknown files need
all-customer access.

Tests: `test_auth.py`, `test_workspace.py::test_dataset_tools_are_scoped`, `test_learning.py::test_learning_is_customer_scoped`.

### F3. A cell that tries to give instructions
**As** Dao **I want** spreadsheet content treated as data in every prompt **so that** prompt injection through
a customer file is impossible by construction.

AC: `SYSTEM_BASE` states it; the guard enforces it; tools mark truncation; tests inject and verify rejection.

---

## Non-functional stories

| Story | Measure | Where verified |
|---|---|---|
| A 300-file night finishes in time | ≤ 10 min profile+propose, ≤ 15 min transform on one 12-core node | `forma bench`, PERFORMANCE.md |
| Accuracy never drops | identity 1.00, field 0.996, critical 1.00, drift 9/9 on the corpus | `make eval-gate`, CI |
| Nothing partial is ever visible | object-first writes, atomic exports | `test_failure_injection.py` |
| Everything is auditable | every write has actor, action, entity, details | `/api/audit`, tests |
| Data loss on node loss ≤ 24 h; serve again ≤ 1 h | nightly backup, documented restore | RUNBOOK.md |
| Every setting is documented | generated `.env.production.example`, drift-checked | CI |

## Traceability

| Epic | ADRs |
|---|---|
| A Receiving | 003, 005, 012, 013, 027, 033, 034, 043, 044 |
| B Meaning | 001, 002, 006, 007, 008, 015, 017, 018, 019, 021, 025, 036, 038, 046 |
| C Data | 020, 022, 023, 024, 026 |
| D Verdict & review | 029, 035, 036, 042 |
| E Governance | 016, 017, 025, 032, 037, 039, 040, 041, 045 |
| F Security | 013, 029, 030, 040 |
