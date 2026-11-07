# ห้องทำงาน FORMA

เปิด `/activity` จากเมนู **ห้องทำงาน** หรือปุ่มในรอบงาน/รายละเอียดไฟล์ ใช้ `?batch=…` จำกัดรอบอัปโหลด และ `?round=…` จำกัดชุดไฟล์ของลูกค้า

- ห้อง 2D แสดงโต๊ะสำรวจ จับคู่ ตรวจความหมาย ระบบ รอคนยืนยัน และผลลัพธ์พร้อมใช้
- กรอบกิจกรรมอ้างอิงเหตุการณ์จาก worker และ tool จริง ไม่สร้างข้อความคิดภายในโมเดล ไม่จำลองความคืบหน้าตามเวลา
- งาน running ต้องมี lease ที่ยังไม่หมดอายุจึงเคลื่อนไหวได้ งานที่สำเร็จการประมวลผลไม่ได้แปลว่าอนุมัติ/ดาวน์โหลดได้แล้ว
- ค้นชื่อไฟล์ เลือกงาน และเลือกครั้งที่ retry เพื่ออ่าน input/output, Python, หลักฐาน, ข้อผิดพลาด, ระยะเวลา และผลการจับคู่ที่บันทึกไว้
- Log แบ่งหน้า 100 เหตุการณ์ การเปิดเหตุการณ์โหลดคู่เริ่ม–จบของ span เดียวกันได้ แม้อยู่คนละหน้า
- พักการแสดงผลไม่หยุด worker; ลดการเคลื่อนไหวและ `prefers-reduced-motion` รองรับผู้ที่ไม่ต้องการ animation
- โต๊ะแสดงงาน running สูงสุด 100 งานในขอบเขตที่เลือก ส่วนงานจบ/รอคนแสดงจากหน้ารายการปัจจุบัน (50 งานต่อหน้า) ตัวเลขบนสุดนับ **งาน** ไม่ใช่จำนวนไฟล์ เพราะหนึ่งไฟล์อาจมีหลายงาน

## ข้อมูลและขอบเขต

`job_activity` เป็นบันทึกเพิ่มอย่างเดียว ผูก job/attempt/span/parent/role/action มีเวลา UTC และผลจริง การตัดสินใจทางธุรกิจยังอยู่ใน mapping/policy/review เดิม ไม่มีการอนุมัติจากหน้าห้องทำงาน

บันทึกครอบคลุม job lifecycle, pipeline stages, graph roles, model requests (provider/model/token usage ไม่เก็บ prompt หรือ raw reasoning), file inspection, deterministic verification, sandbox Python และการนำ mapping เดิมกลับมาใช้ ข้อมูลจากโมเดล/ไฟล์ถือเป็นหลักฐานที่ต้องตรวจ ไม่ใช่คำสั่งของระบบ

API ใช้ customer scope เดียวกับ jobs เดิม รวมถึงการเปิด span ย้อนหลัง งานที่ไม่อนุญาตตอบ 404 งานเก่าก่อนติดตั้งไม่มี live receipts; เปิดผลและ agent log เดิมได้ถ้างานอ้างอิง mapping ไว้

รายละเอียดจำกัดความลึก 12 ระดับ, 200 รายการต่อ array, 24,000 ตัวอักษรต่อข้อความ และ 256,000 ตัวอักษรต่อเหตุการณ์ โดยระบุเมื่อถูกตัด ข้อมูลเต็มทางธุรกิจยังเปิดผ่านหน้าไฟล์/การจับคู่เดิม ไม่อ้างว่าบันทึกนี้เก็บทุกแถวของ Excel มีการปิดบังคีย์/credential fields; ไม่มี API key หรือ prompt ถูกส่งเป็น input ให้ journal

## Deploy และดูแลข้อมูล

รัน `forma db upgrade` ถึง `d9a012345679` ก่อนเริ่ม API/worker ใหม่: `d8a012345678` เพิ่ม journal และ `d9a012345679` ปรับชนิด JSON ของ review drafts/receipts ให้ตรงกับ model โดยรักษาค่าเดิม (พบจากการตรวจ backup/restore) ต้องอัปเดตทั้ง API, worker และ web จึงเห็นกิจกรรมใหม่

