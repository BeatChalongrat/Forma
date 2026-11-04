# ผลทดสอบความสัมพันธ์ Excel ของ FORMA

อัปเดต 2026-11-03T09:39:07.457947+00:00 | โมเดลที่ตั้งค่า: deepseek-flash | ระบบจริง localhost (API, worker, PostgreSQL, S3, Redis/arq)

**สถานะ: การทดลองหลักจบแล้ว**; รอบลองซ้ำยังไม่จบ: T08, T03

สร้าง 12 กรณี รวม 14 workbook และ 30 ชีต แต่ละกรณีมีสินค้า 12 รายการ ผลที่ได้จากโมเดลโดยไม่มี LLM fallback ขณะนี้ 6 กรณี ในจำนวนนี้ข้อมูลตรงเฉลยทุก record 5 กรณี

กรณีที่มี LLM failure: T03, T07, T08, T10, T11, T12 — ต้องอ่านสาเหตุด้านล่าง ไม่ใช้ศูนย์จาก fallback เป็นคะแนนความสามารถของโมเดล

**ยังไม่ให้คะแนนรวมความแม่นของ AI หรือรับรองพร้อมใช้งานจริง** ถ้ายังมีกรณี API ล้มเหลว/งานไม่จบ การรวมเฉพาะกรณีที่สำเร็จจะทำให้คะแนนดูดีเกินจริง

## ผลต่อกรณี

| กรณี | สิ่งที่ทดสอบ | Field accuracy | Exact record | แถว | การ์ด / คำถามข้อความ | สถานะ | เวลา (วินาที) |
|---|---|---:|---:|---:|---:|---|---:|
| T01 | Three files, shuffled joins | 100.0% | 100.0% | 12 | 2 / 2 | review | 75.26 |
| T02 | Company + item composite key | 100.0% | 100.0% | 12 | 0 / 0 | review | 40.38 |
| T03 | Date + currency + price list | ประเมิน AI ไม่ได้ | — | 0 | 1 / 1 | LLM failure / block | 772.93 |
| T04 | Merged headings and currency outside table | 100.0% | 100.0% | 12 | 2 / 0 | review | 30.44 |
| T05 | Cross-sheet formula with correct cache | 100.0% | 100.0% | 12 | 0 / 0 | review | 73.42 |
| T06 | Formula result cache contradicts inputs | 100.0% | 100.0% | 12 | 0 / 0 | review | 230.15 |
| T07 | Formula has no stored result | ประเมิน AI ไม่ได้ | — | 0 | 0 / 0 | LLM failure / block | 132.23 |
| T08 | Missing external formula workbook | ประเมิน AI ไม่ได้ | — | 0 | 0 / 0 | LLM failure / block | 7.47 |
| T09 | Undefined unit in separate reference sheet | 66.7% → 100.0% | 0.0% → 100.0% | 12 | 1 / 2 | review | 1143.56 |
| T10 | Duplicate price key without precedence | ประเมิน AI ไม่ได้ | — | 0 | 1 / 1 | LLM failure / block | 8.31 |
| T11 | Unmatched stock keys and missing stock | ประเมิน AI ไม่ได้ | — | 0 | 0 / 1 | LLM failure / block | 62.41 |
| T12 | Lots + units + validity + multiple sheets | ประเมิน AI ไม่ได้ | — | 0 | 1 / 2 | LLM failure / block | 8.36 |

เวลาในตารางเป็น wall-clock ตั้งแต่เริ่มกรณีจนงาน propose จบ รวมอัปโหลด/profile/คิว ไม่ใช่เวลาโมเดลอย่างเดียว และไม่เริ่มนับใหม่เมื่อ client รันต่อจาก checkpoint.

Field accuracy ตรวจตามเฉลยที่แยกไว้ ไม่เพิ่มคะแนนด้วย field ว่างที่ไม่เกี่ยวข้อง ส่วน inventory เทียบตาม warehouse_code ไม่บังคับลำดับ และไม่ซ่อนแถว/คลังซ้ำ. กรณี T09–T10 มีข้อเท็จจริงที่ต้องรอคนตอบ จึงต้องประเมินการถามแยกจากค่าก่อนตอบ. คำถามข้อความอาจซ้ำกับการ์ด จึงไม่นำสองจำนวนมาบวกเป็นจำนวนปัญหาธุรกิจ

