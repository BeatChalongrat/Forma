# ผลก่อน–หลังแก้ FORMA: ไฟล์ที่มีความสัมพันธ์ซับซ้อน

วันที่ 4 พฤศจิกายน 2026 · โมเดล deepseek-flash · ไม่ commit · ใช้ Docker stack จริง

## วิธีอ่านผล

ทดสอบ 12 กรณี / 14 ไฟล์ Excel / 30 ชีต / กรณีละ 12 สินค้า โดยใช้ไฟล์และ schema เดิมที่ตรวจ SHA-256 แล้ว แต่สร้างลูกค้าและรอบใหม่ แยกคำทำนายก่อนคนช่วยออกจากหลังคนตอบ ระบบเห็นเฉพาะไฟล์ โจทย์ และ schema ไม่ส่ง expected JSON หรือคำตอบที่ซ่อนไว้ให้โมเดล ไม่มีการอนุมัติหรือส่งออกชุดทดสอบนี้เป็นข้อมูลใช้งาน

**ตัวเลขนี้เป็นผลชุดจำลอง ไม่ใช่เปอร์เซ็นต์ความพร้อมใช้จริงหรือหลักฐานว่าดีกว่า ChatGPT/Claude** ฐานก่อนแก้มี API 503/วงจรพัก provider หลายกรณี ดังนั้นส่วนที่ดีขึ้นเกิดจากทั้งการแก้ระบบและรอบ provider ที่สำเร็จ ไม่สามารถยกทั้งหมดเป็นความฉลาดของโมเดลที่เพิ่มขึ้นได้

ชุดนี้ถูกใช้ค้นหาบั๊กและปรับระบบมาแล้ว จึงเป็น regression suite ไม่ใช่ blind holdout ของผู้พัฒนา แม้ไม่ส่งเฉลยให้โมเดลก็ตาม การวัดความสามารถกับ format ใหม่จริงต้องใช้ชุดใหม่ที่ยังไม่เคยใช้ปรับระบบและไฟล์ ERP จริงเพิ่มเติม

- Field accuracy แบบนับทุกค่ารวมกัน ก่อนแก้: **48.00%** (288/600)
- หลังแก้ ก่อนคนช่วย: **97.83%** (587/600)
- หลังตอบคำถามจำลองที่ตรวจแล้ว: **100.00%** (600/600)
- เฉพาะ 6 กรณีที่ฐานเดิมสร้างผลได้แล้ว เปรียบเทียบก่อนคนช่วย: 96.00% → 96.00%. ความก้าวหน้าหลักรอบนี้คือทำกรณีที่เคยไปต่อไม่ได้ให้ตรวจสอบและดำเนินการได้ ไม่ใช่หลักฐานว่าเปลี่ยนโมเดลให้ฉลาดขึ้นสองเท่า
- การเก็บ null และขอไฟล์ที่หายตามเฉลยของ T08 นับเป็นการหยุดอย่างถูกต้อง ไม่ใช่ได้จำนวนสินค้าที่พร้อมใช้งาน

## 6 เรื่องที่แก้

