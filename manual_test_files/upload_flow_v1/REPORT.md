# ทดสอบตั้งแต่อัปโหลดจนดาวน์โหลด — 4 พฤศจิกายน 2026

## ขอบเขต

แก้ 3 จุด: การตัดไฟล์ซ้ำที่หน้าอัปโหลด, การบันทึกงานให้ต่อเนื่องกับการเปลี่ยนข้อมูล, และรายการกำกับ ZIP
ใช้โมเดลจำลอง HTTP `fake-stability` เท่านั้น ค่า API จริง **$0** ไม่เปลี่ยน provider/key ของระบบใช้งาน
และไม่มีการ commit งานนี้

## ก่อน–หลัง

| จุด | ก่อน | หลัง |
|---|---|---|
| เลือกไฟล์ | ชื่อและขนาดเท่ากันอาจถูกตัดทิ้ง ทั้งที่ข้อมูลต่างกัน | ส่งครบ ให้เซิร์ฟเวอร์เทียบ SHA-256; ข้ามเฉพาะชื่อและเนื้อหาเดียวกันในรอบเดิม |
| คิวกับข้อมูล | ข้อมูลถูก commit ก่อนสร้าง Job; หากเกิดข้อผิดพลาดระหว่างสองขั้นตอนอาจค้างโดยไม่มีงาน | บันทึกข้อมูลและ Job ใน transaction เดียว; คิวเต็มยกเลิกการเปลี่ยนแปลง; Redis ล่มส่งงานซ้ำจาก outbox |
| เน็ตหลุดหลังรับไฟล์ | กดสร้างรอบซ้ำอาจสร้างรอบใหม่ | ใช้ upload_key ของคำขอเดิม คืนรอบเดิม; ทดสอบคำขอพร้อมกันแล้ว |
| ดาวน์โหลดบางส่วน | มีเฉพาะ JSON ที่พร้อม จึงไม่รู้ว่าอะไรตกหล่น | `_forma/manifest.json` บอกไฟล์ที่รวม/ไม่รวม เหตุผล จำนวนที่ยังไม่รับ และเวอร์ชันพร้อม checksum |

## ผล flow จริงในสภาพแวดล้อมแยก

API → PostgreSQL 16 → Redis 7/ARQ worker → LangGraph → HTTP fake model → ตรวจ/ตอบคำถาม → อนุมัติ → JSON → ZIP
ใช้ local storage ของระบบทดสอบ; ไม่ได้เขียนข้อมูลลง MinIO ของ localhost ที่ใช้งานจริง

| กรณี | ผล |
|---|---|
| CSV ชื่อเดียวกัน ขนาดเท่ากัน แต่ราคาแรก 10.50 และ 99.50 | รับทั้งสองไฟล์; JSON แยกกันและราคาตรงต้นทาง |
| ส่ง CSV เดิมซ้ำในคำขอ | ข้าม 1 ไฟล์ซ้ำจาก 3 ไฟล์ที่ส่ง เหลือ 2 ไฟล์จริง |
| ส่งคำขอแรกซ้ำหลังรับแล้ว | ได้ batch ID เดิม ไม่สร้างซ้ำ |
| หาเจ้าของไฟล์ไม่ชัด | รอเลือกลูกค้า แล้วจึงสร้าง mapping |
| VAT ไม่ปรากฏในหัวตาราง | มีคำถาม boolean; preview ก่อนบันทึก; สร้าง mapping เวอร์ชันใหม่ |
| ใช้ digest ของ preview เก่า | ปฏิเสธ 409 |
| อนุมัติ mapping | สร้างข้อมูล 12 แถวต่อไฟล์; ตรวจ JSON ก่อนดาวน์โหลด |
| ดาวน์โหลดเมื่ออีกไฟล์ยังรอตรวจ | ZIP มี 1 JSON + manifest ระบุอีก 1 ไฟล์ที่ไม่รวม และอีก 2 ไฟล์ที่ยังไม่ได้รับตามเป้า |
| Excel 12 สินค้า | JSON 12 แถว; ราคาสินค้าแรก 20 ตรงต้นทาง; รอบต่อมาใช้รูปแบบที่อนุมัติแล้วได้ |
| ไฟล์ .xlsx ที่เนื้อหาไม่ใช่ Excel | ระบุ failed พร้อมเหตุผล ไม่ปะปนในผลลัพธ์ |
| แทนที่ CSV โดยแก้ราคาเป็น 55.50 | เก็บรอบเก่าเป็น superseded; manifest ชี้รอบใหม่; JSON มีราคา 55.50 |
| ราคาที่เปลี่ยนสร้าง anomaly | รอคนอนุมัติข้อมูลก่อนส่งออก |
| อัปโหลดพร้อมกัน 2 คำขอ ด้วย upload_key เดียว | สร้างรอบเดียว อีกคำขอเป็น replay |
| ดาวน์โหลดรอบที่ครบแล้ว | 1/1 JSON, 12 แถว, complete=true, omitted=0 |
| ตรวจไฟล์ ZIP | จำนวนแถว เวอร์ชัน และ SHA-256 ของ JSON ตรง manifest ทุกไฟล์ |

รอบยืนยันท้ายใช้ **153.50 วินาที** รวมการรอ worker และการตรวจผ่าน API มีการเรียก fake provider 9 ครั้ง
Job ที่เกี่ยวกับ flow นี้ **10 งานสำเร็จ ไม่มี Job failed** (`jobs.json` กรองเฉพาะรอบที่ทดสอบ; log ดิบรวมงานรอบก่อนด้วย)
ตัวเลขนี้ไม่ใช่ความเร็วหรือความแม่นยำของ DeepSeek จริง รายละเอียดอยู่ใน `result.json` และ ZIP ตัวอย่าง

