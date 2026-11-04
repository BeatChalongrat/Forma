# ผลทดสอบ LangGraph + 3 roles กับ DeepSeek เดิม — 4 พฤศจิกายน 2026

## สรุปผลที่วัดได้

- ก่อนเพิ่ม graph: **97.74%** (563/576 ค่าที่ตรวจ)
- หลังเพิ่ม graph รอบเปรียบเทียบครบชุด: **97.74%** (563/576)
- คะแนนนี้เป็นความตรงของค่ากับเฉลย ก่อนคนตอบคำถาม ไม่ใช่เปอร์เซ็นต์ความพร้อมใช้จริง
- T09 และ T10 เป็นเรื่องที่ต้องให้คนตัดสิน ตรวจคำถามและทดสอบ preview/apply แยกจากคะแนนดิบ
- กรณี T08 ไม่มีไฟล์อ้างอิง: เฉลยยอมรับค่า null จึงได้คะแนนเต็ม แต่ผลยังไม่พร้อมใช้งานและต้องบล็อกไว้
- ผลชุดนี้ยังไม่พิสูจน์ว่าเพิ่ม agent แล้ว map แม่นขึ้น หรือแม่นกว่า ChatGPT/Claude

## วิธีทดสอบ

รันผ่าน API → PostgreSQL/Redis/ARQ worker → DeepSeek → เครื่องมืออ่านไฟล์/sandbox → engine ตรวจข้อมูลจริง บน stack ในเครื่อง ใช้ `deepseek-flash` และไม่มี fallback model ทั้งสองฝั่ง รันเรียงทีละกรณี ลูกค้าแยกกัน ไม่อนุมัติหรือส่งออกข้อมูล ใช้ไฟล์และ JSON contract ชุดเดียวกัน ตรวจ hash ก่อนรันและเก็บ proposal ก่อนอ่านเฉลย

เลือก 10 กรณี รอบ r1: key หลายคอลัมน์, merged headers, สูตรค่าค้าง, สูตรไม่มี cache, อ้าง workbook ที่หายไป, หน่วย BT, ราคาขัดกัน, หลายชีต/ล็อต/ช่วงวันที่, CSV อินโดนีเซีย และ TSV ภาษาญี่ปุ่น UTF-16 ทุกกรณีมีสินค้า 12 รายการ ชุดนี้เป็น regression ที่เคยใช้พัฒนาระบบ ไม่ใช่ holdout ใหม่ ไม่ได้รันครบ 25 กรณีหรือทุก r2/r3 และไม่ได้ทดสอบโหลดพร้อมกัน 300 ไฟล์

ก่อน = `wf-before` โหมด legacy หลัง = `wf-after` graph รุ่น `2026.11.04.1` ส่วน `wf-evidence-fix` เป็นการรันซ้ำเฉพาะกรณีหลังแก้การส่งต่อหลักฐาน รุ่น `2026.11.04.2` เก็บผลรอบแรกไว้ทั้งหมด ไม่แทนที่ผลที่พบปัญหา ไม่ควรเรียกผลแบบผสมนี้ว่าการรันครบชุดบนรุ่นล่าสุด

## ความถูกต้องและคำถามต่อกรณี

| กรณี | ก่อน | หลังรอบแรก | แถวหลัง | คำถามก่อน → หลัง (ชนิด:จำนวน) | Verification หลัง |
|---|---:|---:|---:|---|---|
| G02_id_decimal_r1 | 100.00% | 100.00% | 12 | confirm:2 → confirm:2 | True |
| G09_jp_utf16_r1 | 100.00% | 100.00% | 12 | confirm:3 → confirm:1, number:1 | True |
| T02_r1 | 100.00% | 100.00% | 12 | 0 → 0 | True |
| T04_r1 | 100.00% | 100.00% | 12 | confirm:2 → confirm:2 | True |
| T06_r1 | 100.00% | 100.00% | 12 | 0 → confirm:1 | True |
| T07_r1 | 100.00% | 100.00% | 12 | 0 → 0 | True |
| T08_r1 | 100.00% | 100.00% | 12 | confirm:1 → confirm:1 | False |
| T09_r1 | 66.67% | 66.67% | 12 | text:1 → text:1 | True |
| T10_r1 | 97.92% | 97.92% | 12 | enum:1, number:1 → enum:1, number:1 | False |
| T12_r1 | 100.00% | 100.00% | 12 | confirm:2 → confirm:2 | True |

