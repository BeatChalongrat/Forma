# รีวิวโค้ดทั้งระบบ FORMA — 1 พ.ย. 2026

> **สรุปสั้น** — อ่านโค้ดครบทุกไฟล์ (backend ~30,700 บรรทัด, frontend ~6,800 บรรทัด, compose/Dockerfile/schema)
> ระบบ **ไม่ได้มั่ว**: ทุกขั้นเป็นกฎที่ตรวจสอบได้ มีหลักฐานต่อฟิลด์ และนโยบายเป็นคนตัดสิน แต่คำว่า "เข้าใจทุกคำในไฟล์"
> ยังเกินจริง สิ่งที่ระบบเข้าใจคือ **หัวตาราง + รูปร่างของค่า + ความสอดคล้องกับรอบก่อน** ไม่ใช่ความหมายของข้อความ
> และมี **ค่าเริ่มต้นที่เติมให้เงียบ ๆ เมื่อไม่แน่ใจ** หลายจุด ที่สำคัญที่สุดคือเมื่อโครงสร้างไฟล์เหมือนเดิมทุกประการ
> ระบบจะใช้ mapping เดิมและ **ข้ามการตรวจความหมายของข้อมูลทั้งหมด** ทำให้ราคาที่ถอด VAT ออกแล้วผ่านไปได้โดยไม่มีคำเตือน
> พบบั๊กที่ยืนยันแล้ว 31 ข้อ (ร้ายแรง 6) และข้อเสนอแนะ 40 กว่าข้อ รายละเอียดและหลักฐานอยู่ด้านล่าง

> **สถานะหลังรีวิว (2 พ.ย. 2026)** — ข้อ A1–A6 แก้แล้วใน ADR-056 และยืนยันด้วยการรันเคสกลุ่ม R ซ้ำ: R10/R11/R12 ไปที่
> review ด้วย `scaled`/`vat_shift` ที่ขั้น mapping, รหัสซ้ำที่ค่าต่างกันเป็น error, P01 ตรง `expected.json` แล้ว
> และรอบปกติ (R01–R04) ยังผ่านเองเหมือนเดิม ข้อ B, C, D, E ยังเปิดอยู่

## 1. ขอบเขตและวิธีอ่าน

