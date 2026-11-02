# ผลรัน FORMA stack จริงกับ DeepSeek — 25 กรณี / 33 รอบ

วันที่รายงาน: 2026-11-02T21:54:50+07:00 · โมเดล `deepseek-flash` · repo commit `628c94625a9e318b6a822b05261829ec924c9758`

## ข้อสรุปจากผลที่วัดได้

- รันครบ **25 กรณี / 33 รอบที่มีไฟล์จริง**: r1 จำนวน 25 รอบ และ r2/r3 ของ C01, C03, C16, C17 อย่างละ 4 รอบ ไม่มีรอบซ้ำ ไม่มี crash; job done 33/33, เผยแพร่แล้ว 33/33, ได้ 12 SKU ทุกครั้ง ไม่มี missing/extra SKU และ invalid_records = 0 ทุก dataset
- เทียบ r1 ด้วย **scorer เดียวกับ baseline**: field accuracy **97.22% → 99.25%** (+2.03 จุดเปอร์เซ็นต์); ดีขึ้น 9, แย่ลง 1, เท่าเดิม 15 กรณี
- exact-record accuracy ด้วย scorer เดียวกัน: **64.67% → 88.00%**; เป็นการเทียบ JSON อย่างเคร่งครัด รวมชื่อ price list/จำนวน price entries จึงมีกรณีเรื่องตัดสินที่ถูกนับไม่ตรงด้วย
- `score.field_accuracy` ของ airun ตามคำสั่ง: ค่าเฉลี่ย r1 **98.85%**, ทุก 33 รอบ **98.93%**; ทุกคะแนนเป็น macro average ของกรณี/รอบ ไม่ใช่การอ้างความแม่นในประชากรไฟล์ ERP ทั้งหมด
- คำถามบนการ์ดรวม **80 ข้อ** (boolean 7, choice 11, confirm 48, enum 4, number 10); contested **71 ข้อ**; decision ตอนเสนอ mapping: review 30, auto_approve 3
- ต้นทุนรวมตาม `cost_usd` ใน `mapping.agent_log`: **$2.159846**; ผลรวมเวลาทุกรอบ `seconds_total` **1684.4 วินาที / 28.07 นาที** รวมการอัปโหลด ตอบคำถาม อนุมัติ และส่งออก
- ความผิดจริงที่ยืนยัน: **C09** AI map VAT แบบแยกแถวถูก แต่คำตอบจำลองทำให้กลายเป็นค่าคงที่ผิด 6 SKU; **C17** BT ถูกแปลงเป็น BAG แทน BOTTLE ผิด 6 SKU ต่อรอบ และ r2 auto-approve ความผิดเดิมซ้ำ
- เรื่องตัดสิน C10/C13/C25: **ผ่านเกณฑ์การตัดสินของงานนี้ทั้ง 3 กรณี** เพราะมี choice/confirm บนการ์ด; คงคะแนนดิบไว้ตามจริง ไม่แก้เฉลยหรือเปลี่ยนคะแนนเป็น 100% เพื่อกลบความต่าง
- ทุก `verification.passed` เป็น true แต่ยังมีความผิดเชิงความหมาย จึงสรุปไม่ได้ว่า verification ผ่าน = map ถูกทั้งหมด

## วิธีรันและขอบเขตการตีความ

ตรวจ `GET http://127.0.0.1/ready` ก่อนและหลังรันได้ `status: ok`; ตรวจ `GET /api/settings/ai` ก่อนและหลังเป็น `deepseek-flash` โดยไม่ได้เปลี่ยน provider/model/key ระหว่างงาน โมเดลที่บันทึกใน proposal ที่เรียก AI เป็น deepseek-flash

รันจาก repo root ด้วยคำสั่งที่ได้รับ:

```powershell
backend\.venv\Scripts\python manual_test_files/tools/airun.py cases manual_test_files/cases/report-llm.json
backend\.venv\Scripts\python manual_test_files/cases/tools/compare.py manual_test_files/cases/report-heuristic.json manual_test_files/cases/report-llm.json
```