PostgreSQL เก็บ journal ในฐานเดียวกับแอปโดยใช้ transaction แยก จึงยังอ่านได้ระหว่าง mapping และอยู่ใน `pg_dump` ตามปกติ

SQLite ใช้ `<database>.activity.sqlite` ข้างฐานหลัก เพื่อไม่แย่ง writer lock ของ transaction ที่กำลังจับคู่ เครื่องมือ `forma backup` รวม snapshot ของ journal เข้า `db.sqlite` และ `forma restore` แยกกลับให้อัตโนมัติ การสำรองด้วยเครื่องมือภายนอกต้องเก็บทั้งฐานหลักและ journal ด้วย SQLite backup API

เมื่อ retention ลบ job ที่หมดอายุ จะลบกิจกรรมของ job หลัง commit; rollback ไม่ลบหลักฐาน หาก cleanup ล้มเหลวมี server log `activity retention cleanup failed` พร้อม job IDs ให้ตรวจและเรียก `purge_jobs(ids)` ซ้ำได้ มันไม่ลบกิจกรรมของ job ที่ยังมีอยู่

หากเขียน journal ไม่ได้ ระบบบันทึก server error แต่ไม่ทำให้ไฟล์ที่ถูกต้อง rollback ดังนั้นควรติดตาม log `activity journal unavailable` ประวัติอาจไม่ครบในช่วง storage ผิดปกติ หน้าจอแจ้งเตือนเมื่ออ่าน journal/API ไม่สำเร็จ

## ทดสอบโดยไม่เสียค่า API

```powershell
cd backend
.venv/Scripts/python -m pytest tests/test_activity.py tests/test_mapping_workflow.py tests/test_jobs.py tests/test_retention_backup.py -q --no-cov
cd ../frontend
npm test
npm run build
```

ชุด stack แยก: `backend/.venv/Scripts/python deploy/repro/repro.py up --name activity --web-port 8083 --s3-port 19004 --console-port 19005` จากนั้น `deploy/repro/smoke.py --name activity` ตรวจ upload/review/edit/approve/download/restart/restore

`deploy/repro/activity_probe.py` ใช้ใน container mock-only เท่านั้น โดยรับ round ID ของไฟล์จำลองจาก smoke: รัน graph ทั้ง 3 หน้าที่ด้วย scripted model, เปิดไฟล์จริง, เรียก sandbox จริง และตรวจผลทั้งไฟล์ ไม่มีการอนุมัติ mapping หรือเรียกผู้ให้บริการโมเดล ผลนี้พิสูจน์การเชื่อมต่อและบันทึกกิจกรรม ไม่ใช่ benchmark ความแม่นยำของ DeepSeek

### ผลตรวจ 7 พฤศจิกายน 2026

- Backend: activity, retention/backup, mapping workflow และ job resilience รวม 60 tests ผ่าน; jobs + workflow อีกชุด 32 tests ผ่าน (มีส่วนซ้ำกัน ไม่นับรวมเป็น 92)
- Frontend: 30 tests ผ่าน, build ผ่าน; lint ไม่มี error แต่ยังมี warning เดิมในไฟล์อื่น; mypy ของ 5 โมดูลที่เกี่ยวข้องและ Ruff ผ่าน
- Repro `activity`: Excel 3 ชีต → 12 records → review → แก้ข้อมูลพร้อมกันสอง request (201/409) → approve → JSONL → restart → backup/restore ผ่าน
- SHA-256 ของไฟล์ดาวน์โหลดก่อนและหลัง restore ตรงกัน; schema ตรง model และตรวจจำนวน journal events หลัง restore เท่าก่อน backup
- Graph probe `job_01M6S8RWA5KZBNA8GK6ZWVTAX0`: ทั้ง 3 บทบาท, source inspection, full-file execution และ sandbox Python จริงผ่าน; sandbox status `ok`, exit code 0
- ตรวจ UI: กรอบกิจกรรมแสดงขณะ mock request ทำงาน, เปิดคู่คำสั่ง/ผล Python, เปิดไฟล์แล้วกลับงานเดิม, พักหน้าจอ, ธีมสว่าง/มืด และหน้าจอ 390px ไม่ล้นแนวนอน
- ค่า API ของการตรวจรอบนี้ 0 USD; ผลพิสูจน์ flow/observability เท่านั้น ไม่ใช่ผลวัดคุณภาพของโมเดลจริงหรือ load test 300 ไฟล์