| เรื่อง | ก่อน | หลัง |
|---|---|---|
| วันที่มีผลของราคา | comparison ส่ง Date ผ่านตัวแปลงตัวเลข ทำให้กรองผิด | เทียบ Date/Datetime ตามชนิดจริง พร้อมทดสอบวันที่และตัวเลขแยกกัน |
| สูตร Excel | สูตรไม่มี cache แม้ไม่ใช้งานอาจบล็อกทั้งชีต | ตรวจคอลัมน์ที่ใช้จริง; คอลัมน์ที่ใช้แล้วมี error/ไม่มี cache/อ้างไฟล์ภายนอกยังบล็อกพร้อมตำแหน่งเซลล์; AI สร้าง DSL จากตัวตั้งที่ครบได้ |
| คำถามค้าง/ความเห็นไม่ตรง | ชื่อ alias ใหม่ถูกมองเป็นคนละคอลัมน์ และคำถาม heuristic ค้าง | เปรียบเทียบชื่อจริงหลัง rename และปิดเฉพาะคำถามโครงสร้างที่พิสูจน์ได้ ไม่ปิดเรื่อง VAT/ความหมายรหัสเพราะมั่นใจเฉย ๆ |
| Provider และเวลารอ | response ผิดรูปแบบ/keepalive อาจรอนานหรือผิดพลาดไม่ชัด | ตรวจรูปแบบ response, wall-clock timeout ต่อ call และงบเวลารวม, แสดงขั้นตอนที่ worker กำลังทำ |
| การอนุมัติ | คนอาจยืนยันข้าม verification ที่ล้มเหลว | ปฏิเสธทั้ง API และ UI เมื่อ verification ไม่ผ่าน/ไม่ครบ แม้ส่ง override; ขอไฟล์ต้นทางไม่ใช่กรอกค่าทับสูตร |
| ความสัมพันธ์และการตัดสินใจ | กรณีราคาชน/สต๊อกขาด/รวมล็อตเคยถูก provider บัง | ทดสอบครบ; เพิ่ม aggregate แบบระบุ group+sum, รักษาแถวต้นทางทุกล็อต, ราคาแข่งขันกันให้เลือกแถว ไม่รวมราคาและไม่ทับทุก SKU |

## ผลรายกรณี ก่อนคนช่วย

การ์ด/หมายเหตุ = จำนวนคำถามบนการ์ด / open_questions; self-check = fix_rounds / grounded / python_turns; เวลาเป็นวินาทีตั้งแต่เริ่มกรณีจน job propose เสร็จ รวมอัปโหลด/profile/คิว ไม่ใช่เฉพาะเวลาโมเดล

| กรณี | ก่อน | หลัง | แถว ก่อน→หลัง | การ์ด/หมายเหตุ ก่อน→หลัง | verify หลัง | self-check หลัง | เวลา ก่อน→หลัง (s) |
|---|---:|---:|---|---|---|---|---|
| T01 Three files, shuffled joins | 100.00% | 100.00% | 12→12 | 2/2→0/0 | True | 0/3/0 | 75.26→69.58 |
| T02 Company + item composite key | 100.00% | 100.00% | 12→12 | 0/0→0/0 | True | 0/0/0 | 40.38→55.04 |
| T03 Date + currency + price list | 0.00% | 100.00% | 0→12 | 1/1→2/0 | True | 0/6/1 | 772.93→97.72 |
| T04 Merged headings and currency outside table | 100.00% | 100.00% | 12→12 | 2/0→2/0 | True | 0/2/2 | 30.44→48.97 |
| T05 Cross-sheet formula with correct cache | 100.00% | 100.00% | 12→12 | 0/0→0/0 | True | 1/0/2 | 73.42→88.21 |
| T06 Formula result cache contradicts inputs | 100.00% | 100.00% | 12→12 | 0/0→0/0 | True | 0/0/1 | 230.15→101.11 |
| T07 Formula has no stored result | 0.00% | 100.00% | 0→12 | 0/0→0/0 | True | 1/0/2 | 132.23→260.61 |
| T08 Missing external formula workbook | 0.00% | 100.00% | 0→12 | 0/0→1/1 | False | 2/0/1 | 7.47→244.15 |
| T09 Undefined unit in separate reference sheet | 66.67% | 66.67% | 12→12 | 1/2→1/1 | True | 0/2/0 | 1143.56→40.27 |
| T10 Duplicate price key without precedence | 0.00% | 97.92% | 0→12 | 1/1→1/2 | False | 2/3/1 | 8.31→258.46 |
| T11 Unmatched stock keys and missing stock | 0.00% | 100.00% | 0→12 | 0/1→0/0 | True | 1/2/1 | 62.41→146.94 |
| T12 Lots + units + validity + multiple sheets | 0.00% | 100.00% | 0→12 | 1/2→2/0 | True | 0/6/2 | 8.36→332.97 |

## คำถาม ความเสี่ยง และรายละเอียดคำตอบ

### T01: Three files, shuffled joins