สคริปต์ airun ใช้ stack จริงและโมเดลจริง จากนั้นจำลองคนตอบ VAT จาก expected.json, ยืนยัน confirm และเลือกตัวเลือกแรกของ choice/enum ก่อนอนุมัติ/แปลง/ส่งออก ดังนั้น `score.field_accuracy` เป็น **ผลหลังขั้นตอนคนจำลอง** ไม่ใช่คะแนนโมเดลล้วนก่อนมีคนช่วย และไม่ใช่ fully autonomous accuracy

มี baseline เฉพาะ r1 ทั้ง 25 กรณี จึงเทียบ baseline เฉพาะ r1; r2/r3 รายงานความถูกต้องตามเฉลยของรอบนั้น แต่ไม่ถือว่า delta กับ baseline r1 เป็นผลก่อน–หลังที่เทียบกันได้

`compare.py` ตามคำสั่งใช้ baseline จาก `forma benchmark` ซึ่งเฉลี่ยทุก leaf field ของ schema เท่ากัน (รวมฟิลด์ที่ทั้งสองฝั่งไม่มีค่า) แต่ฝั่ง airun ใช้ scorer ที่นับใบข้อมูลใน expected.json และจับคู่ collection ด้วย key; ชื่อ price_list เองไม่ถูกนับคะแนน แต่ใช้เป็น key ของราคา จึงเกิดโทษทางอ้อมได้ ผล compare ดิบ 97.22% → 98.85%, ดีขึ้น 9/แย่ลง 2/เท่าเดิม 14 **ไม่ใช่ apples-to-apples** จึงเพิ่มการ re-score got.json ด้วยฟังก์ชัน benchmark เดิม โดยไม่ได้แก้สคริปต์หรือ baseline

ตรวจ hash ไฟล์ต้นทาง / schema / gold เทียบกับ baseline: **True / True / True** ทั้ง 25 กรณี ผล re-score และหลักฐาน preview อยู่ในไฟล์ประกอบ รายงานไม่ส่ง gold ให้โมเดล mapping แต่สคริปต์ใช้ gold ในขั้นตอบแทนคนตามที่อธิบายข้างต้น

r1 ใน live stack ยังได้รับ learning/anchors จากประวัติที่มีอยู่ และกรณีก่อนหน้าใน run เดียวกันเริ่มถูกอนุมัติเข้าระบบแล้ว จึงเป็นการวัดระบบปัจจุบันตามลำดับจริง **ไม่ใช่ cold benchmark ที่ล้างความรู้ตระกูล ERP ทั้งหมด** baseline เป็น deterministic offline mapping จึงมีความต่างทั้ง model + memory + ขั้นคนจำลอง ไม่สามารถยกผลต่างทั้งหมดเป็นคุณภาพโมเดลอย่างเดียว

## ตารางครบทุกกรณีและรอบ

Accuracy คือ `score.field_accuracy` หลังขั้นคนจำลอง; C = confirm, Ch = choice, B = boolean, N = number, E = enum; contested นับจาก `person.questions`; disagree นับจาก `mapping.agreement.disagree`; F/G/P = fix_rounds / grounded / python_turns ของ self-check สุดท้าย; — หมายถึง memory reuse ที่ไม่มี self-check agent ใหม่ ไม่ใช่ข้อมูลหาย เวลา Job/รวมเป็นวินาที Decision เป็นตอนเสนอ mapping ก่อนตอบ/อนุมัติ ทุกแถว verification complete/passed = true

