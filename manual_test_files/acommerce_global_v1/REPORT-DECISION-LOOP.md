# ผลปรับวงจรถาม–ตรวจ–เรียนรู้ FORMA

วันที่ 3 พฤศจิกายน 2026 · DeepSeek `deepseek-flash` · stack จริง PostgreSQL/S3/Redis/ARQ

## สิ่งที่เปลี่ยนทั้ง 6 ข้อ

| ข้อ | ก่อน | หลัง |
|---|---|---|
| 1. คำถามที่นำไปใช้ได้จริง | คำตอบหลายชนิดถูกแทนเป็นค่าคงที่ | แยก parser วันที่, สกุลเงินตาม contract, กฎแปลรหัสหน่วย, ยืนยันการอ่าน และเลือกแถวที่ขัดกัน |
| 2. ตรวจผลหลังตอบ | ตัวอย่างบนหน้าจอไม่ครอบคลุมทุกแถว | preview/apply รันครบไฟล์ ป้องกันรายการหาย/เพิ่ม ข้อมูลส่วนอื่นเปลี่ยน และ validation error ใหม่ |
| 3. จำคำตอบ | ค่าบางอย่างถูกจำเหมารวม | เริ่มเรียนรู้เมื่ออนุมัติ เก็บผู้ตอบ/ต้นทาง/ขอบเขต ค่าคงที่ต้องเลือกให้จำ ข้อยกเว้นเฉพาะแถวไม่ใช้ข้ามรอบ |
| 4. ใช้ความรู้ก่อนถาม | ไม่แยกความรู้ขัดแย้งหรือถูกถอน | ตรวจลูกค้า contract รูปแบบ เงื่อนไขงาน และ expression; ขัดแย้งหรือถอนแล้วไม่เรียกใช้ หน่วยใหม่ถามใหม่ |
| 5. ลดคำถาม/ข้อผิดพลาดเทียม | VAT รายแถวอาจโดนถามให้แทนทั้งไฟล์; สต็อกหลายคลังถูกมองว่าซ้ำผิด | รักษาค่ารายแถว ตรวจ grain ของ collection; แก้ schema refresh, queue retry และ CSV เปลี่ยนชื่อ |
| 6. วัดผลซ้ำ | คะแนนก่อนแก้ครั้งเดียว | ชุดจำลองต่างประเทศ + contract อีกแบบ + รันทวนจุดเสี่ยง + replay คำตอบคนผ่าน API และ regression |

## ขอบเขตและผลที่วัดได้

ไม่มีไฟล์ลูกค้าจริงในรอบนี้ ตามคำยืนยันผู้ใช้ ใช้ชุดจำลองเดิมและไม่เปลี่ยนเฉลยเพื่อทำให้คะแนนดีขึ้น
รอบสุดท้าย `intl-release-a` + `intl-release-b` มี 17 งาน (16 กรณี + contract อีกแบบ) บน prompt 2026.11.03.3 เก็บ hash ของโค้ดและ worker image ใน metadata.json; intl-dl4 เป็นรอบทวน 5 กรณีเสี่ยงก่อนปรับ prompt ล่าสุด
กรณีโจทย์ชัดเจน 13 งาน: ฟิลด์ที่เฉลยกำหนดถูก 1428/1428 = **100.00%** ก่อนคนช่วย
Field accuracy ไม่เท่ากับความพร้อมใช้งานทั้งหมด: ต้องดูข้อมูลที่เกินเฉลย, จำนวนแถว, verifier, คำถาม และการยืนยันทางธุรกิจด้วย
จำนวนการ์ดในตารางอ่านซ้ำผ่าน API หลัง patch รวมคำถามหน่วยที่โมเดลกับ server ถามซ้ำ (`questions-current.json`); prediction และคะแนนเดิมไม่ถูกแก้หรือเลือกใหม่
หลังเก็บ prediction พบกรณีสัญลักษณ์สกุลเงินฝังในราคาที่ต้องใช้เงื่อนไข contains เดิมแทนเทียบทั้งเซลล์ จึงแก้ชั้นใช้คำตอบและทดสอบใหม่ผ่าน preview/apply รวมทั้งเทสที่ยืนยันว่าราคาและแถวที่ไม่มีสัญลักษณ์ไม่เปลี่ยน ไม่ใช้การรันโมเดลใหม่มาแทน prediction เดิม
G07 มีการหยุดที่ harness ฝั่ง Windows ขณะ replace state.tmp (Access denied) แล้ว resume จาก checkpoint เดิม เก็บ error.json และ eval-release-a-resume.log ไว้ ไม่จัดเป็น job/โมเดลล้ม

