# prod-local / reproduce bug แบบแยกจาก dev

ชุดนี้ใช้ topology แบบ production: nginx + API + PostgreSQL + Redis/ARQ worker + MinIO + sandbox และรัน migration ในฐานของตัวเอง แต่ใช้ **โมเดลจำลองเท่านั้น** เปิดสองงานพร้อมกันเพื่อทดสอบ flow ไม่ใช่ profile ทดสอบกำลังรองรับ server

## คำสั่งใช้ประจำ

เปิด PowerShell ที่ `C:\Users\Beat Beat\Desktop\Forma` (Docker Desktop ต้องทำงาน):

```powershell
# สร้างฐานใหม่ครั้งแรก / เปิดข้อมูลเดิมครั้งถัดไป / build โค้ดปัจจุบัน
.\deploy\repro\repro.ps1 up

# ดูสถานะ / ดู log เฉพาะ API และ worker ของชุดทดลอง
.\deploy\repro\repro.ps1 status
.\deploy\repro\repro.ps1 logs

# ปิดและคืน RAM แต่เก็บฐานข้อมูลและไฟล์ไว้ เปิดด้วย up เพื่อทำต่อ
.\deploy\repro\repro.ps1 stop

# ลบฐานข้อมูล ไฟล์ คิว cache และ socket เฉพาะ forma-repro-local
.\deploy\repro\repro.ps1 destroy --confirm local
```

หน้าเว็บ: **http://127.0.0.1:8081** — คนละ origin กับ dev ที่ localhost:80 จึงแยก localStorage ของหน้าเว็บด้วย ใช้ URL นี้ให้เหมือนกันทุกครั้ง

ดาวน์โหลดไฟล์ผ่านพอร์ต `19000`; MinIO console อยู่ `http://127.0.0.1:19001` ผู้ใช้ `repro` และรหัสสุ่มอยู่ใน config เฉพาะเครื่อง ไม่จำเป็นต้องเปิด console สำหรับ flow ปกติ

`destroy` เก็บ config และ build images ไว้ให้เปิดฐานว่างใหม่ได้เร็ว ไม่ลบ dev, Docker image อื่น หรือ build cache ทั้งเครื่อง **ไม่มีคำสั่ง prune** และไม่แก้ `.env.production` / `.wslconfig` / RAM ของ Docker

ถ้า PowerShell ของเครื่องบล็อก script ใช้ Python โดยตรง ไม่ต้องเปลี่ยนนโยบาย execution ทั้งเครื่อง:

```powershell
backend\.venv\Scripts\python deploy/repro/repro.py up
backend\.venv\Scripts\python deploy/repro/repro.py destroy --confirm local
```

## ทดสอบ flow ฟรีด้วยคำสั่งเดียว

หลัง `up`:

```powershell
backend\.venv\Scripts\python deploy/repro/smoke.py
```

สคริปต์ตรวจป้ายเจ้าของ + mock lock ทั้ง API/worker ก่อน จากนั้นสร้างลูกค้าจำลองหนึ่งราย อัปโหลด Excel 3 ชีต 12 สินค้า ส่งงานเข้า Redis worker ตรวจ mapping อนุมัติในฐานะผู้ทดสอบ แปลงข้อมูล อนุมัติผล และดาวน์โหลด JSONL ตรวจ SKU/ราคา/สต็อกสองคลัง ข้อมูลทดสอบอยู่ใน repro เท่านั้น ผลล่าสุดอยู่ `deploy/repro/generated/local/smoke-result.json` รันซ้ำจะเพิ่มลูกค้าจำลองรายใหม่

สคริปต์ยังตรวจ preview/แก้ข้อมูลจากสอง request พร้อมกัน (สำเร็จหนึ่งครั้ง อีกครั้งต้องได้ 409), อนุมัติและดาวน์โหลด จากนั้น restart เฉพาะ API/worker ของ repro และเทียบ checksum ไฟล์เดิม สุดท้าย backup พร้อมไฟล์และ restore ลง **database กับ bucket ใหม่ภายใน repro เท่านั้น** ตรวจข้อมูล 12 แถวและ checkpoint/attempt ledger ของ workflow ที่ใส่เป็นข้อมูลจำลอง ฐานและ bucket ทดลองเหล่านี้ถูกลบพร้อม instance เมื่อใช้ `destroy`

การอนุมัติในสคริปต์เป็นกรณีที่รู้คำตอบจาก fixture แล้ว ไม่ใช่หลักฐานว่า mock เข้าใจ ERP รูปแบบใหม่ได้ งานนี้ทดสอบระบบและการแยก environment; การตัดสินใจของ DeepSeek, latency, rate limit, OIDC/TLS และโหลดจริงต้องทดสอบแยก

## สิ่งที่แยกและตัวป้องกัน