| กรณี | รอบ | Accuracy | rows_got | คำถามบนการ์ด: จำนวน/ชนิด | contested | disagree | decision | drift | F/G/P | Job/รวม (s) |
|---|---|---:|---:|---|---:|---:|---|---|---|---:|
| C01_wide_stock_thai | r1 | 100.00% | 12 | 3 (Ch1, C2) | 2 | 3 | review | — | 0/6/0 | 48.3/52.5 |
| C01_wide_stock_thai | r2 | 100.00% | 12 | 0 | 0 | 0 | auto_approve | identical | — | 3.1/3.2 |
| C01_wide_stock_thai | r3 | 100.00% | 12 | 0 | 0 | 3 | review | columns_renamed | 0/6/0 | 27.2/30.6 |
| C02_two_header_rows_codes | r1 | 100.00% | 12 | 0 | 0 | 0 | review | — | 0/7/0 | 45.3/48.5 |
| C03_sections_subtotals | r1 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | — | 0/4/0 | 36.2/40.0 |
| C03_sections_subtotals | r2 | 100.00% | 12 | 0 | 0 | 0 | auto_approve | identical | — | 3.1/3.4 |
| C03_sections_subtotals | r3 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | columns_removed | 0/4/0 | 18.2/22.3 |
| C04_cp874_semicolon_csv | r1 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | — | 0/4/0 | 45.2/49.1 |
| C05_tsv_bom_net_prices | r1 | 100.00% | 12 | 4 (C3, N1) | 3 | 4 | review | — | 0/4/0 | 48.3/52.2 |
| C06_dates_every_form | r1 | 100.00% | 12 | 3 (C2, N1) | 2 | 3 | review | — | 0/8/0 | 144.6/188.0 |
| C07_numbers_messy | r1 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | — | 0/4/0 | 27.3/31.6 |
| C08_vat_exclusive_note_row | r1 | 100.00% | 12 | 3 (B1, C2) | 3 | 4 | review | — | 0/4/0 | 36.2/40.3 |
| C09_vat_flag_per_row | r1 | 95.56% | 12 | 4 (B1, C2, E1) | 4 | 4 | review | — | 0/5/0 | 48.3/52.3 |
| C10_two_price_columns | r1 | 78.95% | 12 | 6 (B1, Ch3, C1, N1) | 5 | 6 | review | — | 0/5/0 | 54.4/58.5 |
| C11_units_pack_in_text | r1 | 100.00% | 12 | 3 (C2, N1) | 2 | 3 | review | — | 0/4/0 | 33.4/37.3 |
| C12_status_numeric_legend | r1 | 100.00% | 12 | 3 (C2, E1) | 3 | 3 | review | — | 0/4/0 | 42.3/46.3 |
| C13_category_code_and_name | r1 | 100.00% | 12 | 2 (Ch1, C1) | 2 | 4 | review | — | 0/6/0 | 60.6/64.7 |
| C14_leading_zeros_duplicates | r1 | 100.00% | 12 | 3 (C2, N1) | 3 | 3 | review | — | 0/4/2 | 96.4/100.3 |
| C15_merged_title_blank_columns_injection | r1 | 100.00% | 12 | 1 (Ch1) | 1 | 2 | review | — | 0/4/0 | 81.3/85.2 |
| C16_three_files | r1 | 100.00% | 12 | 4 (Ch2, C2) | 4 | 5 | review | — | 0/8/0 | 63.4/67.8 |
| C16_three_files | r2 | 100.00% | 12 | 0 | 0 | 0 | review | identical | — | 3.1/6.4 |
| C16_three_files | r3 | 100.00% | 12 | 1 (Ch1) | 1 | 5 | review | columns_renamed | 0/8/0 | 27.2/32.6 |
| C17_abbreviations_en | r1 | 96.72% | 12 | 2 (B1, N1) | 1 | 1 | review | — | 0/8/0 | 36.3/40.4 |
| C17_abbreviations_en | r2 | 96.72% | 12 | 0 | 0 | 0 | auto_approve | identical | — | 3.1/3.3 |
| C17_abbreviations_en | r3 | 96.72% | 12 | 1 (B1) | 0 | 0 | review | columns_added | 0/8/0 | 30.2/34.8 |
| C18_inventory_subtotals | r1 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | — | 0/7/0 | 45.3/49.4 |
| C19_hidden_calc_sheet | r1 | 100.00% | 12 | 3 (C2, N1) | 3 | 3 | review | — | 0/4/0 | 48.3/52.3 |
| C20_promotions_on_product_rows | r1 | 100.00% | 12 | 3 (C2, N1) | 2 | 3 | review | — | 0/8/0 | 48.3/52.5 |
| C21_codes_as_numbers | r1 | 100.00% | 12 | 2 (C2) | 2 | 3 | review | — | 0/4/0 | 36.2/39.9 |
| C22_mixed_language_headers | r1 | 100.00% | 12 | 2 (E1, N1) | 1 | 0 | review | — | 0/7/0 | 42.2/46.1 |
| C23_usd_currency_column | r1 | 100.00% | 12 | 4 (C2, E1, N1) | 4 | 3 | review | — | 0/5/0 | 54.2/58.1 |
| C24_blank_rows_and_footer | r1 | 100.00% | 12 | 8 (B1, C7) | 8 | 12 | review | — | 0/4/0 | 39.2/43.1 |
| C25_old_and_new_price | r1 | 100.00% | 12 | 5 (B1, Ch2, C2) | 5 | 6 | review | — | 0/5/0 | 147.6/151.4 |