- decision: `review` · disagree: 1 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T01_r1/before.json) · [score](runs/rel-20261104-fixed2/T01_r1/score-reviewed.json)

### T02: Company + item composite key

- decision: `review` · disagree: 0 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T02_r1/before.json) · [score](runs/rel-20261104-fixed2/T02_r1/score-reviewed.json)

### T03: Date + currency + price list

- decision: `review` · disagree: 3 · flags: ไม่มี
- งาน profile/propose: done/done
- คำถาม `/price`: /price · kind=confirm · action=None · contested=True
- คำถาม `/currency`: /currency · kind=confirm · action=None · contested=True
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T03_r1/before.json) · [score](runs/rel-20261104-fixed2/T03_r1/score-reviewed.json)

### T04: Merged headings and currency outside table

- decision: `review` · disagree: 4 · flags: ไม่มี
- งาน profile/propose: done/done
- คำถาม `/price`: /price · kind=confirm · action=None · contested=True
- คำถาม `/currency`: /currency · kind=confirm · action=None · contested=True
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T04_r1/before.json) · [score](runs/rel-20261104-fixed2/T04_r1/score-reviewed.json)

### T05: Cross-sheet formula with correct cache

- decision: `review` · disagree: 0 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T05_r1/before.json) · [score](runs/rel-20261104-fixed2/T05_r1/score-reviewed.json)

### T06: Formula result cache contradicts inputs

- decision: `review` · disagree: 0 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T06_r1/before.json) · [score](runs/rel-20261104-fixed2/T06_r1/score-reviewed.json)

### T07: Formula has no stored result

- decision: `review` · disagree: 0 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T07_r1/before.json) · [score](runs/rel-20261104-fixed2/T07_r1/score-reviewed.json)

### T08: Missing external formula workbook

- decision: `review` · disagree: 2 · flags: verification false
- งาน profile/propose: done/done
- คำถาม `/qty`: The stock formula multiplies Counts!B by missing-pack.xlsx Pack factors!B, but that external workbook is not in the upload and this workbook's 'Pack factors'!B2:B13 (header 'Pieces per carton') is empty. Please supply the pieces-per-carton factor for each SKU (file missing-pack.xlsx, sheet 'Pack factors', cells B2:B13, or fill in this workbook's 'Pack factors'!B2:B13). · kind=confirm · action=request_source · contested=True
  - หลักฐาน/เหตุผล: qty must be in individual pieces; the only reproducible path is cartons (Counts!B2:B13) times pieces-per-carton (Pack factors!B2:B13). Without the factor column every qty stays null, and the cached constant 999 in Export!C2:C13 is an artefact of the broken external link, not stock evidence.
- หมายเหตุค้าง: The stock formula multiplies Counts!B by missing-pack.xlsx Pack factors!B, but that external workbook is not in the upload and this workbook's 'Pack factors'!B2:B13 (header 'Pieces per carton') is empty. Please supply the pieces-per-carton factor for each SKU (file missing-pack.xlsx, sheet 'Pack factors', cells B2:B13, or fill in this workbook's 'Pack factors'!B2:B13).
- ไม่ผ่านการตรวจ: /qty: empty in 12 of 12 records
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T08_r1/before.json) · [score](runs/rel-20261104-fixed2/T08_r1/score-reviewed.json)

### T09: Undefined unit in separate reference sheet

- decision: `review` · disagree: 0 · flags: ไม่มี
- งาน profile/propose: done/done
- คำถาม `/sell_unit:value:9ed0965ce03b`: The only unit code in the workbook is 'BT' (12/12 materials) and there is no unit legend. What full selling-unit name does 'BT' represent? · kind=text · action=value_map · contested=True
  - หลักฐาน/เหตุผล: The contract requires a full selling unit name and the operator states the meaning of BT is unspecified. The workbook gives no synonym table, pack-size or description to expand the abbreviation, so a full name cannot be derived from source evidence.
