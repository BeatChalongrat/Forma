# FORMA — ตรวจความพร้อมทั้งระบบก่อนเผยแพร่

วันที่ตรวจ: 5 พฤศจิกายน 2026 · repository: `C:\Users\Beat Beat\Desktop\Forma`

ฐานโค้ด: local `main` ที่ `1e5a559eb28864c05491282d0731d2ae3cb0c599` รวมงานที่ยังไม่ได้ commit ณ วันที่ตรวจ

## ข้อสรุป

**ยังไม่ผ่านสำหรับ commit/push เป็นรุ่นพร้อมใช้งานจริง** พบปัญหาความถูกต้องของข้อมูลที่ทำซ้ำได้ 5 ข้อ และ quality gate บางรายการยังไม่ผ่าน จึงไม่ได้ commit, merge หรือ push ในรอบนี้

คะแนนความพร้อมทางวิศวกรรมโดยรวม **69/100** (คำนวณถ่วงน้ำหนักได้ 69.19 แล้วปัดเป็นจำนวนเต็ม) ระบบมีพื้นฐานที่ดีและมี flow ใช้งานจริงจำนวนมาก แต่ยังมีช่องว่างเรื่อง transaction, การแก้ข้อมูลหลายครั้ง, การยกเลิกงาน และการกู้คืนข้อมูล คะแนนนี้เป็นการประเมินจากหลักฐานและดุลยพินิจ ไม่ใช่เปอร์เซ็นต์ความแม่นยำของโมเดล ไม่ใช่ SLA และไม่ใช่ผลแข่งกับ ChatGPT/Claude

ไม่จำเป็นต้องรื้อเป็น microservices ทั้งหมดหรือเพิ่มโมเดลเพื่อแก้ปัญหาที่พบก่อน ปัญหาสำคัญที่สุดอยู่ที่การบังคับกฎความถูกต้องของข้อมูลให้เหมือนกันในทุกทางเข้า

## ขอบเขตและวิธีตรวจ

สำรวจโครงสร้างไฟล์ ตำแหน่งฟังก์ชัน route/dependency การตั้งค่า migration เอกสารและผลทดสอบ อ่าน implementation ตามเส้นทางธุรกิจและส่วนที่มีความเสี่ยง แล้วทดสอบกับฐานและ storage ชั่วคราวที่แยกจาก dev ใช้โมเดลจำลองเท่านั้น ไม่มีการเรียก API โมเดลที่เสียเงิน

| พื้นที่ที่สำรวจ | ไฟล์ข้อความที่นับ | บรรทัด |
|---|---:|---:|
| Backend application | 148 | 39,763 |
| Frontend application | 66 | 7,781 |
| Backend tests | 86 | 19,565 |
| Database migrations | 19 | 1,158 |
| Documentation | 98 | 7,507 |
| Deployment ที่อยู่ในชุดสแกน | 13 | 1,879 |
| ชุดจำลองและผลทดสอบย้อนหลัง | 1,571 | 4,249,960 |

มีไฟล์ tracked 2,512 ไฟล์ และ API route declarations 153 รายการ รายละเอียดอยู่ใน `inventory.json`, `scope.json`, `routes.json` ผล benchmark JSON จำนวนมากตรวจผ่านโครงสร้างและตัวชี้วัด ไม่ได้อ้างว่าอ่านทุกบรรทัดด้วยสายตาหรือพิสูจน์ว่าไม่มีบั๊กเหลืออยู่

เปิด UI จริงแบบอ่านอย่างเดียว: หน้าหลัก ประวัติไฟล์ อัปโหลดแบบแยกไฟล์/รวมไฟล์ รายการคำถาม หน้าตรวจไฟล์ และการตั้งค่า ไม่ส่งข้อความให้ผู้ช่วย ไม่อนุมัติหรือแก้ข้อมูลจริง UI ที่กำลังรันเป็น image เดิม ส่วน build/tests ตรวจ working tree ปัจจุบัน จึงแยกหลักฐานสองส่วนนี้ออกจากกัน

ไม่ได้ทำ penetration test เต็มรูปแบบ ไม่ได้ทดสอบ hardware production ไม่ได้ทดสอบโมเดลจริงรอบใหม่ และยังไม่มีไฟล์ ERP จริงพร้อมผลที่ตรวจยืนยันแล้วสำหรับ blind holdout จึงยังรับรองความแม่นยำกับ unknown format ใน production ไม่ได้

## ปัญหาที่ต้องแก้ก่อนปล่อย — ยืนยันด้วยการทำซ้ำ

หลักฐานรวม: `probe-results.json` และสคริปต์ `reproduce.py` ใช้ private SQLite/storage และ mock model สร้าง workbook และเรียก API จริงใน TestClient ไม่แตะฐาน dev สคริปต์บันทึกอาการที่พบ ไม่ใช่ regression test ที่คาดหวังพฤติกรรมถูกต้องแล้ว

### F1 — บันทึกฐานข้อมูลไม่สำเร็จ แต่ API ตอบว่าสำเร็จ [P1]