## Browser และเหตุขัดข้อง

- Chromium + frontend จริงที่พอร์ต 15173 ต่อ API ทดสอบพอร์ต 18084
- เลือกไฟล์ชื่อเดียวกัน 36 bytes ทั้งสองไฟล์ เห็นครบ 2 แถว
- ตัด response หลัง API commit สำเร็จ หน้าเว็บแจ้งให้ retry; กดอีกครั้งใช้ upload_key เดิมและกลับรอบเดิม
- ไม่มี JavaScript page error; หลักฐาน `browser-result.json` และภาพ `browser-*.png`
- ปิด Redis เฉพาะโปรเจกต์ `forma-flow`: รับไฟล์ได้ HTTP 201 และมี Job queued 1 งานใน PostgreSQL
- เปิด Redis กลับ: Job เดิมทำต่อจนรอเลือกลูกค้า ไม่สร้าง Job ซ้ำ ใช้ **32.92 วินาที** นับรวม outage/recovery
- หลักฐาน `broker-before.json`, `broker-after.json`, `broker-result.json`

## การทดสอบอัตโนมัติ

- กลุ่ม upload/lifecycle/file actions/job resilience/workspace ในโค้ดสุดท้าย: **57 ผ่าน** (122.57 วินาที)
- ครอบคลุม approve/replace rollback, งาน queued เก่าที่ไม่ควรสร้างซ้ำ และ legacy gap ที่ควร resume ได้
- ตรวจว่า Job แบบ run_pipeline ที่ยังทำอยู่ไม่ถูกมองเป็นไฟล์ค้าง; ไม่สร้าง process_batch_item ซ้ำ
- Frontend tests: **12 ผ่าน**
- Mypy: ผ่าน 143 source files
- Frontend build และ lint: ผ่าน; lint มี warning เดิม และ build มีคำเตือน bundle ใหญ่
- Full backend regression: **915 ผ่าน / 5 ข้าม / ไม่มี failed** (`-m 'not slow' --no-cov`, exit 0)
- กลุ่ม 57 รายการข้างต้นรันซ้ำบนการแก้สุดท้าย เพื่อครอบคลุม Resume กับชนิด Job ที่ต่างกันเพิ่มเติมจากรอบ regression
- Restart API/worker ของ stack จำลองด้วย image สุดท้าย: ready=ok, queued=0, running=0, failed=0 (`restart.json`)

## ข้อจำกัดที่ยังต้องเข้าใจ

1. โมเดลจำลองยืนยันความต่อเนื่องของระบบและค่าที่คาดหวังใน fixture ไม่ใช่หลักฐานว่า AI จริงอ่าน ERP ใหม่ถูกทุกแบบ
2. ปุ่ม retry ของหน้าที่ยังเปิดอยู่ใช้ key เดิม แต่ยังไม่ใช่อัปโหลดแบบปิดเบราว์เซอร์แล้วส่ง bytes ต่อได้
3. ZIP ยังสร้างในหน่วยความจำ; งานที่ JSON ใหญ่มากควรมีขอบเขตขนาดหรือ streaming export ก่อนขยายโหลดมากขึ้น
4. ไฟล์ที่ยังรอตรวจ/เสียไม่ถูกนับว่าดาวน์โหลดครบ และหากไม่มี JSON พร้อมเลยยังตอบ 409
5. เมื่อคิวเต็ม transaction ฐานข้อมูลยกเลิกทั้งหมด แต่ blob ที่เขียนไปก่อนอาจคงอยู่เพื่อให้ retention จัดการ

## สถานะ localhost หลังอัปเดต

อัปเดต API, worker และ web แล้ว ตรวจเมื่อ 23:44 น. วันที่ 4 พฤศจิกายน 2026:
ready=ok, queued=0, running=0 และยังเป็น `deepseek-flash` โดยไม่ได้เรียก inference
Job failed เดิม 5 งานยังเท่าเดิม ไม่ได้กด retry หรือเปลี่ยนข้อมูลเหล่านั้น
ดาวน์โหลด ZIP ของรอบเดิมแบบอ่านอย่างเดียวผ่าน MinIO ได้ มี manifest ใหม่ และ checksum ตรงทุกไฟล์
หลักฐาน `deployment.json` เก็บ Docker image ก่อนแก้ไว้สำหรับ rollback; ยังไม่ commit
หยุด stack จำลองหลังทดสอบ โดยเก็บ volume และหลักฐานไว้

## รันซ้ำโดยไม่เสีย API

จาก repo root:

```powershell
docker build -f backend/Dockerfile -t forma-flow-code .
docker compose -p forma-flow -f manual_test_files/stability_v1/compose.yml -f manual_test_files/upload_flow_v1/compose.yml up -d
backend\.venv\Scripts\python manual_test_files/upload_flow_v1/run_flow.py
backend\.venv\Scripts\python manual_test_files/upload_flow_v1/broker_recovery.py
```

สคริปต์ตรวจ endpoint/model ของ fake ก่อนทำงาน สคริปต์ outage ตรวจ label โปรเจกต์ก่อนหยุด Redis
ห้ามชี้สคริปต์ไปยัง provider จริงเพื่ออ้างว่าเป็นเทสฟรี