## ก่อน–หลัง ต่อกรณี (ยังไม่ตอบคำถาม)

| กรณี | ก่อน Field % | หลัง Field % | Exact record % | แถว | การ์ด ก่อน→หลัง | ข้อสงสัยค้าง | verifier | ธง |
|---|---:|---:|---:|---:|---|---:|---|---|
| G01_th_split_r1 | 100.00 | 100.00 | 100.00 | 12 | 10→0 | 1 | True | — |
| G02_id_decimal_r1 | 100.00 | 100.00 | 100.00 | 12 | 2→2 | 1 | True | — |
| G03_my_warehouses_r1 | 100.00 | 100.00 | 100.00 | 12 | 2→1 | 0 | True | — |
| G04_ph_variants_r1 | 100.00 | 100.00 | 0.00 | 12 | 7→7 | 0 | True | — |
| G05_sg_currency_alt | 100.00 | 100.00 | 100.00 | 12 | 0→0 | 2 | True | — |
| G05_sg_currency_r1 | 100.00 | 100.00 | 100.00 | 12 | 3→2 | 1 | True | — |
| G06_fr_amount_r1 | 100.00 | 100.00 | 100.00 | 12 | 3→1 | 2 | True | — |
| G07_de_integer_r1 | 100.00 | 100.00 | 100.00 | 12 | 2→2 | 2 | True | — |
| G08_us_dates_r1 | 100.00 | 100.00 | 100.00 | 12 | 2→2 | 1 | True | — |
| G09_jp_utf16_r1 | 100.00 | 100.00 | 100.00 | 12 | 3→1 | 1 | True | — |
| G10_kr_workbook_r1 | 100.00 | 100.00 | 0.00 | 12 | 2→1 | 1 | True | — |
| G11_active_price_r1 | 100.00 | 100.00 | 0.00 | 12 | 8→7 | 0 | True | — |
| G12_row_tax_r1 | 100.00 | 100.00 | 100.00 | 12 | 3→1 | 0 | True | — |
| G13_ask_date_r1 | 100.00 | 100.00 | 100.00 | 12 | 3→3 | 2 | True | — |
| G14_ask_currency_r1 | 87.50 | 87.50 | 0.00 | 12 | 4→3 | 1 | True | — |
| G15_ask_unit_r1 | 87.50 | 87.50 | 0.00 | 12 | 2→3 | 1 | True | — |
| G16_ask_conflict_r1 | 100.00 | 100.00 | 91.67 | 12 | 2→4 | 2 | True | extra collection items |

## จุดที่ยังต้องตรวจ ไม่รวมเป็นความผิดแบบเดียวกัน

### G04_ph_variants_r1

ข้อมูลเกินเฉลย 12 จุด; ตัวอย่าง:
```json
[
  {
    "key": "'STYLE-1-BLK-40'",
    "path": "/attributes/parent_style",
    "value": "STYLE-1",
    "reason": "unasserted output fact"
  },
  {
    "key": "'STYLE-1-BLK-41'",
    "path": "/attributes/parent_style",
    "value": "STYLE-1",
    "reason": "unasserted output fact"
  }
]
```

### G10_kr_workbook_r1

ข้อมูลเกินเฉลย 12 จุด; ตัวอย่าง:
```json
[
  {
    "key": "'00470001'",
    "path": "/attributes",
    "value": {
      "가격표": "standard",
      "세전 가격": "25000",
      "세금 포함": "아니오",
      "세율": "5%"
    },
    "reason": "unasserted output fact"
  },
  {
    "key": "'00470002'",
    "path": "/attributes",
    "value": {
      "가격표": "standard",
      "세전 가격": "26000",
      "세금 포함": "아니오",
      "세율": "5%"
    },
    "reason": "unasserted output fact"
  }
]
```