- จุดตรวจ: `backend/src/forma/db/session.py:121` (`commit_request_sessions`) และ middleware ใน `api/app.py`
- สิ่งที่เกิด: exception ระหว่าง commit ถูกจับแล้ว rollback แต่ไม่ส่งต่อความผิดพลาด การตอบกลับเดิมยังถูกส่งให้ผู้ใช้
- ทดลองบังคับ commit ล้มเหลวในการสร้างลูกค้า: HTTP **201** แต่ตรวจฐานแล้ว **ไม่มี record**
- ผลกระทบ: ผู้ใช้เชื่อว่าบันทึกแล้ว ทั้งที่ข้อมูลไม่อยู่จริง ปัญหานี้อยู่ที่ส่วนกลาง ไม่จำกัดเฉพาะหน้าเพิ่มลูกค้า
- วิธีแก้: จบ transaction ก่อนส่ง success response; ให้ commit failure เดินผ่าน error handling; rollback และบันทึกเหตุการณ์ให้ครบ รวมถึงจัดการ object ที่เขียนก่อน commit ไม่สำเร็จ
- เกณฑ์ผ่าน: injected commit failure ต้องไม่ตอบ 2xx, ต้องไม่เกิดข้อมูลครึ่งชุด และ retry ต้องไม่สร้างซ้ำโดยไม่ตั้งใจ

### F2 — แก้ dataset จากหน้าเก่าซ้ำได้ ทำให้เลขเวอร์ชันชนและผลแก้ไม่รวมกัน [P1]

- จุดตรวจ: `backend/src/forma/services/datasets.py:517` (`apply_changes`), `backend/src/forma/db/models.py:249`
- สิ่งที่เกิด: เปิด dataset v1 สองหน้า แก้สินค้า A001 จากหน้าแรก แล้วแก้ A002 จากอีกหน้าที่ยังเป็น v1 ระบบสร้าง **v2 สองรายการ** ซึ่งมี parent เดียวกัน ผลลัพธ์ของการแก้ครั้งที่สองไม่รวมการแก้ A001
- ประวัติครั้งแรกยังมีอยู่ ไม่ใช่การลบทิ้ง แต่เกิดสาขาและเลขเวอร์ชันซ้ำโดยไม่แจ้งผู้ใช้
- วิธีแก้: ส่ง expected base/head ID, ตรวจและ lock/CAS ก่อนเขียน, เพิ่ม unique constraint ของเลขเวอร์ชันภายในรอบตาม semantics ที่เลือก; ถ้าอนุญาต branch ต้องแสดงและเลือก merge อย่างชัดเจน
- เกณฑ์ผ่าน: สอง request ที่ใช้ base เก่าต้องเกิด conflict ที่อธิบายได้ หรือ merge อย่างตั้งใจ ต้องไม่มี v2 ซ้ำและไม่มีการแก้ที่หายจากผลลัพธ์โดยเงียบ ๆ

### F3 — กฎแก้ไขสำหรับรอบถัดไปกระทบข้อมูลมากกว่า preview [P1]

- จุดตรวจ: `backend/src/forma/services/datasets.py:517`, `:607` (`apply_persistent_overrides`)
- สิ่งที่เกิด: `set_field` เลือก identities A001 และ A005 พร้อมเงื่อนไข `status = active`; preview แก้ **1 แถว** แต่ตอนบันทึก persistent override ราย identity ไม่เก็บเงื่อนไขเดิมครบ
- ทดสอบใช้กฎในรอบถัดไป: แก้ **2 แถว** รวม A005 ซึ่งเป็น inactive และไม่เข้าเงื่อนไข preview
- วิธีแก้: กำหนด semantics ของ selection + predicate ให้ตรงกันทั้ง preview/save/replay; ผูกกฎกับ schema/ความหมายที่เข้ากันได้; ปฏิเสธ operation ที่ยังเก็บเป็นกฎถาวรไม่ได้
- เกณฑ์ผ่าน: ข้อมูลและเงื่อนไขเดียวกันต้องให้ผล preview และ replay ตรงกัน มีกรณีทดสอบทั้งเข้าเงื่อนไข/ไม่เข้าเงื่อนไข และ schema เปลี่ยน

### F4 — รอบที่ยกเลิกแล้วกลับมาเผยแพร่ได้ผ่าน dataset เดิม [P1]

- จุดตรวจ: `backend/src/forma/services/datasets.py:682` (`approve_dataset`), `:838` (`export_dataset`)
- สิ่งที่เกิด: ยกเลิกรอบแล้วเรียก approve dataset เดิมได้ HTTP **200**; export ได้ HTTP **201**; สถานะรอบสุดท้ายกลายเป็น **published**
- ฝั่ง worker มีการป้องกันงานที่ปิดแล้วบางส่วน แต่ทาง API สำหรับคนยังไม่บังคับกฎเดียวกัน
- วิธีแก้: มี lifecycle guard ส่วนกลางที่ทั้ง API/worker ใช้ ตรวจภายใต้ transaction/lock เดียวกัน; การเปิดงานใหม่ต้องเป็นคำสั่งที่ตั้งใจ ไม่ใช่ผลข้างเคียงของ export
- เกณฑ์ผ่าน: cancelled/moved/superseded ต้องไม่ถูกแก้หรือเผยแพร่ใหม่โดยข้ามขั้นตอน ส่วนการดาวน์โหลดผลเก่าที่เคยเผยแพร่ให้กำหนดสิทธิ์แยกอย่างชัดเจน