## เทียบ baseline เฉพาะ r1 ด้วย scorer เดียวกัน

ตารางนี้แยก original proposal (preview แบบอ่านอย่างเดียว ก่อนคำตอบจำลอง) และผลหลังตอบ เพื่อเห็นผลของขั้น review; preview ใช้ endpoint เดิม limit 500 ซึ่งครอบคลุมข้อมูลทั้งหมดใน fixture 12 SKU ไม่มีการบันทึก mapping หรือเรียกโมเดลเพิ่ม

| กรณี | Baseline field | AI ก่อนตอบ field | หลังตอบ field | Δ หลัง−baseline (pp) | Exact baseline→หลัง |
|---|---:|---:|---:|---:|---:|
| C01_wide_stock_thai | 93.75% | 100.00% | 100.00% | +6.25 | 0.00% → 100.00% |
| C02_two_header_rows_codes | 87.50% | 100.00% | 100.00% | +12.50 | 0.00% → 100.00% |
| C03_sections_subtotals | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C04_cp874_semicolon_csv | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C05_tsv_bom_net_prices | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C06_dates_every_form | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C07_numbers_messy | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C08_vat_exclusive_note_row | 96.88% | 100.00% | 100.00% | +3.12 | 0.00% → 100.00% |
| C09_vat_flag_per_row | 98.44% | 100.00% | 98.44% | +0.00 | 50.00% → 50.00% |
| C10_two_price_columns | 87.50% | 96.88% | 96.88% | +9.38 | 0.00% → 0.00% |
| C11_units_pack_in_text | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C12_status_numeric_legend | 96.88% | 100.00% | 100.00% | +3.12 | 0.00% → 100.00% |
| C13_category_code_and_name | 96.88% | 100.00% | 100.00% | +3.12 | 0.00% → 100.00% |
| C14_leading_zeros_duplicates | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C15_merged_title_blank_columns_injection | 99.48% | 100.00% | 100.00% | +0.52 | 83.33% → 100.00% |
| C16_three_files | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C17_abbreviations_en | 82.03% | 98.44% | 98.44% | +16.41 | 0.00% → 50.00% |
| C18_inventory_subtotals | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C19_hidden_calc_sheet | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C20_promotions_on_product_rows | 97.40% | 100.00% | 100.00% | +2.60 | 83.33% → 100.00% |
| C21_codes_as_numbers | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C22_mixed_language_headers | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C23_usd_currency_column | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C24_blank_rows_and_footer | 100.00% | 100.00% | 100.00% | +0.00 | 100.00% → 100.00% |
| C25_old_and_new_price | 93.75% | 87.50% | 87.50% | -6.25 | 0.00% → 0.00% |

ค่าเฉลี่ยก่อนคนตอบด้วย scorer เดียวกัน **99.31%**, หลังตอบ **99.25%** การลดลงมาจาก C09 โดยตรง ค่าเฉลี่ยฟิลด์ที่สูงได้รับอิทธิพลจากฟิลด์ว่างที่ตรงกัน จึงต้องอ่านคู่กับ exact-record, wrong และการตรวจเชิงธุรกิจ

