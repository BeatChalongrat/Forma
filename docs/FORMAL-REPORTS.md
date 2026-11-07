# รายงานทางการจากผู้ช่วย FORMA

ปรับปรุง 7 พฤศจิกายน 2026 — งานนี้ยังไม่ commit

## ก่อนและหลัง

| ส่วน | ก่อน | หลัง |
|---|---|---|
| ตัวเลข | มี `build_report` คำนวณจากข้อมูลจริง แต่เส้นทางสร้างไฟล์ทั่วไปให้โมเดลเขียนโค้ดเอง | เส้นทางรายงานทางการรับเฉพาะ evidence ของ `build_report` ในเทิร์นปัจจุบัน เก็บ snapshot และ checksum แล้วใช้ข้อมูลชุดเดียวในทุกไฟล์ |
| รูปแบบ | มี sandbox, ไฟล์แนบ, ผลงานและเวอร์ชันอยู่แล้ว | เพิ่มแม่แบบสรุปผู้บริหาร รายเดือน สไลด์ รายงานปัญหา และขั้นตอนการทำงาน พร้อม skills แบบ versioned allowlist |
| คุณภาพ | มีภาพตัวอย่างและจำนวนภาพที่ส่งให้โมเดล แต่ไม่ใช่ใบรับรองความถูกต้อง | อ่านตารางกลับมาตรวจทุกแถว ตรวจตัวเลขใน PDF ที่เรนเดอร์จริง สร้างภาพทุกหน้า ตรวจขอบหน้าและ checksum พร้อมใบผลตรวจ |
| การทำงาน | งานเขียนไฟล์ทั่วไปทำระหว่างตอบแชต | รายงานทางการเข้าคิว `arq:reports` มี worker และ sandbox แยก หยุด/ลองใหม่ได้ และกลับมาเปิดผลหลังปิดแชตได้ |
| การแก้ไข | มีเวอร์ชันของผลงานทั่วไป | รายงานที่แก้ไขผูกเป็นเวอร์ชันใหม่ในผลงานเดิม เปิดรายงานเก่าจากการ์ดงานได้ตรงเวอร์ชัน |
| ความเป็นส่วนตัว | ผลงานแยกตามเจ้าของแชต | งานรายงานและ snapshot ตรวจเจ้าของ/ขอบเขตสิทธิ์ ไม่แสดง payload ในหน้า log งานทั่วไป ลบแชตแล้วลบข้อมูล Job ของรายงานด้วย |

## วิธีใช้

1. เปิด **ผู้ช่วย → แม่แบบรายงาน** เลือกรูปแบบ Word (`.docx`), PowerPoint (`.pptx`) หรือ PDF
2. แม่แบบเติมข้อความลงช่องแชต ยังไม่ส่งเอง ระบุลูกค้า ช่วงเวลา และข้อมูลที่ต้องการ แล้วส่ง
3. ผู้ช่วยเลือกข้อมูลผ่านเครื่องมือเดิม โหลด skill ที่เกี่ยวข้อง และเรียก `create_document`
4. การ์ดงานรายงานแสดงสถานะ รอคิว / กำลังสร้างและตรวจ / พร้อมให้ตรวจทาน / ไม่สำเร็จ / หยุดแล้ว
5. เปิดไฟล์ดูตัวอย่างทุกหน้า เปิด **ข้อมูลอ้างอิงและผลตรวจ** เพื่อดูขอบเขต จำนวนแถว แหล่งข้อมูล และดาวน์โหลด snapshot/ผลตรวจ
6. แก้รายงานโดยเปิดผลงานเดิมแล้วขอแก้ ผู้ช่วยใช้ `revise_artifact_id` เพื่อรักษาเวอร์ชันต้นฉบับ

การลองใหม่ใช้ snapshot เดิม หากต้องการตัวเลขล่าสุดต้องขอรายงานใหม่ การบันทึกไฟล์ไม่ได้อนุมัติหรือแก้ไขข้อมูลธุรกิจ

## สิ่งที่เพิ่ม

- `backend/src/forma/reporting/catalog.json`: brand และแม่แบบ 5 แบบ
- `backend/src/forma/reporting/skills/*/SKILL.md`: formal-report, presentation, process-flow, quality-check
- `backend/src/forma/reporting/render.py`: renderer ที่ระบบควบคุม ไม่ใช้โค้ดจากโมเดลในเส้นทางทางการ
- `backend/src/forma/services/document_reports.py`: freeze, queue, ตรวจผล, เผยแพร่ไฟล์และเวอร์ชันใน transaction เดียว
- `backend/src/forma/api/document_routes.py`: รายการงาน รายละเอียด หยุดและลองใหม่ ภายใต้เจ้าของแชต
- `frontend/src/components/ReportTemplates.tsx` และ `DocumentJobs.tsx`: เลือกแม่แบบและติดตามงาน

