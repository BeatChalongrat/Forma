# ผลทดสอบ Excel 100 ไฟล์ผ่าน FORMA — โมเดลจำลอง ไม่เสียค่า API

ทดสอบวันที่ 5 พฤศจิกายน 2026 (Asia/Bangkok), โค้ด commit `1e5a559eb28864c05491282d0731d2ae3cb0c599`
Docker image `forma-flow-final` / `sha256:7a02fe31ea5b61e7281e4e97369f3631fbf64457451f1d0c4a448738a812695c`

## ข้อสรุป

การประมวลผลข้อมูลเต็มไฟล์และการสร้าง JSON ผ่าน แต่ flow ตรวจคำตอบยังมีบั๊กที่ควรแก้ก่อนใช้จริง: ใน 80 ไฟล์ที่ควรทำสำเร็จ มี 43 ไฟล์ผ่านขั้นตอนปกติ และ 37 ไฟล์ติดหน้า preview แม้ข้อมูลเต็มถูกต้อง อีก 20 ไฟล์ตั้งใจใส่ปัญหาและถูกระงับครบ 20/20.

การตรวจแยกผ่าน API หลังยืนยันข้อมูลเต็ม (ข้ามเฉพาะข้อจำกัดของ preview UI สำหรับวินิจฉัย ไม่แก้โค้ด) ทำให้ดาวน์โหลดได้ 80 ไฟล์ รวม 2,208 สินค้า; เทียบค่าที่กำหนดไว้ตรง 24,288/24,288 ค่าที่ตรวจ ไม่มีแถวเพิ่ม/ซ้ำหรือยอดคลังหายจาก JSON ที่ส่งออก.

คำว่า “ผ่าน flow ปกติ 43 ไฟล์” หมายถึงสามารถกดบันทึก/อนุมัติตามเงื่อนไข UI ได้ ไม่ได้หมายความว่าตัวอย่างบนหน้าจอถูกต้องทั้งหมด: ในกลุ่มนี้ 29 ไฟล์ยังมีตัวอย่างผิด ดังนั้นผ่านทั้งขั้นตอนและแสดงตัวอย่างถูกต้องเพียง 14/80 ไฟล์ที่ควรสำเร็จ ต้องแก้ preview ก่อนให้ผู้ใช้อ้างอิงเพื่อตัดสินใจ.

**ผลนี้ไม่ใช่ accuracy ของ DeepSeek หรือคะแนนความสามารถอ่าน unknown format ของ AI**: fake model คืน mapping DSL ที่เตรียมไว้ ระบบ FORMA เป็นผู้อ่าน Excel และคำนวณค่าจริง ผู้ตรวจอ่านเฉลยแยกต่างหาก ไม่ส่งเฉลย JSON ให้โมเดลจำลอง.

## สิ่งที่ทดสอบ

- 100 XLSX, 520 ชีต, 17,654 แถวจริงใน worksheet, ขนาดรวม 1,039,275 bytes (~1 MB); ไฟล์ละ 12–48 สินค้า จึงยังไม่ใช่การทดสอบ Excel ขนาดใหญ่
- 10 ประเภท × 10 ตัวแปร: join ข้ามชีต, composite key, รวมล็อต, unpivot คลัง, ช่วงเวลา/สกุลเงิน, merged header/TOTAL, สูตรข้ามชีต, หน่วยกำกวม, ราคาขัดกัน, สูตรไม่มี cache
- อัปโหลดผ่าน HTTP API เป็น chunk ไม่เกิน 10 ไฟล์, PostgreSQL + Redis + ARQ workers 2 ตัว × 2 slots, LangGraph analyst/mapper/reviewer กับ fake HTTP provider
- ยืนยันลูกค้าเมื่อรูปแบบชนกัน → preview → ตอบคำถาม → ปฏิเสธ digest เก่า → apply สร้าง mapping version → อนุมัติ → transform/export → ดาวน์โหลด ZIP
- ทดสอบ HTTP flow และเงื่อนไขใน UI จากโค้ด ไม่ได้คลิกเบราว์เซอร์ 100 รอบ
- Docker internal network ปิดทางออกอินเทอร์เน็ต ใช้เฉพาะ `fake-excel100` / `http://fake:8001/v1`; fake container mount เฉพาะ provider/ ไม่มี oracle/
- ไม่มีการเปลี่ยนโค้ด production, การตั้งค่า DeepSeek หรือข้อมูล localhost จริง และไม่ได้ commit งานรอบนี้