## กรณีคะแนนต่ำกว่า baseline และความผิดจริง

### C09 — VAT เปลี่ยนตามแถว: ความผิดจริงในขั้น review/harness

compare ดิบลดจาก baseline 98.4375% เป็น airun 95.56%; เมื่อใช้ scorer เดียวกันได้เท่า baseline 98.4375% แต่ original AI proposal ได้ 100% ก่อนคนจำลองตอบ จึงไม่ใช่หลักฐานว่าโมเดล map แย่กว่า heuristic

`score.wrong`: `/prices/*/vat_included` ผิด 6 SKU เช่น `100102: want False got True`, `100104: want False got True`, `200101: want False got True` โมเดลระบุชัดใน changes_from_draft ว่า Y/N แบ่ง 6/6 และใช้ `map_values(normalize_whitespace(col("รวมภาษี")))` ให้ true/false ตามแถว แต่การ์ด contested สร้างคำถาม boolean; `person_answers()` ใช้เสียงข้างมากจาก gold โดยเสมอ 6/6 กลายเป็น true และ answers/apply เปลี่ยน expression เป็น `const true` ทั้งไฟล์

`mapping.agreement.disagree` = 4; ในหลักฐาน `agreement.disagree` มี VAT ต่างจาก heuristic ที่สมมติ true; preview เดิมและ document หลังตอบพิสูจน์การเปลี่ยนนี้ได้ ทั้ง original verification และการอนุมัติหลังตอบไม่จับความผิดเชิงความหมายนี้ จัดเป็น **ความผิดจริงของการสร้าง/ใช้คำถามและ harness** ไม่ใช่เรื่องตัดสิน C10/C13/C25

### C10 — ราคาปลีก/ส่ง: คะแนนดิบต่ำ แต่เป็นเรื่องตัดสินตามเกณฑ์งาน

compare ดิบ: baseline 87.50% → airun 78.95%; scorer เดียวกันกลับเป็น 87.50% → 96.875% โมเดลสร้าง source unpivot จากราคาทั้งสองคอลัมน์และได้ 2 price items ต่อสินค้า (24 รวม) จึงไม่ได้ทิ้งราคาส่ง; ตั้ง price_list เป็น `retail`/`wholesale` ในขณะที่เฉลยใช้ `standard`/`wholesale`

`score.wrong` ดิบแสดง amount, vat_included, vat_rate อย่างละ 12 ค่าเป็น missing เพราะ scorer หา key standard ไม่พบ แม้ราคา retail อยู่จริง ไม่ใช่ตัวเลขราคาทั้ง 36 ค่าหายจาก output โดยตรง การ์ดมี choice สำหรับ `/prices/*/amount` และ `/prices/*/price_list`, พร้อม confirm อื่น; contested 5 ข้อ, disagree 6 ฟิลด์ จัดเป็น **เรื่องตัดสินและผ่านเกณฑ์ที่ผู้ใช้กำหนด** แต่ยังต้องตั้งชื่อชุดราคาให้ตรงกับทีมปลายทาง

### C25 — ราคาเก่า/ใหม่: scorer เดียวกันต่ำกว่า baseline แต่มีการถามให้ตัดสิน

airun ให้ 100% ขณะที่ scorer เดียวกับ baseline ลดจาก 93.75% → 87.50% โมเดล unpivot ราคาเก่าและใหม่เป็น 2 entries ต่อสินค้า ใช้ price_list standard ทั้งคู่ แยกด้วย effective_from = null / 2026-10-01 แต่เฉลยต้องการเพียงราคาใหม่ ตัวอย่าง SKU แรกได้ 6.65 เดิม และ 7.00 ใหม่

scorer ของ airun ทำ dictionary โดย price_list key จึงทับ standard เดิมด้วยรายการหลัง และเห็นเฉพาะราคาใหม่จนได้ 100%; uniform scorer เก็บจำนวนรายการและจับความต่างได้ (`price_list`, `amount`, `vat_included`, `vat_rate` มีสองค่าแทนหนึ่ง) จึงต้องไม่อ้างว่า JSON ตรงเฉลยทั้งหมดจาก score ดิบอย่างเดียว