แถวไม่ครบเป็นธงให้ตรวจ ไม่ใช่ข้อสรุปว่าระบบทำผิดเสมอ: กรณีที่หลักฐานไม่พอ การ block พร้อมอธิบายและขอข้อมูลตรงจุดเป็นพฤติกรรมที่ถูกต้อง แต่ยังไม่นับว่าการแปลงข้อมูลสำเร็จ. โดยเฉพาะ T08 ไม่ควรคาดหวังผลจำนวนชิ้นที่ถูกต้องจนกว่าจะได้ไฟล์อ้างอิงที่ขาด.

## สิ่งที่ยืนยันได้และข้อจำกัด

- T01 เชื่อม 3 ไฟล์และเก็บ 2 คลังได้ถูก แต่ถามเรื่องราคา/สกุลเงินทั้งที่ข้อมูลชัด และยังมีคำถามเก่าจาก heuristic เรื่องรหัสสินค้า/จำนวนค้างอยู่
- T02 คีย์บริษัท + รหัสสินค้า เชื่อมได้ถูก ไม่จับข้อมูลข้ามบริษัท
- T04 อ่านหัวตารางรวมเซลล์ ตัด TOTAL และอ่าน EUR นอกตารางได้ถูก แต่ยังมีการ์ดยืนยันที่ไม่จำเป็น
- T05 อ่านผลสูตรข้ามชีตที่ถูกต้องได้ มีหลักฐานเรียก sandbox จริง
- T06 รอบชื่อเป็นกลางพบว่า cache 813 ไม่ตรงกับ 3 × 12 = 36 จึงสร้าง mapping คำนวณจาก input sheets ได้ถูกทั้ง 12 สินค้า เป็นหลักฐานเชิงบวกเฉพาะสูตรคูณที่ทดสอบ ไม่ใช่การรองรับทุกสูตร Excel
- T09 ถามถูกจุดว่า BT หมายถึงหน่วยอะไร ไม่ยืนยันว่าเป็น Bottle เอง การ์ดเป็น text/value_map เลือกหรือพิมพ์คำตอบได้. ก่อนตอบได้ 66.7% เพราะ 12 ช่อง sell_unit ยังเป็น BT ไม่ใช่ชื่อเต็มที่มีเฉพาะในเฉลย นี่เป็นการรอข้อเท็จจริง ไม่ใช่การ map ผิดโดยเดา. ทดสอบตอบ BOTTLE ผ่าน answers/preview และ answers/apply แล้วได้ 100% ทั้ง 12 record การ์ดหน่วยหายไป แต่ยังมีคำถาม heuristic เรื่อง Material code ค้าง. ผลหลังตอบเก็บแยกจากก่อนตอบ และไม่ได้อนุมัติ/export
- รอบที่ API ตอบ HTTP 503 ไม่ได้พิสูจน์ว่า AI เข้าใจหรือไม่เข้าใจกรณีนั้น แม้ policy จะบล็อก output ที่ไม่ครบไว้ก็ตาม

## ข้อผิดพลาดที่พิสูจน์สาเหตุได้

### 1. การเปรียบเทียบวันที่ใน engine

Pilot A ของกรณี R03 ได้ชื่อสินค้า/รหัสถูก แต่ราคาและสกุลเงินเป็น null ทั้ง 12 รายการ (24 field ผิด). AI อธิบายและเลือกเป้าหมาย RETAIL/THB/2026-11-03 ถูก แต่กฎ LTE/GTE ของ engine แปลง operand เป็นตัวเลข รวมถึงค่าชนิด Date. การ replay แบบอ่านอย่างเดียวพบ predicate ชื่อราคาและสกุลเงินผ่านอย่างละ 36 จาก 48 แถว แต่ predicate วันที่ทั้งสองเป็น null ทั้ง 48 แถว จึงกรองทุกแถวทิ้ง

