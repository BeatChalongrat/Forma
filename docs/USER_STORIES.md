# FORMA — User stories, every case, and the technique behind each step

> **สรุปภาษาไทย** — เอกสารนี้เล่าว่าผู้ใช้ใช้ FORMA ทำอะไรและเพื่ออะไร ระบบมีผู้ใช้คนเดียว จึงเขียนด้วยคำว่า "ฉัน"
> เริ่มจากเส้นทางใช้งานจริงตั้งแต่ต้นจนจบ แล้วตามด้วยแต่ละเรื่อง ครอบคลุมทั้งเส้นทางปกติและกรณีขอบ
> ทุกเรื่องมีเงื่อนไขการยอมรับ และตารางว่าแต่ละขั้นตอน "ใช้เทคนิคอะไร เพราะอะไร" พร้อม ADR อ้างอิง
> ใช้เป็นฐานของ test plan ได้

Format: **ฉันอยาก** … **เพื่อ** …. Each story has acceptance criteria (AC), the edge cases it covers, and a table
of the techniques used at each step with the reason. Tests that cover each story are named so they can be traced.

## ผู้ใช้

FORMA มีผู้ใช้คนเดียว คือคนที่รับไฟล์จากลูกค้า ตรวจ อนุมัติ และดูแลการตั้งค่าเอง ระบบปลายทางเป็นผู้รับ JSON
และไม่ได้ใช้หน้าเว็บ เมื่อไม่มีคนที่สองตรวจซ้ำ คำตัดสินของ FORMA ด่านตรวจของ policy และการสุ่มตรวจจึงเป็นด่านสุดท้าย

## ใช้งานจริงตั้งแต่ต้นจนจบ

ก่อนเริ่มใช้ครั้งแรก ฉันลงทะเบียนรูปแบบ JSON ปลายทาง (E1) และเชื่อม AI ถ้าจะใช้ (E2)

จากนั้นทุก 14 วันที่ได้ไฟล์ Excel หรือ CSV จากลูกค้า:

1. เปิด Home ดูว่าลูกค้ารายไหนส่งช้าหรือน่าจะต้องตรวจ (E4) และมีคำถามค้างมานานแค่ไหน (A6)
2. อัปโหลดไฟล์ทั้งหมดทีเดียว แล้ว FORMA จับคู่แต่ละไฟล์กับลูกค้า (A2) ไฟล์ที่จับคู่ไม่ได้ ฉันเลือกลูกค้าให้ (A3)
3. ระหว่างรอ การ์ดสีกรมท่าบอกความคืบหน้าในทุกหน้า ฉันจึงไปทำอย่างอื่นก่อนได้ (A7)
4. ไฟล์ที่ผ่านทุกด่านเดินต่อจนได้ JSON เอง ฉันไม่ต้องเปิดดู (A2, B1)
5. ไฟล์ที่ต้องดู FORMA เปิดมาด้วยข้อสรุปและคำถามเท่าที่จำเป็น (D1) ถ้าชัดเจนฉันกดยอมรับได้ในคลิกเดียว (D2)
6. ไฟล์ที่มีปัญหาจะบอกเหตุผล เช่น คอลัมน์เปลี่ยนความหมาย (B3), เรื่อง VAT (B4) หรือไฟล์ผิดลูกค้า (B5)
7. ถ้าอัปโหลดผิด ผิดลูกค้า หรือลูกค้าส่งไฟล์แก้มาใหม่ ฉันยกเลิก ย้าย หรือแทนรอบนั้นได้โดยประวัติไม่หาย (A5)
8. เรื่องที่ต้องรอลูกค้าตอบ ฉันพักคำถามไว้พร้อมวันที่ แล้วมันจะกลับมาเองในวันนั้น (A6)
9. ถ้า mapping ผิด ฉันแก้ใน Studio (B10) แล้วรอบหน้าระบบจำได้ (B11)
10. ตรวจข้อมูลจากรายงานสิ่งที่เปลี่ยน (C1) และแก้รายการที่ผิด (C2, C3)
11. อนุมัติแล้วดาวน์โหลด JSON (C6) ถ้าพบภายหลังว่าผิด ฉันย้อนกลับได้ (C7)

ระหว่างทาง ฉันถามผู้ช่วยได้ทุกเมื่อ (E5) ส่งรูปหรือไฟล์ที่ลูกค้าส่งมาให้มันอ่าน (E8) ให้มันเขียนโค้ดวิเคราะห์ ทำกราฟ
เอกสาร หรือสไลด์ (E7) เปิดดูและแก้งานนั้นข้างแชต (E9) และมันจำวิธีทำงานของฉันข้ามแชตได้ (E10)