agent_log ระบุการเพิ่ม price_rows, การอ่านวันที่จากหัวคอลัมน์ และ clarification VAT ที่ไม่มีหลักฐาน; มี inspect 1 รอบ, attempts 2, fix_rounds 0, grounded 5, python_turns 0 การ์ดมี choice ราคากับ confirm ชุดราคาและ boolean VAT; contested 5, disagree 6 จัดเป็น **เรื่องตัดสินและผ่านเกณฑ์งานนี้** ต้องให้คนกำหนดว่าจะเก็บประวัติราคาเก่าหรือส่งเฉพาะราคาที่มีผล ไม่ใช่ลบราคาทิ้งโดยไม่อธิบาย

### C17 — หน่วย BT กลายเป็น BAG: ความผิดจริงที่ยังผ่านการตรวจและถูกจำซ้ำ

แม้ดีกว่า baseline (82.03125% → 98.4375% ด้วย scorer เดียวกัน), airun ยังได้ 96.72% ทั้ง r1/r2/r3; `/unit/sell_unit` ผิด 6 ค่า/รอบ เช่น BOTTLE → BAG โมเดลใช้ `unit_normalize(trim(col("UoM")))` และหลักฐานของโมเดลอ้างว่าจะได้ BOTTLE/PACK/PCS/CARTON/BOX/DOZEN แต่ตารางจริงใน `backend/src/forma/engine/thai.py` มี `"bt": "BAG"` (Beutel) จึงผิดกับบริบทตัวย่อภาษาอังกฤษของกรณีนี้

จัดเป็น **ความผิดจริงด้านความหมายของหน่วย/การตรวจผล** โมเดลเชื่อเครื่องมือผิดบริบท และ self-check ไม่พบปัญหา; r2 reused mapping จาก r1 แล้ว auto_approve โดยไม่ถามหน่วยซ้ำ, r3 คง mapping เดิมตาม agent_log ทุกครั้ง verification.passed = true หน่วยผิดไม่ใช่ขาดข้อมูลชนิด integer/string ที่ schema จะจับได้

## แยกเรื่องตัดสิน C10 / C13 / C25

| กรณี | สิ่งที่ต้องตัดสิน | หลักฐานบนการ์ด/ผล | การจัดประเภท |
|---|---|---|---|
| C10 | ชื่อ/การใช้ชุดราคาปลีกและส่ง | มี choice ราคาและ price_list; สร้างทั้งสองราคา | ผ่านตามเกณฑ์การถาม แม้คะแนนดิบ 78.95% |
| C13 | ใช้รหัสหมวดหรือชื่อหมวด | โมเดลเลือกชื่อหมวดตรงเฉลย; การ์ดมี choice/confirm (ไม่ได้เป็นคำถามหมวดโดยตรง) | ถูกตามเฉลยอยู่แล้ว ไม่ใช่ความผิดจริง |
| C25 | ใช้ราคาใหม่เท่านั้นหรือเก็บประวัติ + VAT | มี choice ราคา, confirm price_list, boolean VAT; effective_from ถูก | ผ่านตามเกณฑ์การถาม แต่ output มีราคาเก่าเกินเฉลย |

การผ่านด้านการถามไม่ใช่การเพิ่มคะแนน field_accuracy หรือรับรองว่าระบบส่ง JSON ตรง contract ทางธุรกิจแล้ว คงคะแนนทุกสูตรไว้ตามจริง

## ธงแดงตามเกณฑ์ที่กำหนด

| ธง | จำนวนรอบ | รายละเอียด |
|---|---:|---|
| `job failed` | 0 | ไม่พบ |
| `rows != 12` | 0 | ไม่พบ |
| `extra_rows` | 0 | ไม่พบ |
| `llm_failed` | 0 | ไม่พบ |
| `disagree >= 8` | 1 | C24_blank_rows_and_footer/r1 |
| `questions > 8` | 0 | ไม่พบ |
| `verification.passed = false` | 0 | ไม่พบ |