## ผลแยกตามประเภท

| ประเภท | ไฟล์ | ผ่าน flow ปกติ | ติด preview | JSON ถูกต้องหลังวินิจฉัย | ระงับถูกต้อง |
|---|---:|---:|---:|---:|---:|
| 1. Shuffled master / prices / inventory / unit joins | 10 | 5 | 5 | 10 | 0 |
| 2. Composite company + item keys with repeated item codes | 10 | 6 | 4 | 10 | 0 |
| 3. Three lots per warehouse, sum without multiplying root rows | 10 | 2 | 8 | 10 | 0 |
| 4. Wide stock matrix unpivoted into warehouse collections | 10 | 10 | 0 | 10 | 0 |
| 5. Price history, current retail filter, mixed currencies | 10 | 5 | 5 | 10 | 0 |
| 6. Merged report headings, repeated header, footer totals | 10 | 4 | 6 | 10 | 0 |
| 7. Cross-sheet price formulas with cached results | 10 | 5 | 5 | 10 | 0 |
| 8. Undefined selling unit: ask, answer, preview, apply | 10 | 6 | 4 | 10 | 0 |
| 9. Competing price rows: must not silently choose a price | 10 | 0 | 0 | 0 | 10 |
| 10. Mapped formula without cache: must not publish invented values | 10 | 0 | 0 | 0 | 10 |

## จุดที่ต้องแก้ก่อนลองไฟล์จริง

### 1. Preview ตัดแถวก่อน join/aggregate จึงแสดงผลผิด — ควรแก้ก่อน pilot

พบ 66/80 ไฟล์ที่ตัวอย่างหลังตอบคำถามมีข้อมูลไม่ตรงผลเต็ม รวม 29 ไฟล์ที่ preview ยังรายงาน `ok=true`; และ 37 ไฟล์รายงาน invalid ทำให้ปุ่มบันทึกคำตอบใน UI ถูก disable.
สาเหตุ: `review_evidence.answer_impact` ใช้ SAMPLE_LIMIT=50 และ `preview_mapping` ส่ง limit ไปจำกัดทุก source ก่อนเชื่อมตาราง/รวมล็อต ส่วน Mapping Studio ส่ง limit=40 เช่นกัน ตัวอย่างจึงอาจไม่มีแถวราคาที่ต้องใช้ หรือได้สต็อกเพียงบางล็อต.
ตัวอย่าง X0310: full execution อ่าน stock 432 แถวและได้ข้อมูลครบ 48 สินค้า แต่ answer-preview อ่าน stock แค่ 50 แถว ทำให้บาง SKU ไม่มี inventory และบางยอดคลังต่ำกว่าจริง ทั้งที่ full validation ผ่าน.
แนวทางแก้: เลือก SKU ตัวอย่างที่ root แล้วอ่านแถวลูก/lookup ที่สัมพันธ์กับ SKU เหล่านั้นให้ครบ หรือประมวลผลครบก่อนจำกัด JSON สำหรับแสดงผล ต้องระบุขอบเขตตัวอย่างและใช้ผลตรวจเต็มเป็นตัวตัดสินการบันทึก แยกกรณีจริงผิดออกจากตัวอย่างไม่ครบ.
เพิ่ม regression test สำหรับ 3 คลัง × หลายล็อต, shuffled lookup, price-history และกรณีที่ preview.ok=true แต่ยอดถูกตัด ห้ามแก้ด้วยการปิด validation.

### 2. ลดคำถามยืนยันที่เกิดจากรูปแบบ expression ต่างกัน

พบคำถาม contested เรื่อง Currency/Full unit/Price แม้โมเดลจำลองชี้คอลัมน์ถูกและผลเต็มตรงทั้งหมด เช่น matcher ใช้ค่าคงที่ THB แต่ mapping ใช้คอลัมน์ Currency; หรือ expression แปลงตัวเลขต่างรูปแบบกัน การถามครั้งแรกเป็นเรื่องเหมาะสม แต่ควรไม่รบกวนผู้ใช้ซ้ำเมื่อหลักฐานเท่ากัน.
แนวทางปรับ: เปรียบเทียบตัวตนของ source และผล/ความหมายภายใต้ schema ไม่ใช้ความต่างของชื่อ source หรือรูป expression เพียงอย่างเดียว และคงคำถามที่ยังมีความหมายทางธุรกิจไม่ชัดเจนไว้.