นาน ๆ ครั้ง ฉันดูหน้า System health ว่าทุกอย่างปกติ (E6) ระบบล้างข้อมูลเก่าและสำรองข้อมูลเอง (E11)
และฉันปรับ policy จากผลที่เกิดขึ้นจริง (E3)

---

## Epic A — Receiving files

### A1. Upload one delivery
**ฉันอยาก**อัปโหลดไฟล์ของลูกค้ารายหนึ่งเป็นหนึ่งรอบ ไม่ว่าจะไฟล์เดียวหรือหลายไฟล์ **เพื่อ**ให้ FORMA อ่านไฟล์และบอกว่าขั้นต่อไปต้องทำอะไร

AC: the same bytes under the same name are stored once, and a file already uploaded in another round is marked
as a duplicate; every sheet is read; the round shows its three stages (upload, answer and check, download) and FORMA's conclusion; unreadable files fail loudly on the round, not
silently as empty data.

Edge cases: xlsx with a title block and the header on row 7 · CSV in TIS-620 · an xlsx whose formulas have no
cached values (transformation refused, review forced) · legacy .xls (review forced) · a file over the size cap
(413) · an encrypted workbook (unsupported, with guidance).

| Step | Technique | Why |
|---|---|---|
| Store | sha256 content addressing, storage key on the row | immutability and duplicate detection (ADR-005, ADR-012) |
| Sniff | content sniffing, strict CSV decoding with BOMs | extensions lie; a decode error must not become `?` characters (ADR-013, ADR-043) |
| Read | fastexcel for cell values, full-row OOXML scan for structure facts | speed for 100K rows; hidden data, error cells and uncached formulas are facts the policy needs (ADR-003, ADR-043) |
| Profile | full-sheet blank-row block detection, bounded header window, type inference, hints | multiple tables per sheet and long preambles are normal in ERP exports |

Tests: `test_api.py::test_full_flow`, `test_ingestion_readiness.py`, `test_tables.py`, `test_guard_sniff.py`.

### A2. Upload many files at once (bulk)
**ฉันอยาก**อัปโหลดไฟล์ของทุกลูกค้าทีเดียว **เพื่อ**ให้แต่ละไฟล์ถูกจับคู่กับลูกค้าและได้ JSON ของตัวเอง โดยฉันไม่ต้องเปิดทีละไฟล์

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
**ฉันอยาก**เลือกลูกค้าให้ไฟล์ที่ FORMA จับคู่ไม่ได้ จะเป็นลูกค้าเดิมหรือลูกค้าใหม่ก็ได้ **เพื่อ**ให้งานเดินต่อ

AC: candidates with similarity are shown; assigning re-enqueues the items; a tenant-scoped user cannot assign
across customers (403).

Tests: `test_batches.py::test_unknown_file_gets_a_customer_then_finishes_after_review`. Scoped users:
`test_batches.py::test_customer_scoped_users_cannot_bulk_upload` covers upload; assigning as a scoped user has no
test yet.

### A4. Work rounds
**ฉันอยาก**มีรอบงานที่มีชื่อและวันที่ เพิ่มไฟล์ภายหลังได้ และเทียบกับจำนวนที่คาดไว้ **เพื่อ**ดูได้ว่า "ได้รับแล้ว 280 จาก 300 พร้อมแล้ว 270"

AC: rename, date and expected target editable (importer, all-customer access); dashboard on Home and the
round; status counts link to filtered files; unknown targets are labelled unknown, never "missing".

Tests: `test_batches.py::test_work_round_date_append_and_retry`, `test_append_scope_validation_and_audit`,
`test_expected_files_target_is_separate_from_received_and_ready`.

---

### A5. Undo a wrong upload
**ฉันอยาก**ยกเลิกไฟล์ที่อัปโหลดผิด ย้ายไฟล์ไปลูกค้าที่ถูก หรือแทนที่ด้วยไฟล์ที่ลูกค้าแก้มาใหม่ **เพื่อ**ให้คิวงานและ JSON ที่ส่งออกตรงกับความจริง โดยไม่ลบประวัติ

AC: cancel, move and replace each ask for a reason and are audited; a closed round leaves the review queue, the
history list (shown under one filter), foresight's cadence and the work round's zip; only a cancelled round can be
reopened; move reuses the stored file and runs the pipeline for the right customer; replace links the new round to
the old one and the work round's file row follows the file; the verdict's "move to the right customer" opens the
move dialog with the matched customer selected.

Technique: three closed statuses with `closed_from/by/at/reason` and successor/predecessor links, no row deleted
(ADR-052).

Tests: `test_round_lifecycle.py`.

### A6. Know whose turn a question is
**ฉันอยาก**เห็นว่าคำถามแต่ละข้อรอมานานแค่ไหน และพักเรื่องที่รอลูกค้าตอบไว้ได้ **เพื่อ**ให้คิวเหลือแต่งานที่ฉันทำได้จริง