### G11_active_price_r1

ข้อมูลเกินเฉลย 12 จุด; ตัวอย่าง:
```json
[
  {
    "key": "'00470001'",
    "path": "/attributes",
    "value": {
      "Record state": "active"
    },
    "reason": "unasserted output fact"
  },
  {
    "key": "'00470002'",
    "path": "/attributes",
    "value": {
      "Record state": "active"
    },
    "reason": "unasserted output fact"
  }
]
```

### G14_ask_currency_r1

ค่าต่างจากเฉลย (กรณี G13–G16 มีข้อมูลธุรกิจที่ต้องถามก่อน):
```json
[
  {
    "key": "'00470001'",
    "path": "/currency",
    "expected": "SGD",
    "actual": "USD",
    "missing": false
  },
  {
    "key": "'00470002'",
    "path": "/currency",
    "expected": "SGD",
    "actual": "USD",
    "missing": false
  },
  {
    "key": "'00470003'",
    "path": "/currency",
    "expected": "SGD",
    "actual": "USD",
    "missing": false
  },
  {
    "key": "'00470004'",
    "path": "/currency",
    "expected": "SGD",
    "actual": "USD",
    "missing": false
  }
]
```

### G15_ask_unit_r1

ค่าต่างจากเฉลย (กรณี G13–G16 มีข้อมูลธุรกิจที่ต้องถามก่อน):
```json
[
  {
    "key": "'00470001'",
    "path": "/unit/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'00470002'",
    "path": "/unit/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'00470003'",
    "path": "/unit/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'00470004'",
    "path": "/unit/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  }
]
```

### G16_ask_conflict_r1

ข้อมูลเกินเฉลย 1 จุด; ตัวอย่าง:
```json
[
  {
    "key": "'00470001'",
    "path": "/prices",
    "value": {
      "price_list": "standard",
      "amount": 9999.0,
      "vat_included": false,
      "vat_rate": 0.05
    },
    "reason": "extra collection member"
  }
]
```
ธง: extra collection items

## ทวนจุดเสี่ยงก่อนปรับคำอธิบาย DSL รอบสุดท้าย

| กรณี | Field % | แถว | การ์ด | verifier | ธง |
|---|---:|---:|---:|---|---|
| G12_row_tax_r1 | 100.00 | 12 | 2 | True | — |
| G13_ask_date_r1 | 100.00 | 12 | 3 | True | — |
| G14_ask_currency_r1 | 87.50 | 12 | 3 | True | — |
| G15_ask_unit_r1 | 87.50 | 12 | 3 | True | — |
| G16_ask_conflict_r1 | 100.00 | 12 | 4 | False | extra collection items, verification false |

## ความผิดจริงที่พบระหว่างพัฒนาและเก็บไว้

ใน intl-dl3/G04 โมเดลส่ง nested target ของ attributes ที่ไม่ได้อยู่ใน DSL catalogue ทั้งที่ JSON Schema เปิด object ได้ guard จึงปฏิเสธและใช้ heuristic fallback: field accuracy 70%, อยู่สถานะ review ไม่ได้อนุมัติอัตโนมัติ แก้คำสั่งให้ใช้ SourceSpec.rename + attributes_passthrough และไม่อ้างว่า BT เป็นหน่วยที่ระบบรู้จักแล้ว
ผลรอบสุดท้ายของ G04 อยู่ในตารางหลักด้านบน; รันทวนบน build สุดท้ายอีกครั้ง:
- G04_ph_variants_r1: Field 100.00%, Exact 0.00%, flags=[]

## หลังตอบข้อมูลที่ขาดผ่าน preview/apply จริง

คำตอบจากโจทย์เฉลยใช้เฉพาะขั้นจำลองผู้ตรวจหลังเก็บ prediction แล้ว ไม่ป้อนเฉลยให้โมเดลก่อน map

| กรณี | Field % | แถว | Full effect check | ยังต้องตรวจเพิ่ม |
|---|---:|---:|---|---:|
| G13_ask_date | 100.00 | 12 | True | 1 |
| G14_ask_currency | 100.00 | 12 | True | 0 |
| G15_ask_unit | 100.00 | 12 | True | 0 |
| G16_ask_conflict | 100.00 | 12 | True | 1 |

