# Pilot: ทดสอบกับคนตรวจ ก่อนเริ่มใช้ไฟล์จริง

ชุดนี้เป็น **staging บนเครื่อง local** แยกจาก dev ใช้ topology nginx / PostgreSQL / Redis / worker / MinIO / sandbox
ไม่ใช่ staging ที่เปิดออกอินเทอร์เน็ต ยังไม่ได้ตั้ง host, TLS หรือ OIDC ขององค์กร

## เปิดและหยุด

รันจาก repo root:

```powershell
backend\.venv\Scripts\python deploy/repro/repro.py up --name pilot-nov14 --pilot --capacity ci --web-port 8086 --s3-port 19010 --console-port 19011
backend\.venv\Scripts\python deploy/repro/repro.py status --name pilot-nov14
backend\.venv\Scripts\python deploy/repro/repro.py stop --name pilot-nov14
```

เว็บ: http://127.0.0.1:8086 ใช้ URL นี้เสมอเพื่อแยก browser storage จาก dev
`up` เปิดข้อมูลเดิมและ build ใหม่; `stop` หยุดเฉพาะชุดนี้โดยเก็บข้อมูลไว้
ไม่มีการอ่านคีย์โมเดลหรือฐาน dev ไม่มีการย้ายความรู้เดิม ไม่ต้องแก้ `.env` ของ dev

API/worker ล็อก `FORMA_LLM_MOCK_ONLY=true`, `FORMA_PILOT_MODE=true`, ความรู้ strict และปิด retention scheduler
ส่งออกอัตโนมัติไม่ได้ แม้ส่ง request เปิด auto_publish เอง คนต้องตรวจและอนุมัติ dataset **เวอร์ชันนั้น** ก่อน export
หากแก้ข้อมูลแล้วได้เวอร์ชันใหม่ต้องอนุมัติใหม่ ไม่มีการใช้ approval ของเวอร์ชันเก่าแทน
การตอบคำถามหรือบันทึกเหตุผลไม่ได้เท่ากับอนุมัติผลลัพธ์

ใช้ 2 งานพร้อมกันเพื่อทดสอบ flow ไม่ใช่ benchmark 300 ไฟล์ จำกัดหน่วยความจำรวม container 5.5 GiB
ก่อนเริ่มเผื่อ RAM 1.5 GiB และ usage ปัจจุบันของ container อื่น ไม่เปลี่ยน RAM ของ Docker หรือหยุดโปรแกรมอื่น
ยังไม่เปิด report-worker/report-sandbox ใน local pilot นี้เพื่อเหลือ RAM; ไม่ใช้ชุดนี้วัดการสร้าง PPT/PDF

## ลองด้วยโมเดลจำลอง

```powershell
# ตอบคำถาม เก็บร่าง/ชนกันสอง request ยืนยัน สร้างข้อมูล อนุมัติ และดาวน์โหลด
backend\.venv\Scripts\python deploy/repro/review_smoke.py --name pilot-nov14
# นำเข้า/แก้ข้อมูลพร้อมกัน/ส่งออก/restart/backup/restore ในฐานและ bucket ทดลองแยก
backend\.venv\Scripts\python deploy/repro/smoke.py --name pilot-nov14
# เตรียมคำถามที่ยังไม่ตอบสำหรับลองหน้าเว็บ (แสดงลิงก์ในผลลัพธ์)
backend\.venv\Scripts\python deploy/repro/review_smoke.py --name pilot-nov14 --prepare-only
# หลัง prepare-only: ทดสอบหน้าเว็บจริง (ใช้ fixture นี้จนจบ; เตรียมใหม่อีกครั้งถ้าจะลองเอง)
node frontend/tests/pilot.browser.cjs
```

สคริปต์สร้างลูกค้าจำลองใหม่ทุกครั้ง ไม่ลบงานเก่า
ไฟล์ตัวอย่างมี 3 ชีต 12 สินค้า; คำถาม VAT ตั้งขึ้นโดยเจตนาแม้หัวคอลัมน์มีหลักฐาน ส่วนจำนวนต่อแพ็กไม่มีข้อมูล
นี่ใช้ทดสอบการแยกสาเหตุและ flow ไม่ใช่หลักฐานว่าโมเดลจริงถามผิด/ถูกกี่เปอร์เซ็นต์

## เก็บเหตุผลโดยคนตรวจ

