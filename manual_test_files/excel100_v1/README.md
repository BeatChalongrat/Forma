# ชุด Excel 100 ไฟล์: ความสัมพันธ์หลายชีต

ข้อมูลจำลองทั้งหมด ไม่ใช่ไฟล์ที่ได้จากลูกค้าจริง และไม่ยืนยันว่าเป็นรูปแบบ export ของ ERP ยี่ห้อใด

- `inputs/`: Excel 100 ไฟล์, ไฟล์ละ 5–6 ชีต รวม 520 ชีต
- `schemas/`: JSON Schema ที่ใช้ทดสอบแต่ละไฟล์ (โครงสร้างปลายทางเดียวกัน แต่แยกรหัสเพื่อไม่ปนความจำลูกค้า)
- `manifest.json`: รายการไฟล์และพฤติกรรมที่ต้องการ
- `REPORT.md`: ผลทดสอบและข้อจำกัด หลังรันเสร็จ
- `REPORT-AFTER.md`: เปรียบเทียบก่อน–หลังแก้ preview และคำถาม พร้อมตัวเลขคอขวด
- `run-after/`: หลักฐานรอบใหม่ ฐานข้อมูลคนละชุดกับรอบแรก
- `run/`: หลักฐาน HTTP, mapping, คำถาม, preview, ผลดาวน์โหลด และงานเบื้องหลัง
- `oracle/`: ค่าคำตอบสำหรับผู้ตรวจเท่านั้น ห้ามส่งให้โมเดลที่ต้องการวัดความสามารถ

| ชื่อไฟล์ | ประเภท | สิ่งที่ควรเกิด |
|---|---|---|
| X0101–X0110 | สินค้า ราคา คลัง หน่วย แยกชีตและสลับลำดับแถว | เชื่อมด้วยรหัส ไม่ใช่ตำแหน่งแถว |
| X0201–X0210 | รหัสสินค้าเดียวกันในคนละบริษัท | ใช้ Company + Item ป้องกันข้ามบริษัท |
| X0301–X0310 | 3 ล็อต × 3 คลังต่อสินค้า | รวมล็อตในคลังเดียวกัน คงคลังทั้งสามไว้ |
| X0401–X0410 | คลังเป็นคอลัมน์แนวนอน | แปลงเป็นรายการคลังใน inventory |
| X0501–X0510 | ราคาปัจจุบัน/หมดอายุ/ขายส่ง/สกุลอื่น | ใช้ RETAIL สกุลใน Delivery Notes ณ 2026-11-05 |
| X0601–X0610 | หัวรายงาน merge, หัวตารางซ้ำ, TOTAL | ไม่นับหัวตารางและ TOTAL เป็นสินค้า |
| X0701–X0710 | ราคาจากสูตรข้ามชีต มี cached result | อ่านราคาที่มีหลักฐานและผลคำนวณตรงกัน |
| X0801–X0810 | หน่วย ZQ ไม่มีคำจำกัดความ | ถามผู้ใช้; ผู้ทดสอบยืนยัน BOTTLE |
| X0901–X0910 | ราคาสองรายการขัดกัน ไม่มีลำดับความสำคัญ | หยุดให้คนตรวจ ห้ามเลือกแถวแรกเอง |
| X1001–X1010 | สูตรราคาขาด cached result | หยุดแจ้งปัญหา หรือคำนวณใหม่จากหลักฐานที่ครบพร้อมตรวจสอบ |

แต่ละประเภทมี 10 ตัวแปร: 12/18/24/36/48 สินค้า, สลับชีต/แถว, ชื่อหัวคอลัมน์ต่างกัน, ไทย มาเลเซีย อินโดนีเซีย เวียดนาม ฟิลิปปินส์ และสกุลเงินของแต่ละประเทศ
ไม่ได้ครอบคลุมทุก locale/วันที่/ภาษาหรือ ERP จริงทั้งหมด ขนาดรวมประมาณ 1 MB จึงเป็นการทดสอบความสัมพันธ์ ไม่ใช่ทดสอบไฟล์ขนาดใหญ่