- หมายเหตุค้าง: The only unit code in the workbook is 'BT' (12/12 materials) and there is no unit legend. What full selling-unit name does 'BT' represent?
- ค่าที่ยังไม่ตรงเฉลยก่อนคนช่วย:
```json
[
  {
    "key": "'000001'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000002'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000003'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000004'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000005'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000006'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000007'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000008'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000009'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000010'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000011'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  },
  {
    "key": "'000012'",
    "path": "/sell_unit",
    "expected": "BOTTLE",
    "actual": "BT",
    "missing": false
  }
]
```
- หลังตอบจริงผ่าน preview/apply: 100.00%, 12 แถว, verification=True, การ์ดเหลือ 0, หมายเหตุเหลือ 0
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T09_r1/before.json) · [score](runs/rel-20261104-fixed2/T09_r1/score-reviewed.json)

### T10: Duplicate price key without precedence

- decision: `review` · disagree: 1 · flags: verification false
- งาน profile/propose: done/done
- คำถาม `row:f0c2b80e3339d0a5e1ce4219`: รายการ 000004 มีข้อมูลขัดแย้งกัน ให้ใช้รายการต้นทางใด? · kind=enum · action=select_rows · contested=True
  - หลักฐาน/เหตุผล: Choose one source row for this record; other records are unchanged.
- หมายเหตุค้าง: No SKU-like header found in root table; using 'Selling price' as identity — confirm
- หมายเหตุค้าง: รายการ 000004 มีข้อมูลขัดแย้งกัน ให้ใช้รายการต้นทางใด?
- ไม่ผ่านการตรวจ: 1 duplicate identities on ['/sku'] (1 of them with different values in selling_price, /price; keep=review)
- ไม่ผ่านการตรวจ: dedupe: 1 duplicate identities on ['/sku'] (1 of them with different values in selling_price, /price; keep=review)
- ค่าที่ยังไม่ตรงเฉลยก่อนคนช่วย:
```json
[
  {
    "key": "'000004'",
    "path": "/price",
    "expected": 152,
    "actual": 9999.0,
    "missing": false
  }
]
```
- หลังตอบจริงผ่าน preview/apply: 100.00%, 12 แถว, verification=True, การ์ดเหลือ 0, หมายเหตุเหลือ 0
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T10_r1/before.json) · [score](runs/rel-20261104-fixed2/T10_r1/score-reviewed.json)

### T11: Unmatched stock keys and missing stock

- decision: `review` · disagree: 1 · flags: ไม่มี
- งาน profile/propose: done/done
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T11_r1/before.json) · [score](runs/rel-20261104-fixed2/T11_r1/score-reviewed.json)

### T12: Lots + units + validity + multiple sheets

- decision: `review` · disagree: 3 · flags: ไม่มี
- งาน profile/propose: done/done
- คำถาม `/price`: /price · kind=confirm · action=None · contested=True
- คำถาม `/currency`: /currency · kind=confirm · action=None · contested=True
- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/T12_r1/before.json) · [score](runs/rel-20261104-fixed2/T12_r1/score-reviewed.json)

## ต้นทุนและขอบเขต

- cost_usd ที่บันทึกใน mapping.agent_log รอบแก้ครั้งแรก: $1.629458
- cost_usd ที่บันทึกใน mapping.agent_log รอบยืนยัน: $1.436454
- ตัวเลขเป็นประมาณการจาก usage ที่ระบบบันทึก ไม่ใช่ใบแจ้งหนี้; call ที่ล้มเหลวก่อนส่ง usage อาจมีต้นทุนที่ไม่ปรากฏ และ ledger รวม schema/agent อื่นจึงอาจต่างจาก mapping.agent_log
- `source-repair-guard.json` บันทึกการลอง confirm ข้อมูลต้นทางที่หาย: API ต้องตอบ 422 ไม่สร้างเวอร์ชันใหม่
- ชุดทดสอบไม่มีไฟล์ลูกค้าจริง จึงยังไม่ครอบคลุม Excel ทุก format, macro, encrypted workbooks, ขนาดหลายล้านแถว หรือ network/provider outage ทุกแบบ
- ไม่ใช่เครื่องคำนวณสูตร Excel ทุกชนิด: ถ้าค่าไม่ครบหรือสูตรแปลงเป็น DSL ที่รองรับไม่ได้ ต้องซ่อม/คำนวณต้นทางแล้วส่งใหม่
- Legacy mapping ที่ไม่มี verification snapshot ยังมีเส้นทาง compatibility เดิม ต้องทดสอบ/ตรวจใหม่ก่อนนำไปใช้เป็นหลักฐานความถูกต้อง