C24 disagree = 12 แต่คำถามมี **8 ข้อพอดี** จึงติด `disagree ≥ 8` และไม่ติด `คำถาม > 8` output ตรงเฉลย 100% ทั้งสอง scorer; agent เปลี่ยนวิธีอ่าน/ขอบเขตตารางและตัวกรอง แต่ความต่างเชิง expression ถูกนับ contested หลายฟิลด์ ทำให้ต้องยืนยันทั้ง SKU, currency, amount, product_name, unit, pack_size, price_list และ VAT ภาระ review สูงโดยไม่ได้แปลว่าทั้ง 12 ฟิลด์ผิด

ไม่มีค่า verification.passed หายไป: เป็น true ครบ 33/33 การไม่ติดธงส่วนใหญ่ไม่ใช่หลักฐานว่าไม่มี silent error; C09 และ C17 เป็นตัวอย่างที่ความหมายผิดหลังผ่านการตรวจ ส่วน C25 แสดงช่องโหว่การนับคะแนนกรณี collection key ซ้ำ

## การใช้ tools / self-check / memory และพฤติกรรมที่พบ

- fix_rounds = 0 ทุกรอบที่เรียก agent: รอบนี้ไม่มีหลักฐานว่า loop แก้ mapping จากผลรันช่วยแก้ความผิดที่เกิดขึ้นจริง แม้ตัวระบบรองรับ
- C14 ใช้ Python sandbox 2 ครั้งสำเร็จ (status ok, exit_code 0): อ่าน manifest แล้วเปิด workbook ด้วย openpyxl ตรวจ data_type/number_format/ค่ารหัสศูนย์นำหน้า ก่อน dedupe 15 source rows เหลือ 12 SKU ถูกต้อง เป็นหลักฐานการใช้ sandbox จริง แต่ยังไม่ได้วัด ablation ว่าถ้าไม่มี sandbox จะผิดหรือไม่
- ขอ inspect เพิ่มใน C01 r3 และ C25 r1 อย่างละ 1 รอบ (2 requests); grounded อัตโนมัติอยู่ในตาราง F/G/P ส่วนใหญ่โมเดลใช้ข้อมูลที่ระบบดูให้แล้ว
- r2 ทั้ง 4 กรณีใช้ mapping memory ไม่เรียกโมเดลใหม่; C01/C03/C17 auto_approve, C16 ยังคง review แม้ drift identical; ดังนั้นคำว่า known format ไม่ได้รับประกันว่าข้อมูลรอบใหม่ผ่านอัตโนมัติทุกครั้ง
- C14 ศูนย์นำหน้ากับแถวซ้ำถูกต้อง, C15 ข้อความสั่ง AI ในไฟล์ไม่เปลี่ยนราคา, C16 ผูกสินค้า/สต็อก/โปรโมชั่นจากสามไฟล์ถูกต้อง, C18 ไม่นำยอดรวมสต็อกมาสร้าง orphan, C19 ไม่ใช้ชีตคำนวณซ่อน, C20 ไม่สร้างโปรในแถวที่ไม่มีโปร, C21 รหัสตัวเลขออกเป็นข้อความ, C23 ใช้ USD ตามคอลัมน์
- C08 ได้ VAT-exclusive ถูก แต่ agent อ้างชื่อ workbook/ชีต “ราคาก่อนภาษี” เป็นหลักฐานร่วมด้วย จึงไม่ใช่การทดลองที่แยกพิสูจน์การอ่านหมายเหตุเหนือหัวตารางเพียงอย่างเดียว
- `person_answers()` ไม่รองรับ number/text; รอบนี้ number 10 ข้อเรื่อง vat_rate ถูกเว้นคำตอบ แต่สคริปต์ยังอนุมัติ mapping สำเร็จทุกครั้ง ต้องตีความว่า flow อนุมัติอนุญาตคำถามบางส่วนค้างอยู่ ไม่ใช่ทุกข้อได้รับการตอบแล้ว
- คำถาม choice/enum เลือกตัวเลือกแรกโดยอัตโนมัติ และ confirm ยอมรับโดยไม่ตรวจหลักฐานจริง จึงยังไม่ใช่การวัดความเข้าใจ/ภาระการใช้ UI ของคนจริง