### F5 — Restore ระบุ verify แต่ไม่ตรวจ checksum ที่บันทึกไว้ [P1]

- จุดตรวจ: `backend/src/forma/services/backup.py:232` (`restore_backup`)
- สิ่งที่เกิด: manifest มี SHA-256 แต่ `verify=True` ตรวจเพียงว่า object มีอยู่หรือไม่ และเริ่ม restore ฐานก่อนตรวจ object ทั้งชุด
- ทดลองแก้ bytes ของ object ใน backup แล้ว restore ไปฐานว่างอีกชุด: `missing_objects=[]` แต่ hash ที่ restore **ไม่ตรง manifest** และไฟล์เสียถูกนำกลับเข้า storage
- วิธีแก้: ตรวจ manifest, รายการไฟล์, ขนาดและ hash ก่อนเปลี่ยนปลายทาง; fail เมื่อไม่ตรง; ตรวจหลัง restore และทำ business consistency checks; ใช้ staging/fresh target เพื่อไม่ให้ restore ครึ่งชุดทำลายระบบเดิม
- เกณฑ์ผ่าน: backup ที่เสียเพียงหนึ่ง object ต้องถูกปฏิเสธอย่างชัดเจนก่อนเปิดให้ใช้ พร้อมทดสอบกู้คืนสำเร็จทั้งฐานและไฟล์ในสภาพปกติ

## ผลตรวจอัตโนมัติรอบนี้

| รายการ | ผล | ข้อจำกัด/หมายเหตุ |
|---|---|---|
| Backend suite พร้อม coverage | 956 ผ่าน, 1 ไม่ผ่าน, 5 ข้าม | 19 นาที 30 วินาที; ไม่ใช่ทุก test ใช้ PostgreSQL |
| Coverage | 90.88% | ผ่านเกณฑ์ 85%; coverage ไม่เท่ากับความถูกต้องของทุกกรณี |
| ทดสอบข้อ worker recovery ซ้ำโดยไม่เก็บ coverage | ยังไม่ผ่าน | อาการต่างจากรอบเต็ม ดูรายละเอียดด้านล่าง |
| Python type check | ผ่าน 147 source files | `mypy.log` |
| Ruff | ไม่ผ่าน 2 E501 | `core/settings.py:48,100` |
| Formatter check | ต้องจัดรูปแบบ 2 ไฟล์ | `core/settings.py`, `tests/test_knowledge_transfer.py` |
| Frontend unit tests | 12/12 ผ่าน | จำนวน test ยังน้อยเมื่อเทียบกับจำนวน interaction |
| Frontend lint | ไม่มี errors แต่มี warnings | รวม effect/state, unused expressions, fast refresh |
| TypeScript/Vite build | ผ่าน | main JS 1,056.68 kB ก่อน gzip; ควรแบ่งโหลดตามหน้า |
| Production config validation | ผ่าน | ตรวจโครงสร้าง/ค่า example ไม่ใช่ credentials หรือ server จริง |
| Server planner tests | 7/7 ผ่าน | capacity planning logic |
| Compose profile merge tests | 1/1 ผ่าน | ครอบคลุม profile 2/4/8/16/32; ไม่ได้เปิด worker 32 ตัวจริง |
| Heuristic evaluation | ผ่าน baseline, 30 variants | field accuracy 99.62%, critical/identity 100%, drift 9/9; ไม่ได้ใช้ DeepSeek |
| `git diff --check` | ผ่าน | มีข้อความเตือน CRLF normalization |

### ข้อ worker recovery ที่ยังไม่ผ่านต้องแยกสองสาเหตุ

ชื่อ test: `tests/test_failure_injection.py::test_killed_worker_process_is_recovered`

รอบเต็มที่เก็บ coverage ล้มระหว่างเตรียม pipeline workbook 70,000 แถว บน SQLite มี `database is locked`, heartbeat ต่ออายุ lease ไม่ทัน และระบบปฏิเสธ output ด้วย `Worker lease lost; discarded uncommitted output` การปฏิเสธ output เมื่อเสีย lease เป็นการป้องกันที่ถูกต้อง แต่ workload/test environment ยังต้องจัดการ

เมื่อรันเฉพาะข้อนี้โดยไม่เก็บ coverage ขั้นเตรียมผ่านและฆ่า worker ได้ จากนั้น test เรียก `recover_stale(stale_s=0)` แล้วคาดว่าจะ requeue ทันที แต่ implementation ปัจจุบันใน `jobs/runner.py` ตรวจ `lease_expires_at` ก่อน และ lease ยังไม่หมดอายุ จึงคืน `[]` ต้องแก้ test ให้จำลอง/รอ expiry ตามสัญญาปัจจุบัน แล้วพิสูจน์ recovery บน PostgreSQL + Redis แยกจากข้อจำกัด SQLite ไม่ควรตัด safety lease ทิ้งเพื่อทำให้ test ผ่าน

