# ผลแก้ไขและทดสอบซ้ำ Excel 100 ไฟล์ — 5 พฤศจิกายน 2026

ใช้ไฟล์เดิม 100 XLSX / 520 ชีต / 17,654 แถวจริง ชุดใหม่อยู่ในฐานข้อมูลแยก ใช้โมเดลจำลองและ Docker internal network ค่า API $0 ไม่มีการส่งไฟล์ไป DeepSeek

## ก่อน–หลัง

| สิ่งที่ตรวจ | ก่อน | หลัง |
|---|---:|---:|
| ผ่าน flow ปกติใน 80 ไฟล์ที่ควรสำเร็จ | 43/80 | 80/80 |
| ผ่าน flow พร้อม preview ถูกต้อง | 14/80 | 80/80 |
| ระงับไฟล์ราคาซ้ำ/สูตรไม่มี cache | 20/20 | 20/20 |
| ถามหน่วย ZQ ก่อนเผยแพร่ | 10/10 | 10/10 |
| คำถามแบบเลือก/ยืนยัน/กรอกบนการ์ด รวมทุกไฟล์ | 203 | 136 |
| JSON ส่งออกตรงเฉลย | 80/80 หลังวินิจฉัยข้าม preview 37 ไฟล์ | 80/80 ผ่านขั้นตอนปกติ |

ผลหลังแก้: 2,208 สินค้า, ถูก 24,288/24,288 ค่า; ตรวจ hash ไฟล์ต้นทางและ ZIP พร้อม mapping/dataset version: True. งาน: {'done': 238}.

## สิ่งที่แก้

1. Preview และหลักฐานคำถามประมวลผล source ครบก่อนจำกัดรายการแสดง จึงไม่ตัด lookup หรือบางล็อตออกจากยอดรวม ตรวจความถูกต้องทั้งไฟล์ รวมแถวที่ไม่ได้แสดงบนหน้าจอ
2. Answer preview ใช้ผลคำนวณก่อน/หลังร่วมกับตัวตรวจ แทนการรันซ้ำ 4 ครั้ง เหลือ 2 ครั้งต่อคำขอ Apply ยังคำนวณใหม่และตรวจ digest เพื่อกันผลล้าสมัย
3. ยอมรับช่วงคอลัมน์ที่เท่ากันเมื่อ metadata พิสูจน์ว่าอ่านเต็มชีต และยุติข้อขัดแย้งสกุลเงินเฉพาะเมื่อชื่อคอลัมน์ตรง schema และค่าทั้งไฟล์ตรงกัน ไม่ยกเลิกคำถามที่หน่วย/ราคา/ความหมายยังคลุมเครือ
4. แก้ข้อความ UI ให้แยกผลตรวจเต็มไฟล์กับตัวอย่างรายการแสดง และทดสอบซ้ำผ่าน upload → mapping → questions → preview → apply/version → approval → export → ZIP

ยืนยัน preview คำตอบ 62 ไฟล์ และปฏิเสธ digest เก่า 62 ครั้ง; Mapping Studio preview ขนาด 40 รายการตรวจครบทุกไฟล์ที่ควรผ่าน แม้ไม่มีคำถาม.

ตรวจหลักฐานเซลล์จริงผ่าน API ของไฟล์รวมล็อตทั้ง 10 ไฟล์: 50/50 ตัวอย่างมีเซลล์ครบ 3 ล็อตและรวมได้ตรงค่าที่แสดง ไม่ชี้แค่บางแถว.

## คอขวดความเร็ว

รอบนี้ตั้ง worker 2 ตัว × 2 slots: เวลาตั้งแต่งานแรกเข้าคิวจนงานสุดท้ายเสร็จ 652.6 วินาที (10.9 นาที) รวมเวลาผู้ทดสอบอัตโนมัติเรียก preview/ตอบ/อนุมัติ ไม่ใช่เวลา AI ล้วน