AC: every open question shows its age, and those past seven days are called out on the home page; a question can wait
for the customer with a note and a date, leaves the queue and its counts, and comes back on that day or sooner by hand;
an approval, a rejection or a closed round settles it; foresight says when a customer's answer is due; a round with no
activity for 60 days is flagged and never closed.

Technique: status `waiting` woken when the queue is read, so there is no timer to fail (ADR-053).

Tests: `test_review_queue.py`.

### A7. See how an upload is going
**ฉันอยาก**เห็นว่าไฟล์ที่อัปโหลดไปถึงไหนแล้วโดยไม่ต้องเฝ้าหน้าเดียว **เพื่อ**ไปทำงานอื่นระหว่างรอ และรู้ทันทีเมื่อมีไฟล์รอฉันหรือระบบหยุด

AC: sending shows how many files have gone; while a round works, a card shows files finished, mapped, ready, waiting
and with problems on every page; on the round's page it disappears when the round is done, elsewhere it stays until
read; closing it while files are moving hides it until the end; a stopped queue says so, says nothing is lost, leads
to Resume and cannot be closed.

Technique: a small summary endpoint polled every few seconds, with the round kept in the browser's session so the card
follows across pages (`components/UploadCard.tsx`).

Tests: `test_batches.py::test_the_summary_counts_what_the_full_view_counts`.

## Epic B — Deciding what a file means

### B1. A returning customer with an unchanged layout
**ฉันอยาก**ให้ไฟล์ที่ layout เหมือนที่อนุมัติไว้ผ่านไปเองโดยฉันไม่ต้องดู **เพื่อ**ให้ฉันเห็นเฉพาะสิ่งที่เปลี่ยน

AC: the approved mapping is reused verbatim (`memory`), confidence 1.0, policy auto-approves, verdict `clean`.

| Technique | Why |
|---|---|
| strict/loose fingerprint equality → reuse | a person approved exactly these rules for exactly this layout (ADR-006) |

Tests: `test_api.py::test_full_flow`, `test_heuristics_eval.py` (drift 9/9).

### B2. A renamed or moved column
**ฉันอยาก**ให้ FORMA ปรับ mapping เองและพิสูจน์จากข้อมูลว่าคอลัมน์ที่เปลี่ยนชื่อยังมีความหมายเดิม **เพื่อ**กดยอมรับได้ในคลิกเดียวโดยไม่ต้อง map ใหม่

AC: a moved header (`header_moved`) reuses the approved mapping verbatim, as in B1. For `columns_renamed`,
`columns_added`, `columns_removed` or `types_changed`, the new draft (or the AI adaptation) is checked field by field against last round's published values on the same SKUs; when every important field keeps its
values, the verdict is `rename_only` → **Accept** with one click; with `sandbox.trust_data_checks` on, the
policy auto-approves it outright.

| Technique | Why |
|---|---|
| adaptation agent with the approved mapping + drift report + fresh draft | keep business decisions (VAT, value maps), re-point only what moved (ADR-021) |
| data check: equal share, steady ratio (×1.07 = VAT), scaled, different | the header is a hint; the values decide (ADR-021) |
| sandbox delta interpreted in the light of the data check | the delta describes the *approved* mapping breaking on the new layout, not a risk in the proposal (ADR-037) |

Tests: `test_healing.py`, `test_sandbox.py`, `test_verdict.py`, `test_calibration.py::…renamed…`.

### B3. A column that changed meaning
**ฉันอยาก**รู้เมื่อคอลัมน์ที่เปลี่ยนชื่อไม่ได้เก็บค่าเดิมแล้ว **เพื่อ**แก้ mapping ก่อนจะส่งราคาผิดออกไป

AC: data check `different` on a critical/high field → policy review with a `data check:` reason and a high
risk factor; verdict `meaning_changed` names the field and the share of SKUs that kept their value; the evidence
links to the Studio's *What changed* tab.

Tests: `test_healing.py::test_data_check[different]`, `test_policy.py::…meaning_checks…`.

### B4. VAT included or excluded
**ฉันอยาก**ให้ FORMA บอกเมื่อคอลัมน์ราคาขยับ 7 % พอดี **เพื่อ**ยืนยันเรื่อง VAT ได้ด้วยคำตอบเดียว

AC: data check `vat_added`/`vat_removed`; verdict `vat_shift` with one decision on the VAT field, whose default
follows the direction of the move and is stated as a suggestion; answering it on the card or in the review
questions saves a new mapping version with evidence; with no VAT field to answer, the verdict sends the mapping
to be fixed.

| Technique | Why |
|---|---|
| steady-ratio detection with the named constant `VAT_RATE` | VAT is the most common silent price change in Thai price lists (ADR-007, ADR-021) |
| answer → `ConstExpr`, confidence 1.0, open question retired | the decision is bounded and auditable (ADR-029) |