ข้อมูลใน snapshot มี query, report, เวลา, timezone, template version, skill version/hash และ renderer hash การ deploy renderer ใหม่ไม่เปลี่ยนวิธีทำงานของคำขอเก่าเงียบ ๆ งานเก่าที่ยังไม่สร้างจะขอให้สร้างรายงานใหม่ หากเผยแพร่เสร็จแล้วจะคืนผลเดิมโดยไม่สร้างไฟล์ซ้ำ

Word และ PowerPoint มีตารางแก้ไขได้ PowerPoint ใช้กราฟ native และแผนผังขนาดไม่เกิน 8 จุดเป็นกล่อง/เส้นที่แก้ไขได้ แผนผังใหญ่เก็บรายละเอียดครบในตาราง แผนผังที่ผู้ช่วยเขียนระบุชัดว่าเป็นคำอธิบายกระบวนการ ไม่ใช่ log การทำงานจริง

## ความถูกต้องและขอบเขต

- จำนวนเงิน/ทศนิยมคงรูปแบบค่าที่ `build_report` คำนวณไว้ รวมถึง `0.10 + 0.20 = 0.30` ไม่ให้โมเดลส่งตัวเลขมาเขียนทับ
- ค่าที่ไม่ทราบไม่กลายเป็น 0 และข้อมูลบางส่วนมีป้ายกำกับ
- ค่าขนาดใหญ่เกินขอบเขตกราฟจะเก็บในตารางโดยไม่แปลงเป็น float จนเสียความละเอียด
- กราฟค่าติดลบใช้แกนศูนย์ร่วม ไม่วาดแท่งด้วยเซลล์ตาราง ตรวจความยาวแท่งตรงกับค่า
- ตารางยาวแบ่งหน้า/สไลด์ ไม่ย่อข้อความจนอ่านไม่ได้และไม่ตัดแถวทิ้งเงียบ ๆ
- รายงานรายเดือนต้องมีวันที่เริ่ม–สิ้นสุดจาก query ที่รองรับช่วงเวลา
- จำกัด 6 แหล่งข้อมูล, 500 แถว/25 คอลัมน์ต่อแหล่ง, snapshot 1 MB, ผลแต่ละรูปแบบไม่เกิน 60 หน้า และข้อจำกัด output ของ sandbox ถ้าเกินให้ลดขอบเขต ไม่ส่งไฟล์ที่ขาดข้อมูล
- `.doc` และ `.ppt` แบบเก่าไม่ได้เพิ่ม ใช้ `.docx` และ `.pptx`

สถานะผ่านตรวจอัตโนมัติ **ไม่เท่ากับคนตรวจความหมายแล้ว** ใบผลตรวจระบุ `visual_review=human_required` และ `business_review=human_required` การตรวจขอบหน้าไม่ตรวจจับข้อความซ้อนกันได้ทุกแบบ และ Microsoft Office อาจแบ่งหน้าต่างจาก LibreOffice

แม่แบบเป็นโครงรายงานมาตรฐานที่เน้นข้อมูลตรวจสอบได้ ยังไม่ใช่โปรแกรมออกแบบแบบ Canva หรือเครื่องสร้างบทวิเคราะห์อิสระทุกชนิด เนื้อหานอกแม่แบบยังใช้เส้นทาง sandbox เดิมได้ แต่ไม่ได้รับประกันการตรวจตัวเลขแบบเดียวกับเส้นทางทางการ การถามกลับและการเลือกข้อมูลของโมเดลจริงยังต้องประเมินเมื่ออนุญาตให้ใช้ API

## คิวและ deploy

Compose เพิ่ม `report-worker` และ `report-sandbox` พร้อม socket volume แยกจาก mapping จำนวนงาน mapping เดิมไม่เปลี่ยน

| ค่า | เริ่มต้น |
|---|---|
| `FORMA_REPORT_WORKERS` | 1 |
| `FORMA_REPORT_QUEUE_MAX` | 20 |
| `FORMA_REPORT_TIMEOUT_S` | 240 วินาที |
| `FORMA_REPORT_SANDBOX_SOCKET` | `/run/report-sandbox/runner.sock` |
| Worker command | `forma worker --queue reports` |