## การตรวจโค้ดและข้อจำกัดคงเหลือ

- Backend regression ทั้งชุดบน Windows: **867 passed, 5 skipped** ใน 695.95 วินาที ไม่มี failure (รวมการกู้ worker ที่หยุดกลางงาน)
- Frontend: **12/12 tests ผ่าน**, production build ผ่าน; lint ไม่มี error แต่ยังมี warning เดิมเรื่อง effect/fast refresh
- Ruff ผ่าน, Mypy ผ่าน 135 source files, `git diff --check` ผ่าน
- Synthetic evaluation gate ผ่าน baseline เดิม (`eval-gate-final.json`)
- รันกฎที่แช่ไว้ซ้ำบน runtime รุ่นสุดท้าย **14 snapshots**: 12 ก่อนคนช่วย + 2 หลังคนตอบ ผล records ตรงเดิมทั้งหมด (`final-replay.json`)
- ตรวจ hash โค้ด Python ทั้ง 135 ไฟล์ใน API และ worker ตรงกับ working tree ทุกไฟล์ (`deployed-code-check.json`)
- Coverage บน Linux: **91.35%** ผ่านเกณฑ์ 85%; **866 passed, 4 skipped, 2 deselected**, ไม่มี failure ใน 910.92 วินาที ([log](coverage-linux.txt)) — แยก stress test 2 รายการออกเฉพาะรอบเก็บ coverage; ทั้งสองผ่านแล้วในชุด Windows เต็มด้านบน

### การตัดสินใจที่ถามถูกจุด

- T08 ระบุชื่อไฟล์ `missing-pack.xlsx`, ชีต `Pack factors`, ช่วง `B2:B13` และข้อมูล pieces-per-carton ที่ขาด ไม่เชื่อค่า cache 999 และไม่แทนด้วย 0; verification ยังเป็น false และไม่สามารถยืนยันข้ามได้
- T09 คง `BT` ไว้จนคนตอบ `BOTTLE`; หลังตอบเปลี่ยนเฉพาะ `/sell_unit` ทั้ง 12 รายการ ถูกครบ ไม่มีแถวหาย/เพิ่ม และการ์ด/หมายเหตุเหลือ 0/0
- T10 เก็บปัญหาราคา 9999 กับ 152 ของ SKU `000004` ไว้ให้คนเลือก หลังเลือกแถว 152 เปลี่ยนเพียง 1 ฟิลด์ของ 1 สินค้า อีก 11 สินค้าไม่เปลี่ยน verification ผ่าน และการ์ด/หมายเหตุเหลือ 0/0
- T11 ไม่สร้างสินค้า orphan `999999` เพิ่มเข้าผล และคง stock ที่ไม่พบเป็น null แทน 0; มี warning ว่า left join ขาดหนึ่งรายการ การถามเพื่อเติมสต๊อกเป็นงานต่อเนื่องได้ แต่ไม่ใช่ข้อผิดพลาดของ nullable contract นี้
- T12 รวมล็อตหลังแปลงหน่วยได้ถูกทุกคลังทุกสินค้า โดยไม่ทิ้งล็อตที่บังเอิญมีจำนวนเท่ากัน

### ข้อผิดพลาดที่พบและแก้ระหว่างยืนยันผล

รอบ fixed1 โมเดลถามเรื่องตัวคูณที่ขาดถูกจุด แต่เสนอ action ที่อาจทับค่าจำนวนสุดท้ายด้วยค่าคงที่ จึงเพิ่ม `request_source` และป้องกันการใช้ constant ทับสูตรคำนวณ กรณีราคาเคยเสนอ value-map รหัสสินค้าไปยังคอลัมน์ราคา จึงเปลี่ยนเป็น source-row decision ที่ผูกกับสินค้าและแถวจริง