หลักฐาน: `runs/rel-20261103-a/R03_validity_r1/filter-diagnostic.json`, `before.json`, `score-before.json`; จุดแก้ `backend/src/forma/engine/ops.py` สาขา GT/GTE/LT/LTE. Hash ของไฟล์ ops.py และ mapping.py ใน worker ตรงกับ checkout ตอนตรวจ. Pilot A ไม่นำมารวมคะแนนหลัก เพราะชื่อ Schema บอกใบ้กรณี แต่การ replay นี้พิสูจน์ข้อผิดพลาดเชิง execution ได้โดยไม่อาศัยคะแนนโมเดล

A03 มี verification.passed=false แต่ decision=review พร้อมการ์ดยืนยันราคา/สกุลเงิน. การยืนยันว่าจับคอลัมน์ถูกไม่ควรกลบ execution error ที่ทำให้ผลเป็น null. จากการอ่าน approve_mapping พบ guard เรื่อง unresolved business decisions และ policy block แต่ไม่มี guard verification.passed โดยตรง; require_verification ป้องกัน auto-approve ด้วยการส่ง review. ต้องทดสอบ/กำหนดกติกาการอนุมัติและส่งออกให้ชัดก่อนใช้จริง ไม่สรุปว่า export ผิดหลุดได้แล้ว เพราะรอบนี้ไม่ได้ approve/export.

### 2. คำถามจาก heuristic ไม่ถูกสะสางหลัง AI ตรวจหลักฐาน

T01 และ T04 ได้ผลถูก แต่ policy ลดความมั่นใจเพราะไม่ตรงกับ heuristic แล้วสร้างคำถามยืนยันซ้ำ. ใน T01 ยังมีข้อความว่าไม่มีคอลัมน์จำนวนที่จำได้ ทั้งที่ AI map Quantity pieces ได้ถูกแล้ว. ควรตรวจความขัดแย้งกับข้อมูล/ผลรัน และปิดคำถามที่หลักฐานปัจจุบันแก้แล้ว ไม่ลดการถามด้วยการลด threshold อย่างเดียว

### 3. ความทนทานเมื่อ API ไม่พร้อม

พบ HTTP 503 Service is too busy และ fallback เป็น heuristic. บางกรณีค้างนานกว่า 10 นาทีที่สถานะรวม analysing structure. ควรมี deadline รวมต่อคำขอ/งาน, จำกัดงบ retry และจำนวน token, แสดงแยกว่ารอผู้ให้บริการหรือกำลังตรวจข้อมูล และให้ลองต่อได้โดยไม่อัปโหลดซ้ำ. ค่า timeout HTTP 90 วินาทีที่มีอยู่ไม่ใช่หลักประกันเวลา wall-clock ทั้งงาน

T09 จบด้วยคำตอบจากโมเดลจริงหลังประมาณ 19 นาที จึงไม่สรุปว่างานที่ช้าทุกงานเป็น 503. ปัญหาที่พิสูจน์คือประสบการณ์รอและการมองไม่เห็นขั้นตอนย่อย ต้องแยกเวลารอ API, reasoning/response, sandbox และ self-check ออกจากกัน.

C07 ลองซ้ำแล้วพบ TypeError: 'NoneType' object is not subscriptable และ fallback เป็น heuristic หลังประมาณ 25 นาที เป็นข้อผิดพลาดของ pipeline ที่ต่างจาก HTTP 503; log ที่เก็บไม่มี traceback จึงยังยืนยันจุดกำเนิดไม่ได้. ต้องเพิ่ม stage/traceback ที่ไม่เผยข้อมูลลับ และตรวจรูปแบบ response รวมถึงค่า null ก่อนเข้าถึง object ไม่ควรเหมารวมเป็นความไม่แม่นของโมเดล.

### 4. สูตรที่ไม่มีผลบันทึก: อ่านได้ใน sandbox แต่ engine ปฏิเสธชีต