งานใช้ Job/outbox/lease/recovery เดิม ผลงานเผยแพร่เมื่อ lease ยังใช้ได้และ Job ไม่ถูกหยุด การหยุดระหว่างเรนเดอร์ป้องกันการเผยแพร่ไฟล์ แต่ subprocess ที่ทำอยู่สามารถใช้เวลาจนจบรอบหรือ timeout ก่อนคืนทรัพยากรได้

Capacity planner รวม report worker 0.75 GiB + sandbox 3 GiB และ CPU รวม 2.5 เพิ่มแล้ว ตัวอย่าง 16 งานปรับสมมติฐานเป็น 32 vCPU / 80 GiB (ชื่อ directory ตัวอย่างเดิมยังเป็น `server-64g-16jobs`) ชุด repro ใหม่มีคิวรายงานและ sandbox แยกเช่นกัน ชุด repro เก่ายังคงตรวจ ownership และลบด้วยคำสั่งเดิมได้

ก่อน deploy ตรวจ Compose ด้วย env ของเป้าหมาย และวางแผน restart หลังงานเดิมว่าง ไม่ควร restart worker ที่ผูก API จริงเพื่อทำการทดสอบฟรี งานนี้ทดสอบด้วยชุดแยก ไม่มีการเปลี่ยน key/model หรือข้อมูล dev

## การทดสอบที่ทำ

- ชุดรายงาน/ผลงาน/sandbox/เครื่องมือรายงาน: เพิ่ม tests สำหรับ snapshot/output ที่ถูกแก้และช่วงวันที่/การแยก snapshot รวม 71 tests (2 tests ของ sandbox ต้องใช้ POSIX; 8 renderer cases รันแยกใน Docker)
- ชุด queue/recovery/resilience/conversations/retention/backup: ผ่าน 68 tests
- Frontend tests: ผ่าน 30 tests; build ผ่าน; lint ไม่มี error แต่มี warnings เดิม
- Server capacity + Compose: ผ่าน 8 tests
- Renderer จริงใน Docker ปิด network: ผ่าน 9 กรณี รวม full tool flow ด้วยโมเดล scripted mock และกรณีภาษาไทย–อังกฤษ ตารางยาว ข้อมูลว่าง ข้อมูลบางส่วน ค่าติดลบ ตัวเลขใหญ่ และ process flow
- ตรวจภาพด้วยตาพบและแก้แท่งกราฟขนาดไม่ตรงค่า และฟอนต์กราฟที่ไม่มี glyph ตัวเลข/อังกฤษ แล้วรันทดสอบ renderer ใหม่ครบ
- ทดสอบคิว PostgreSQL/Redis/worker/sandbox จริงใน project `forma-report-probe` ที่ล็อก mock-only ด้วย `deploy/repro/report_probe.py` แยกจาก dev ผ่านการดาวน์โหลดทั้ง 3 รูปแบบ และ restart API/worker แล้วยังเปิดรายงานเดิมได้ ไม่มีเวอร์ชันซ้ำ
- พบและแก้ deadlock ตอนเตรียม checkpoint PostgreSQL พร้อมกัน: เปลี่ยนเป็น try-lock แบบ autocommit และให้ worker รายงานข้ามขั้นเตรียม mapping ทดสอบ schema ใหม่พร้อมกัน 8 connections ผ่าน
- Regression รายงาน + mapping workflow + repro: ผ่าน 46 tests; mypy ผ่านทั้ง 163 source files

ภาพและไฟล์ตัวอย่างจาก renderer อยู่ใน `data/bench_reports/verified/` ซึ่งไม่ติด Git ตัวเลขทุกไฟล์ตัวอย่างเป็นข้อมูลสมมติ ห้ามนำไปใช้เป็นผลธุรกิจ

รัน regression โดยไม่เรียก API:

```powershell
# จาก backend
.venv/Scripts/python -m pytest tests/test_document_reports.py tests/test_artifacts.py tests/test_code_sandbox.py tests/test_assistant_reports.py --no-cov -ra

# ทดสอบ renderer จริง ต้องมี forma-sandbox:latest
$env:FORMA_TEST_REPORT_DOCKER='1'
$env:FORMA_TEST_REPORT_OUTPUT='C:/Users/Beat Beat/Desktop/Forma/data/bench_reports/verified'
.venv/Scripts/python -m pytest tests/test_document_reports.py::test_end_to_end_snapshot_publish_and_access tests/test_document_reports.py::test_real_formal_templates --no-cov -ra
```

ค่า API ในการทดสอบรอบนี้ **0** ไม่ได้ใช้ DeepSeek หรือผู้ให้บริการแบบเสียเงิน และยังไม่ได้ commit