ผลนี้เป็น **gate ที่ยังแดง** แต่ยังไม่ใช่หลักฐานว่า recovery บน production PostgreSQL ใช้งานไม่ได้

## หลักฐานเดิมที่อ่านประกอบ — ไม่ได้รันซ้ำรอบนี้

- `manual_test_files/excel100_v1/REPORT-AFTER.md`: 100 Excel, 520 sheets, 17,654 rows; 80 กรณีถูกต้องผ่าน และ 20 กรณีผิดโดยตั้งใจถูกปฏิเสธ; ตรวจ 24,288 ค่าได้ตรง แต่ใช้ mock DSL ที่เตรียมไว้ จึงพิสูจน์ flow มากกว่าความสามารถโมเดลอ่าน format ใหม่
- `manual_test_files/parallel300_v1/REPORT.md`: 300 CSV ขนาดเล็ก, 12 แถว/ไฟล์, mock latency 2 วินาที; 4 slots 20.56 นาที เทียบ 8 slots 15.36 นาที เร็วขึ้น 1.34 เท่า; ทั้งสองรอบ 300 งานจบ มีการทดสอบ fault 16 งาน หลักฐานนี้ไม่ใช่เวลาสำหรับ 300 Excel ซับซ้อนกับ DeepSeek จริง
- `deploy/repro/VERIFICATION.md`: isolated PostgreSQL/Redis/MinIO/sandbox stack ทดสอบ upload → review/approve → export/download และ stop/up/destroy; เป็นหลักฐานก่อนหน้า ไม่ได้เปิดชุดทดลองใหม่ในการตรวจนี้

## คะแนนแต่ละส่วนและการเทียบแนวปฏิบัติ

เกณฑ์คะแนน: 90+ ต้องมีหลักฐานใช้งานจริง/กู้คืน/ควบคุมความเสี่ยงครบ, 80 มีฐานแข็งแรงและทดสอบหลายระดับ, 70 ทำงานได้แต่มีช่องว่างสำคัญ, 50–60 ยังมีข้อผิดพลาดที่กระทบความถูกต้อง คะแนนเชิงคุณภาพมีความไม่แน่นอน ไม่ใช้ค่าเฉลี่ยกลบ release blocker

| ส่วน | คะแนน /100 | น้ำหนัก | สิ่งที่มีและสิ่งที่ยังขาด |
|---|---:|---:|---|
| Architecture / ขอบเขตระบบ | 80 | 8% | API, worker, sandbox แยกหน้าที่; business services ยังใหญ่และใช้ฐานร่วม |
| การวางไฟล์/ชื่อ/ดูแลโค้ด | 68 | 5% | มี layers ชัด; routes/mapping ใหญ่, เอกสารซ้ำและชื่อบางส่วนไม่สะท้อน scope ปัจจุบัน |
| การอ่าน Excel/CSV และหลักฐาน | 80 | 10% | อ่านโครงสร้างหลายชีตและ metadata มากกว่าข้อความล้วน; ยังไม่รองรับทุกความหมายของ Excel |
| Mapping / unknown format / การตรวจคำตอบ | 74 | 12% | มี graph, tools, DSL, validation, ถามคน; ยังขาด blind real-ERP holdout ที่ยืนยันความแม่นยำ |
| Queue / worker / retry / concurrency | 82 | 10% | durable jobs, lease/fencing, retry, reconciliation; recovery gate ต้องยืนยันใหม่ |
| วงจรไฟล์และสถานะงาน | 58 | 8% | มีแทนที่/ย้าย/ยกเลิก/ประวัติ; cancelled round ยังกลับมา published ได้ |
| แก้ JSON / version / preview | 50 | 12% | มี editor/preview/validation แต่ stale edit และ persistent predicate ไม่ปลอดภัยพอ |
| ความรู้/คำตอบเดิม/การนำเข้า production | 78 | 6% | provenance, strict eligibility, preview/import/revoke; ต้องผูก retention กับหลักฐานที่ยังใช้อยู่ |
| ผู้ช่วย/บริบท/tools/ตารางกราฟ | 78 | 5% | history, tools ตามสิทธิ์, evidence, structured output; ไม่ได้รู้ทุกข้อมูลโดยอัตโนมัติ |
| Security / สิทธิ์ / sandbox | 70 | 5% | OIDC/PKCE, scope, encrypted secrets, sandbox จำกัด; ยังไม่ได้ pentest และ hardening ทุก integration |
| Deploy / environment / upgrades | 60 | 7% | แยก env และ capacity profiles; ยังไม่มี release pipeline/rollback proof แบบครบชุด |
| Backup / restore / disaster recovery | 45 | 4% | มี backup/restore แต่ integrity verification มีบั๊กและยังขาด recovery drill ตามเป้าหมาย |
| Logs / metrics / cost / audit | 73 | 3% | มีข้อมูลการทำงานมาก; ต้องเพิ่ม SLO, alert และตรวจ aggregation ของ worker metrics |
| UX / ความต่อเนื่องของหน้าจอ | 68 | 3% | งานหลักใช้ได้; ยังมี navigation, label, ขอบเขตตัวเลขและ responsive gaps |
| Tests / CI / release evidence | 68 | 2% | backend tests/coverage มาก; ยังมี gate แดงและ browser/integration gates ไม่ครบ |
| **รวมถ่วงน้ำหนัก** | **69** | **100%** | **ยังไม่ผ่านปล่อยรุ่น** |