Pilot A07 จบแล้ว: AI เปิด workbook จริงด้วย openpyxl อ่านสูตรและตัวตั้งครบ และเสนอ qty = Cartons × Pieces per carton ได้ แต่ engine ไม่ยอมอ่านชีต Export เพราะ C2:C13 ไม่มี cached result แม้ SKU/ชื่อที่ต้องใช้ในชีตนั้นเป็นข้อความปกติ จึงออก 0 record และ block. Error แนะนำ recalculate/save หรือ export values ถูกทิศทาง แต่ไม่มีการ์ดคำถามหรือ action ให้ผู้ใช้ทำต่อ. ไม่รวม pilot นี้ในคะแนนหลัก; ใช้หลักฐาน execution เป็นจุดตรวจ ส่วน C07 ชื่อกลางล้มเหลวที่ pipeline จึงยังไม่ใช้ยืนยันพฤติกรรมโมเดล. อย่าปิด guard ทั้งหมด: ถ้าใช้เฉพาะคอลัมน์ค่าคงที่และคำนวณผลสูตรจากแหล่งที่พิสูจน์ได้ ควรอ่านได้พร้อม lineage; ถ้าพิสูจน์ไม่ได้ต้องหยุดและขอข้อมูล.

## ลำดับที่ควรทำต่อ

1. แก้ comparison ให้รองรับชนิด Date/Datetime และเพิ่ม regression จาก T03 พร้อมตรวจเงื่อนไขที่คืน null ทั้งคอลัมน์
2. เมื่อ self-check พบข้อมูลหาย ให้ส่งหลักฐานของแต่ละ filter/join กลับให้ AI ซ่อม ไม่ถามคนให้ยืนยันราคาในขณะที่ engine ยังให้ null
3. ยืนยัน T07–T12 ที่ติดปัญหาผู้ให้บริการอีกครั้งก่อนสรุปการถามกลับและความถูกต้องของการรวมล็อต ห้ามแทนผลที่ไม่จบด้วยคะแนนของ heuristic
4. ปิดคำถามที่หมดเหตุแล้วด้วยหลักฐาน และแยกคำถามธุรกิจออกจากปัญหากฎแปลงข้อมูล
5. ตรวจความสามารถการรวมล็อตต่อ SKU+คลังหลังแปลงหน่วย. จากโค้ด CollectionMapping มี link/filter/dedupe แต่ไม่มี declarative aggregate ของ numeric item fields โดยตรง; ต้องยืนยันจาก T12 ว่า pipeline ทำได้หรือรายงานข้อจำกัดตรงจุด ห้ามใช้การเลือกแถวแรกแทน SUM
6. เพิ่มการแจ้งเหตุที่แก้ได้จริง เช่น เซลล์สูตรไม่มี cache หรือ workbook ภายนอกหาย พร้อมตำแหน่งไฟล์/ชีต/เซลล์ ไม่ให้เหลือแค่ missing required qty

เกณฑ์ยืนยันหลังแก้: กรณีที่มีหลักฐานครบต้องตรง oracle ทั้ง field/record และไม่มีแถวหรือสมาชิกคลังเกิน; กรณีกำกวมต้องถามชื่อค่า/สินค้า/แหล่งที่ขาดอย่างเจาะจง ไม่เติมข้อมูลเอง; หลังตอบต้องตรวจทั้งชุดใหม่และเก็บเวอร์ชันก่อน-หลัง; execution error ต้องไม่ถูกกลบด้วยการกดยืนยันความหมาย. ใช้ชุดนี้เป็น regression แล้วเพิ่ม permutation/perturbation (สลับแถว เปลี่ยนค่า เพิ่ม SKU และรหัส 001 กับ 1 ที่หมายถึงคนละสินค้า) เพื่อจับ mapping ที่ผ่านเพราะจำค่าตัวอย่างหรือ normalize key ผิด.

## วิธีรันและขอบเขตความน่าเชื่อถือ