### 3. ยืนยันด้วยไฟล์จริงชุดเล็กก่อนเพิ่มปริมาณ

หลังแก้ preview ให้รันชุดนี้ซ้ำแบบฟรี แล้วทดลองไฟล์จริงที่ปกปิดข้อมูล 5–10 ไฟล์ รวมไฟล์หลายชีต/สูตร/ตัวเลข locale/รอบแก้ไข พร้อม JSON ที่ทีมปลายทางยืนยัน ชุดนี้ยังไม่ได้พิสูจน์การตัดสินใจของโมเดลจริงหรือไฟล์ทุก ERP.

## การถามและการหยุด

- หน่วย ZQ: มีคำถามเฉพาะจุดครบ 10/10; ใช้คำตอบผู้ทดสอบ BOTTLE ผ่านเส้นทาง preview/apply และตรวจว่าฟิลด์อื่นไม่เปลี่ยน นี่ตรวจการส่งต่อคำถามจาก fake ไม่ใช่ความสามารถที่โมเดลจริงจะรู้ว่าควรถามเอง
- ราคาขัดกัน 10 ไฟล์: ไม่เดาแถวแรก และ POST approve ถูกปฏิเสธ HTTP 422 เพราะ join 1:1 พบคีย์ซ้ำ
- สูตรไม่มี cached result 10 ไฟล์: ไม่แทนด้วย 0 และ approve ถูกปฏิเสธ HTTP 422 พร้อมตำแหน่งเซลล์ที่ต้องแก้
- ลูกค้าที่รูปแบบคล้ายกัน: ขอการยืนยันเจ้าของไฟล์ก่อนเดินต่อ ผู้ทดสอบตอบตามเจ้าของ fixture ที่กำหนดไว้ ไม่ข้ามหรือปิดตัวตรวจนี้

## งานเบื้องหลัง ดาวน์โหลด และต้นทุน

- Jobs ทั้งหมดรวม pilot/การตรวจเพิ่ม: 220; สถานะ {'done': 220}; งานค้าง/ล้มเหลว ณ สิ้นสุด 0
- ระยะเวลางานแต่ละ job: median 1.02s, สูงสุด 7.86s; queue wait สูงสุด 229.61s
- ช่วงเวลาเริ่ม job แรกถึงเสร็จ job สุดท้าย 15.4 นาที รวมการตรวจ harness, pilot และวินิจฉัยซ้ำ จึงไม่ใช่เวลารัน 100 ไฟล์แบบต่อเนื่องครั้งเดียว และห้ามใช้คูณเป็นเวลาของ DeepSeek จริง
- Fake HTTP calls 375, cost_usd ใน mapping agent logs ที่ตรวจ 0.000000, ค่า API ภายนอกจริง $0 (network isolation)
- ZIP สุดท้าย: 80 exported / 20 omitted / 0 not received; complete=false ถูกต้องเพราะ 20 ไฟล์ต้องระงับ
- source hash ตรงต้นฉบับทั้งหมด: True; JSON SHA256, จำนวนแถวและ mapping/dataset version ตรวจใน run/download.json
- มีหลักฐานงานพร้อมดาวน์โหลดพร้อมกับงานที่ยัง processing ใน run/in-progress.json; การตอบคำถามไม่ต้องรอไฟล์ทั้งชุดจบ แต่ job อนุมัติ/ส่งออกอาจรอคิวงานเดิม

## ข้อจำกัดและความโปร่งใส