Tests: `test_workspace.py::test_answer_preview_versions_and_policy` (the question path),
`test_verdict.py::test_vat_shift_asks_about_the_vat_field_with_a_stated_default` (the rule),
`test_verdict.py::test_the_vat_decision_on_the_card_saves_and_the_round_can_finish` (saving from the card).

### B5. A wrong file (another customer's export)
**ฉันอยาก**ให้ไฟล์ของลูกค้ารายอื่นถูกหยุดและบอกว่าน่าจะเป็นของใคร **เพื่อ**ไม่ให้ข้อมูลออกไปผิดลูกค้า

AC: layout `unrelated_file` to this customer → block; when it matches another customer's approved format and
the values on the shared SKUs differ, the verdict is `wrong_customer`: "This file is probably C901's, not
C902's", outcome *move to the right customer*, no questions asked; approving a block needs an approver and a
reason (audited).

| Technique | Why |
|---|---|
| other-customer fingerprint match + identity-matched data checks with constants and identity keys excluded | the layout alone is weak (shared ERPs); layout + different values on the same SKUs is strong (ADR-036) |
| block as an explicit override path | a wrong file must never be an ordinary approval (ADR-008) |

Tests: `test_api.py::test_export_needs_approved_data_and_block_needs_explicit_override`,
`test_verdict.py::test_wrong_file_is_named_through_the_api`.

### B6. A first-time customer
**ฉันอยาก**ให้ไฟล์แรกของลูกค้าใหม่มาถึงฉันพร้อมฟิลด์ไม่กี่ตัวที่ FORMA ไม่แน่ใจ **เพื่อ**ยืนยันครั้งเดียวแล้วจบ

AC: policy review ("first time"); verdict `first_time` lists ≤ 3 decisions; after approval the mapping is the
customer's memory and the format library entry.

Tests: `test_api.py::test_full_flow`, `test_verdict.py::test_first_time_and_clean`.

### B7. A new customer whose layout equals another customer's
**ฉันอยาก**รู้ว่า layout นี้ใช้ร่วมกับลูกค้ารายอื่น เช่นมาจาก ERP เดียวกัน **เพื่อ**ไม่ถูกเตือนผิดว่าเป็นไฟล์ผิดลูกค้า

AC: verdict `first_time_shared_layout`, outcome *answer* when fields need confirming and *accept* otherwise, sender
confirmed in passing.

Tests: `test_api.py::test_file_matching_another_customer_goes_to_review` covers the policy; no test asserts this
verdict rule.

### B8. The AI is unavailable
**ฉันอยาก**ให้ FORMA ทำงานต่อได้เมื่อไม่มี AI และบอกฉันว่าทำแบบนั้น **เพื่อ**ให้งานไม่หยุดและไม่มีอะไรถูกเชื่อโดยไม่ตรวจ

AC: heuristic draft used; `llm_fallback` → review, never auto-approve; verdict `ai_unavailable` (a first-time file
shows `first_time`, because that rule runs first); backup keys tried in order first; breaker state on Ops for
failures in the API process (R27).

| Technique | Why |
|---|---|
| deterministic floor + circuit breaker + backup routes | availability without lowering the bar (ADR-015, ADR-025) |

Tests: `test_failure_injection.py::test_llm_timeout_falls_back_and_never_auto_approves`, `test_backup_keys.py`.
No test asserts the `ai_unavailable` verdict rule.

### B9. The model returns something unsafe
**ฉันอยาก**ให้ผลลัพธ์จาก AI ที่ผิดรูปแบบหรือถูกแทรกคำสั่งถูกปฏิเสธ **เพื่อ**ไม่ให้ข้อความในเซลล์อย่าง "ignore the rules" เปลี่ยน mapping ได้

AC: guard rejects instruction-like text and pointers outside the schema; the draft is used; the round goes to
review; `test_guard_sniff.py`, `test_workspace.py::test_assistant_rejects_injected_output`.

### B10. Edit the mapping by hand
**ฉันอยาก**ลากคอลัมน์ไปวางบนฟิลด์ หรือเชื่อมสองชีตใน Studio **เพื่อ**แก้ mapping โดยไม่ต้องเขียน JSON

AC: every save is a new proposed version with parent; validation, simulation, sandbox and policy run again;
a wrong-file block cannot be cleared by an edit; relationships are measured on the data before a join is added.

Tests: `test_sandbox.py::test_an_edit_that_makes_data_worse_is_blocked`, `test_relationships.py`,
`test_api.py::test_export_needs_approved_data_and_block_needs_explicit_override` (the wrong-file block).