## ทดลองด้วยมือ

1. เริ่มทีละไฟล์ เช่น X0101, X0301, X0501, X0801 อย่าเปิด auto-publish ในรอบแรกของไฟล์จริง
2. ลงทะเบียน schema จาก `schemas/<ชื่อไฟล์>.json` และตั้งเป็นรูปแบบปลายทางของลูกค้าทดสอบ
3. อัปโหลดเข้า “ประวัติไฟล์” เลือกลูกค้าทดสอบที่ถูกต้อง ถ้ารูปแบบคล้ายลูกค้ารายอื่นให้ยืนยันเจ้าของไฟล์
4. เปิด “คำถามและปัญหา” ตรวจชื่อชีต/คอลัมน์/ข้อมูลจริงก่อนตอบ ถ้าไม่ทราบอย่ายืนยัน
5. ตรวจราคาตามช่วงเวลา และสต็อกครบ 3 คลัง ห้ามนับ TOTAL/หัวตารางซ้ำเป็นสินค้า
6. ดูตัวอย่าง → บันทึกคำตอบ → อนุมัติ → ดาวน์โหลด ตรวจจำนวน SKU และค่ารายฟิลด์
7. X09/X10 ตั้งใจมีปัญหา จึงไม่ควรถูกปล่อยเป็นผลสำเร็จโดยไม่แก้ข้อมูลหรือรับหลักฐานเพิ่ม

## วิธีทดสอบอัตโนมัติรอบนี้

ใช้สแต็กแยก `forma-excel100`: PostgreSQL, Redis, ARQ workers 2 ตัว × 2 slots, LangGraph และ fake HTTP model
Docker network เป็น internal จึงไม่มีทางออกอินเทอร์เน็ต; fake provider เห็นเฉพาะสัญญา DSL ไม่เห็น oracle หรือผล JSON
สัญญา DSL เป็นคำตอบ mapping ที่กำหนดไว้สำหรับทดสอบ ไม่ใช่โมเดลที่ค้นพบความสัมพันธ์เอง
ระบบ FORMA อ่าน Excel, ประมวลผล join/filter/aggregate/formula cache, ตรวจข้อมูล, จัดการคำถาม และสร้าง JSON จริงทั้งหมด

```powershell
docker compose -f manual_test_files/excel100_v1/compose.yml up -d --scale worker=2
docker compose -f manual_test_files/excel100_v1/compose.yml exec -T api python /suite/run.py
docker compose -f manual_test_files/excel100_v1/compose.yml stop
```

คำสั่งข้างต้นใช้ไฟล์ที่สร้างไว้แล้ว ไม่เรียก API เสียเงิน ผลชุดนี้ไม่ใช่คะแนนความฉลาดหรือ accuracy ของ DeepSeek

## รันซ้ำหลังแก้โค้ด

คง `run/` ไว้เป็น baseline และใช้ชื่อโปรเจกต์ `forma-excel100-after` แยกฐานข้อมูล/ที่เก็บไฟล์

```powershell
docker build --network=none -f manual_test_files/excel100_v1/Dockerfile.after -t forma-excel100-after .
docker compose -f manual_test_files/excel100_v1/compose.yml -f manual_test_files/excel100_v1/compose.after.yml up -d --scale worker=2
docker compose -f manual_test_files/excel100_v1/compose.yml -f manual_test_files/excel100_v1/compose.after.yml exec -T api python /suite/run.py
docker compose -f manual_test_files/excel100_v1/compose.yml -f manual_test_files/excel100_v1/compose.after.yml exec -T api python /suite/audit_evidence.py
backend/.venv/Scripts/python manual_test_files/excel100_v1/analyze_after.py
docker compose -f manual_test_files/excel100_v1/compose.yml -f manual_test_files/excel100_v1/compose.after.yml stop
```

คำสั่งทดสอบต่อจาก state ที่บันทึกไว้ได้ อย่าลบฐานข้อมูลแต่ใช้ state เดิม หรือคัดลอก state ไปใช้กับฐานข้อมูลคนละชุด