## ต้นทุนและเวลา

| Agent | cost_usd รวม |
|---|---:|
| schema | $0.533646 |
| mapping | $1.444218 |
| adaptation | $0.181982 |
| **รวม** | **$2.159846** |

รวม usage จากแต่ละ entry ของ `mapping.agent_log` เพียงครั้งเดียว ไม่บวกซ้ำจาก evidence ที่สำเนาไว้ และ preview เสริมไม่เรียก LLM ค่า cost_usd เป็นค่าที่ระบบบันทึก/ประมาณด้วย pricing configuration ไม่ใช่ใบแจ้งหนี้จริงหรือยอดหักเครดิตที่ตรวจจากผู้ให้บริการ

ผลรวม job.seconds = 1524.3 วินาที; seconds_total = 1684.4 วินาที; เวลาเฉลี่ย r1 = 61.9 วินาทีต่อกรณี เวลาเหล่านี้รวมการรอ worker และ polling จึงไม่ใช่ latency โมเดลล้วน

## ข้อสรุปสำหรับการนำไปใช้

ในชุดจำลองนี้ AI + grounding + memory + การตอบแทนคน ช่วยเพิ่ม field accuracy จาก heuristic และอ่านรูปแบบซับซ้อนหลายกรณีได้จริง แต่ยังไม่ผ่านเกณฑ์ “ปล่อยผ่านได้อย่างเชื่อถือโดยไม่ตรวจ” เพราะ C17 r2 อนุมัติหน่วยผิดซ้ำอัตโนมัติ, C09 ถูกทำให้ผิดตอนตอบคำถาม และ scorer หนึ่งตัวซ่อน price entry เกินของ C25 คะแนนฟิลด์เกิน 99% จึงยังไม่พอจะสรุปความปลอดภัยของ auto-approval

ผลนี้ไม่ใช่การเปรียบเทียบกับผู้ให้บริการรายใหญ่ และยังไม่แทนไฟล์ลูกค้าจริงจำนวนมาก ชุดนี้มี 12 SKU ต่อรอบ ใช้ product@1 เป็น target และมีประวัติเรียนรู้ใน stack อยู่แล้ว

ประเด็นที่ควรพิจารณาแก้ต่อจากหลักฐาน (งานนี้ยังไม่ได้แก้): ป้องกัน boolean แบบทั้งไฟล์ทับ mapping ที่แปรตามแถว; แยกศัพท์หน่วยตาม ERP/บริบทและตรวจ output semantic; เพิ่มการตรวจ collection key ซ้ำ/ราคาเกินใน scorer; ลด contested ที่เกิดจากวิธีอ่านต่างแต่ผลเท่ากัน; ชี้ให้ชัดเมื่อยังมีคำถาม number/text ที่ไม่ตอบ

## หลักฐานและไฟล์ที่ได้

- [ผลรันตามคำสั่ง](report-llm.json)
- [ผล compare.py ดิบ](report-llm-compare.txt)
- [หลักฐาน original/after-answer mapping และ preview พร้อม re-score](report-llm-evidence.json)
- [สรุปตัวเลขและ re-score รายรอบ](report-llm-summary.json)
- [baseline เดิม](report-heuristic.json)
- `C*/r*/got.json` คือข้อมูลที่ได้จาก dataset จริงของแต่ละรอบ; `ผลที่ควรได้.md` และ `expected.json` ในโฟลเดอร์นั้นคือกับดัก/เฉลยที่ใช้ตรวจ
- งานนี้สร้างเฉพาะผลทดสอบและรายงาน ไม่แก้ source code / test tools / baseline / gold และไม่ commit; git HEAD คงเดิมตลอดงาน