## เวลาและต้นทุน

| กรณี | วินาทีก่อน → หลัง | calls ที่มี usage ก่อน → หลัง | cost_usd ก่อน → หลัง |
|---|---:|---:|---:|
| G02_id_decimal_r1 | 44.4 → 154.7 | 2 → 5 | 0.0633 → 0.1705 |
| G09_jp_utf16_r1 | 40.5 → 136.6 | 2 → 6 | 0.0581 → 0.1996 |
| T02_r1 | 50.2 → 185.2 | 4 → 5 | 0.0866 → 0.1759 |
| T04_r1 | 28.3 → 64.5 | 3 → 4 | 0.0517 → 0.0751 |
| T06_r1 | 121.9 → 193.5 | 4 → 6 | 0.1435 → 0.1663 |
| T07_r1 | 116.6 → 249.0 | 4 → 9 | 0.0990 → 0.2294 |
| T08_r1 | 194.6 → 321.6 | 8 → 13 | 0.1922 → 0.3889 |
| T09_r1 | 43.4 → 58.1 | 2 → 4 | 0.0459 → 0.0644 |
| T10_r1 | 107.4 → 202.6 | 6 → 8 | 0.1646 → 0.2553 |
| T12_r1 | 303.4 → 621.9 | 6 → 9 | 0.2718 → 0.4789 |

รวมรอบเปรียบเทียบ: 17.51 → 36.46 นาที; 41 → 69 calls; cost_usd 1.1769 → 2.2043 (1.87 เท่า)

รวมการทดลองทั้งก่อน หลัง และรอบแก้เพิ่มเติม: **120 calls ที่มี usage; cost_usd 3.6772**

**cost_usd ไม่ใช่ยอดเรียกเก็บจริงจาก DeepSeek:** โมเดลนี้ไม่มีราคาในตาราง PRICING เดิม จึงใช้ fallback $1/input 1M tokens และ $4/output 1M tokens; ไม่ได้หัก cache discount งบใน runner เป็น checkpoint หยุดระหว่างกรณี ไม่ใช่เพดานค่าใช้จ่ายจากผู้ให้บริการ กรณี T12 ฝั่ง graph มี 1 attempt ที่ไม่มี usage ตอบกลับ จึงไม่ทราบว่าคิดเงินเท่าใด ตัวเลขรวมไม่รวมต้นทุนที่ไม่ทราบนี้ และ legacy ไม่มี ledger เทียบ failed attempts ได้ครบ เวลาเป็น wall-clock จากการทดลองครั้งเดียว รวม API/งานในเครื่องและความผันผวนของโมเดล ไม่ใช่ latency benchmark

## สิ่งที่พบและแก้ระหว่างทดสอบ

T06 mapper เปิดสูตรใน sandbox และคำนวณใหม่ถูกแล้ว แต่ reviewer ไม่ได้รับ tool receipt จึงถามให้เลือก cached value กับผลคำนวณใหม่ ทั้งที่สูตรมีหลักฐานอยู่จริง แก้โดยส่งผล inspection และ code/output ของ sandbox พร้อม source IDs/hashes ให้ reviewer รวมทั้งส่ง scoped operator decisions เพื่อไม่ทำให้ความรู้ที่เคยยืนยันหายไประหว่าง roles ยังไม่ส่ง confidence หรือคำสรุปของ mapper ให้เป็นเหตุผลในการผ่านตรวจ เพิ่ม validation ป้องกัน confirm card ที่เสนอทางเลือกซึ่ง action นั้นเปลี่ยนค่าให้ไม่ได้