### B11. The mapping is learned from my correction
**ฉันอยาก**ให้คอลัมน์ที่ฉันแก้เมื่อรอบก่อนถูกจับคู่ถูกในรอบนี้ **เพื่อ**ไม่ต้องแก้เรื่องเดิมซ้ำ

AC: the approved version is diffed against FORMA's proposal; the alias is stored for the customer; the next
proposal maps it with evidence "learned: you mapped 'col_17' here on …"; a confirmed constant replaces the
guess and drops its question; another customer does not inherit it; the Settings page shows corrections per
approval falling.

Tests: `test_learning.py`.

### B12. An alias several customers taught becomes everyone's
**ฉันอยาก**ตั้งหัวคอลัมน์ที่ลูกค้าสามรายจับคู่แบบเดียวกันให้เป็นค่ากลาง **เพื่อ**ให้ลูกค้ารายที่สี่ได้ตั้งแต่วันแรก

AC: suggestion listed when ≥ 3 distinct customers taught it and it is not already a synonym; promote (admin,
audited); forget retires it.

Tests: `test_learning.py::test_accepted_as_proposed_is_recorded_and_promotion_shares_an_alias`.

---

## Epic C — The data

### C1. Create the data and see what changed
**ฉันอยาก**ได้ข้อมูลเต็มชุดพร้อมรายงานสิ่งที่เปลี่ยนจากรอบก่อน **เพื่อ**ตรวจเฉพาะสิ่งที่ขยับ ไม่ต้องดูทุกแถว

AC: SKUs added/removed, changed fields, price moves (large ones flagged), promotion changes; anomalies within
the round, vs last round, vs usual size with "% normal"; data-stage verdict (`contract_broken`, `price_move`,
`anomalies`, `publishable`).

| Technique | Why |
|---|---|
| diff by schema identity; robust outliers (median/MAD) | means and standard deviations are dragged by the outliers themselves (ADR-022) |

Tests: `test_anomalies.py`, `test_api.py::test_full_flow` (the no-change report). A non-zero change report has no
end-to-end test yet.

### C2. Fix records that break the contract
**ฉันอยาก**เห็นรายการที่ผิดรูปแบบปลายทางและแก้ทีละรายการ **เพื่อ**ไม่ให้ข้อมูลที่ผิดรูปแบบถูก export ออกไป

AC: approval and export refused while `invalid_records` > 0 (409 with the errors); the Data Editor lists
them; a save is refused only for errors it *adds*; a deleted invalid record is a fix too.

Tests: `test_schema_gate.py`.

### C3. Edit like a spreadsheet
**ฉันอยาก**วางข้อมูลจาก Excel เลือกช่วง และแก้หลายแถวพร้อมกัน **เพื่อ**แก้เสร็จในไม่กี่วินาที

AC: staged operations, impact preview (rows, cells, before/after), new version with a change set; persistent
edits become overrides for every future round and collide loudly with a new mapping.

Tests: `test_assistant.py::test_overrides_that_collide_with_a_new_mapping`, `test_schema_gate.py::test_overrides_are_validated_after_they_apply`, `test_lineage.py::test_manual_edits_show_in_history_and_cause`.

### C4. Ask the copilot
**ฉันอยาก**พิมพ์ว่า "ลดราคาหมวด X 5 % เฉพาะที่สต็อกเกิน 100" **เพื่อ**ให้ระบบวางแผนการแก้และแสดงผลกระทบก่อนลงมือจริง

AC: a plan, never an application; conditions across collections via derived figures; ambiguity → a
clarification in Thai; "why" questions answered from lineage and join evidence.

Tests: `test_assistant.py::test_copilot_api_plans_conditions_without_an_llm`,
`test_assistant.py::test_why_questions_are_answered_from_evidence`.

### C5. Trace a value to its cell
**ฉันอยาก**คลิกค่าหนึ่งแล้วเห็นไฟล์ ชีต แถว คอลัมน์ ค่าดิบ และกฎที่สร้างค่านั้น รวมถึงเหตุผลที่ต่างจากรอบก่อน **เพื่อ**มั่นใจในสิ่งที่ส่งออก

Tests: `test_lineage.py`, `test_review_evidence.py`.

### C6. Approve and export
**ฉันอยาก**อนุมัติข้อมูลโดยรับทราบความผิดปกติระดับสูงอย่างชัดเจน แล้ว export เป็น JSON, JSONL หรือ CSV ที่มีฟิลด์ตรงตามรูปแบบปลายทาง **เพื่อ**ให้ปลายทางได้สิ่งที่ต้องการ

AC: export needs approved data (409 otherwise); atomic write; sha256 recorded; `export_view` drops internal
keys; a bulk round exports automatically after a manual approval.

Tests: `test_export_stream.py`, `test_api.py::test_export_needs_approved_data_and_block_needs_explicit_override`.