- Fake model ได้สัญญา mapping ที่ถูกกำหนดไว้ ไม่ได้ค้นหาความสัมพันธ์เอง ไม่มี Python sandbox reasoning จริงและไม่มีการวัด DeepSeek/ChatGPT/Claude
- ใช้ local test storage ไม่ใช่ MinIO/S3; ไม่ครอบคลุมไฟล์ระดับหลายสิบ MB, 300 Excel จริง, restart/recovery, login/RBAC หรือ cross-round learning ในรอบนี้
- Pilot แรกพบ DSL contract ของชุดทดสอบใช้ pointer collection ไม่เต็ม จึงแก้เฉพาะ prepare.py/contracts และเสนอ mapping ใหม่ เก็บหลักฐานไว้ใน pilot-invalid-contract/; ไม่รวมเป็นบั๊ก production
- Harness รุ่นแรกยังไม่จัดการ choice ที่หุ้ม parse_number และกรณีขอยืนยันลูกค้าหลังคิวเดิน จึงปรับ harness แล้วทำขั้นตอนที่ค้างต่อ หลักฐานก่อนปรับอยู่ใน run/harness-first-pass.json
- ไฟล์ที่ติด preview มีการทดสอบผ่าน API แยกใน run/diagnostics/ หลังตรวจข้อมูลเต็มแล้วเท่านั้น ยังถือว่า flow UI ปกติไม่ผ่าน ไม่ได้อ้างว่าบั๊กถูกแก้

## ผลต่อไฟล์

ตารางนี้แสดงผลเต็มและผลปลายทาง; “วินิจฉัย” คือผ่าน backend หลังข้ามข้อจำกัด preview UI ในชุดทดสอบ ไม่ใช่ normal UI pass.

| ไฟล์ | สินค้า | คำถาม | ชนิดคำถาม | Full verify | Flow ปกติ | JSON เทียบเฉลย | หมายเหตุ |
|---|---:|---:|---|---|---|---|---|
| X0101.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0102.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0103.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0104.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0105.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0106.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0107.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0108.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0109.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0110.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0201.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0202.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0203.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0204.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0205.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0206.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0207.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0208.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0209.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0210.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0301.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0302.xlsx | 18 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0303.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0304.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0305.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0306.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0307.xlsx | 18 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0308.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0309.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0310.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0401.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0402.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0403.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0404.xlsx | 36 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0405.xlsx | 48 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0406.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0407.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0408.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0409.xlsx | 36 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0410.xlsx | 48 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0501.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0502.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0503.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0504.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0505.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0506.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0507.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0508.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0509.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0510.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0601.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0602.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0603.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0604.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0605.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0606.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0607.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0608.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0609.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0610.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0701.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0702.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0703.xlsx | 24 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0704.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0705.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0706.xlsx | 12 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0707.xlsx | 18 | 2 | {'choice': 2} | True | ผ่าน | 100.0% | ผ่าน |
| X0708.xlsx | 24 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0709.xlsx | 36 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0710.xlsx | 48 | 2 | {'choice': 2} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0801.xlsx | 12 | 2 | {'choice': 1, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0802.xlsx | 18 | 3 | {'choice': 2, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0803.xlsx | 24 | 2 | {'choice': 1, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0804.xlsx | 36 | 2 | {'choice': 1, 'text': 1} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0805.xlsx | 48 | 3 | {'choice': 2, 'text': 1} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0806.xlsx | 12 | 2 | {'choice': 1, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0807.xlsx | 18 | 2 | {'choice': 1, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0808.xlsx | 24 | 3 | {'choice': 2, 'text': 1} | True | ผ่าน | 100.0% | ผ่าน |
| X0809.xlsx | 36 | 2 | {'choice': 1, 'text': 1} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0810.xlsx | 48 | 2 | {'choice': 1, 'text': 1} | True | ติด preview | 100.0% | วินิจฉัยผ่าน API |
| X0901.xlsx | 12 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0902.xlsx | 18 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0903.xlsx | 24 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0904.xlsx | 36 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0905.xlsx | 48 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0906.xlsx | 12 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0907.xlsx | 18 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0908.xlsx | 24 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0909.xlsx | 36 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X0910.xlsx | 48 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1001.xlsx | 12 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1002.xlsx | 18 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1003.xlsx | 24 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1004.xlsx | 36 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1005.xlsx | 48 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1006.xlsx | 12 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1007.xlsx | 18 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1008.xlsx | 24 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1009.xlsx | 36 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |
| X1010.xlsx | 48 | 2 | {'choice': 2} | False | — | — | ระงับถูกต้อง |

ข้อมูลตัวเลขสำหรับกรองต่อ: case-results.csv และ run/case-metrics.json; หลักฐาน preview: run/preview-audit.json.
