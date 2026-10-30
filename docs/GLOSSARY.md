# Glossary

> **สรุปภาษาไทย** — คำศัพท์ที่ใช้ทั้งใน UI, โค้ด และเอกสาร พร้อมความหมายสั้นๆ ภาษาไทย

| Term | Thai | Meaning |
|---|---|---|
| **Target schema / target format / contract** | รูปแบบปลายทาง | The JSON Schema (+ `x-forma`) the downstream system expects. Configuration, not code. Versioned (`product@1`). |
| **Semantic** | ความหมายของฟิลด์ | The `x-forma.semantic` tag on a field (`sku`, `price.amount`, `stock.qty`, …). Code finds fields by semantic, never by name. |
| **Synonym** | คำพ้องของหัวคอลัมน์ | Thai/English header words that map to a field; part of the schema. Learned *aliases* are the runtime layer on top. |
| **Identity key** | คีย์ระบุตัวตน | The field(s) that identify a record (usually the SKU); from the schema. |
| **Risk** | ระดับความเสี่ยงของฟิลด์ | low / medium / high / critical per field; drives confidence bars and review priority. |
| **Customer** | ลูกค้า | A sender of files; has code, ERP type, default format, overrides, policy overrides. |
| **Round / import round / delivery** | รอบนำเข้า | One delivery of one customer: files → mapping → dataset → export. Has a status machine. |
| **Work round / batch** | รอบงาน | A named, dated bulk upload of many files (possibly many customers); each file becomes its own round. |
| **Batch item** | ไฟล์ในรอบงาน | One file of a bulk upload with its match, state and round. |
| **Profile** | โปรไฟล์ไฟล์ | What the reader found: sheets, tables, columns, types, samples, hints, fidelity facts. |
| **Fingerprint** | ลายนิ้วมือโครงสร้าง | Structural hash of the tables (strict: headers + types; loose: normalised headers). |
| **Drift** | การเปลี่ยนของโครงสร้าง | How a layout differs from the approved one: identical, header_moved, columns_added/renamed/removed, sheets_added/removed, types_changed, unrelated_file. |
| **Mapping / MappingDocument** | การจับคู่ | The declarative DSL document: sources, joins, record, fields, collections, quality rules. |
| **Mapping version** | เวอร์ชันการจับคู่ | An append-only row holding one document with status proposed / approved / rejected / superseded, its policy report and agent log. |
| **Memory** | ความจำ | Reuse of the approved mapping when the layout is identical. |
| **Approved-format library** | คลังรูปแบบที่อนุมัติ | Every approved layout in a customer's history, keyed by target schema digest and fingerprint; newest exact match first. |
| **Heuristic draft** | ร่างจากกฎ | The deterministic mapper's proposal (synonyms, hints, overlap). Always available, fully explainable. |
| **Agent** | เอเจนต์ AI | A bounded model call with a JSON schema and validation: schema, mapping, adaptation, diagnosis, copilot, assistant. |
| **Guard** | ตัวกรองผลลัพธ์ AI | Rejects model output with instruction-like text or pointers outside the schema. |
| **Breaker / route / backup key** | เบรกเกอร์ / เส้นทาง / คีย์สำรอง | Provider resilience: a route is a provider+model+key; each has a circuit breaker; backups are tried in order. |
| **Simulation** | จำลอง | Running a mapping on a bounded sample to get rows, findings and sample records. |
| **Sandbox** | แซนด์บ็อกซ์ | Running the new mapping next to the approved one on the same files and comparing. |
| **Field review / data check** | ตรวจรายฟิลด์กับข้อมูล | Per changed field, comparing values on the same SKUs with last round's published data: same, vat_added, vat_removed, scaled, different, no_data. |
| **Policy** | กฎอนุมัติ | `approval.yaml` + web edits: thresholds that produce auto_approve / review / block with reasons; sandbox, anomaly, publish and verdict sections. |
| **Policy overrides** | กฎเฉพาะลูกค้า | Sections of the policy a customer overrides (from calibration proposals). |
| **Decision** | คำตัดสินของกฎ | auto_approve, review, block. |
| **Risk level** | ระดับความเสี่ยงของการตัดสิน | low / medium / high with factors and impact in records (ADR-019). |
| **Review task** | งานที่รอตรวจ | A queue row: kind (mapping, drift, blocked, data), priority, reasons, verdict brief. |
| **Block / override** | ระงับ / อนุมัติข้าม | A block is not an ordinary review; approving it needs an approver and a reason. |
| **Verdict** | คำตัดสินของ FORMA | One conclusion per file: outcome, confidence word, must-decide items, evidence, suppressed, rules fired, digest. |
| **Outcome (verdict)** | ข้อสรุปว่าควรทำอะไร | accept, answer, assign_customer, fix_mapping, check_data, fix_data, reject_file, wait. |
| **Question / answer** | คำถาม / คำตอบ | An uncertain field turned into a bounded decision; the answer becomes a new mapping version. |
| **Evidence** | หลักฐาน | For a field: why it was mapped; for a question: the actual source cells; for a verdict: the numbered lines that support it. |
| **Dataset version** | เวอร์ชันข้อมูล | Append-only JSONL object + row: draft / approved / published / superseded; stats include `invalid_records`. |
| **Change set** | ชุดการแก้ | A reviewed batch of edits with impact that produced a new dataset version. |
| **Override (data)** | กฎแก้ถาวร | A persistent edit re-applied on every future import of the customer. |
| **Lineage / trace** | ที่มาของค่า | File, sheet, row, column, raw cell and rule behind a value, with the cause of a change vs last round. |
| **Anomaly** | ความผิดปกติ | price jump, discount too high, promo dates reversed, stock negative, outlier, SKUs removed, row count; with severity and "% normal". |
| **Contract gate** | ด่าน contract | Records that break the target schema block approval and export. |
| **Export / artifact** | ส่งออก | JSON / JSONL / CSV written atomically with sha256; exporting publishes. |
| **Correction** | การแก้ที่ระบบเรียน | What an approval taught: alias (column → field), constant, accepted (as proposed), promoted (for everyone). |
| **Learned / alias** | สิ่งที่เรียนรู้ | Aliases and constants the mapper reads before guessing, with evidence. |
| **Calibration** | การปรับกฎจากผลลัพธ์ | Outcomes per decision, precision per rule, proposals an admin applies. |
| **Foresight / cadence / watchlist** | คาดการณ์ / จังหวะส่ง / รายการเฝ้าระวัง | Expected next delivery from the median gap between rounds; customers likely to need a check. |
| **Job / handler / stage** | งานเบื้องหลัง | Durable job rows run by thread, process or arq workers; handlers are idempotent and record stages. |
| **Reconciliation** | กระทบยอดคิว | On startup, queued jobs whose results already exist are marked done, not re-run. |
| **Principal / role / tenant scope** | ตัวตน / บทบาท / ขอบเขตลูกค้า | The authenticated identity, its role (importer ⊂ reviewer ⊂ approver ⊂ admin) and the customers it may see. |
| **Audit log** | บันทึกการทำงาน | Every write with actor, action, entity and details (never secrets or chat contents). |
| **LLM ledger** | บัญชีการเรียก AI | Tokens, estimated cost, latency and attempts per model call. |
| **MCP** | — | Model Context Protocol endpoint exposing the assistant's read-only tools to external clients. |
| **ADR** | บันทึกการตัดสินใจ | Architecture decision record in `docs/adr/`; binding unless superseded. |