### C7. Roll back
**ฉันอยาก**ย้อนข้อมูลกลับไปเวอร์ชันก่อนหน้า **เพื่อ**ยกเลิกการแก้ที่ผิดโดยประวัติเดิมไม่หาย

AC: rollback = new version equal to the parent; audit lists it; re-export yields a new artifact.

---

## Epic D — The verdict and the review

### D1. Read one sentence, decide one thing
**ฉันอยาก**ให้แต่ละไฟล์เปิดมาด้วยข้อสรุปของ FORMA ความมั่นใจ และเรื่องที่มีแต่ฉันตัดสินได้ **เพื่อ**ไม่ต้องอ่าน 23 ตาราง

AC: verdict card on the Review page, the Round page, the queue rows, Home and batch rows; evidence lines link to
the page that proves them; "folded away" says what was hidden and why; the same stored reports always give
the same verdict (digest).

| Technique | Why |
|---|---|
| ordered hypothesis rules over stored reports; suppression (same rule + different data = data change) | deterministic, explainable, cheap; a model may narrate later, never decide (ADR-036) |
| confidence as a word, number kept for eval | words are honest at this stage; calibration measures them (ADR-036, ADR-037) |

Tests: `test_verdict.py`.

### D2. Accept with one click
**ฉันอยาก**ยอมรับคำตัดสินได้ในคลิกเดียว **เพื่อ**ให้เคสที่ชัดเจนจบในไม่กี่วินาที

AC: `POST /rounds/{id}/verdict/accept` goes through `approve_mapping`/`apply_answers`/`approve_dataset`;
a stale digest is refused; a block is never acceptable this way; open decisions turn `accept` into `answer`, the
button stays disabled and the server refuses with 409; `verdict.accept` is audited with the rules that fired and
the decisions that differed from the defaults.

Tests: `test_verdict.py::test_open_decisions_turn_accept_into_answer`,
`test_verdict.py::test_accept_is_refused_while_decisions_remain`.

### D3. Understand why a file is in review
**ฉันอยาก**เห็นเหตุผลเป็นภาษาไทยหรืออังกฤษที่เข้าใจง่าย พร้อมตัวเลขในขอบเขตจริง **เพื่อ**ไม่ต้องอ่านข้อมูลวินิจฉัยดิบ

AC: deterministic bilingual explanations; raw diagnostics under a disclosure (ADR-042).

Tests: `test_review_assistant.py`.

---

## Epic E — Configuration and governance

### E1. Register the real target format
**ฉันอยาก**วาง JSON Schema ของปลายทาง ติดป้ายความหมายและคำพ้อง ตรวจ แล้วลงทะเบียน **เพื่อ**ให้ mapping การตรวจ และการ export เป็นไปตามรูปแบบจริงโดยไม่ต้องแก้โค้ด

AC: validity, mapping findings and diff to the previous version before registering; default or per customer;
unsupported shapes refused with a reason; exports carry exactly the schema's fields.

Tests: `test_schema_registry.py`, `test_custom_schema.py`, `test_contract_readiness.py`, `test_config.py`.

### E2. Connect AI with fail-over
**ฉันอยาก**วาง API key เลือกโมเดล เพิ่ม key สำรอง และทดสอบทุกตัว **เพื่อ**ไม่ให้งานหยุดเมื่อผู้ให้บริการล่ม

AC: key stored encrypted; applied without restart; per-route breaker; backups tried in order; model discovery
admin-only and rate-limited; the saved key is never forwarded to a changed endpoint.

Tests: `test_backup_keys.py`, `test_model_discovery.py`, `test_config.py`.

### E3. Edit the policy and see whether it is right
**ฉันอยาก**แก้เกณฑ์ในเว็บ ดูความแม่นยำของแต่ละกฎ และใช้ข้อเสนอที่มีหลักฐานรองรับ **เพื่อ**เพิ่มงานอัตโนมัติโดยไม่ลดมาตรฐาน

AC: validated, audited policy edits; calibration report; proposals with evidence and the reviews they would
have saved; apply with a digest (stale refused); per-customer overrides; the next report shows the effect.

Tests: `test_calibration.py`, `test_policy.py`.

### E4. Know who is late and who will need a check
**ฉันอยาก**ให้หน้า Home แสดงลูกค้าที่ส่งไฟล์ช้าและลูกค้าที่น่าจะต้องตรวจ **เพื่อ**ตามงานและเตรียมตัวล่วงหน้า

AC: cadence from round dates (median gap, grace a quarter of the cycle), due/overdue with the reason;
watchlist from the last three rounds' drift and decisions; one round → unknown, never overdue.

Tests: `test_foresight.py`.