รอบ fixed2 เมื่อทดลองตอบแถวราคาจริง พบตัวตรวจผล preview ยังถือว่าทุก row choice ต้องเป็น collection ทำให้ HTTP 500 ก่อนบันทึก แก้ให้รองรับ root-row choice โดยตรวจเฉพาะฟิลด์ที่เปลี่ยนได้ในสินค้าเดียวกัน พร้อม regression ที่จงใจเปลี่ยนสินค้าคนอื่นแล้วต้องถูกปฏิเสธ หลังแก้ preview/apply สำเร็จและได้ 100% ไม่ได้รันโมเดลใหม่เพื่อเลือกคำตอบที่ดีกว่า

มีการ bump เวอร์ชันกฎ verdict เพื่อให้หน้าสรุปเก่าเปลี่ยนตามกฎใหม่ และตรวจผ่าน browser แล้วว่าข้อที่ต้องขอไฟล์ต้นทางไม่มีช่องกรอกค่าหรือปุ่มยืนยันค่าแทนไฟล์

### สิ่งที่ยังควรทำต่อ

1. ทดสอบไฟล์ ERP จริงที่ปกปิดข้อมูลแล้ว โดยเฉพาะหลายภาษาและรูปแบบไม่เคยเห็น; 12 กรณีนี้ยังไม่ใช่ acceptance ของลูกค้าจริง
2. ลดคำถามเชิงเทคนิคที่ยังเหลือ: T03, T04, T12 มีคำถามยืนยัน 2 ข้อแม้ค่า JSON ตรงเฉลย ควรลดด้วยหลักฐานเพิ่มเติม ไม่ลดเพียง confidence threshold
3. ทำการขอไฟล์/เติมข้อมูลที่ขาดให้ครบวงจร เช่น T08 เมื่อได้ไฟล์ภายนอกมาแล้ว ต้องรันใหม่และยืนยันการคำนวณ ไม่ถือว่าสถานะ null คือทำงานเสร็จ
4. Lineage ของ aggregate เก็บแถวล็อตครบแล้ว แต่การแสดงเซลล์จาก lookup ที่ซ้อนอยู่ใน collection ทุกระดับยังควรเพิ่ม coverage; ไม่ควรอ้างว่าหลักฐานเซลล์ครบทุก operand ในทุกรูปแบบ
5. ความเร็ว/ต้นทุนยังแปรตาม provider และจำนวนรอบ self-check; deadline จำกัด AI I/O แต่ไม่ใช่ตัวตัดงาน CPU ทั้ง pipeline จึงยังต้องมี load test แยก

### หลักฐานและขอบเขตรุ่นโค้ด

- [audit ณ จบรอบโมเดล](runs/final-audit-fixed.json): ไฟล์ต้นทาง 14 ไฟล์และ schema 12 ชุดตรง hash, profile/propose 24 jobs เป็น done, worker image ไม่เปลี่ยนระหว่างการประเมิน
- [audit หลังแก้เส้นทางตอบคำถาม](runs/post-answer-audit.json): เปลี่ยนเพียงกฎหน้าสรุปและการตรวจขอบเขต row decision หลังคำทำนายถูกแช่ไว้; engine/parser/provider/prompt ของรอบ fixed2 ไม่เปลี่ยน
- [ผลก่อนคนช่วย](runs/rel-20261104-fixed2/summary.json), [คำตอบหน่วย](runs/rel-20261104-fixed2/T09_r1/human-preview.json), [คำตอบราคา](runs/rel-20261104-fixed2/T10_r1/human-preview.json)
- ต้นทุนที่ ledger บันทึกของ fixed1 + fixed2 รวม **$3.065912**; เป็นประมาณการ usage ไม่ใช่ยอดเรียกเก็บจาก DeepSeek
- ไม่ commit และไม่ย้อนคืนงานเดิมที่อยู่ใน working tree ก่อนเริ่มงานนี้