| กรณีรันทวนรุ่น .2 | ความถูกต้อง | คำถาม | เวลา (วินาที) | calls | cost_usd |
|---|---:|---|---:|---:|---:|
| T06_r1 | 100.00% | 0 | 197.1 | 10 | 0.2960 |

## การตรวจว่า mapping ตอบสนองต่อข้อมูลรอบใหม่จริง

ใน T12 reviewer พบว่า candidate ฉบับแรกฝัง pack size เป็น literal lookup แม้ผลรอบนี้ถูก จึงให้แก้เป็น join กับชีต Pack sizes แล้วตรวจซ้ำ ผลปลายทางยังถูกครบ ทดสอบเพิ่มเติมโดยใช้ engine เดิมและเปลี่ยน pack size ของ SKU 000001 จาก 12 เป็น 18 ในหน่วยความจำ ไม่แก้ fixture และไม่เรียกโมเดล:

- ค่าที่ควรได้จากจำนวนในแต่ละล็อต: `{'BKK': 57.0, 'CNX': 76.0}`
- Candidate ก่อน reviewer แก้: `{'BKK': 39.0, 'CNX': 52.0}` — ไม่เปลี่ยนตามต้นทาง
- หลัง reviewer แก้: `{'BKK': 57.0, 'CNX': 76.0}` — เปลี่ยนถูกต้อง
- Legacy ในรอบ baseline: `{'BKK': 57.0, 'CNX': 76.0}` — ทำถูกอยู่แล้วเช่นกัน

นี่เป็นหลักฐานว่า reviewer จับข้อผิดพลาดเชิงการนำ mapping กลับมาใช้ได้ใน candidate หนึ่ง ไม่ได้พิสูจน์ว่า graph ชนะ legacy โดยรวม หรือรับประกันการเปลี่ยนข้อมูลทุกแบบ ไฟล์ตรวจซ้ำ: `sensitivity.py`, `sensitivity-result.json`, `t12-initial-mapping.json`

## ทดสอบ human in the loop

| ฝั่ง | กรณี | หลังตอบ | แถว | การตรวจผลใหม่ | คำถามคงเหลือ |
|---|---|---:|---:|---|---:|
| wf-before | T09_r1 | 100.00% | 12 | True | 0 |
| wf-before | T10_r1 | 100.00% | 12 | True | 1 |
| wf-after | T09_r1 | 100.00% | 12 | True | 0 |
| wf-after | T10_r1 | 100.00% | 12 | True | 1 |

T09 ตอบ BT = BOTTLE แบบ value_map; T10 เลือกแถวราคา 152 เฉพาะ SKU 000004 แบบ select_rows ไม่ตั้งราคาทุกสินค้าเป็น 152 ใช้ preview + digest + apply จริง สร้าง mapping version ใหม่ ไม่มี API call ไปยังโมเดลในขั้นตอนตอบ และไม่มีการ approve/export

ยังพบคำถามราคาอีกหนึ่งคำถามค้างหลังเลือกแถว T10 ทั้งสองฝั่ง แม้ข้อมูลและ verification ถูกแล้ว เป็นงานลดคำถามซ้ำที่ยังต้องปรับ ไม่ควรแก้โดยล้างทุกคำถามของฟิลด์นั้น เพราะอาจลบปัญหาของสินค้าอื่น

## ธงแดงและขอบเขตของผล

- `wf-before`: `{"job_failed": [], "rows_mismatch": [], "extra_rows": [], "llm_failed": [], "questions_over_8": [], "verification_false": ["T08_r1", "T10_r1"], "unknown_usage_attempts": null}`
- `wf-after`: `{"job_failed": [], "rows_mismatch": [], "extra_rows": [], "llm_failed": [], "questions_over_8": [], "verification_false": ["T08_r1", "T10_r1"], "unknown_usage_attempts": 1}`
- `wf-evidence-fix`: `{"job_failed": [], "rows_mismatch": [], "extra_rows": [], "llm_failed": [], "questions_over_8": [], "verification_false": [], "unknown_usage_attempts": 0}`