### E5. Ask the assistant and let it do the one safe thing
**ฉันอยาก**ถามว่า "ทำไม C902 ยังรออยู่" หรือ "เทียบสามรอบงานล่าสุด" แล้วได้คำตอบพร้อมหลักฐาน และได้กราฟเมื่อช่วยให้เข้าใจ **เพื่อ**ทำงานต่อจากในบทสนทนาได้ เมื่อคำตัดสินคือยอมรับและไม่มีคำถามค้าง ผู้ช่วยจะเสนอให้ฉันกดยืนยันได้เลย

AC: read-only tools, citations to real evidence ids, server-built navigation and act actions, server-computed
reports (the model picks a view, never values), clarification questions before assuming, streaming with
Stop, persistent conversations scoped to my identity and access; the act runs only after I confirm, through
the ordinary endpoint.

Tests: `test_workspace.py`, `test_workspace_tools.py`, `test_assistant_navigation.py`, `test_assistant_reports.py`,
`test_conversations.py`, `test_chat_stream.py`.

### E6. Operate the system
**ฉันอยาก**เห็นคิว worker งานที่ล้มเหลวพร้อมปุ่มลองใหม่ สถานะ breaker และค่าใช้จ่ายในหน้าเดียว และมีคู่มือสำหรับเหตุขัดข้องทุกแบบ **เพื่อ**รับมือได้ทันทีเมื่อเกิดปัญหา

Note: breakers live in each process's memory, so the Ops page does not show worker failures yet (R27).

Tests: `test_jobs.py`, `test_failure_injection.py`, `test_postgres.py`, `test_minio.py`, `test_migrations.py`.

### E7. Let the assistant work out what the tools cannot
**ฉันอยาก**ถามเรื่องที่ระบบไม่มีรายงานให้ เช่น "วิเคราะห์ราคาทั้งชุดแล้วทำกราฟให้หน่อย" แล้วให้ผู้ช่วยเขียนโค้ด รันเอง แก้เองถ้าพลาด และส่งกราฟกับไฟล์กลับมา **เพื่อ**ได้คำตอบโดยไม่ต้องรอใครเขียนฟีเจอร์ใหม่

AC: the code, its output, charts and files of every run are shown in the chat, failed runs included; the run has no
network and no secrets, works on copies and cannot change FORMA data; a dataset or source file outside my access is
refused before anything runs; a run that fails is read and tried again; files saved by a run can be used by the next;
each run is audited; with the sandbox off the assistant has no such tool and says so.

Technique: Python in a separate container reached through one Unix socket (ADR-048).

Tests: `test_code_sandbox.py`.

### E8. Send the assistant a picture or a file
**ฉันอยาก**แนบ วาง หรือลากรูปหน้าจอ เอกสาร สไลด์ อีเมล หรือไฟล์ข้อมูลเข้าแชตแล้วถามได้เลย **เพื่อ**ไม่ต้องพิมพ์สิ่งที่ลูกค้าส่งมาใหม่

AC: images go only to a model that passed the image test, tested per model in Settings; when no connected model reads
images the page says so and the assistant does not guess; what an image shows is reported as read from it, never as
confirmed; a file is reachable only through its chat and is deleted with it; a wrong type or a too-large file is
refused with a plain reason.

Technique: type taken from the bytes, per-model test with random colours, route filtering in `LLMClient` (ADR-049).

Tests: `test_chat_images.py`.

### E9. See and change what the assistant makes
**ฉันอยาก**ให้สิ่งที่ผู้ช่วยสร้างเปิดข้างแชต เลือกจุดที่อยากแก้แล้วสั่งได้เลย **เพื่อ**ไม่ต้องดาวน์โหลดทุกครั้งหรืออธิบายว่าหมายถึงตรงไหน

AC: a flow, document, deck, sheet or web page opens beside the chat by itself; picking a box, page, slide or cell tells
the assistant what to change; a change is a new version and every version stays; an old version comes back as the next
number; a work says what data it rests on and warns when that data has changed, without rewriting itself; a file that
cannot be previewed can still be downloaded; every work across chats is listed beside chat history in the sidebar.

Technique: append-only versions, one version per message, previews made in the sandbox, a flow stored as data and drawn
by the page, a web page in a frame with no origin and no network (ADR-050).

Tests: `test_artifacts.py`.

### E10. Let the assistant remember me
**ฉันอยาก**ให้ผู้ช่วยจำว่าฉันเป็นใคร ทำงานแบบไหน และเคยคุยอะไรไว้ **เพื่อ**ไม่ต้องบอกซ้ำทุกแชต

AC: what I say is kept as topics during the chat, and I can say remember, change or forget; a new chat knows them;
earlier chats can be searched and the answer links to them; I can read, edit, delete, add, export and reset; I can pause
memory or turn off chat search; a chat can start with memory off or incognito; a file can never plant a memory; ID and
account numbers and passwords are never kept; memory never changes a mapping, a value or an approval.