| ส่วนงาน | ค่ากลาง (วินาที) | P95 (วินาที) | สูงสุด (วินาที) |
|---|---:|---:|---:|
| ระยะทำงานที่ DB บันทึก | 0.946 | 6.470 | 8.020 |
| ระยะถือ slot ของ ARQ | 8.575 | 13.570 | 17.110 |
| ส่วนต่างเริ่ม/ปิด process และงานรอบนอก | 6.407 | 8.287 | 9.476 |
| รอก่อนเริ่มงาน รวม import ก่อน claim | 8.136 | 214.826 | 228.376 |
| ขั้น profile | 0.029 | 0.055 | 0.067 |
| ขั้น propose | 5.172 | 6.249 | 7.179 |
| ขั้น publish | 0.026 | 0.045 | 0.068 |
| ขั้น transform | 0.457 | 0.617 | 0.854 |
| ขั้น identify | 0.255 | 0.422 | 0.513 |

วัด import Python ใหม่โดยไม่รันงาน/โมเดลอีก 3 ครั้ง ได้ [4.061, 4.381, 4.738] วินาที; ค่ากลาง 4.381 วินาที

ARQ เปิด Python process ใหม่ทุกงานเพื่อให้หยุดงานค้างได้จริง ส่วนต่างระหว่าง ARQ/DB จึงบอก overhead รอบงาน ไม่ใช่เวลาคำนวณ Excel เพียงอย่างเดียว คิวรับไฟล์และคิวประมวลผล/ส่งออกใช้ slots ร่วมกัน

หลักฐาน DeepSeek เดิม (workflow_v1/wf-after-summary.json): 10 กรณีซับซ้อนรวม 2,187.62 วินาที / 69 model calls (~219 วินาทีต่อกรณีรวม flow) รอบนี้ไม่ได้เรียกซ้ำ จึงไม่ใช้เวลาของ fake ทำนายความเร็ว DeepSeek หรืออ้างว่า AI เร็วขึ้นเท่านี้
แนวทางเร่งต่อ: ลดการอ่าน/ส่งบริบท/เรียกโมเดลซ้ำอย่างมีหลักฐาน, ใช้ mapping ที่อนุมัติแล้วกับ format ที่เข้ากันได้, วัดและแยกคิวรับไฟล์กับคิวส่งออก และพิจารณา worker process ที่อุ่นไว้แต่ยังหยุดแยกงานได้ การเปลี่ยน worker ต้องทดสอบ timeout/cancel/recovery ใหม่

## ขอบเขตการพิสูจน์

fake provider คืน DSL ที่เตรียมไว้และมองไม่เห็น oracle ระบบจริงเป็นผู้อ่าน Excel/เชื่อมชีต/รวมข้อมูลและสร้าง JSON; การตรวจ oracle ทำใน test harness หลังได้รับผลเท่านั้น คะแนนนี้พิสูจน์ความถูกต้องของ execution และ flow ไม่ใช่ความฉลาดของ DeepSeek ต่อ unknown format
ไฟล์รวมประมาณ 1 MB และมี 12–48 สินค้าต่อไฟล์ จึงยังไม่พิสูจน์หน่วยความจำหรือเวลา Excel ขนาดใหญ่มาก ยังไม่มีไฟล์ ERP จริงตามคำยืนยันผู้ใช้ จึงยังต้อง pilot 5–10 ไฟล์จริงพร้อมผลที่ทีมปลายทางตรวจ เมื่อมีข้อมูล
ทดสอบผ่าน HTTP/API ตามเงื่อนไข UI ไม่ได้คลิกเบราว์เซอร์ 100 ครั้ง ไม่ได้ commit งานรอบนี้

การตรวจโค้ด: regression tests ที่เกี่ยวข้อง 100 ข้อผ่าน (preview/evidence, mapping harness, relationship, decisions/memory, lineage, custom schema และ review queue), Ruff และ mypy ผ่านสำหรับ backend ที่แก้, frontend TypeScript/Vite build ผ่าน มีคำเตือน bundle ขนาดใหญ่เดิม

อัปเดต API, worker และเว็บที่ localhost แล้ว ตรวจ /ready ผ่าน และยืนยันข้อความ preview ใหม่ใน frontend; รุ่นที่ตั้งไว้ยังเป็น deepseek-flash ไม่มีการรันงานผ่านโมเดลจริง ดูหลักฐาน run-after/localhost-check.json