- รอบ A เป็น pilot มีชื่อ Schema บอกกับดัก จึงหยุดส่งเคสใหม่หลัง R07 และเก็บไว้สำหรับ audit เท่านั้น
- รอบ B ใช้ manifest-neutral.json: ชื่อ Schema Product delivery, customer/schema/round IDs T01–T12 ไม่บอกชนิดกับดัก. ใส่ inventory identity_keys ที่ถูกต้อง. Root product population, task, schema และ source hashes ถูกล็อกก่อนส่ง
- รอบ C/D เป็นการลองใหม่เฉพาะเคสที่ LLM/API ล้มเหลว เก็บแยกจาก B ไม่เขียนทับความล้มเหลวเดิม. ตารางหลักใช้ผลจากโมเดลที่ลองซ้ำสำเร็จเมื่อมี แต่รายงานรอบเดิมและต้นทุนยังเก็บครบ ไม่ใช่การประเมินความสำเร็จต่อ attempt
- ส่งเฉพาะ source, task และ schema. อ่าน oracle หลังเก็บ prediction แล้ว. ไม่มีการ approve/export และไม่มีการแก้ production code เพื่อให้เทสผ่าน
- ลูกค้าทดสอบเป็นลูกค้าใหม่ แต่ stack ไม่ได้ล้างฐานความรู้รวม/ตัวอย่าง mapping ที่เคยอนุมัติของระบบ. เป็น cold customer บนระบบจริง ไม่ใช่โมเดลที่ปิด retrieval ทั้งหมด. ไม่มีเฉลยชุดนี้ถูกส่งเข้า retrieval; T09 ตอบภายหลัง snapshot และเก็บในลูกค้าทดสอบแยก
- เป็น small synthetic test ไม่ใช่ไฟล์ลูกค้าจริง ไม่ใช่ load test และยังไม่ใช่ผลเทียบ ChatGPT/Claude โดยตรง
- ยังไม่ครอบคลุมสูตรทุกชนิด เช่น XLOOKUP/SUMIFS/INDIRECT, named ranges, pivot/Power Query, VBA, circular references, encrypted workbook หรือขนาดหลายแสนแถว. การผ่านสูตรคูณข้ามชีตไม่ยืนยันความสามารถเหล่านี้
- วัดผลทุกกรณีจาก API; เปิดเว็บตรวจ T01 และ T09 ก่อน/หลังตอบจริงแบบอ่านอย่างเดียวแล้ว. T01 แสดงการ์ดยืนยันราคา/สกุลเงิน; T09 เวอร์ชัน 1 เก็บคำถาม BT และถูกทำเป็น read-only เมื่อแทนด้วยเวอร์ชัน 2; เวอร์ชัน 2 แสดง 0 ฟิลด์ต้องยืนยัน. การ submit คำตอบ T09 ทดสอบผ่าน API ไม่ใช่การคลิกส่งใน browser และไม่ได้กดอนุมัติ
- ข้อสังเกต UI: คำถาม BT กับเหตุผลยังเป็นอังกฤษแม้ UI เป็นไทย; ข้อความ action บอกเปลี่ยนเฉพาะรหัสนี้ แต่ label ช่องกรอกเขียนว่าค่าที่จะใช้กับทุกรายการในไฟล์นี้ ควรระบุให้ตรง scope เช่น “BT ให้ใช้ชื่อหน่วยว่าอะไร”. หลังตอบยังมีคำถาม heuristic ใน API แต่หน้าใหญ่ขึ้น 0 ฟิลด์และยอมรับได้ ต้องแยก unresolved business facts ออกจาก legacy notices ให้สอดคล้องกัน

## ต้นทุนที่ระบบบันทึก

รอบ B และการลองซ้ำที่มี snapshot แล้ว: 35 calls; cost_usd รวม USD 0.6198. เป็นค่าที่ระบบประมาณ/บันทึก ไม่ใช่ยอดใบแจ้งหนี้ provider และไม่รวม usage ที่ provider ไม่คืนมาจาก timeout/503. ค่าใช้จ่าย pilot A แสดงใน ledger แยก