| ส่วน | อ่านโดย | ยืนยันอย่างไร |
|---|---|---|
| เส้นทางข้อมูลหลัก: ingestion → profiling → fingerprint → heuristics → agents/llm/guard → policy → DSL/validate → engine (ops, thai, transform) → schema registry → mapping/sandbox/healing/verdict → datasets/export → learning/questions/anomalies → batches/rounds → eval (~9,000 บรรทัด) | ผม อ่านทุกบรรทัด | รันเคสจริง 54 เคสผ่าน API เดียวกับหน้าเว็บในฐานข้อมูลทดลอง และ probe ฟังก์ชันตรงจุดที่สงสัย |
| jobs, storage, db, core, auth, api/*, config, retention, backup, lifecycle, review queue, calibration, lineage, relationships, cli, migrations | ผู้อ่านย่อย A | ผมเปิดโค้ดตรวจซ้ำทุกข้อที่ระดับสูง (6 ข้อ) ตรงทั้งหมด |
| ผู้ช่วย: workspace_assistant, tools, conversations, artifacts, attachments, memory, code_sandbox (service + runner + client), text_stream, workspace_routes, Dockerfile ของ sandbox | ผู้อ่านย่อย B | ตรวจซ้ำ 4 ข้อด้วยโค้ดและ probe ตรงทั้งหมด |
| frontend ทั้ง 59 ไฟล์ + nginx + Dockerfile เทียบกับ serialize.py | ผู้อ่านย่อย C | ตรวจซ้ำ 5 ข้อด้วยโค้ด ตรงทั้งหมด |

ข้อที่ผมไม่ได้ตรวจซ้ำเองติดป้าย **(รายงานจากผู้อ่าน)** ข้อที่ยืนยันด้วยการรันจริงติดป้าย **(รันแล้ว)** ด้วยโค้ดติดป้าย **(โค้ด)**

## 2. คำตอบตรง ๆ: ระบบเข้าใจไฟล์จริงไหม

### สิ่งที่ระบบ "เข้าใจ" จริง และเข้าใจได้ดี

1. **โครงสร้างไฟล์** (`profiling/tables.py`): หาตารางทุกบล็อกในทุกชีต หัวตาราง 1–2 แถว ตารางซ้อนกัน แถวรวม หัวที่พิมพ์ซ้ำทุกหน้า ทุกอย่างมีเหตุผลเป็นคะแนน ไม่ใช่สมมติว่าแถวแรกคือหัว
2. **ชนิดและนิสัยของค่า** (`profiling/profile.py`): ตัวเลข วันที่ไทย/สากล บูลีน บาร์โค้ด รหัสสินค้า หน่วย ลำดับวัน-เดือน ความซ้ำ ความว่าง
3. **การจับคู่หัวตารางกับสัญญา JSON** (`ai/heuristics.py::match_columns`): คะแนนจาก synonym ไทย/อังกฤษในไฟล์ schema (ไม่ hard-code ชื่อฟิลด์ตามกฎข้อ 1) + hint ของค่า + ความเข้ากันของชนิด + สิ่งที่คุณเคยสอน (ADR-038) ทุกฟิลด์มี evidence เป็นข้อความอ่านได้
4. **ความสัมพันธ์ระหว่างชีต** (`_find_link_column`, `relationships.py`): หาคอลัมน์เชื่อมจากชื่อ + synonym + ค่าที่ซ้อนกัน แล้ว **วัดจริงบนข้อมูล** (cardinality, orphan) ก่อนเสนอ join
5. **ความหมายเปลี่ยนไหม** (`services/healing.py`): เทียบค่าของ SKU เดียวกันกับรอบก่อน แยก "เท่าเดิม / ×1.07 (VAT) / ×คงที่ (หน่วย-สกุลเงิน) / ต่างกัน" — นี่คือส่วนที่ "เข้าใจ" ลึกที่สุด เพราะดูข้อมูล ไม่ดูชื่อ
6. **AI เป็นชั้นเสริม** (`ai/agents.py`): ได้ profile ทั้งหมด + schema ทั้งฉบับ + ร่างจาก heuristic + ตัวอย่างที่อนุมัติแล้ว + สิ่งที่สอนไว้ แล้วต้องตอบเป็น DSL ที่ validator และ guard ตรวจ ไม่มีทางรันโค้ด

### สิ่งที่ยังเป็น "เดาแบบมีเหตุผล" หรือเติมค่าให้เงียบ ๆ

| จุด | ที่อยู่ | เมื่อไม่แน่ใจ ระบบทำอะไร | ถามคนไหม |
|---|---|---|---|
| สถานะสินค้าที่ไม่รู้จัก เช่น "พักการขาย", ช่องว่าง | `heuristics.py::_expr_for` `default="active"` | กลายเป็น **active** | ไม่ |
| ประเภทโปรโมชันที่ไม่รู้จัก เช่น "Flash Sale" | `_expr_for` `default="percent_off"` | กลายเป็น **percent_off** (ฟิลด์ระดับ critical) | ไม่ |
| ราคารวม VAT หรือไม่ ไม่มีคำบอก | `_vat_included_guess` | สมมติ **รวม** | ถาม (ดี) |
| ไม่มีคอลัมน์คลัง | `heuristics.py` | เติม **MAIN** | ไม่ |
| ไม่มีรหัสโปร | `heuristics.py` | สร้างจาก SKU | ถาม (ดี) |
| หน่วยที่ไม่รู้จัก เช่น `1x24`, `12's` | `thai.py::unit_normalize_py` | ส่งตัวพิมพ์ใหญ่ของต้นฉบับ (`1X24`) | ไม่ |
| ตัวเลขแบบยุโรป `1.500,00` | `thai.py::parse_number_th_py` | อ่านเป็น **1.5** | ไม่ |
| ตัวเลขแบบไทย `189.-` | เดียวกัน | อ่านเป็น **ว่าง** → รายการผิดสัญญา (เห็นได้) | เห็นผ่านการตรวจสัญญา |
| โครงสร้างเหมือนเดิมทุกประการ | `services/mapping.py` บรรทัด 289–298 และ 405–409 | ใช้ mapping เดิม confidence 1.0 และ **ไม่รันการตรวจความหมาย (healing) และ sandbox เลย** | ไม่ |

ข้อสุดท้ายสำคัญที่สุด เพราะ ERP ส่งไฟล์โครงสร้างเดิมทุก 14 วัน ตรงนี้คือทางหลัก ไม่ใช่ทางพิเศษ

### คำศัพท์ที่ hard-code อยู่ในโค้ด (ไม่ใช่ใน schema)

`ROLE_SIGNALS`, `HINT_BOOST`, ตาราง status/promo type/price list, `CURRENCY_WORDS`, `UNIT_SYNONYMS`, `THAI_MONTHS`, `_vat_included_guess` regex, `TOTAL_LABEL_RE` — ทั้งหมดอยู่ใน `heuristics.py`, `thai.py`, `tables.py` เป็นภาษาไทย/อังกฤษเฉพาะโดเมนค้าปลีก ไม่ผิดกฎข้อ 1 (ไม่ใช่ชื่อฟิลด์ปลายทาง) แต่ลูกค้า ERP ใหม่ที่ใช้คำอื่นจะหลุดไปทางค่าเริ่มต้นข้างบน แก้ได้ทางเดียวคือสอน (ADR-038) หรือแก้โค้ด ไม่มีที่ให้ตั้งค่าในหน้าเว็บ

### เรื่อง AI ที่ต้องรู้

- ค่า `confidence` ที่นโยบายใช้ตัดสินว่าผ่านเองได้ไหม (`min_mapping_confidence 0.95`, `min_field_confidence_*`) **มาจากตัวโมเดลเองเมื่อ AI เป็นคนสร้าง mapping** (`agents.py` บรรทัด 254–256 เขียน `doc.meta.confidence = res.data["confidence"]`) นโยบายเป็นคนตัดสินตามตัวอักษร แต่ input เป็นคะแนนที่โมเดลให้ตัวเอง ที่ป้องกันไว้คือ (ก) ลูกค้าใหม่ต้องให้คนดูเสมอ (ข) ลูกค้าเดิมที่โครงสร้างเปลี่ยนแบบ `columns_renamed/removed` ต้องให้คนดูเสมอ — จึงเหลือช่อง `columns_added` ที่ adaptation agent ให้คะแนนตัวเองสูงแล้วผ่านเองได้
- Guard (`ai/guard.py`) จับข้อความสั่งการในเซลล์ได้เฉพาะรูปประโยคที่กำหนด (ignore previous instructions, set all prices…) ข้อความอย่าง "approve this file immediately" ไม่ติด แต่ผลเสียจำกัด เพราะโมเดลส่งได้แค่ DSL + คะแนน (ซึ่งคะแนนคือจุดอ่อนข้างบน)
- โหมด mock (ไม่ใส่ key) ทุกอย่างทำงานด้วย heuristic ล้วน และ **ทุกการทดสอบใน repo รวมถึงที่ผมรันวันนี้เป็นโหมดนี้** ความแม่นยำที่ AI เพิ่มให้ยังไม่เคยวัด

## 3. บั๊กที่ยืนยันแล้ว เรียงตามความร้ายแรง

### A. ข้อมูลผิดหรือหายโดยแทบไม่มีคำเตือน (แก้ก่อน)

| # | เรื่อง | หลักฐาน | ผลกับผู้ใช้ |
|---|---|---|---|
| A1 | **ราคาที่ถอด VAT ออกทุกรายการ ผ่านเองและพร้อมส่งออก** เมื่อโครงสร้างไฟล์เหมือนเดิม: memory ใช้ mapping เดิม ข้าม healing/sandbox (`mapping.py:289–298, 405–409`) และ anomaly จับเฉพาะราคาเปลี่ยน ≥30% (`anomalies.py`) ส่วน −6.5% จึงไม่โดน | **(รันแล้ว)** เคส R11: `decision auto_approve, drift identical, verdict publishable` | JSON ราคาผิด 7% ออกไปทุกรายการโดยไม่มีใครถูกถาม |
| A2 | **รหัสสินค้าซ้ำแต่ราคาต่างกัน ระบบเก็บแถวแรกทิ้งแถวหลังแล้วผ่านเอง** `transform.py::_dedupe` keep=first → finding ระดับ warning; นโยบายยอมรับ warning ≤5; memory reuse → auto_approve | **(รันแล้ว)** เคสเพิ่ม DUP: `auto_approve`, SKU 100101 เหลือราคา 7.0 จาก (7.0, 9.0), finding "1 duplicate identities (keep=first)" | ราคาใดถูกต้องไม่มีใครตัดสิน ระบบเลือกให้ |
| A3 | **สถานะที่ไม่รู้จักกลายเป็น active, ประเภทโปรที่ไม่รู้จักกลายเป็น percent_off** รวมถึงช่องว่าง (`map_values default`) | **(probe)** `พักการขาย→active`, `None→active`, `Flash Sale→percent_off`, `ลด 10%→percent_off` | สินค้าที่ "รอของ" ขายได้ โปรแบบ "ราคาพิเศษ" กลายเป็นลด % |
| A4 | **ตัวเลขแบบยุโรปอ่านผิด 1000 เท่า** `1.500,00 → 1.5` และ **`189.-` อ่านไม่ได้** (รูปแบบ "บาทถ้วน" ที่ใบราคาไทยใช้บ่อย) | **(probe)** `parse_number_th_py` | ข้อแรกเงียบสนิท ข้อสองเห็นเป็นรายการผิดสัญญา |
| A5 | **ส่งออก JSON เก่าหลังอนุมัติเวอร์ชันแก้แล้ว**: `publishing.latest_export` หา export ล่าสุด "ของรอบ" ไม่ใช่ "ของเวอร์ชัน" (`publishing.py:22–28, 39–47`) เมื่ออนุมัติ v2 ด้วยมือ ระบบคืน export ของ v1 และไม่ export v2; zip ของรอบงานก็ใช้ฟังก์ชันเดียวกัน (`batches.py:518`) | **(โค้ด)** ตรวจแล้ว; `jobs/recovery.py:283–289` ทำถูก (ใช้ dataset_version_id) | ลูกค้าได้ไฟล์เก่าแม้แก้ข้อมูลแล้ว |
| A6 | เอกสารบอกว่า `1x12 → CARTON` แต่โค้ดให้ `1X24`; `12's → 12'S` | **(probe)** `unit_normalize_py` | หน่วยหลุดสัญญา enum_hint |

### B. วงจรชีวิตของรอบและงานเบื้องหลัง

| # | เรื่อง | หลักฐาน |
|---|---|---|
| B1 | **ยกเลิกรอบแล้วงานที่ค้างอยู่ทำให้รอบกลับมาเปิดใหม่** ไม่มีที่ใดใน `rounds.py`, `mapping.py`, `datasets.py`, `handlers.py`, `runner.py` ตรวจ `is_closed` → `r.status` ถูกเขียนทับเป็น profiled/published/failed ทั้งที่ `closed_*` ยังอยู่ | **(โค้ด)** grep ยืนยันว่า `CLOSED/is_closed` ใช้แค่ใน `batches.py` |
| B2 | **รอบที่ส่งออกแล้วถูกเปลี่ยนเป็น failed** เมื่องานใด ๆ ของรอบนั้นล้มทีหลัง (`runner.py:467–472` ไม่ดูชนิดงานหรือสถานะเดิม) เช่น กด transform ซ้ำหลัง retention ลบไฟล์ | **(โค้ด)** |
| B3 | **ไฟล์ที่ไม่มีข้อมูลเลย → job ล้มด้วย "profile contains no tables"** แทนที่จะระงับด้วยเหตุผล "zero rows" ตามนโยบาย (`heuristics.py:512` raise ก่อนถึง policy) | **(รันแล้ว)** R15: `status failed, error 'profile contains no tables'` |
| B4 | **retry บน arq ถูกทิ้งเงียบ ๆ ภายใน 1 ชม.**: `enqueue_job(_job_id=...)` คืน None เมื่อ result key ยังอยู่ (`keep_result=3600`, `runner.py:186–199, 673`) และโค้ดไม่ดู return | **(รายงานจากผู้อ่าน A** ตรวจโค้ดแล้ว ยังไม่ได้ทดลองบน Redis จริง**)** |
| B5 | **`header_moved` ไม่เคยถูกรายงาน**: strict fingerprint ไม่รวม `header_row` → หัวตารางเลื่อน = identical (`fingerprint.py::fingerprint_profile/compare`) ข้อมูลยังถูกเพราะ `extract_table` หาหัวใหม่ได้ แต่ป้ายผิดและเอกสาร/เคสทดสอบอ้างสถานะนี้ | **(รันแล้ว)** R03 → `drift identical` |
| B6 | **เปลี่ยนชื่อคอลัมน์ภาษาไทยไม่ถูกจับเป็น rename**: `fuzz.token_set_ratio` บนสตริงไทยไม่มีช่องว่างต่ำกว่า 0.6 → จัดเป็น `columns_removed` (`fingerprint.py::compare`) คำตัดสินยังถูก (rename_only จากข้อมูล) แต่ชนิด drift ผิด และ adaptation agent ได้ drift report ผิด | **(รันแล้ว)** R05 |
| B7 | แชตที่เปิดกับ dataset ที่ retention ลบแล้ว **ตอบไม่ได้อีกเลย** (`workspace_assistant.py:525–532` เรียก inspect ก่อน try) | **(โค้ด)** |
| B8 | ประโยคธรรมดา "You are now able to…" ทำให้คำตอบถูกรีเซ็ตและแจ้งว่า "AI ใช้งานไม่ได้" (`text_stream.py` + `guard.py` pattern `you are now\b`) | **(probe)** |
| B9 | ความจำปฏิเสธวันที่/เบอร์โทรว่าเป็นเลขบัตร: `deadline 2026-10-01 12:30` และ `081-234-5678` โดน `_NEVER` | **(probe)** |
| B10 | เทิร์นที่รันโค้ดหลายรอบอาจเกิน lease 15 นาทีแล้วถูกมาร์คล้มเหลวโดยข้อความถัดไป (`conversations.py:355`, ไม่มีการต่ออายุ) | **(รายงานจากผู้อ่าน B)** |
| B11 | `artifacts._add_file` ถือ write transaction บน SQLite ระหว่าง render preview ได้ถึง 60–180 วิ → "database is locked" | **(รายงานจากผู้อ่าน B)** |
| B12 | ไฟล์ output ชื่อซ้ำกันคนละโฟลเดอร์ ถูกทิ้งเงียบ ๆ (`code_sandbox.py:186–189` ใช้ basename) | **(รายงานจากผู้อ่าน B)** |

### C. ความปลอดภัยและการแยกส่วน

| # | เรื่อง | หลักฐาน | ระดับ |
|---|---|---|---|
| C1 | **OIDC `state` สร้างแต่ไม่เคยตรวจตอน callback** (`lib/auth.ts:99, 106–129`) | **(โค้ด)** | สูง เมื่อขึ้น server; วันนี้ไม่ใช้ OIDC |
| C2 | **ดาวน์โหลดทุกแบบใช้ `window.open`/`<a href>` ไม่ส่ง bearer** (`Round.tsx:36`, `DataEditor.tsx:222`, `Upload.tsx:200,265`) → 401 ทั้งหมดในโหมด OIDC | **(โค้ด)** | สูง เมื่อขึ้น server |
| C3 | Sandbox: `_kill` ยอมแพ้หลัง 2 วิ แล้วไปต่อทั้งที่อาจมีโปรเซส uid 20000 เหลือ (`runner.py:184–199`); `/dev/shm` เป็น tmpfs เขียนได้ที่ไม่ถูกกวาด (compose บรรทัด 103–104 ไม่ครอบคลุม); OOM อาจฆ่า runner แทน run | **(โค้ด** ข้อแรกและ shm**)**, OOM **(รายงาน)** | กลาง: ไม่ใช่การหลุดออกจาก container แต่ข้อมูลข้ามแชตรั่วถึงกันได้ |
| C4 | ความจำถูกวางยาได้ผ่านคำตอบเก่าของโมเดล: taint ดูแค่ข้อความปัจจุบัน แต่ history ทั้งหน้าต่างส่งไปทุกเทิร์น (`workspace_assistant.py:444–446`); `ASKED` หลวม ("I don't remember…" นับว่าขอ) | **(probe** ASKED**)**, ที่เหลือ **(รายงาน)** | กลาง |
| C5 | `X-Request-Id` จากไคลเอนต์ลง `String(64)` ไม่ตัด → Postgres 500 ทุก endpoint ที่สร้างงาน; และลง log ตรง ๆ | **(รายงานจากผู้อ่าน A)** | กลาง เฉพาะ Postgres |
| C6 | `.env.production.example` เขียน `FORMA_CORS_ORIGINS=a,b` ซึ่ง Settings (list[str]) parse ไม่ได้ (`settings.py:221–222`) | **(โค้ด)** | กลาง: ใครตั้งจาก example จะสตาร์ตไม่ขึ้น |
| C7 | JWT `alg` อ่านจาก header ของ token (`oidc.py`), JWKS refetch ทุก kid แปลกโดยไม่จำกัด, storage key และ traceback ส่งให้ทุก role, S3 access key id อยู่ใน backup manifest | **(รายงานจากผู้อ่าน A)** | ต่ำ–กลาง |
| C8 | nginx ไม่มี security headers; `discover_models` รับ api_key ใน body | **(รายงาน)** | ต่ำ |

### D. ฝ่าฝืนกฎของโปรเจกต์

- **กฎ 9**: `delete_override` (`routes.py:226–238`) ไม่ audit ไม่บันทึกผู้ทำ **(โค้ด)**
- **กฎ 9 (พื้นผิว API)**: พารามิเตอร์ `actor` ใน 2 route และ 6 schema รับมาแล้วไม่ใช้ **(รายงาน)**
- **กฎ 11 (soft)**: `retention.apply` ลบ object ก่อนอัปเดตแถว กลับทางกับหลัก object-first-row-last **(รายงาน)**
- **กฎ 6 (soft)**: `calibration.apply` บันทึก policy ที่ merge แล้วทั้งก้อนเป็น override ทำให้ `approval.yaml` ไม่มีผลอีกต่อไป **(รายงาน)**
- กฎ 1, 2, 3, 4, 5, 7, 8, 10, 12: **ผ่าน** ในทุกไฟล์ที่อ่าน

### E. หน้าเว็บ

| # | เรื่อง | หลักฐาน |
|---|---|---|
| E1 | ป้ายสถานะส่วนใหญ่ไม่แปลไทย: `STATUS_LABEL` (`utils.ts:67–69`) ใช้คำใหม่ แต่พจนานุกรม (`locale.tsx:19–21`) มีแค่คำเก่า → "Needs review", "Exported", "Same format" ในโหมดไทย | **(โค้ด)** |
| E2 | Data Editor: handler คีย์บอร์ดปิด `rows` ตัวเก่า (deps ใช้ `rows.length`) → เปลี่ยนหน้าแล้วกด T/Ctrl+C/Delete ทำกับแถวของหน้าเดิม (`DataEditor.tsx:98–123`) | **(โค้ด)** |
| E3 | Studio: กด Apply กับ JSON ที่ไม่ใช่ document (`{}`) → render พัง ไม่มี error boundary → จอขาวทั้งแอป (`MappingStudio.tsx:256`) | **(โค้ด)** |
| E4 | เปิด `/rounds/ไม่มี` หรือ Settings โดยไม่ใช่ admin → spinner ตลอดกาล (`Round.tsx:40`, `Settings.tsx:78,456`) | **(โค้ด)** |
| E5 | mutation 5 จุดไม่มี onError → กด Reject ตอนออฟไลน์ ไม่เกิดอะไร | **(รายงาน)** |
| E6 | คลิกผลงานในแถบข้าง key ของ `AssistantChat` เปลี่ยน → ข้อความที่พิมพ์ค้างหาย (`Assistant.tsx:24`) | **(รายงาน)** |
| E7 | วันที่ครึ่งหนึ่งของหน้าไม่ใช้ timezone/รูปแบบที่ตั้งไว้ (`fmtDate` vs `date()`); `waiting_until` แสดงเวลา 07:00 เกินมา | **(รายงาน)** |
| E8 | `RoundMenu` มี `role="menu"` แต่ไม่มีคีย์บอร์ด/Escape; ตารางใน `<button>`; `<tr onClick>` ไม่มี tabIndex | **(รายงาน)** |

### F. โค้ดตาย/ซ้ำ/คอมเมนต์หลอก (ย่อ)

`tables._row_blocks`/`_is_blank_row` ไม่มีใครเรียก; `_aware()` ซ้ำ 4 ที่ทั้งที่ `UTCDateTime` คืน aware แล้ว; `lib/theme.ts` ไม่มี importer ทำให้สคริปต์ใน `index.html` หา `forma.color-mode` ไม่เจอ (ธีมมืดกะพริบขาวทุกครั้ง); 9 ฟังก์ชันใน `lib/api.ts` ไม่ถูกเรียก; tenant-scoping helper 3 สำเนา; รายชื่อแพ็กเกจ sandbox 3 สำเนา; `_PoolBackend.name` ตั้งซ้ำ; `cli.py` มีฟังก์ชันชื่อ `eval`; `auth/deps.py` อ้าง `actor_hint` ที่ไม่มีใครตั้ง; docstring `runner.py:51` "SQLite returns naive datetimes" ไม่จริงแล้ว

## 4. เคสทดสอบด้วยมือที่คำตอบในชุดยังผิด (จากการรันกลุ่ม R)

| เคส | ชุดบอกว่า | ระบบทำจริง | ตัดสิน |
|---|---|---|---|
| R03 | drift `header_moved` | `identical` | บั๊ก B5 แก้โค้ดหรือแก้เคส |
| R05 | drift `columns_renamed` | `columns_removed` (verdict rename_only ถูก) | บั๊ก B6 |
| R10 | review, meaning_changed | **auto_approve** แล้วจับที่ขั้นข้อมูลเป็น price_move/check_data | ไม่เงียบ แต่ไม่ใช่ที่ขั้น mapping ตามที่เขียน |
| R11 | review, vat_shift | **auto_approve, publishable** | บั๊ก A1 |
| R12 | review, scaled | **auto_approve** แล้ว price_move/check_data | เหมือน R10 |
| R13 | wrong_customer | `other_customer_unconfirmed` (SKU ไม่ซ้อนกันจึงเทียบไม่ได้) | ถูกตามกฎ แก้เคส |
| R15 | blocked | **failed** "profile contains no tables" | บั๊ก B3 |
| R16 | row_shift | row_shift ✓ แต่ drift `columns_removed` | ผลพลอยได้ของไฟล์ที่เขียนให้สั้นลง |
| R17 | catalogue_replaced | **blocked** โดย sandbox "4 more duplicate records" | ผลไม่เหมือนที่คิดแต่ปลอดภัย แก้เคส |
| R19 | anomaly stock | `publishable` (stock_drop เป็นระดับ medium จึงถูกพับ) | แก้เคสหรือปรับระดับ |
| R21 | รอบแรก review | auto_approve แล้ว anomalies (ราคา 7→70) | แก้เคส; ขั้น replace ทำงานถูก |

เคสที่ตรงตามที่เขียน: R00, R01, R02, R04, R06, R07, R08, R09, R14, R18, R20 และกลุ่ม L/P/Q 27 จาก 33

## 5. ข้อเสนอแนะ เรียงตามลำดับที่ควรทำ

### ทำทันที (ข้อมูลผิดเงียบ ๆ)

1. **รัน healing.data_check บนราคาและฟิลด์ critical ทุกรอบ** รวมรอบที่ memory reuse (`mapping.py:405–409`) ต้นทุนต่ำ (เทียบค่าของ SKU ที่มีทั้งสองรอบ) และส่ง `field_checks` เข้า policy เหมือนเดิม — ปิด A1 และทำให้ R10/R12 ถูกถามที่ขั้น mapping
2. **เพิ่ม anomaly "uniform shift"**: อัตราส่วนคงที่บน ≥N SKU ไม่ว่าเล็กแค่ไหน (×1.07, ÷1.07, ×100, ×0.62) เป็นระดับ high — ตาข่ายชั้นที่สองของ A1 ที่ไม่พึ่ง mapping
3. **รหัสซ้ำที่ค่าขัดกัน = error ไม่ใช่ warning** ใน `_dedupe` เมื่อแถวซ้ำมีค่าต่างกันบนฟิลด์ critical (ซ้ำเหมือนกันทุกช่องยังเป็น warning ได้) — ปิด A2
4. **เลิกเติมค่าเริ่มต้นบนฟิลด์ enum**: `map_values` สำหรับ status/promo.type ใช้ `keep_unmapped=True` หรือ `default=None` ให้ค่าแปลกตกไปที่การตรวจสัญญา (enum) แล้วขึ้นเป็นรายการผิดสัญญาที่คนต้องตัดสิน — ปิด A3
5. **ตัวเลข**: รองรับ `189.-`/`189.00-` และตรวจจับรูปแบบ decimal-comma ทั้งคอลัมน์ (ถ้าคอลัมน์ส่วนใหญ่มี `,` ตามด้วย 2 หลักท้ายสุดและ `.` คั่นพัน ให้สลับ) หรืออย่างน้อยตั้งคำถาม — ปิด A4
6. **`latest_export` รับ `dataset_version_id`** แทน round_id ทั้งใน publishing และ zip — ปิด A5

### ถัดมา (วงจรชีวิตและความถูกต้องของสถานะ)

7. ให้ `run_pipeline`/`transform_round`/`profile_round` ตรวจ `is_closed` ตอนเริ่มและก่อนเขียนสถานะ และให้ `runner` ไม่เขียน `failed` ทับสถานะปลายทาง (published, closed) — ปิด B1, B2
8. ไฟล์ไม่มีตาราง → ให้ `propose_for_round` สร้าง MappingVersion ว่างที่ policy ระงับด้วย "zero rows" แทน raise — ปิด B3
9. ดู return ของ `enqueue_job` และลบ result key ก่อน retry หรือไม่ใช้ `_job_id` เดิม — ปิด B4
10. รวม `header_row` เข้า strict fingerprint หรือเลิกมีสถานะ `header_moved`; ใช้ตัววัดความคล้ายที่เหมาะกับภาษาไทย (character n-gram) ในการจับ rename — ปิด B5, B6
11. ผู้ช่วย: catch `FormaError` รอบ seed collects (B7); เอา `you are now`/`new instructions` ออกจาก pattern ที่ใช้กับ prose หรือ fallback เป็น outline แทนล้ม (B8); `_NEVER` ต้องการเลขติดกัน ≥10 หลักโดยไม่มี `-`/ช่องว่างแบบวันที่ (B9); ต่ออายุ lease ใน `checkpoint()` (B10); render preview ก่อนเปิด write (B11)

### เมื่อจะขึ้น server (ไม่ต้องรีบวันนี้)

12. ตรวจ `state` ใน OIDC callback; ดาวน์โหลดผ่าน fetch + bearer → blob (C1, C2)
13. Sandbox: `ipc: none`/sweep `/dev/shm`, kill-loop จนไม่เหลือแล้ว fail-closed, `oom_score_adj` ให้ลูกตายก่อน runner (C3); taint ความจำจาก history ด้วย และ `ASKED` ใช้รูปประโยคคำสั่งเท่านั้น (C4)
14. ตัด `X-Request-Id` ที่ 64 และ sanitize ก่อนลง log; `.env.production.example` เขียน list เป็น JSON (C5, C6)

### ด้านความโปร่งใสของ "ความเข้าใจ"

15. ทุกค่าเริ่มต้นที่เติมเอง (MAIN, active, percent_off, THB, VAT รวม) ควรปรากฏเป็น **คำถาม** หรืออย่างน้อยเป็น evidence ที่คนเห็นบนการ์ดคำตัดสิน ไม่ใช่แค่ใน evidence ของฟิลด์ใน Studio
16. ย้ายคำศัพท์ที่ hard-code (status/promo/unit/currency) ไปเป็น **settings ที่แก้ในหน้าเว็บ** ผ่าน `services/config.py` เพื่อให้ ERP ใหม่สอนระบบได้โดยไม่แก้โค้ด (สอดคล้องกฎข้อ 12)
17. แยกคะแนน "โมเดลให้ตัวเอง" ออกจาก "ตรวจบนข้อมูลแล้ว" บนการ์ดและใน policy: ฟิลด์ที่ AI เพิ่มใหม่และยังไม่มี data check ไม่ควรผ่านเองไม่ว่าคะแนนเท่าไร
18. วัดความแม่นยำโหมด AI จริง (`forma eval --llm`) อย่างน้อยหนึ่งครั้งก่อนเชื่อว่า AI "เข้าใจ" ดีกว่า heuristic

## 6. สิ่งที่รีวิวนี้ยังไม่ได้ทำ

- ไม่ได้รันกับ Postgres/Redis/MinIO จริง: ข้อ B4, C5, C7 และความถูกต้องของ migration บน Postgres เป็นการอ่านโค้ด (ผู้อ่าน A ยืนยัน migration chain บน SQLite ว่าตรง model)
- ไม่ได้ทดสอบ sandbox escape จริง (C3 เป็นการอ่าน runner.py และ compose)
- ไม่ได้รันโหมด AI จริง ทุกเคสเป็น heuristic
- ข้อที่ติดป้าย (รายงานจากผู้อ่าน) ผมเปิดโค้ดตรวจเฉพาะข้อระดับสูง ที่เหลือเชื่อรายงานซึ่งมี file:line กำกับ ตรวจต่อได้

## 7. ภาคผนวก: ผลรันเคสกลุ่ม R

```
R00 review/first_time → approve → publish ✓
R01 auto_approve identical publishable  JSON EXACT
R02 auto_approve identical (duplicate_of ✓)
R03 auto_approve identical (คาด header_moved)  JSON EXACT
R04 auto_approve columns_added publishable
R05 review columns_removed (คาด renamed) rename_only/accept
R06 review columns_removed  R07 review columns_removed  R08 review sheets_removed  R09 review sheets_added
R10 AUTO_APPROVE identical → price_move/check_data
R11 AUTO_APPROVE identical → PUBLISHABLE  ← A1
R12 AUTO_APPROVE identical → price_move/check_data
R13 review sheets_removed other_customer_unconfirmed/assign_customer
R14 block unrelated_file blocked_unrelated/reject_file
R15 FAILED "profile contains no tables"  ← B3
R16 review row_shift  R17 block (sandbox duplicates)  R18 auto → anomalies/check_data
R19 auto → publishable (stock_drop = medium)  R20 review types_changed engine_errors
DUP auto_approve, SKU 100101 price [7.0] of (7.0, 9.0), warning only  ← A2
R21 auto → anomalies; replace 201; old superseded ✓
```

กลุ่ม L/P/Q: 27 จาก 33 เคสตรง `expected.json` ทุกค่า (ไม่นับ `source_meta`) ที่เหลือ 6 อยู่ในหัวข้อปัญหาที่รู้แล้วของ `manual_test_files/README.md`