## การเรียนรู้ข้ามรอบที่ทดสอบกับ API จริง

กรณี G15: รอบแรกยืนยัน BT=BOTTLE และตรวจธงภาษี/รายการราคาให้ตรงไฟล์ จากนั้นอนุมัติเฉพาะข้อมูลจำลอง รอบถัดไป source=memory, field accuracy=100.00%, exact record=100.00%, คำถาม=0, LLM calls=0 ไม่มีการส่งออก
เทส regression แยกยืนยันว่าเปลี่ยนชื่อ CSV อย่างเดียวใช้รูปแบบเดิมได้ แต่เปลี่ยนรหัสหน่วยเป็น XYZ ต้องถามใหม่ และความรู้ข้ามลูกค้า/contract/เงื่อนไขงานหรือที่ถอนแล้วใช้ไม่ได้
หลักฐาน: runs/decision-memory-release และ runs/intl-release-b/G15_ask_unit_r2_memory

## การตรวจโค้ด

- Full regression ยืนยันก่อน commit วันที่ 3 พ.ย. 2026: 829 passed, 5 skipped จาก 834 tests; coverage 91.41% ผ่านเกณฑ์ 80% (`precommit-check.log` ซึ่งเป็นไฟล์ local ที่ Git ไม่เก็บ)
- Ruff ผ่าน; mypy ผ่านทั้ง 132 source files; frontend tests 7/7 ผ่าน; TypeScript/Vite build ผ่าน
- Frontend lint ไม่มี error แต่ยังมี warnings ของ repository; build มีคำเตือน chunk size
- Eval gate ชุดสังเคราะห์เดิมผ่านเกณฑ์ baseline (`eval-gate-decision-loop.log`)

## ต้นทุนและหลักฐาน

ชุดสุดท้าย 17 งานรวมกับรอบทวน intl-dl4 อีก 5 งาน มี `cost_usd` จาก usage ใน agent_log = **USD 1.913409** เป็นค่าประเมินของระบบ ไม่ใช่ใบแจ้งหนี้ DeepSeek; ตารางราคา fallback อาจไม่ตรงผู้ให้บริการ
รวม mapping runs ระหว่างพัฒนางานนี้ทั้ง 7 ชุด (ไม่รวม baseline ก่อนงานนี้) = **USD 5.490201 ตาม log** ไม่ใช่ยอดเรียกเก็บจริง และไม่รวมงานอื่นนอก mapping agent_log
รอบพัฒนาก่อน build สุดท้ายเก็บแยกไว้ที่ intl-dl1, intl-dl2 และ decision-loop-replay ไม่นำมาปนเป็นผลของ build สุดท้าย
รายละเอียดรายกรณี: runs/intl-release-a, runs/intl-release-b, runs/intl-release-check, runs/intl-dl4 (before.json, score-before.json, job JSON, state และ metadata)

## ความหมายต่อการนำไปใช้

พร้อมก้าวไปสู่ pilot ที่มีผู้ตรวจและตรวจผลก่อนส่งออก ไม่ใช่หลักฐานว่าถูก 100% กับทุก ERP หรือเทียบเท่า ChatGPT/Claude ทุกกรณี การถามข้อมูลธุรกิจที่ไม่มีในไฟล์เป็นพฤติกรรมที่ถูกต้อง
ความรู้คือกฎที่คนอนุมัติแล้วและมีขอบเขต ไม่ใช่การฝึกน้ำหนักโมเดลใหม่; ไม่รับประกันว่ารอบถัดไปจะไม่ถาม หากหลักฐานหรือความหมายเปลี่ยน
ขั้นถัดไปเมื่อมีไฟล์จริง: ทำชุด holdout ที่ไม่ใช้แก้ prompt, ให้เจ้าของข้อมูลตรวจคำตอบ และวัด critical-field error, จำนวนคำถามต่อไฟล์, เวลา review และสัดส่วนอัตโนมัติ

หลักการออกแบบและแหล่งอ้างอิงผู้ให้บริการ: docs/adr/062-typed-decisions-and-scoped-memory.md