## รายละเอียดคำถาม การตรวจ และธงแดง

Ledger ทุกการทดลองรวม pilot A ที่เก็บได้ขณะสรุป: 32 call records, USD 1.555789. หลักฐาน `runs/ledger.json`; งานที่ยังไม่จบอาจยังไม่ commit usage จึงเป็นยอดที่บันทึกแล้ว ไม่ใช่เพดานค่าใช้จ่ายสุดท้าย.

## ผลการลองซ้ำและการถามกลับ

เกณฑ์ถามถูกจุด: T08 ต้องขอ workbook ภายนอกที่หาย; T09 ต้องขอความหมาย BT; T10 ต้องระบุ SKU 000004 และราคาสองค่า; T11 ต้องแยก SKU ที่ขาดสต็อกกับสต็อกที่ไม่มีสินค้า. การบล็อกเพราะ output ไม่ครบอย่างเดียวไม่ถือว่าถามถูกจุด. T07 จะคำนวณจากตัวตั้งที่ให้ครบ หรือขอให้บันทึกผลสูตรใหม่พร้อมตำแหน่งก็ได้.
- C T07: LLM/pipeline failure ซ้ำ; round rnd_01M6GA5EW419SSVQAJ5ZEXZ7KR
- C T08: ยังไม่มีผลครบ; round rnd_01M6GBKBM1TK89BC6N7BS10ZWY
- D T03: ยังไม่มีผลครบ; round rnd_01M6GBNSCS5WV3DB5KY77MDTH7

T09 ผ่านการถามและใช้คำตอบจากคน: preview ตรวจผ่านก่อน apply; mapping สร้างเวอร์ชันใหม่แล้ว 100% field/record. หลักฐาน `runs/rel-20261103-b/T09_r1/human-preview.json`, `human-applied.json`, `after-human.json`, `score-after-human.json`. การยืนยันครั้งนี้ไม่ใช่การทดสอบเรียนรู้ข้ามรอบ. ยังไม่ให้คะแนนการถามจากผล fallback และยังไม่ทดสอบหลังคนตอบในกรณีที่ไม่ได้คำถามจากโมเดล.

## หลักฐานรายกรณี

### T01 (b)

Round: `rnd_01M6G9DXFFY22T60YC6BYK55MW`; mapping: `map_01M6G9G5Z6ZNJNT7SAQ88FCWNA`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9G5Z6ZNJNT7SAQ88FCWNA&round=rnd_01M6G9DXFFY22T60YC6BYK55MW)
verification.passed=True; fix_rounds=1; grounded=3; python_turns=0; disagree=3
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 0; extra rows: 0; extra facts/items: 0
- การ์ด /price | confirm |  | ตัวเลือก []
- การ์ด /currency | confirm |  | ตัวเลือก []
- คำถามข้อความ: No SKU-like header found in root table; using 'Material code' as identity — confirm
- คำถามข้อความ: Inventory table 'Stock' has no recognisable quantity column
หลักฐานเต็ม: `runs/rel-20261103-b/T01_r1/`

### T02 (b)

Round: `rnd_01M6G9FBDX9D77BTKYG78NBDEV`; mapping: `map_01M6G9GJ6H6VSFNG656F5DEWNN`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9GJ6H6VSFNG656F5DEWNN&round=rnd_01M6G9FBDX9D77BTKYG78NBDEV)
verification.passed=True; fix_rounds=1; grounded=0; python_turns=0; disagree=0
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 0; extra rows: 0; extra facts/items: 0
หลักฐานเต็ม: `runs/rel-20261103-b/T02_r1/`

### T03 (b)

Round: `rnd_01M6G9G8WXX3TTGNA6Q3B2FY74`; mapping: `map_01M6GA7V7CQ2P6ASPH0DZZZ1HA`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6GA7V7CQ2P6ASPH0DZZZ1HA&round=rnd_01M6G9G8WXX3TTGNA6Q3B2FY74)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 48; extra rows: 0; extra facts/items: 0
- การ์ด /price | choice |  | ตัวเลือก ["Price list", "Selling price"]
- คำถามข้อความ: No SKU-like header found in root table; using 'Material code' as identity — confirm
- LLM failure: ['llm_failed: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: df6ba2b3-8a6e-4338-a757-4391f796d28d)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}']
หลักฐานเต็ม: `runs/rel-20261103-b/T03_r1/`