Verification false ของไฟล์อ้างอิงหายและราคาขัดกันเป็นการบล็อกที่จำเป็น workflow.status เป็นสถานะของ proposal ตอนนั้น ส่วนสถานะหลังคนตอบต้องดู verification ของ mapping version ใหม่ ทุกเคสชุดนี้อยู่ใน review จึงไม่ได้พิสูจน์ความปลอดภัยของ auto-approval ทุกสถานการณ์

## สิ่งที่เพิ่มในระบบ

1. LangGraph จริง: Evidence Analyst → Mapping Builder → engine ตรวจทั้งไฟล์ → Semantic Reviewer; ย้อนแก้ได้สูงสุด 1 รอบเมื่อพบ mapping error
2. Checkpoint PostgreSQL สำหรับ production / SQLite สำหรับ dev; snapshot เปลี่ยนตามไฟล์ schema คำสั่ง ความรู้ โมเดลและเวอร์ชัน; มี lock กัน workflow เดียวทำซ้อน
3. เพดาน 18 provider attempts และ deadline ถาวร; สำรองจำนวนครั้งก่อนเรียกจริง เก็บ usage แม้ JSON ไม่ผ่าน validation และระบุ usage ที่ไม่ทราบจากคำขอที่ขัดจังหวะ
4. หลักฐานและเวลาแต่ละ role อยู่ใน agent_log / policy.workflow / inspection trace; คำถามใช้ระบบ preview/apply/versioning เดิม ไม่ได้เพิ่ม LangGraph interrupt สำหรับคนตอบ
5. Format เดิมที่อนุมัติแล้วใช้ fast path เดิม ไม่เรียก 3 roles ซ้ำ; มี integration test ยืนยันว่า API calls ไม่เพิ่ม

## การตรวจโค้ด

- Backend regression: 890 passed, 5 skipped, 2 deselected; coverage 91.24% (เกณฑ์ 80%)
- หลังแก้ handoff เพิ่มเติม: workflow tests 19 passed; mypy ผ่าน 140 source files; Ruff ผ่านไฟล์ที่เพิ่ม
- PostgreSQL smoke test: checkpoint/attempt/usage อยู่ครบเมื่อปิดแล้วเปิด connection ใหม่ ไม่มี model call
- Tests ครอบคลุม crash/resume, ไม่เรียกซ้ำเมื่อจบแล้ว, เปลี่ยน snapshot, budget/deadline หลัง restart, invalid evidence, bounded repair, failed execution, actionable questions และ upload/profile/propose API

## ข้อสรุปสำหรับใช้งาน

โครงสร้างพร้อมให้ทดลองแบบมีคนตรวจมากขึ้น: ดูย้อนหลัง แยกหน้าที่ ตรวจซ้ำ และจำกัดการลองได้ แต่ผลรอบนี้ยังไม่รองรับคำกล่าวว่าความแม่นยำสูงขึ้นจาก LangGraph และมีต้นทุน/เวลาสูงขึ้น ควรใช้กับ format ใหม่หรือเปลี่ยนที่ต้องตรวจละเอียด และรักษา fast path สำหรับ format ที่อนุมัติแล้ว

งานที่ยังควรทำ: ลดคำถามซ้ำหลังแก้ปัญหา, เพิ่มเครื่องมืออ่านสูตรโดยตรงให้ reviewer ตรวจเองได้ง่าย, ตั้ง retention สำหรับ checkpoint ที่เก็บข้อมูลลูกค้า และเก็บ holdout/ไฟล์ ERP จริงที่ปกปิดข้อมูล ก่อนสรุปความพร้อม production การใช้โมเดลเดียวทุก role ยังอาจผิดแบบเดียวกันได้ ไม่ได้ทดสอบเหนือกว่า ChatGPT/Claude และไม่รับประกัน format ใหม่ทุกชนิด

รายละเอียดสถาปัตยกรรม: [ADR-067](../../docs/adr/067-mapping-role-workflow.md). อ้างอิง [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) และ [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).

**ยังไม่ commit และไม่ push**