### สิ่งที่เทียบกับผู้ให้บริการรายใหญ่ได้จริง

1. **การแยก service:** Microsoft อธิบายว่าการแยก microservices มีต้นทุนเรื่องธุรกรรมกระจาย การสื่อสาร และการทดสอบ FORMA ปัจจุบันเป็น modular monolith ที่มี API/worker/sandbox แยก process เป็นทางเลือกเหมาะสมกับขนาดทีม ไม่จำเป็นต้องแบ่งทุก domain เป็น service เพื่อให้ดูเป็นระบบใหญ่ ควรทำ transaction และ ownership ของข้อมูลให้ถูกก่อน ([Microsoft architecture](https://learn.microsoft.com/en-us/azure/architecture/microservices/), [data considerations](https://learn.microsoft.com/en-us/azure/architecture/microservices/design/data-considerations))
2. **งานเบื้องหลังที่ไม่หล่น:** AWS transactional outbox เน้นบันทึก business change กับ intent ส่งงานอย่างสอดคล้องกัน และรองรับการส่งซ้ำ FORMA มี staged DB jobs, dispatch/reconciliation และ lease fencing ใกล้กับหลักการนี้แล้ว จุดที่ต้องแก้คือ commit boundary และทดสอบการกู้คืนให้ตรงสัญญา ไม่ใช่เพิ่ม Redis อีกตัวแล้วถือว่าปลอดภัย ([AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html))
3. **Mapping และจำคำตอบ:** Flatfile มี column matching, ความจำจากการเลือกเดิม, preview และให้คนแก้ mapping/ค่าหมวดหมู่ FORMA มีแนวทางใกล้เคียง พร้อม DSL และความสัมพันธ์ข้ามชีตที่เหมาะกับงานของเรา แต่ยังไม่มีการทดสอบข้อมูลชุดเดียวกันเพื่อบอกว่าแม่นกว่าหรือเท่ากัน ตัวเลข marketing ของ vendor ใช้เทียบกับ field accuracy ของเราโดยตรงไม่ได้ ([Flatfile mapping](https://support.flatfile.com/articles/8569925090-mapping-data))
4. **Agent ที่กลับมาทำต่อได้:** LangGraph แยก checkpoint ของงาน/บทสนทนาออกจาก memory ข้ามงาน FORMA ใช้ทั้ง graph checkpoints และ business knowledge ในฐานข้อมูลแล้ว นี่เป็นพื้นฐานที่ถูก แต่เพิ่ม agent หลายบทบาทด้วยโมเดลเดียวไม่ได้รับประกันว่าความผิดพลาดจะเป็นอิสระต่อกัน ([LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence))
5. **Data quality ที่ตรวจได้:** Great Expectations ใช้ชุดข้อกำหนดและ Checkpoint เพื่อให้ผลตรวจนำไปสู่การกระทำและรายงาน FORMA มี schema/semantic validation อยู่แล้ว ควรเพิ่มสัญญาธุรกิจที่วัดได้ เช่น identity ไม่ซ้ำ ความสัมพันธ์ครบ และผลรวมสอดคล้องตาม schema โดยไม่ hardcode product ทุกงาน ([GX checkpoints](https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/))
6. **Document pipeline:** Unstructured แยก partition/metadata/enrichment/chunking/indexing ชัดเจน หลักการแยก evidence จาก retrieval ใช้เทียบได้ แต่ระบบนั้นเน้นเตรียมเอกสารสำหรับ retrieval มากกว่างาน ERP-to-JSON จึงไม่ใช่คู่แข่งที่ใช้คะแนนชุดเดียวกันได้โดยตรง ([Unstructured pipelines](https://docs.unstructured.io/pipelines/overview))
7. **Backup ที่เชื่อถือได้:** AWS แนะนำการ restore เป็นระยะและตรวจความครบถ้วน พร้อมเป้าหมาย RPO/RTO FORMA ยังต่ำกว่าระดับนี้ชัดเจนเพราะ verify รับไฟล์เสีย ต้องแก้ก่อนนับ backup เป็นหลักประกัน ([AWS recovery testing](https://docs.aws.amazon.com/wellarchitected/2024-06-27/framework/rel_backing_up_data_periodic_recovery_testing_data.html))

จึงสรุปได้ว่า **แนวคิดหลายส่วนไปในทางเดียวกับระบบใหญ่ แต่ความแข็งแรงของ implementation และหลักฐาน production ยังไม่เท่ากัน** ไม่ได้ให้คะแนนคู่แข่งโดยเดาภายในระบบของเขา และยังไม่มีหลักฐานว่า FORMA map แม่นเท่า ChatGPT/Claude บนไฟล์เดียวกัน

## ตรวจ flow ตั้งแต่ต้นจนจบ

| ขั้นตอน | สิ่งที่พบ | สิ่งที่ควรทำต่อ |
|---|---|---|
| เลือกรอบ/ลูกค้า/อัปโหลด | มี work round และ import round แยกกัน มีแยกไฟล์กับรวมไฟล์ | อธิบายหน่วยใน UI ให้ชัด: รอบงาน, ชุดประมวลผล, ไฟล์, คำถาม |
| อ่านไฟล์/หลายชีต/relations | มี profiler, OOXML evidence, cached formula/error/hidden/merged metadata และเครื่องมืออ่านเพิ่ม | ระบุ capability matrix ต่อ extension และสิ่งที่ต้องถาม; ไม่สัญญาว่าอ่าน Excel ได้ทุกความหมาย |
| เลือก schema/format เดิม | มี schema registry, format memory, eligibility และ compatibility checks | ทดสอบ schema เปลี่ยนแต่ชื่อ/คอลัมน์เดิม และป้องกันการ reuse กฎที่ความหมายเปลี่ยน |
| Map format ใหม่ | มีหลายบทบาท, sandbox/tools, deterministic transform และ verification | real-ERP holdout, metamorphic tests เช่นสลับแถว/เปลี่ยนชื่อชีตแต่ผลธุรกิจต้องเดิม |
| ถาม/ตอบ/แก้ mapping | มี evidence, alternatives, human review และสร้าง mapping รุ่นใหม่ | ทดสอบคำตอบที่ขัดกัน, ตอบบางข้อ, schema เปลี่ยนระหว่างเปิดหน้า |
| จำคำตอบและนำไปใช้ | มี provenance, scoped retrieval, import/revoke | กฎแก้ข้อมูลถาวรต้องเก็บเงื่อนไขครบ และหลักฐานต้องไม่ถูก retention ลบขณะยังใช้งาน |
| ตรวจและแก้ JSON | มี preview, bulk edit, operation history, validation | แก้ F2/F3 ก่อนให้หลายคนหรือหลายแท็บแก้พร้อมกัน |
| อนุมัติ/สร้างไฟล์/ดาวน์โหลด | happy path มีและทดสอบได้ | แก้ F1/F4; ทำ race test approve/export/cancel/replace |
| แทนที่/ย้ายลูกค้า/ยกเลิก | มี API และ UI รองรับหลายขั้น | กฎสถานะต้องใช้ร่วมกันทุก endpoint; identity, learning, output เก่าต้องเชื่อมประวัติถูก |
| Retry/worker ตาย/timeout | มี persisted stages, lease, limits และ recovery | ยืนยัน lease-expiry recovery บน production-like stack และไฟล์ใหญ่ |
| ย้อนดู log/decision | มี agent events, source evidence, mapping versions, user actions | ทำหน้าเหตุผลแบบ source → ทางเลือก → ผลตรวจ → ผู้ตัดสิน; ไม่อ้างว่า log คือความคิดภายในทั้งหมดของโมเดล |
| Backup/restore/deploy รอบต่อไป | มีคำสั่งและ config แยก env | F5, restore drill, migration rehearsal, automated smoke และ rollback plan |

## ช่องว่างเพิ่มเติมที่ควรจัดการ

ส่วนนี้รวมข้อค้นพบจากการอ่านโค้ดและ UI; รายการที่ไม่ได้ทำ failure reproduction ระบุเป็นความเสี่ยง/งานตรวจต่อ ไม่ยกระดับเป็นเหตุการณ์ข้อมูลเสียที่เกิดขึ้นแล้ว

### โครงสร้างและการตั้งชื่อ

- ยังไม่จำเป็นต้องแยกทุกฟังก์ชันเป็น microservice ให้คง API, workers และ sandbox ที่ deploy/scale แยกได้ แล้วปรับขอบเขต modules ภายในก่อน
- `api/routes.py` 1,577 บรรทัด, `services/mapping.py` 1,516 บรรทัด, `services/datasets.py` 933 บรรทัด ทำให้กฎธุรกิจอยู่หลายที่ ควรแยกตาม domain โดยมี transaction/lifecycle policy กลาง และรักษา public contract เดิม
- ชื่ออย่าง `engine/thai.py`/`thai_vec.py` ไม่สื่อขอบเขตการ parse หลาย locale ในปัจจุบัน ควรจัด naming ใหม่อย่างมี compatibility ไม่ใช่เปลี่ยนชื่อทั่ว repo พร้อมแก้ logic
- package `quality` เป็น placeholder; เอกสาร architecture หลายชุดและ README บางคำอธิบายไม่ตรงความสามารถปัจจุบัน ควรมี capability matrix และเอกสารหลักชุดเดียวที่อ้าง source/test
- fixture/report จำนวนมากควรแยกจาก runtime image และกำหนด retention ของผลทดสอบใน repo ให้ชัด ไม่จำเป็นต้องลบหลักฐานเดิม

### ข้อมูลขนาดใหญ่และความหมายของเวอร์ชัน

- Endpoint ดูแถวอ่าน JSONL ทั้งชุดก่อน filter/sort/page จึงเป็น O(n) RAM/เวลาแม้ขอเพียงหน้าแรก ควรเพิ่ม index/query engine หรือ batch scan ที่มีขอบเขตตามขนาดจริง
- การ sort บางทางใช้ `str(value)` ทำให้ตัวเลขเรียงแบบข้อความได้ ควรใช้ชนิดข้อมูลจาก schema และทดสอบค่าติดลบ/ทศนิยม/null
- `previous_published` เลือกรอบอื่นตาม created order แต่ยังไม่มีข้อจำกัดก่อนรอบปัจจุบันและความเข้ากันได้ของ schema ที่ชัดเจน จึงควรทดสอบการเปิดรอบเก่าย้อนหลังและ schema เปลี่ยนก่อนใช้เป็น baseline เปรียบเทียบ
- Retention ต้องรู้ว่าไฟล์ใดเป็นหลักฐานของ knowledge release ที่ยังอนุญาตอยู่ ควรมี dependency pin/dry-run ก่อนลบ ไม่ใช่ดูเพียงอายุและสถานะรอบ
- Excel parser ไม่ได้ประมวลผล macro, external links หรือสูตรทุกชนิดใหม่ทั้งหมด การมี cached value ไม่รับประกันความสดของค่า; ควรแสดงข้อจำกัดและถามเมื่อหลักฐานไม่พอ แทนการเดาผลสูตร

### Deploy, security และ observability

- Dependency Python ยังไม่ frozen ทุกตัว และมี image tag ที่เปลี่ยนได้ ควรมี reproducible lock/digests และเลือก Node/Python รุ่นเดียวกันใน CI กับ image
- Profile 16/32 เป็นแผน capacity ไม่ใช่ autoscaler หรือหลักฐาน load test; การเลือกต้องคิด host RAM/disk reserve และ memory peak ต่อ workbook พร้อม global LLM rate limit
- ชุด deploy บน host เดียวยังไม่ใช่ HA ต้องกำหนด RPO/RTO, off-host backups, disk-full behavior, health checks และวิธีย้ายเครื่องก่อนเพิ่มจำนวน workers
- CI ควรเรียก server/repro config checks และเพิ่ม PostgreSQL + Redis integration/browser happy-path gate รวม migration rehearsal, readiness, smoke test และขั้น rollback image/config ที่ผ่านการทดสอบแล้ว
- Nginx ใช้ upstream ชื่อ API แบบคงที่ ต้องพิสูจน์ว่าการ recreate API ระหว่าง deploy resolve ที่อยู่ใหม่ได้หรือมี reload ขั้นตอนที่เชื่อถือได้ ประเด็นนี้ยังไม่ได้ทำให้เกิด 502 ในรอบตรวจ
- มี OIDC PKCE, JWT checks และ customer scope แต่ควรตรวจ callback state ให้ครบ หรือใช้ library OIDC ที่ดูแลต่อเนื่อง จุดนี้เป็น hardening recommendation ไม่ใช่ข้อพิสูจน์ว่า exploit สำเร็จ
- ต้องทดสอบ cross-customer authorization เป็น matrix, สแกน dependencies/secrets และตรวจสิทธิ์ knowledge export ที่อาจมีไฟล์ต้นฉบับ/ข้อมูลส่วนบุคคลก่อนเผยแพร่ repository หรือ package
- มี metrics/tracing และ persisted logs แล้ว แต่ควรมี alert ที่ทำงานจริงสำหรับ queue age, lease loss, failed/retried jobs, disk/RAM, LLM errors และ restore failures พร้อม runbook
- ตรวจว่า metrics จาก worker child processes รวมเข้าระบบสังเกตการณ์ได้จริง ไม่พึ่ง registry ของ API process เพียงตัวเดียว ตัวเลข cost ในระบบเป็นค่าประเมินจาก usage/rate ไม่ใช่ยอดใบแจ้งหนี้ผู้ให้บริการ

### UX ที่ตรวจพบ

- `/imports/new` มีปุ่มกลับแล้ว แต่ `/imports/new/single` ยังไม่มีลูกศรกลับบริเวณหัวหน้า มีเพียงลิงก์ไปอัปโหลดแบบแยกไฟล์ในเนื้อหา ควรใช้ navigation pattern เดียวกัน
- ตัวเลขหน้า Home/รายการไฟล์/คำถามมีคนละหน่วย และบาง query จำกัด 100 รายการ ควรแสดงจำนวนรวมจาก server พร้อมชื่อหน่วยและขอบเขตตัวกรอง ไม่ทำให้ผู้ใช้คิดว่าเป็นจำนวนเดียวกัน
- หน้า review มี horizontal overflow ที่ viewport 1280px ที่ตรวจ และข้อความไทย/อังกฤษยังปะปน ควรแก้เฉพาะจุด พร้อม browser test ที่ desktop แคบและ mobile
- หน้าลูกค้าจำนวนมากควรใช้ searchable selector; ช่วยให้เลือกถูกคนก่อนอัปโหลดและลดความเสี่ยง map เข้าผิดลูกค้า
- Frontend unit tests 12 ข้อยังไม่ครอบคลุมการแก้ข้อมูลหลายแท็บ/กดซ้ำ/ยกเลิกระหว่างโหลด ควรเพิ่ม interaction tests ที่ตรวจผลธุรกิจจริง

## ลำดับทำต่อที่แนะนำ

1. **แก้ F1–F5 และเขียน regression tests ก่อนแก้** โดยเฉพาะ commit failure, stale base, persistent predicate, cancelled state และ backup corruption ทดสอบ concurrency ด้วย PostgreSQL ไม่พึ่ง SQLite อย่างเดียว
2. **ทำ release gates ให้เขียวจริง**: ปรับ recovery test ให้ใช้ lease expiry ตามสัญญา, ตรวจ large-file contention แยก, แก้ lint/format และรวม server/repro checks ใน CI ห้ามลบ/ลด safety assertion เพื่อให้ผ่าน
3. **ยืนยัน deployment/restore รอบเต็มใน repro**: migrate → upload → review → edit → approve → download → restart/recover → backup/restore → compare business data ทั้งหมด ใช้ mock ได้ ไม่เสีย API
4. **เก็บงานความเสี่ยงรอง**: server-side query/counts, lifecycle consistency ทุก endpoint, retention pin, navigation และ observability/alerts ทำทีละกลุ่มเพื่อไม่ให้ refactor กลบการแก้ข้อมูล
5. **ก่อนนำไฟล์จริงขึ้นใช้งาน**: เลือก pilot ที่ไม่ auto-publish ในครั้งแรก มีคนตรวจผลและคำถาม ใช้ target schema จริง สร้างชุดเฉลยที่คนยืนยันแล้ว แยก train/reuse จาก holdout และค่อยวัดความแม่นยำ/เวลา/ต้นทุนจริง ไม่ตั้งคะแนนเป้าหมายแทนหลักฐาน

## สถานะ Git และข้อมูลเดิม

- ไม่แก้ application code ระหว่าง audit นี้ เพิ่มเฉพาะหลักฐาน รายงาน และสคริปต์ทำซ้ำในโฟลเดอร์นี้ งานที่ยังไม่ commit จากก่อนหน้าเก็บไว้ครบ
- ไม่ย้ายหรือลบข้อมูล dev ไม่เปลี่ยน migration/config ที่ใช้อยู่ ไม่เปิด paid model calls
- ตรวจ `/ready` ได้ ok และจำนวนหลักก่อน/หลังยังตรงกัน: 236 customers, 401 import rounds, 393 source files, 449 mapping versions, 124 corrections, 667 LLM calls
- GitHub เข้าถึงผ่าน Git ได้: remote `main` เป็น `5f3bffd52d4ac0b3e77cd4940a36a10a7b2194fd` มี initial commit และ README เพียงไฟล์เดียว
- local และ remote ไม่มี common ancestor (67 local commits เทียบ 1 remote commit) เมื่อแก้จนผ่านแล้วต้องรวมประวัติอย่างไม่ทำลาย remote ก่อน push ไม่ควร force-push ทับเพื่อข้ามขั้นตอนนี้
- รอบนี้ fetch เพื่ออ่านประวัติเท่านั้น **ไม่มี commit, merge หรือ push** เพราะเงื่อนไขที่ผู้ใช้กำหนดว่า “ถ้าผ่าน” ยังไม่ครบ

## ไฟล์หลักฐาน

| ไฟล์ | ใช้ตรวจอะไร |
|---|---|
| `inventory.json`, `scope.json`, `routes.json` | ขอบเขตไฟล์/โครงสร้าง/route ที่สำรวจ |
| `reproduce.py`, `probe-results.json`, `probes.log` | ทำซ้ำ F1–F5 ด้วย private DB และ mock |
| `backend-tests.log`, `worker-recovery-retest.log` | ผล suite และข้อที่ไม่ผ่าน |
| `ruff.log`, `format.log`, `mypy.log` | คุณภาพ source/type |
| `frontend-test.log`, `frontend-lint.log`, `frontend-build.log` | UI tests/lint/build |
| `eval.json`, `eval.log` | Heuristic baseline evaluation |
| `server-plan.log`, `server-compose.log` | Server profile/config validation |

คำสั่งทำซ้ำ probe จาก repo root: `backend\.venv\Scripts\python reports/audit-2026-11-05/reproduce.py` สคริปต์สร้างและล้างฐานชั่วคราวของตัวเอง ส่วนผล JSON จะถูกเขียนทับในโฟลเดอร์รายงานนี้