| ส่วน | ชุดทดลอง |
|---|---|
| Compose project | `forma-repro-local` (ชื่อ dev/prod ใช้ไม่ได้) |
| PostgreSQL / MinIO / Redis / cache / socket | named volumes ใหม่ทั้งหมด มีป้ายเจ้าของเฉพาะ instance |
| API / worker / migration | env สร้างใหม่ ไม่โหลด `.env` หรือค่าคีย์จากเครื่อง |
| Images ของแอป | `forma-repro-local-api/web/sandbox:local` ไม่ทับ tag ของ dev |
| Network | app/DB/storage อยู่ internal; sandbox ไม่มี network; nginx เท่านั้นที่เชื่อมทางเข้า edge |
| Ports | bind เฉพาะ `127.0.0.1` ไม่มี DB/Redis port เปิดออก host |
| AI | `FORMA_LLM_MOCK_ONLY=true` ล็อก provider เป็น mock แม้ฐานมีคีย์หลัก/สำรองเก่า; ไม่ถอดรหัสคีย์; ปิดการเชื่อมต่อ/ดึงรายชื่อ/ทดสอบ AI จาก UI |
| ความรู้ | ฐานใหม่ + `FORMA_KNOWLEDGE_POLICY=strict`; ไม่ย้ายความจำ/ลูกค้า/แชต dev อัตโนมัติ |
| การลบ | ต้องระบุ `--confirm` ให้ตรงชื่อ ตรวจ config hash, ป้าย project+owner และปฏิเสธถ้ามี container อื่นใช้ volume/network ร่วม |
| คำสั่งซ้อนกัน | OS file lock ป้องกัน up/stop/destroy ของ instance เดียวกันทำงานชนกัน |

ไฟล์ที่สร้างมีรหัสสุ่มอยู่ที่ `deploy/repro/generated/<name>/` ถูก ignore ทั้ง Git และ backend Docker build context อย่าส่งโฟลเดอร์นี้เป็นหลักฐาน bug ให้ส่ง fixture ที่ปกปิดข้อมูลแล้ว + ขั้นตอน + log ที่ตรวจแล้วแทน

รหัส local auth เป็น dev auth โดยตั้งข้อยกเว้น production ชัดเจน เพื่อทดลองบน loopback เท่านั้น **ไม่ใช่ config สำหรับเปิดให้ผู้ใช้จริงบน server** server ใช้ชุด `deploy/server/` ที่มี auth/config จริง

## RAM และการใช้ร่วมกับ dev

กำหนด cap รวมช่วง startup 7.75 GiB (sandbox 3 GiB, worker 2 GiB, API 768 MiB และส่วนที่เหลือ) ปกติ idle ใช้น้อยกว่า cap มาก ก่อนเปิดจะตรวจ RAM ที่ Docker มองเห็น หัก usage ของ container อื่นในขณะนั้น และเผื่ออีก 1.5 GiB ถ้าไม่พอจะปฏิเสธโดยไม่หยุดหรือปรับ dev

นี่คือการตรวจจาก usage ณ เวลานั้น ไม่รับประกันหาก dev เริ่มงานหนักพร้อมกันภายหลัง ควรหยุด repro เมื่อไม่ใช้ หลีกเลี่ยง benchmark ทั้งสองชุดพร้อมกัน ไม่เปิด 16/32 workers บนเครื่อง dev ด้วยชุดนี้

## แยกเคส bug เพิ่ม

```powershell
.\deploy\repro\repro.ps1 up --name issue123 --web-port 8082 --s3-port 19002 --console-port 19003
backend\.venv\Scripts\python deploy/repro/smoke.py --name issue123
.\deploy\repro\repro.ps1 stop --name issue123
.\deploy\repro\repro.ps1 destroy --name issue123 --confirm issue123
```

port ใช้ตอนสร้าง instance ครั้งแรก; ครั้งถัดไปใช้ค่าที่บันทึกไว้ ไม่มีการเปลี่ยน project/volume ตาม shell env ค่า config ที่สร้างไม่ควรแก้มือ (คำสั่งจะปฏิเสธ) ถ้าต้องการพอร์ตอื่นให้สร้างชื่อใหม่ การเปลี่ยน template ในโค้ดภายหลังอาจต้องสร้าง instance ใหม่; เก็บเคสจำลองและผลที่จำเป็นก่อนลบชุดเก่า

## วิธี reproduce แล้วแก้ code

1. ใช้โค้ด revision เดียวกับ bug และสร้าง instance แยก
2. อัปโหลดเฉพาะ fixture/ข้อมูลที่ปกปิดแล้ว ตั้ง schema/กฎให้ตรงกรณีที่เกิดปัญหา ไม่ restore ฐาน prod/dev ทั้งก้อน
3. ทำขั้นตอนที่ทำให้เกิด bug บันทึก request ID, round/job ID และผลก่อนแก้
4. แก้โค้ดใน repo แล้ว `up` อีกครั้งเพื่อ build/recreate เฉพาะ repro (เก็บฐานเดิม) **หยุดรอให้งานที่ต้องเก็บจบก่อน rebuild**
5. รันทดสอบซ้ำ ตรวจผลที่คาดหวัง เก็บ fixture เป็น regression test
6. `stop` หากจะกลับมาทำต่อ หรือ `destroy --confirm ...` เมื่อต้องการลบข้อมูลทดลอง

หากต้องใช้ความรู้จริงที่อนุมัติแล้ว ใช้กระบวนการตรวจ/นำเข้า knowledge release แยกต่างหาก ชุดนี้ไม่ทำการย้ายให้อัตโนมัติ ข้อมูลและคำตอบจากโมเดลจำลองไม่ควรถูกส่งเสริมเป็นความรู้ production