Technique: topics owned by one person, a server rule that a message reading others' words saves nothing unasked, notes
sent as data with live data winning (ADR-051).

Tests: `test_memory.py`.

### E11. Let the system tidy up after itself
**ฉันอยาก**ให้ระบบล้างข้อมูลเก่าและสำรองฐานข้อมูลเอง พร้อมบอกว่าทำไปเมื่อไร **เพื่อ**ไม่ต้องคอยจำ และรู้ตัวทันทีถ้ามันหยุดทำ

AC: every pass is recorded with its counts, whether scheduled or run by hand; System health shows the storage used, the
last pass and the age of the last backup, amber past a day; a dry run deletes nothing, and `FORMA_RETENTION_APPLY`
switches deletion on; retention never closes a round; bytes that a round or a file waiting for a customer still uses
stay; the questions of a cancelled round stay, because reopening brings them back; the nightly backup keeps 14 days.

Technique: one `retention.run` for the schedule and the command line, an audit row per pass, a first pass soon after
start, and a backup container that needs no one to remember it (ADR-015, ADR-053).

Tests: `test_retention_backup.py`.

---

## Epic F — Security

### F3. A cell that tries to give instructions
**ฉันอยาก**ให้เนื้อหาในสเปรดชีตถูกถือเป็นข้อมูลเสมอในทุก prompt **เพื่อ**ให้ไฟล์ลูกค้าแทรกคำสั่งเข้ามาไม่ได้

AC: `SYSTEM_BASE` states it; the guard enforces it; tools mark truncation; tests inject and verify rejection.

---

## Non-functional stories

| Story | Measure | Where verified |
|---|---|---|
| A 300-file night finishes in time | ≤ 10 min profile+propose, ≤ 15 min transform on one 12-core node | `forma bench`, PERFORMANCE.md |
| Accuracy never drops | identity 1.00, field 0.996, critical 1.00, drift 9/9 on the corpus | `make eval-gate`, CI |
| Nothing partial is ever visible | object-first writes, atomic exports | `test_failure_injection.py` |
| Decisions are auditable | approvals, edits, exports and settings have actor, action, entity, details (gaps: R21) | `/api/audit`, tests |
| Data loss on node loss ≤ 24 h; serve again ≤ 1 h | `backup-nightly` runs `forma backup` every 24 hours and keeps 14 days on the same machine; documented restore | RUNBOOK.md |
| Every setting is documented | generated `.env.production.example`, drift-checked | CI |

## Traceability

| Epic | ADRs |
|---|---|
| A Receiving | 003, 005, 012, 013, 027, 033, 034, 043, 044, 052, 053 |
| B Meaning | 001, 002, 006, 007, 008, 015, 017, 018, 019, 021, 025, 036, 038, 046 |
| C Data | 020, 022, 023, 024, 026 |
| D Verdict & review | 029, 035, 036, 042 |
| E Governance | 015, 016, 017, 025, 032, 037, 039, 040, 041, 045, 048, 049, 050, 051, 053 |
| F Security | 013, 029, 030, 040 |

## เมื่อมีคนใช้เพิ่ม

วันนี้ฉันถือทุกบทบาทคนเดียว สองเรื่องนี้จึงไม่มีผลกับการใช้งานประจำวัน แต่จะใช้เมื่อมีคนหรือลูกค้าใช้ระบบเดียวกันเพิ่ม
Roles nest: importer ⊂ reviewer ⊂ approver ⊂ admin.

### F1. Roles
**เมื่อมีคนเพิ่ม ฉันอยาก**แบ่งหน้าที่เป็นคนอัปโหลด คนตรวจ คนอนุมัติ และผู้ดูแล **เพื่อ**ให้ความรับผิดชอบชัดเจน

AC: writes declare a role and a lower role gets 403; reads need a FORMA role, so a valid token with no role gets
403 everywhere except `GET /me`, and the web app says so; the actor of approvals, edits and exports is the
authenticated identity, never a body field. Matrix: [RBAC_AND_SECURITY.md](RBAC_AND_SECURITY.md).

### F2. Tenant scope
**เมื่อมีคนเพิ่ม ฉันอยาก**ให้คนที่จำกัดเฉพาะบางลูกค้าไม่เห็นข้อมูลอื่นเลย **เพื่อ**ให้ใช้ระบบเดียวร่วมกันได้อย่างปลอดภัย

AC: customer-owned lists, reads and writes, assistant tools, learned rows, foresight, jobs, dashboard figures and
learning statistics are scoped; every bulk endpoint and all-customer view needs all-customer access. Shared
settings are not limited by scope (R30).

Tests: `test_tenant_scope.py`, `test_auth.py`, `test_workspace.py::test_dataset_tools_are_scoped`,
`test_learning.py::test_learning_is_customer_scoped`.
