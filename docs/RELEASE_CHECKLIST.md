# Release qualification and deployment handoff

## ก่อนเปิดใช้รุ่นใหม่

1. ใช้ commit ที่จะปล่อยจริงจาก checkout สะอาด ห้ามรวมผลจากคนละ commit หรือเอาผลเก่ามาแทนส่วนที่ไม่ได้รัน
2. เปิด GitHub Actions → **release-qualification** บน branch/tag ของรุ่นนั้น เลือก runtime runner ที่มี Docker และ RAM ว่างอย่างน้อย 10 GiB ค่า `ubuntu-latest` ไม่รับประกันว่ามี RAM พอ; ถ้า preflight ไม่ผ่านให้ใช้ runner ที่เหมาะสม ห้ามข้าม runtime เพื่อให้ gate เขียว
3. Workflow เรียก CI (backend/PostgreSQL/MinIO, frontend, browser, renderer, critical flows) และ runtime แยก environment โดยใช้โมเดลจำลองเท่านั้น Runtime ตรวจ upload → review → แก้ข้อมูล → approve → download → restart → backup/restore และ 300 งาน
4. ต้องผ่าน **release-gate** จึงได้ artifact `qualified-release-<SHA>` ภายในมี `release-manifest.json`: commit, run/attempt, image IDs ของ build และ SHA-256 ของหลักฐาน ผลไม่ครบ, suite ล้ม/ข้าม, working tree เปลี่ยน, คนละ run หรือคนละ commit จะไม่ผ่าน
5. เก็บ manifest กับหลักฐานที่เกี่ยวข้องในทะเบียนรุ่นภายในก่อน GitHub artifacts หมดอายุ อ้าง run URL และผู้อนุมัติ ห้ามเปลี่ยนชื่อ image tag แล้วถือว่าเป็น image เดิม

## สิ่งที่ gate นี้ยืนยันและยังไม่ยืนยัน

- ยืนยันกลไกด้วยข้อมูลจำลอง ไม่ใช่คะแนน DeepSeek บน ERP ใหม่ ไม่เรียก API เสียเงิน
- Image IDs ใน manifest เป็น build จาก CI ส่วน runtime สร้าง isolated repro images จาก source revision เดียวกัน ยังไม่ใช่ระบบ promote image จาก registry อัตโนมัติ และยังไม่มี production deployment workflow
- ก่อน deploy ผู้รับผิดชอบต้องตรวจว่า source SHA และ image ที่จะใช้ตรงกับรุ่นที่ผ่าน หาก rebuild/dependencies เปลี่ยนให้ตรวจ candidate ใหม่นั้นอีกครั้ง ไม่ใช้ manifest เก่าอ้างว่า image ใหม่ผ่านแล้ว
- สิทธิ์/การแยกข้อมูลทดสอบใน CI ไม่แทนการตรวจ OIDC ขององค์กรจริง, TLS, DNS, secret rotation และสิทธิ์ storage บน server จริง
- Runtime ทดสอบบนทรัพยากรที่กำหนด ไม่รับรองเวลา/RAM ของ 300 Excel ขนาดใหญ่หรือ DeepSeek จริง
- Workflow นี้ไม่ deploy, ไม่ย้ายความรู้ และไม่แตะฐาน dev/prod การเรียกคำสั่ง deploy นอก workflow ยังต้องมีผู้รับผิดชอบตรวจ gate; ไม่ใช่ตัวล็อกทุกคำสั่ง Docker

## Checklist บน server (ทำกับ staging ก่อน)

- เลือก profile จากทรัพยากรจริงตาม `deploy/server/README.md`; เว้น RAM/disk ให้ระบบอื่นและ backup
- แยก DB, Redis, storage และ encryption key; ใช้ OIDC/TLS จริง; ทดสอบผู้ใช้ไม่มีสิทธิ์/ขอบเขตลูกค้ากับบัญชี staging
- สำรองฐานและ objects ก่อน migration; ทดลอง restore ไปฐาน/bucket ใหม่และตรวจค่าดาวน์โหลด ห้าม restore ทับฐาน dev
- ใช้ `FORMA_KNOWLEDGE_POLICY=strict` และนำเข้าเฉพาะความรู้ที่อนุมัติ ไม่ยกฐานทดสอบไป production
- หลัง migration ตรวจ `/ready`, worker heartbeat, งานค้าง/failed, storage และเส้นทางดาวน์โหลด
- ส่งไฟล์ smoke ที่ระบุว่าเป็นข้อมูลทดสอบ ตรวจคำตอบ/preview/approval/download ทุกขั้น และไม่อนุญาตให้ข้อมูลนี้เป็นความรู้ production
- ตรวจ monitoring receiver, backup นอกเครื่อง, ผู้รับผิดชอบเหตุขัดข้อง และขั้นย้อนกลับตาม `docs/RUNBOOK.md`

## อ่านผลให้ถูก

ผลทดสอบ local ที่ยังมีไฟล์ไม่ commit ใช้ประเมินงานระหว่างพัฒนาได้ แต่ release gate ปฏิเสธ `dirty=true` โดยตั้งใจ
ต้อง commit รุ่นที่อนุมัติแล้วจึงรัน qualification บน GitHub อีกครั้ง ไม่แก้ metadata เพื่อให้ผ่าน
ดู `docs/TESTING_STRATEGY.md` สำหรับคำสั่งรายชุดและข้อจำกัดของ mock evaluation
