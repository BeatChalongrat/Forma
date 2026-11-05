# เริ่ม production ใหม่และนำเข้าความรู้ที่เลือกไว้

แนวทางนี้ใช้ฐาน production ว่าง แล้วนำเข้ารายการที่ตรวจแล้วเป็นชุดที่มีเวอร์ชัน ตรวจล่วงหน้า นำเข้าซ้ำได้ และถอนการใช้ได้ เก็บ dev เดิมครบ ไม่ยกฐาน dev ทั้งก้อนแล้วค่อยลบข้อมูลจำลอง

ใช้หลัก environment isolation, versioned artifacts, approval และ controlled promotion ที่พบใน [MLflow registry workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow) และ [AWS model approval](https://docs.aws.amazon.com/sagemaker/latest/dg/model-registry-deploy.html) โดยปรับมาใช้กับ mapping/คำตอบ/หลักฐานของ FORMA ไม่ได้ติดตั้งบริการเหล่านั้นหรือย้ายโมเดลที่ฝึกใหม่

## 1. เตรียม server ครั้งแรก

ต้องมี Linux host, Docker Compose v2, DNS/TLS, OIDC และพื้นที่สำหรับ backup นอกเครื่อง ใช้ checkout ของ release ที่ตรวจแล้ว แยกจากเครื่อง dev เครื่องมือนี้ไม่สร้างเครื่องหรือบัญชี cloud ให้

จาก repo root **บน server**:

```bash
mkdir -p deploy/server/generated
cp deploy/server/server.env.example deploy/server/generated/server.env
chmod 600 deploy/server/generated/server.env
```

กรอกค่าจริงในไฟล์นั้น:

- `FORMA_SERVER_PROJECT`: ชื่อ project ใหม่ เช่น `forma-production` ใช้ชื่อนี้คงเดิมใน deploy ครั้งต่อไป ห้ามใช้ชื่อ project dev
- รหัสผ่าน PostgreSQL/MinIO ใหม่ และ `FORMA_SECRET_KEY` ใหม่ที่คงเดิมตลอดอายุฐาน production เก็บสำรองอย่างปลอดภัย รหัสผ่าน DB ควรใช้ตัวอักษร/ตัวเลขที่ยาวเพียงพอ เพราะถูกนำไปประกอบ connection URL
- issuer/audience/client ID ของ OIDC, public URL และ object-storage public URL ให้ตรงกับ TLS proxy และ redirect URI
- `FORMA_ENV_FILE` ต้องชี้ไฟล์ env server นี้ ห้ามชี้ `.env.production` ของ dev
- ค่าเริ่มต้นโมเดลเป็น `mock` เพื่อทดสอบโดยไม่เสียเงิน ตั้งผู้ให้บริการจริงภายหลังใน Settings

Compose แยก PostgreSQL, Redis, MinIO, local cache, backup และ sandbox socket เป็น named volumes ของ project ใหม่ ไม่มีการเชื่อมฐาน dev หรือคัดลอก secrets/คิวจาก dev ถ้ามีหลาย stack บน host เดียวต้องแยก host ports เพิ่มด้วย รวมถึง MinIO console 9001; คู่มือนี้ตั้งต้นสำหรับ server แยก

อ่าน [สูตร capacity](README.md) แล้วสร้างแผนจากสเปกจริง ตัวอย่าง 16 งาน:

```bash
python3 deploy/server/plan.py --cpus 24 --ram-gib 64 --max-jobs 16 \
  --disk-total-gib 1000 --disk-free-gib 900 --average-file-mib 5 \
  --files 300 --retained-batches 7 --out deploy/server/generated/capacity-v1
```

แทนค่าขนาดไฟล์/จำนวนรอบ/output multiplier/backup จากงานจริง หรือใช้ `--probe --data-path` บน filesystem ที่เก็บ volumes จริง ค่าเริ่มต้นยังไม่เกิน 8 งาน; 16/32 ต้องเลือกเอง ไม่ใช่ autoscaling

ใช้ command wrapper เดียวกันทุกคำสั่งเพื่อไม่ลืม project/env/override:

```bash
dc() {
  docker compose --env-file deploy/server/generated/server.env \
    -f docker-compose.prod.yml \
    -f deploy/server/generated/capacity-v1/compose.capacity.json \
    -f deploy/server/compose.isolated.yml "$@"
}
dc config --quiet
dc build
dc up -d
dc ps
```

คำสั่งเหล่านี้เป็นขั้นตอน deploy ในอนาคต ไม่ได้ถูกรันขณะเตรียมงานนี้ การ merge ต้องมี isolated override เป็นไฟล์สุดท้ายและห้ามส่ง `-p` เป็นชื่อ dev อย่าใช้ `--scale worker` ทับจำนวน replicas ของแผน Migration ทำงานก่อน API/worker; ตรวจ exit status ของ migrate และ health ของ sandbox ทุกตัว ตรวจ `/ready` ผ่าน port เว็บที่ตั้งไว้

เปิดระบบด้วยบัญชี admin ที่มีสิทธิ์ลูกค้าทั้งหมด ตรวจ AI settings และ effective runtime settings ก่อนเริ่มงาน ใน DB อาจมีค่าที่ override environment เมื่อ deploy ครั้งต่อ ๆ ไป ต้องตรวจ jobs/worker, shared concurrency, RPM และ socket pool ด้วย

## 2. เลือกข้อมูลจากต้นทาง

หลังอัปเดตแอปและ migration ที่ต้นทาง เปิด **ตั้งค่าระบบ → ย้ายความรู้** (`/settings?tab=releases`) การอัปเดตเพิ่มตาราง ไม่มีการลบไฟล์หรือความจำ dev เดิม

1. เปิดดู mapping และหลักฐานของรายการที่จะย้าย ตรวจผล JSON และคำตอบที่ยืนยันแล้ว
2. เลือกที่มา: **ข้อมูลจำลอง / ยังไม่ทราบ / ไฟล์ ERP จริงที่ใช้ทดลอง / ข้อมูลใช้งานจริง** พร้อมเหตุผล
3. อนุญาตเฉพาะข้อมูลจริงที่ mapping อนุมัติและ verification ผ่านครบแล้ว ต้องอนุญาตความรู้ต้นทางที่อ้างอิงก่อน รายการจำลองและไม่ทราบที่มาส่งออกไม่ได้
4. เลือก mapping/คำตอบที่จะย้าย ระบบเพิ่ม dependency ที่จำเป็นให้เอง การอนุญาต mapping ผ่าน UI รวมคำตอบที่ยืนยันแล้ว แต่การเลือกส่งออกคำตอบยังเลือกแยกได้
5. ดาวน์โหลด ZIP ซึ่งมีลูกค้า, schema เวอร์ชันที่ใช้, mapping, คำตอบที่เลือก, ที่มา/ผู้ตรวจ และไฟล์ต้นฉบับที่ใช้เป็นหลักฐาน

ไฟล์จริงไม่ได้รับอนุญาตอัตโนมัติเพียงเพราะเป็นไฟล์ ERP ต้องมีคนตรวจผลและระบุเหตุผล ไม่เดาจากชื่อไฟล์ว่าจริงหรือจำลอง หากคำตอบแบบ global ไม่มี mapping ต้นทางที่ผ่าน verification จะยังย้ายไม่ได้

ป้ายกำกับผูกกับ mapping/คำตอบและครอบคลุมหลักฐานของรอบนั้น หากรอบเดียวปนไฟล์จริงกับไฟล์จำลอง ให้ถือว่าชุดนั้นยังไม่พร้อมย้าย ต้องสร้างและตรวจ mapping จากหลักฐานจริงล้วนก่อน ไม่อนุญาตทั้งชุดเพราะมีไฟล์จริงเพียงบางไฟล์

แพ็กเกจ **มีข้อมูลในไฟล์ลูกค้าจริง** รวมทุกไฟล์ของรอบที่เป็นหลักฐาน ส่งผ่านช่องทางที่ได้รับอนุญาต ไม่บรรจุ chat, credentials/API key, งานใน queue, ผล JSON ที่เผยแพร่ทั้งฐาน, cache หรือ agent logs ทั้งหมด ข้อมูลใน mapping policy/verification ที่ใช้ตรวจความถูกต้องยังติดไปด้วย

เลือกได้ 1–100 รายการ มี dependencies รวมไม่เกิน 200; ZIP/ข้อมูลที่คลายแล้วไม่เกิน 256 MiB, manifest 8 MiB และ archive entries ไม่เกิน 500 ชุดใหญ่ให้แยกส่ง API รับแพ็กเกจหนักครั้งละหนึ่งรายการต่อ process; 429 ให้ลองใหม่ภายหลัง

## 3. ตรวจและนำเข้าที่ production

เปิดหน้า **ย้ายความรู้** ที่ระบบปลายทาง เลือก ZIP → **ตรวจรายการก่อนนำเข้า** หน้านี้แสดงลูกค้า/schema/mapping/คำตอบ/ไฟล์ และสิ่งที่จะสร้างหรือใช้ของเดิม ยังไม่เขียนฐานหรือคิว

- ตรวจ hash ของไฟล์และ manifest, โครงสร้าง mapping กับ schema, การอนุมัติ/verification ต้นทาง และ dependency ครบ ไม่เรียก LLM
- schema ชื่อ+เวอร์ชันเดียวกันแต่เนื้อหาต่างกันจะถูกบล็อก ต้องออก schema เวอร์ชันใหม่และตรวจ mapping ที่ผูกเวอร์ชันนั้น
- รหัสลูกค้าเดิมแต่ชื่อ/ERP ต่างกัน หรือ inactive จะถูกบล็อก ต้องจัดการตัวตนก่อน ไม่รวมลูกค้าให้อัตโนมัติ
- หน้าที่ตรวจแล้วมี `plan_digest` หากสถานะเกี่ยวข้องเปลี่ยนก่อนนำเข้าต้องตรวจใหม่
- ผู้อนุมัติปลายทางยืนยันการใช้แล้วจึงกดนำเข้า ผู้อนุมัติต้นทางและปลายทางมีบันทึก audit

SHA256 ใช้ตรวจความสมบูรณ์และระบุชุด **ไม่ใช่ลายเซ็นยืนยันผู้ส่ง** ต้องเชื่อถือแหล่งแพ็กเกจและตรวจหลักฐาน การนำเข้าไม่ใช่การรันทดสอบ semantic ของ ERP ใหม่: ต้อง smoke test ด้วยข้อมูลและเงื่อนไข production ก่อนปล่อยให้ auto-approve

ID ปลายทางสร้างแบบคงที่จากตัวตนฐานต้นทาง ไม่ทับ record เดิม นำเข้าแพ็กเกจเดิมซ้ำไม่สร้างซ้ำ แพ็กเกจใหม่ที่ใช้หลักฐานเดิมใช้ record เดิมได้เมื่อยังเข้ากันและยังอนุญาต หาก record เดิมเปลี่ยนหรือถูกถอน จะไม่คืนสิทธิ์ให้เงียบ ๆ

ลูกค้า/schema สร้างเฉพาะที่ขาด ไม่คัดลอก default target schema, customer policy overrides หรือกฎ auto-approve ของ dev ต้องเลือกค่าเหล่านี้ที่ production เอง รอบที่ย้ายมามีไว้เป็นหลักฐานและปิดการประมวลผล ไม่มีการ enqueue job หรือสร้างผลเผยแพร่

## 4. การค้นหาและถอนความรู้

Server ใช้ `FORMA_KNOWLEDGE_POLICY=strict`: ทุกครั้งที่เรียก format memory, กฎจากคำตอบ และ learning anchors จะตรวจสิทธิ์/เนื้อหา/dependencies ในฐาน Unknown/pending/synthetic/revoked หรือเนื้อหาที่เปลี่ยนหลังตรวจ ใช้เป็นความรู้ไม่ได้

เครื่อง dev เดิมใช้ `legacy` เพื่อคงพฤติกรรมข้อมูลเก่าที่ยังไม่ติดป้าย แต่รายการที่ติดป้ายว่าเป็นข้อมูลจำลอง/รอตรวจ/ถูกถอนแล้วจะถูกกันออกอยู่ดี การส่งออกบังคับ strict เสมอ การเปลี่ยน dev เป็น strict เป็นการตัดสินใจอีกขั้น ไม่ได้ทำให้ในงานนี้

Production ใหม่ต้องให้ผู้ดูแลอนุญาตความรู้จริงหลังตรวจด้วย ไม่ auto-promote ทุกคำตอบที่เพิ่งตอบ ความรู้ที่รับเข้ามาจะใช้ก็ต่อเมื่อผ่านเงื่อนไขลูกค้า/schema/layout/requirements เดิม ไม่มีการข้ามขอบเขตเพราะได้รับอนุมัติให้ย้าย

ระบบค้นหาความรู้นี้ใช้ SQL และตัวกรองสิทธิ์สด ไม่ใช่ vector index ที่ยกมาจาก dev จึงไม่ต้องคัดลอก embedding/cache การนำเข้า/ถอนเพิ่ม config revision และ refresh registry เพื่อให้กระบวนการอื่นโหลด schema ตามรอบ refresh ของระบบ

ถอนที่ **ชุดความรู้ที่นำเข้าแล้ว → ถอนการใช้ชุดนี้** โดยกรอกเหตุผล จะหยุดเรียกความรู้ชุดนั้นรวมถึงรายการที่อ้างอิงต่อ และรายงาน mapping ที่ควรตรวจ ไม่มีการลบไฟล์หรือย้อนแก้ JSON ที่เผยแพร่แล้ว ลูกค้า/schema/หลักฐานยังอยู่สำหรับตรวจย้อนหลัง

การถอนมีผลในการเรียกข้อมูลครั้งถัดไป ไม่สามารถเรียกคืน context ที่ส่งให้โมเดลในงานที่กำลังทำไปแล้วได้ ให้หยุดรับงาน/รอหรือยกเลิกงานที่เกี่ยวข้อง แล้วตรวจรายการผลกระทบก่อนเผยแพร่ หากแพ็กเกจ B ใช้ record จาก A การถอน A ยังคงบล็อก record นั้น ไม่ถือว่า B อนุญาตให้คืนสิทธิ์เอง

## 5. Deploy ครั้งถัดไป

1. ใช้ชื่อ Compose project, env, encryption key และ named volumes เดิมของ production ห้าม restore ฐาน dev และห้าม `down -v`
2. หยุดรับ uploads/รอคิวและงานที่กำลังทำจบ สำรอง production พร้อมไฟล์และตรวจการ restore บนฐานแยกก่อน migration ที่เสี่ยง สำรองนอก host ด้วย
3. Build release ใหม่ก่อนรับโหลด ไม่ให้ build แย่ง RAM ช่วงประมวลผลไฟล์ ใช้ wrapper `dc` เดิมและตรวจ config ทุกครั้ง
4. รัน `dc up -d --force-recreate migrate` แล้วตรวจ migration สำเร็จก่อน `dc up -d` เพื่อให้แอปใหม่เห็น schema ใหม่ ตรวจ `/ready`, auth, sandbox, queue และ flow จำลองจนดาวน์โหลด
5. ถ้าปรับ capacity สร้างแผน directory ใหม่ ตรวจทานแล้วเปลี่ยน path ใน wrapper ไม่เปลี่ยน project/volumes เมื่อ scale ลงตรวจ orphan sandbox ตอนคิวว่างและลบเฉพาะ container ที่เลิกใช้ด้วยการตรวจทาน ไม่ลบ volumes
6. นำเข้าความรู้เพิ่มเป็นแพ็กเกจใหม่ได้ ไม่ต้อง reset production และไม่ต้องคัดลอกดัชนี/แคช ทดสอบการใช้ซ้ำและการถามกลับบนชุดจำลองก่อนเปิด provider จริง

การอัปเดตแอปไม่ได้รับประกัน rollback ของ DB: เก็บ release ก่อนหน้าและ backup ที่ตรงกันไว้ แต่ไม่ restore ทับข้อมูลใหม่โดยไม่วางแผนกู้คืน

## CLI สำหรับผู้ดูแล server

รัน `forma knowledge --help` ภายใน container ที่มี environment ของฐานที่ตั้งใจใช้ รองรับ `list`, `classify`, `export`, `preview`, `import`, `revoke` ตัวอย่าง:

```bash
forma knowledge list
forma knowledge classify mapping:MAP_ID real_trial allowed admin 'ตรวจ ERP และ JSON แล้ว'
forma knowledge export /tmp/reviewed.zip mapping:MAP_ID --actor admin
forma knowledge preview /tmp/reviewed.zip
forma knowledge import /tmp/reviewed.zip PLAN_DIGEST admin
forma knowledge revoke RELEASE_DIGEST admin 'ถอนเพราะกฎต้นทางผิด'
```

CLI ไม่จำลองสิทธิ์ผู้ใช้เว็บ ผู้ที่เข้าถึง shell/DB อยู่แล้วเป็นผู้ดูแลระบบ ค่า actor ต้องบอกตัวตนจริง; API/หน้าเว็บตรวจ role admin และสิทธิ์ลูกค้าทั้งหมด ใช้หน้าเว็บหากต้องการยืนยันตัวตนจาก OIDC โดยตรง

## การตรวจที่ทำแล้ว

ทดสอบด้วย SQLite/ไฟล์ต้นฉบับจำลองและโมเดลจำลอง: ฐานปลายทางใหม่, preview ไม่เขียนข้อมูล, import/replay/revoke, แพ็กเกจทับซ้อน, ไม่คืนสิทธิ์เมื่อถอนต้นทาง, สถานะ/เนื้อหาที่เปลี่ยน, schema/customer conflict, manifest/หลักฐานไม่ครบ, dependency วน และสิทธิ์ admin พร้อมชุด regression ของ learning/decision memory/format memory/migration/config ทดสอบ few-shot examples ว่าไม่หยิบข้อมูลที่ยังไม่อนุญาต/ถูกถอน และรัน mapping บนไฟล์หลักฐานที่ย้ายแล้วเพื่อเทียบค่าธุรกิจใน JSON กับต้นทาง (lineage ยังคงระบุฐาน/ไฟล์ของตนเอง)

ทดสอบ capacity และ Compose merge ด้วย env จำลองครบ 2/4/8/16/32 งาน ไม่มี API เสียเงิน ไม่มีการสั่ง deploy หรือเปลี่ยนจำนวนงาน/ทรัพยากร dev การใช้ PostgreSQL advisory lock อยู่ในโค้ด แต่ยังไม่ได้ทดสอบ concurrent imports หลาย process บน PostgreSQL server จริง ต้องทำ smoke test ก่อนเปิด production
