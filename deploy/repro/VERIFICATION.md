# ผลตรวจ prod-local/repro — 5 พฤศจิกายน 2026

ทดสอบบน Docker Desktop ของเครื่อง dev โดยใช้ project `forma-repro-local` แยกจาก `forma` ไม่มีการเรียก API โมเดลเสียเงิน และไม่ commit

## ผลตรวจ

| รายการ | ผล |
|---|---|
| เปิด stack และ migration ฐานใหม่ | ผ่าน; repro revision `b5d7f9012345` |
| `/ready` และ mock-only settings | ผ่าน |
| Excel 3 ชีต: สินค้า / สต็อกสองคลัง / โปรโมชั่น | อ่านครบ 3 ตาราง |
| ส่ง mapping/transform เข้า Redis + ARQ worker แบบ asynchronous | ผ่าน |
| ตรวจ mapping → อนุมัติ → transform → อนุมัติ dataset → export | ผ่าน; สถานะ `published` |
| ดาวน์โหลด JSONL จาก MinIO ผ่าน pre-signed URL | 12 แถว; SKU ครบ; A001 ราคา 107; BKK=3 และ CNX=1 |
| sandbox ผ่าน private Unix socket | Python `6 * 7` ได้ 42 |
| API และ worker ออกเครือข่ายภายนอก | เชื่อมต่อ TCP ภายนอกไม่ได้ (`ENETUNREACH`) |
| ปิด `stop` แล้วเปิด `up` | round เดิมยัง published และดาวน์โหลด 12 แถวได้เหมือนเดิม |
| `destroy --confirm local` | ผ่าน; ไม่เหลือ container, volume หรือ network ของ repro; dev ทั้ง 9 ตัว ID เดิม และจำนวนข้อมูล/revision เดิม |
| ตัวล็อก primary/backup keys + API settings/model discovery | ผ่านชุดทดสอบ; mock-only ไม่ถอดรหัส saved key |
| tests เฉพาะส่วนใหม่และ regression ของ config/LLM/model discovery | 36 ผ่าน; หลังปรับรูปแบบ test และเพิ่ม lock ให้ smoke รันส่วน safety ซ้ำ 15 ผ่าน |
| Ruff และ diff whitespace check | ผ่าน |
| ค่า API | `llm_calls` ใน repro 0 แถว; ยอดรวม $0 |

การแก้ระหว่างทดสอบ: Docker Desktop ไม่เปิด published ports ให้ internal-only network จึงให้ nginx เป็นจุดรับจาก host บน edge network เพียงตัวเดียว และ proxy ไป MinIO ภายใน คง API/worker/DB/storage บน internal network และ sandbox ไม่มี network ทดสอบดาวน์โหลดหลังเปลี่ยนแล้วผ่าน

## เปรียบเทียบ dev ก่อน–หลัง

ตรวจด้วย SQL read-only และ Docker IDs ไม่หยุด/recreate/update migration ของ dev:

| รายการ dev | ก่อน | หลังการทดสอบเปิด–ปิด |
|---|---:|---:|
| ลูกค้า | 236 | 236 |
| รอบนำเข้า | 401 | 401 |
| ไฟล์ | 393 | 393 |
| Mapping versions | 449 | 449 |
| Corrections | 124 | 124 |
| LLM calls | 667 | 667 |
| Migration revision | a4c6e8f01234 | a4c6e8f01234 |

container IDs ของ dev ทั้ง 9 ตัวเหมือนเดิม และ `/ready` ของ dev เป็น `ok` ทั้ง DB/storage/queue การเทียบนี้ยืนยันจำนวนและตัวตน runtime ที่ตรวจ ไม่ใช่การทำ checksum ข้อมูลทุกแถว

ตรวจซ้ำหลัง `destroy` แล้วค่าในตารางยังตรงทั้งหมด ตอนส่งงาน **repro ปิดและลบข้อมูลทดลองแล้ว** เพื่อคืน RAM ให้ dev เหลือ config, build images และผลตรวจในเครื่อง เปิดด้วย `repro.ps1 up` จะสร้างฐานทดลองว่างใหม่ ไม่มีข้อมูล synthetic จาก smoke ค้างในฐาน

## ขอบเขตผลทดสอบ

เป็นการยืนยันการแยก environment และ flow จริงด้วยโมเดลจำลอง ไม่ใช่คะแนนความแม่นยำของ DeepSeek, load test, OIDC/TLS test หรือหลักประกันว่าไฟล์ ERP ใหม่ทุกแบบจะ map ถูก ไม่ได้นำข้อมูล/ความจำจาก dev หรือ production เข้ามา