เปิด **คำถามและปัญหา → เปิดไฟล์ → คำถามแต่ละข้อ** จะมี “บันทึกการทดลอง: ทำไมข้อนี้ต้องถามคน?”

| ประเภท | ใช้เมื่อ |
|---|---|
| ยังไม่ได้ตรวจสาเหตุ | ยังไม่รู้ ห้ามตีเป็นข้อมูลขาด |
| ไฟล์ไม่มีข้อมูลที่จำเป็น | ตรวจแหล่งที่เกี่ยวข้องแล้ว ต้องถามเจ้าของข้อมูล พร้อมระบุว่าขาดอะไร |
| มีหลักฐานตอบได้ในไฟล์ แต่ระบบยังถาม | ระบุไฟล์ ชีต เซลล์/คอลัมน์ และหลักฐานที่ใช้ตอบได้ |
| ข้อมูลในไฟล์ขัดกัน | มีหลายข้อเท็จจริงที่ขัดกัน ต้องให้คนตัดสินใจ ไม่ใช่คำถามเกินจำเป็นโดยอัตโนมัติ |

บันทึกแยกจากคำตอบและความรู้ ไม่ใช้สอนโมเดล ไม่เพิ่ม mapping rule อัตโนมัติ
แก้การจัดประเภทได้โดยบันทึกใหม่ ประวัติเก่าคงอยู่ พร้อมผู้บันทึก เวลา mapping version คำถาม และหลักฐาน
สิทธิ์ reviewer และขอบเขตลูกค้ายังใช้กับการอ่าน/เขียน/ส่งออกรายงาน
ผลที่สร้างใน mock lock มีป้าย synthetic ไม่ใช้ปนกับหลักฐานประเมินโมเดลจริง

```powershell
backend\.venv\Scripts\python deploy/repro/pilot_report.py --name pilot-nov14
```

รายงาน JSON และ CSV อยู่ `deploy/repro/generated/pilot-nov14/` (ไม่เข้า Git)
เก็บทุกหน้าของข้อมูล รวมคำถามที่ยังไม่จัดประเภทและประวัติการแก้เหตุผล
หน่วยนับคือ **คำถามต่อ mapping version** รวมเวอร์ชันแก้ไข จึงไม่ใช่จำนวนไฟล์หรือคะแนนความแม่นยำ AI
ควรดูคำถามซ้ำข้ามเวอร์ชันพร้อม round_id; ไม่หารเฉพาะคำถามที่ถูกจัดประเภทแล้วแล้วอ้างว่าเป็น accuracy ทั้งระบบ

## เมื่อพร้อมรับไฟล์จริง

1. กำหนด staging server/URL, TLS, OIDC และผู้มีสิทธิ์ importer/reviewer/approver ก่อนเปิดให้ทีม
2. ใช้ `deploy/server/` แยก project, database, Redis, bucket, secrets และ backups ของ staging จาก dev/production
3. คง `FORMA_PILOT_MODE=true` ใน API และ worker ทุกตัว ไม่ให้ปรับจากหน้า UI
4. ใช้ฐาน staging ใหม่สำหรับไฟล์จริง อย่าย้ายคำตอบ synthetic ของชุดนี้ไปเป็นความรู้
5. ยืนยันสิทธิ์ใช้ไฟล์/ข้อมูลและ JSON Schema ปลายทาง; เริ่มทีละไฟล์ แล้วค่อยเพิ่มชุดเล็ก
6. เปลี่ยนจาก mock เป็น provider จริงเฉพาะ staging server เมื่ออนุญาตงบแล้ว ชุด local นี้ยังล็อกไม่ให้เรียก API เสียเงิน
7. ผู้ตรวจดูข้อมูลต้นทาง ผลลัพธ์ และบันทึกสาเหตุคำถาม ก่อนอนุมัติและส่งออกไปทดสอบกับระบบปลายทาง
8. เก็บผลจริงแยกจาก synthetic: field correctness, คำถามที่จำเป็น/หลักฐานที่พลาด, เวลาคนตรวจ, เวลาประมวลผล, ค่าใช้จ่าย และเหตุขัดข้อง

ก่อน production ต้องมีผลยืนยันกับไฟล์จริงและการรับ JSON ของปลายทาง ไม่ใช้ smoke test นี้แทนหลักฐานดังกล่าว