### T04 (b)

Round: `rnd_01M6G9GPJ1D76AMP9N48VN5QFJ`; mapping: `map_01M6G9HJWHVVXS14M21NFTJRC6`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9HJWHVVXS14M21NFTJRC6&round=rnd_01M6G9GPJ1D76AMP9N48VN5QFJ)
verification.passed=True; fix_rounds=0; grounded=2; python_turns=0; disagree=4
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 0; extra rows: 0; extra facts/items: 0
- การ์ด /price | confirm |  | ตัวเลือก []
- การ์ด /currency | confirm |  | ตัวเลือก []
หลักฐานเต็ม: `runs/rel-20261103-b/T04_r1/`

### T05 (b)

Round: `rnd_01M6G9HNARZWKWSC1CB93GPCSW`; mapping: `map_01M6G9KW7J77ZGC0X315WK2MYP`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9KW7J77ZGC0X315WK2MYP&round=rnd_01M6G9HNARZWKWSC1CB93GPCSW)
verification.passed=True; fix_rounds=0; grounded=0; python_turns=1; disagree=0
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 0; extra rows: 0; extra facts/items: 0
หลักฐานเต็ม: `runs/rel-20261103-b/T05_r1/`

### T06 (b)

Round: `rnd_01M6G9M095K0BTV70688YT6F0Z`; mapping: `map_01M6G9TZXVWGNEG52H4VBWZ7FK`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9TZXVWGNEG52H4VBWZ7FK&round=rnd_01M6G9M095K0BTV70688YT6F0Z)
verification.passed=True; fix_rounds=1; grounded=0; python_turns=2; disagree=0
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 0; extra rows: 0; extra facts/items: 0
หลักฐานเต็ม: `runs/rel-20261103-b/T06_r1/`

### T07 (b)

Round: `rnd_01M6G9VC34FNFA1RCFWK75DVTS`; mapping: `map_01M6G9ZCF4CQDTC3XA8KX6PGSN`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9ZCF4CQDTC3XA8KX6PGSN&round=rnd_01M6G9VC34FNFA1RCFWK75DVTS)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 36; extra rows: 0; extra facts/items: 0
- LLM failure: ['llm_failed: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: 262bea4b-6abe-4907-8c26-097f63eaaf90)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}']
หลักฐานเต็ม: `runs/rel-20261103-b/T07_r1/`

### T08 (b)

Round: `rnd_01M6G9ZEEP0D395084E0E3CXR5`; mapping: `map_01M6G9ZN0C7PZYC56JZ4ESYJVW`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6G9ZN0C7PZYC56JZ4ESYJVW&round=rnd_01M6G9ZEEP0D395084E0E3CXR5)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 36; extra rows: 0; extra facts/items: 0
- LLM failure: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: 7f1ebbf0-8174-4713-b884-ada04feac25b)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}
หลักฐานเต็ม: `runs/rel-20261103-b/T08_r1/`

### T09 (b)

Round: `rnd_01M6G9ZR8YQ647S6515DBZ75JW`; mapping: `map_01M6GB2MAHKJQ24BRRDXS2639X`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6GB2MAHKJQ24BRRDXS2639X&round=rnd_01M6G9ZR8YQ647S6515DBZ75JW)
verification.passed=True; fix_rounds=1; grounded=2; python_turns=0; disagree=0
ธงแดง: ไม่มีตามเกณฑ์ที่ตรวจ
Wrong fields: 12; extra rows: 0; extra facts/items: 0
- การ์ด /sell_unit:value:9ed0965ce03b | text | The workbook's unit code is 'BT' for all 12 materials. What full selling-unit name should 'BT' map to in the output? | ตัวเลือก ["Bottle", "Bag", "Box", "Bottle (BT)", "Carton"]
- คำถามข้อความ: No SKU-like header found in root table; using 'Material code' as identity — confirm
- คำถามข้อความ: The workbook's unit code is 'BT' for all 12 materials. What full selling-unit name should 'BT' map to in the output?
หลักฐานเต็ม: `runs/rel-20261103-b/T09_r1/`