## ผลรายไฟล์

| ไฟล์ | กลุ่ม | คำถามก่อน→หลัง | ผ่าน flow | Preview | ระงับถูกจุด | จำนวน JSON | ถูกต้อง |
|---|---:|---:|---|---|---|---:|---:|
| X0101 | 1 | 2→1 | True | True | None | 12 | 1.0 |
| X0102 | 1 | 2→0 | True | True | None | 18 | 1.0 |
| X0103 | 1 | 2→2 | True | True | None | 24 | 1.0 |
| X0104 | 1 | 2→2 | True | True | None | 36 | 1.0 |
| X0105 | 1 | 2→0 | True | True | None | 48 | 1.0 |
| X0106 | 1 | 2→1 | True | True | None | 12 | 1.0 |
| X0107 | 1 | 2→2 | True | True | None | 18 | 1.0 |
| X0108 | 1 | 2→0 | True | True | None | 24 | 1.0 |
| X0109 | 1 | 2→2 | True | True | None | 36 | 1.0 |
| X0110 | 1 | 2→2 | True | True | None | 48 | 1.0 |
| X0201 | 2 | 2→1 | True | True | None | 12 | 1.0 |
| X0202 | 2 | 2→0 | True | True | None | 18 | 1.0 |
| X0203 | 2 | 2→2 | True | True | None | 24 | 1.0 |
| X0204 | 2 | 2→2 | True | True | None | 36 | 1.0 |
| X0205 | 2 | 2→0 | True | True | None | 48 | 1.0 |
| X0206 | 2 | 2→1 | True | True | None | 12 | 1.0 |
| X0207 | 2 | 2→2 | True | True | None | 18 | 1.0 |
| X0208 | 2 | 2→0 | True | True | None | 24 | 1.0 |
| X0209 | 2 | 2→2 | True | True | None | 36 | 1.0 |
| X0210 | 2 | 2→2 | True | True | None | 48 | 1.0 |
| X0301 | 3 | 2→1 | True | True | None | 12 | 1.0 |
| X0302 | 3 | 2→0 | True | True | None | 18 | 1.0 |
| X0303 | 3 | 2→2 | True | True | None | 24 | 1.0 |
| X0304 | 3 | 2→2 | True | True | None | 36 | 1.0 |
| X0305 | 3 | 2→0 | True | True | None | 48 | 1.0 |
| X0306 | 3 | 2→1 | True | True | None | 12 | 1.0 |
| X0307 | 3 | 2→2 | True | True | None | 18 | 1.0 |
| X0308 | 3 | 2→0 | True | True | None | 24 | 1.0 |
| X0309 | 3 | 2→2 | True | True | None | 36 | 1.0 |
| X0310 | 3 | 2→2 | True | True | None | 48 | 1.0 |
| X0401 | 4 | 2→1 | True | True | None | 12 | 1.0 |
| X0402 | 4 | 2→0 | True | True | None | 18 | 1.0 |
| X0403 | 4 | 2→2 | True | True | None | 24 | 1.0 |
| X0404 | 4 | 2→2 | True | True | None | 36 | 1.0 |
| X0405 | 4 | 2→0 | True | True | None | 48 | 1.0 |
| X0406 | 4 | 2→1 | True | True | None | 12 | 1.0 |
| X0407 | 4 | 2→2 | True | True | None | 18 | 1.0 |
| X0408 | 4 | 2→0 | True | True | None | 24 | 1.0 |
| X0409 | 4 | 2→2 | True | True | None | 36 | 1.0 |
| X0410 | 4 | 2→2 | True | True | None | 48 | 1.0 |
| X0501 | 5 | 2→1 | True | True | None | 12 | 1.0 |
| X0502 | 5 | 2→2 | True | True | None | 18 | 1.0 |
| X0503 | 5 | 2→2 | True | True | None | 24 | 1.0 |
| X0504 | 5 | 2→2 | True | True | None | 36 | 1.0 |
| X0505 | 5 | 2→2 | True | True | None | 48 | 1.0 |
| X0506 | 5 | 2→1 | True | True | None | 12 | 1.0 |
| X0507 | 5 | 2→2 | True | True | None | 18 | 1.0 |
| X0508 | 5 | 2→2 | True | True | None | 24 | 1.0 |
| X0509 | 5 | 2→2 | True | True | None | 36 | 1.0 |
| X0510 | 5 | 2→2 | True | True | None | 48 | 1.0 |
| X0601 | 6 | 2→1 | True | True | None | 12 | 1.0 |
| X0602 | 6 | 2→0 | True | True | None | 18 | 1.0 |
| X0603 | 6 | 2→2 | True | True | None | 24 | 1.0 |
| X0604 | 6 | 2→2 | True | True | None | 36 | 1.0 |
| X0605 | 6 | 2→0 | True | True | None | 48 | 1.0 |
| X0606 | 6 | 2→1 | True | True | None | 12 | 1.0 |
| X0607 | 6 | 2→2 | True | True | None | 18 | 1.0 |
| X0608 | 6 | 2→0 | True | True | None | 24 | 1.0 |
| X0609 | 6 | 2→2 | True | True | None | 36 | 1.0 |
| X0610 | 6 | 2→2 | True | True | None | 48 | 1.0 |
| X0701 | 7 | 2→1 | True | True | None | 12 | 1.0 |
| X0702 | 7 | 2→0 | True | True | None | 18 | 1.0 |
| X0703 | 7 | 2→2 | True | True | None | 24 | 1.0 |
| X0704 | 7 | 2→2 | True | True | None | 36 | 1.0 |
| X0705 | 7 | 2→0 | True | True | None | 48 | 1.0 |
| X0706 | 7 | 2→1 | True | True | None | 12 | 1.0 |
| X0707 | 7 | 2→2 | True | True | None | 18 | 1.0 |
| X0708 | 7 | 2→0 | True | True | None | 24 | 1.0 |
| X0709 | 7 | 2→2 | True | True | None | 36 | 1.0 |
| X0710 | 7 | 2→2 | True | True | None | 48 | 1.0 |
| X0801 | 8 | 2→1 | True | True | None | 12 | 1.0 |
| X0802 | 8 | 3→1 | True | True | None | 18 | 1.0 |
| X0803 | 8 | 2→2 | True | True | None | 24 | 1.0 |
| X0804 | 8 | 2→2 | True | True | None | 36 | 1.0 |
| X0805 | 8 | 3→1 | True | True | None | 48 | 1.0 |
| X0806 | 8 | 2→1 | True | True | None | 12 | 1.0 |
| X0807 | 8 | 2→2 | True | True | None | 18 | 1.0 |
| X0808 | 8 | 3→1 | True | True | None | 24 | 1.0 |
| X0809 | 8 | 2→2 | True | True | None | 36 | 1.0 |
| X0810 | 8 | 2→2 | True | True | None | 48 | 1.0 |
| X0901 | 9 | 2→2 | False | None | True | None | None |
| X0902 | 9 | 2→0 | False | None | True | None | None |
| X0903 | 9 | 2→2 | False | None | True | None | None |
| X0904 | 9 | 2→2 | False | None | True | None | None |
| X0905 | 9 | 2→0 | False | None | True | None | None |
| X0906 | 9 | 2→2 | False | None | True | None | None |
| X0907 | 9 | 2→2 | False | None | True | None | None |
| X0908 | 9 | 2→0 | False | None | True | None | None |
| X0909 | 9 | 2→2 | False | None | True | None | None |
| X0910 | 9 | 2→2 | False | None | True | None | None |
| X1001 | 10 | 2→2 | False | None | True | None | None |
| X1002 | 10 | 2→1 | False | None | True | None | None |
| X1003 | 10 | 2→2 | False | None | True | None | None |
| X1004 | 10 | 2→2 | False | None | True | None | None |
| X1005 | 10 | 2→1 | False | None | True | None | None |
| X1006 | 10 | 2→2 | False | None | True | None | None |
| X1007 | 10 | 2→2 | False | None | True | None | None |
| X1008 | 10 | 2→1 | False | None | True | None | None |
| X1009 | 10 | 2→2 | False | None | True | None | None |
| X1010 | 10 | 2→2 | False | None | True | None | None |