### T10 (b)

Round: `rnd_01M6GA7XQC6NG1DBH3MYEJPCEN`; mapping: `map_01M6GA854NA4Y20W22HKTNDWJS`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6GA854NA4Y20W22HKTNDWJS&round=rnd_01M6GA7XQC6NG1DBH3MYEJPCEN)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 48; extra rows: 0; extra facts/items: 0
- การ์ด /price | choice |  | ตัวเลือก ["Selling price", "Material code"]
- คำถามข้อความ: No SKU-like header found in root table; using 'Selling price' as identity — confirm
- LLM failure: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: 9b14b44a-94ee-4f7b-96cd-5e9966ef6124)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}
หลักฐานเต็ม: `runs/rel-20261103-b/T10_r1/`

### T11 (b)

Round: `rnd_01M6GA87HQSN9E5VD9YHB9RVQJ`; mapping: `map_01M6GAA3RS90TZCK4E57ZSQ5ZE`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6GAA3RS90TZCK4E57ZSQ5ZE&round=rnd_01M6GA87HQSN9E5VD9YHB9RVQJ)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 36; extra rows: 0; extra facts/items: 0
- คำถามข้อความ: No SKU-like header found in root table; using 'Material code' as identity — confirm
- LLM failure: ['llm_failed: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: 1f6f1422-396a-47ab-9425-5447fc368bdc)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}']
หลักฐานเต็ม: `runs/rel-20261103-b/T11_r1/`

### T12 (b)

Round: `rnd_01M6GAA670E8VMGP3Y7S3ZC6KF`; mapping: `map_01M6GAADPP522JT75C0AV5SWS4`
[เปิดงานนี้ใน FORMA](http://localhost/review?mapping=map_01M6GAADPP522JT75C0AV5SWS4&round=rnd_01M6GAA670E8VMGP3Y7S3ZC6KF)
verification.passed=False; fix_rounds=None; grounded=None; python_turns=None; disagree=0
ธงแดง: rows != 12, verification false, llm_failed
Wrong fields: 96; extra rows: 0; extra facts/items: 0
- การ์ด /price | choice |  | ตัวเลือก ["Price list", "Selling price"]
- คำถามข้อความ: No SKU-like header found in root table; using 'Material code' as identity — confirm
- คำถามข้อความ: Inventory table 'Batches' has no recognisable quantity column
- LLM failure: LLMError: openai HTTP 503: {"error":{"message":"Service is too busy. We advise users to temporarily switch to alternative LLM API service providers. (request_id: 9909df8c-88f9-4909-920e-1699f61452ca)","type":"service_unavailable_error","param":null,"code":"service_unavailable_error"}}
หลักฐานเต็ม: `runs/rel-20261103-b/T12_r1/`

## สถานะงานที่ยังรอ ณ การส่งรายงาน

รอบหลัก B จบครบ 12 กรณี; C07 ลองซ้ำจบด้วย pipeline error. C08 และ D03 ยังรอผลใน worker ตาม runs/final-audit.json. หยุด client D ที่จะส่ง T10/T11/T12 เพิ่มแล้ว เพื่อไม่ส่งงานลองซ้ำใหม่ในช่วง API ไม่เสถียร ไม่ได้หยุด worker หรือยกเลิกงานอื่นของผู้ใช้. รายงานฉบับนี้เป็นผลที่ตรวจแล้ว ณ timestamp ด้านบน ไม่ใช่ผลสุดท้ายของสองงานที่ยังรอ. วิธีเก็บผลเดิมต่อโดยไม่สร้างงานซ้ำอยู่ใน README.md.
