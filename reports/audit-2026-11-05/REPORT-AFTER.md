# ผลแก้ไขและประเมิน FORMA หลัง audit — 5 พฤศจิกายน 2026

เทียบกับ [รายงานก่อนแก้](REPORT.md) โดยใช้เกณฑ์และน้ำหนักเดิม คะแนนเป็นการประเมินความพร้อมทางวิศวกรรม ไม่ใช่เปอร์เซ็นต์ความแม่นยำของ AI

## ข้อสรุป

ปิดปัญหาข้อมูลทั้ง 5 ข้อที่ทำซ้ำได้จาก audit แล้ว พร้อม regression tests และทดสอบ flow บน PostgreSQL + Redis/ARQ + MinIO ใน repro แยกจาก dev การแก้ไขนี้มุ่งที่ความถูกต้องของธุรกรรม ประวัติเวอร์ชัน และการกู้คืน ไม่ได้เปลี่ยนโมเดลหรือเพิ่มคะแนนความเข้าใจ ERP ให้ AI โดยไม่มีการวัดจริง

สถานะ gates รอบสุดท้าย: **ผ่าน local gates ครบ — 978 ผ่าน / 5 ข้าม / 0 ล้มเหลว; coverage 90.8% (เกณฑ์ 85%)**

ประเมินใหม่ **81/100** จากเดิม **69/100** จาก gates ที่ผ่านด้านล่าง: เหมาะกับการเริ่ม pilot แบบมีคนตรวจบน environment ที่ตั้งค่าครบ ยังไม่ใช่การรับรองเปิด production แบบไม่ต้องดูแล หรือรับประกัน unknown format ทุกชนิด

## ปัญหาข้อมูลก่อนและหลัง

| จุด | ก่อน | หลัง / หลักฐาน |
|---|---|---|
| F1: commit ล้มเหลว | API ตอบว่าสำเร็จ แต่ไม่มีข้อมูลในฐาน | commit ก่อนส่ง success; ล้มเหลวคืน 503 พร้อม request ID; rollback และไม่ commit ซ้ำตอน cleanup; ทดสอบไม่เกิดแถวหลอกและลบเฉพาะ object ใหม่ของธุรกรรมที่ล้มเหลว |
| F2: หลายแท็บแก้ฐานเดียวกัน | สร้าง version 2 ซ้ำสองรายการและผลแก้หายจากอีกสาย | ล็อกรอบ + ตรวจ head + unique index; สอง request บน SQLite และ PostgreSQL ได้ 201/409; เก็บ draft ที่หน้าจอและชี้เวอร์ชันล่าสุดให้เปิด |
| F3: กฎจำใช้ข้ามรอบ | preview 1 แถว แต่ replay 2 แถวเพราะเงื่อนไขหาย | เก็บ identity และ predicate ร่วมกัน; replay ตรง preview; กฎใหม่ผูก schema reference/hash; operation ที่ยังจำครบไม่ได้ถูกปฏิเสธก่อนเขียน และใช้แบบครั้งเดียวได้ |
| F4: รอบยกเลิกกลับมา published | เรียก approve/export ผ่าน dataset เก่าได้ | edit/rollback/approve/export ตรวจสถานะผ่านล็อกเดียวกับ cancel/move/replace; ทดสอบ cancel แข่งกับ edit และไม่เปิดรอบกลับ; ประวัติเดิมยังอ่านได้ |
| F5: restore verify ไม่ตรวจจริง | ยอมรับ object ที่ checksum เปลี่ยน | ตรวจ dump และทุก object ก่อนนำฐานกลับ; ปฏิเสธ missing/corrupt/traversal และ target object ที่มี bytes คนละชุด; PostgreSQL import ใช้ transaction เดียวและหยุดเมื่อมี SQL error |

Migration `c6e8fa123456` จะหยุดหากมีเลขเวอร์ชันซ้ำในฐานเดิม พร้อมแจ้งรายการให้ตรวจ ไม่ลบหรือเปลี่ยนเลขประวัติอัตโนมัติ ทดสอบยืนยันว่าประวัติที่ชนกันยังอยู่ครบหลัง migration ถูกปฏิเสธ รอบนี้ไม่ได้ migrate ฐาน dev

## Recovery และ gates

- Recovery injection 6 กรณีผ่าน: storage failure, commit failure, ฆ่า worker จริงแล้วกู้คืน, timeout/fallback และขอบเขตความผิดพลาดที่เกี่ยวข้อง
- ปรับ kill test ให้หยุด worker ที่ handler entry อย่างแน่นอน แทนการสร้าง Excel 70,000 แถวเพียงเพื่อทำให้ worker ช้า ยังใช้ process จริงและ default lease; lease ที่ยังไม่หมดอายุต้องไม่ถูกแย่ง เมื่อจำลองหมดอายุแล้วต้องทำ attempt 2 สำเร็จและมี output เพียงชุดเดียว
- รอบ full suite แรกของงานแก้นี้ถูกหยุดระหว่าง fixture 70,000 แถวที่ช้ามากภายใต้ coverage ไม่ได้นับเป็นผลผ่าน จากนั้นรันชุดเต็มใหม่ด้วย fault injection ที่กำหนดจุดหยุดได้ บันทึกเก่าอยู่ `after/backend-tests-slow-fixture.log`
- Full backend + coverage: **978 ผ่าน, 5 ข้าม, 0 ล้มเหลว; coverage 90.8% จากการรวม 4 process ที่รันครบทุก test module ดู [ผลตรวจทั้งหมด](after/verification.json)**
- Ruff check/format และ mypy 149 source files: **ผ่าน**
- Migration/storage/repro safety ชุดเสริม: **46 ผ่าน** ก่อนเพิ่ม cancel-race และ checkpoint proof; ชุดเต็มรอบสุดท้ายรวมกรณีใหม่แล้ว
- Frontend: **12 tests ผ่าน**, TypeScript/build ผ่าน, lint ไม่มี error แต่ยังมี 19 warnings และ build เตือนขนาด bundle; ไม่ได้ปิดกฎเพื่อให้ผ่าน
- Config example ไม่ drift; server planner 7 tests และ Compose merge 2/4/8/16/32 งานผ่าน
- `promtool check rules` ผ่าน **4 rules**; ยังไม่ได้ตั้งผู้รับ Alertmanager หรือพิสูจน์การส่งแจ้งเตือนออกนอกระบบ
- พบ CI อ้าง image `bitnami/minio:2024` ที่ registry ไม่มีแล้ว จึงเปลี่ยนเป็น official MinIO digest เดียวกับ repro ที่ผ่าน และตั้งเวลาเผื่อ 60 นาทีโดยคง checks เดิมครบ
- เพิ่ม lost-COMMIT-acknowledgement test: ฐานบันทึกแล้วแต่ API รับ error ต้องคืน 503 และเก็บ object ที่มี database reference ไว้; ชุด commit/race เพิ่มเติม **7 ผ่าน**
- รอบเต็มก่อนตรวจท้ายได้ 974 ผ่าน / 1 ไม่ผ่าน / 5 ข้าม: helper ใน `test_real_world_cases.py` เลือก dataset เก่าสุดจาก API ที่เรียงใหม่ไปเก่า ทำให้ stale-write guard ปฏิเสธได้ถูกต้อง แก้ helper ให้เลือก version สูงสุด โดยไม่ลด guard ในระบบ; ทดสอบทั้งไฟล์ร่วมกับ integrity/recovery อีก **41 กรณีผ่าน** แล้วรันชุดเต็มรอบสุดท้ายด้านบน
- พบ nginx จำ IP ของ API หลัง restart ทำให้หน้าเว็บตอบ 502 ทั้งที่ API healthy แก้ upstream ให้ resolve ผ่าน Docker DNS ระหว่างทำงานและ pin nginx 1.27.5; `nginx -t` ผ่าน และ flow รอบสุดท้าย restart ได้โดยไม่สั่ง reload proxy ดู [ข้อกำหนด upstream resolve ของ nginx](https://nginx.org/en/docs/http/ngx_http_upstream_module.html#resolve)
- การรันเต็มถัดมาพบ race ของ S3 cache บน Windows: temporary file แยกกันแล้ว แต่ `replace` ปลายทางเดียวกันพร้อมกันยังเกิด `PermissionError` ได้ แก้ให้ใช้ cache ที่ request อื่นเผยแพร่เสร็จแล้วสำหรับ immutable key; ถ้ายังไม่มีไฟล์สำเร็จ ต้องส่ง error ต่อ เพิ่ม fault injection ทั้งสองทางและให้เทสชนกัน 20 รอบ ชุดเฉพาะส่วน 24 กรณีผ่าน แล้วรันชุดเต็มแบ่ง 4 process ที่แยกฐานชั่วคราว รวม coverage และบังคับเกณฑ์ 85% หลังรวม โดยไม่เปลี่ยน config ของ dev
- Eval heuristic 30 variants: field accuracy **99.62%**, critical/identity **100%**, drift **9/9**, ไม่ต่ำกว่า baseline ผลนี้เป็น heuristic/fixture ไม่ใช่คะแนน DeepSeek บน ERP จริง
- Short benchmark ด้วย mock แยกจาก dev: 6 ไฟล์ × 2,000 แถว, thread backend 4 workers, จบ 27.4 วินาที, 12,000 แถว, peak RSS 365.9 MB, failed jobs 0 ดู [ผล benchmark](after/bench-proof.json) ไม่ใช้ตัวเลขนี้ประมาณเวลา DeepSeek หรือ Excel ซับซ้อน 300 ไฟล์โดยตรง

## Full flow บน repro จริง

รัน project `forma-repro-auditfix` คนละ database, Redis, MinIO, network, volumes และ image tags กับ dev; ใช้ `FORMA_LLM_MOCK_ONLY=true` และไม่เชื่อม API ภายนอก

1. สร้าง Excel จำลอง 3 ชีต: สินค้า, stock สองคลัง, promotions รวมผลลัพธ์ 12 สินค้า
2. อัปโหลด → queued mapping ผ่าน ARQ → ตรวจและอนุมัติ mapping โดย test operator
3. สร้างผลลัพธ์ → preview แก้ชื่อสินค้า → ส่งการแก้จากสอง request พร้อมกัน: **201 และ 409**; history มี version 1 และ 2 ไม่ซ้ำ
4. อนุมัติข้อมูล → export JSONL → ดาวน์โหลดและตรวจ SKU ราคา stock และค่าที่แก้
5. Restart เฉพาะ API/worker ของ repro ขณะไม่มีงานค้าง → ดาวน์โหลดเดิมได้ checksum เดิม
6. Backup พร้อม object → restore ไปยัง database และ bucket ใหม่ใน repro → ตรวจ 12 แถว, ค่าที่แก้, สถานะ published และ checksum ไฟล์ส่งออกตรงกัน; ไม่มี object ขาด
7. เพิ่ม checkpoint จำลองผ่าน LangGraph และ attempt ledger จริงโดยไม่มีการเรียกโมเดล → backup/restore อีกรอบ → checkpoint และยอด attempt ยังตรงกัน

หลักฐานรอบสุดท้าย: [ผล flow ทั้งรอบ](after/repro-proof.json), [ผล restore](after/recovery-proof.json), `after/repro-cache-e2e.log` และสคริปต์ที่รันซ้ำได้ [smoke.py](../../deploy/repro/smoke.py) / [recovery_check.py](../../deploy/repro/recovery_check.py) คืน object 20 รายการ ไม่มีไฟล์ขาด และ download checksum ก่อน/หลัง restart/restore ตรงกัน

ข้อจำกัดของหลักฐานนี้: workbook ตัวอย่างขนาดเล็ก ไม่ใช่การทดสอบ 300 ไฟล์หรือความแม่นยำ unknown ERP; restart ของ Docker เป็นช่วง idle ส่วนการตายกลางงานทดสอบแยกด้วย spawned worker; ไม่ได้ทดสอบ VM สูญหาย, region failover, OIDC/TLS หรือ WAN outage ค่า API ในรอบนี้ **$0**

ชุดเต็มที่รันบน Windows มี 5 กรณีที่ข้ามตามเงื่อนไข platform/service (POSIX 2, PostgreSQL 2, MinIO 1) จึงแยกหลักฐาน PostgreSQL/MinIO จาก repro ไว้ชัดเจน ไม่เรียกกรณีที่ข้ามว่าผ่าน ส่วน workflow CI บน Linux เปิด integration tests เหล่านี้ไว้ ผล hosted CI ต้องดูจาก run หลัง push ต่างหาก

## ช่องว่างรองที่เก็บแล้ว

| เรื่อง | ผลลัพธ์ | ขอบเขตที่ยังต้องรู้ |
|---|---|---|
| เปิด/ค้น dataset ใหญ่ | สร้างดัชนี SQLite บนดิสก์ต่อ immutable version, คืนทีละหน้า, numeric sort ถูกชนิด; ทดสอบ 100,000 แถวและ Python peak allocation ต่ำกว่า 25 MiB | ไม่ใช่ memory ของทั้ง process; sort/substring ยังสแกนข้อมูลและใช้ CPU/disk; transformation/editor บางงานยังอ่านข้อมูลเต็ม |
| หลักฐานความรู้ | ความรู้ allowed pin round และ dependencies; retention ตรวจแผนใหม่ภายใต้ knowledge lock ก่อนลบ จึงไม่ลบหลักฐานที่เพิ่งอนุมัติหลัง preview | ต้องถอนสิทธิ์ใช้ความรู้ชัดเจนก่อนหมด retention; ไม่ได้เป็น transaction เดียวระหว่าง DB กับ object storage |
| ปุ่มกลับ | สองโหมด upload ใช้ component เดียว รักษาหน้าและ query ที่มา | คลิกจริงจากประวัติที่กรอง `search=smoke` → upload → รวมไฟล์ → กลับสองครั้ง ตัวกรองยังอยู่; ไม่ได้ redesign navigation ทั้งระบบ |
| จำนวนงาน | dashboard ใช้ยอดรวมจากฐาน แยกจากรายการตัวอย่าง 6 รายการ และระบุหน่วยรอบนำเข้า; ทดสอบ 125 รอบเพื่อกัน cap 100 | จำนวนรอบนำเข้าไม่ใช่จำนวนคำถามหรือจำนวนไฟล์เสมอไป |
| Alerts | งานรอเกิน 15 นาที, lease หมด, job fail 15 นาทีล่าสุด, backup เกิน 26 ชม./ไม่มีไฟล์, retention error และดิสก์แอปเหลือน้อย; มี UI + Prometheus | physical RAM/remote MinIO ต้องใช้ host/container exporters; ผู้รับแจ้งเตือนต้องตั้งตอน deploy |
| S3 cache | หลาย request ดาวน์โหลด key เดียวกันใช้ไฟล์ชั่วคราวคนละไฟล์ และใช้ผลที่บันทึกสำเร็จก่อนเมื่อ Windows ปฏิเสธการแทนที่พร้อมกัน | ทดสอบชนกัน 20 รอบและไม่กลบ permission error เมื่อไม่มีไฟล์สำเร็จ; ไม่ใช่ distributed cache ใหม่ |
| Worker health | ใช้ Redis heartbeat โดยไม่ import mapping stack หนักทุก 5 วินาที; repro หลังแก้ healthy | heartbeat ARQ เป็นระดับคิว ไม่ใช่ inventory จำนวน replica ทั้ง cluster; ใช้ container monitoring ร่วมด้วย |

Human in loop คงเดิม: คำถามที่ไม่ชัดต้องมีคนตอบ การแก้ข้อมูลสร้างเวอร์ชันใหม่ การส่งออกต้องผ่านสถานะ/validation ตามกฎ ไม่ได้ข้ามคำถามหรือเพิ่มการอนุมัติอัตโนมัติเพื่อให้เทสผ่าน

## คะแนนประเมินใหม่

| ส่วน | ก่อน | หลัง | น้ำหนัก | เหตุผล |
|---|---:|---:|---:|---|
| Architecture | 80 | 80 | 8% | ขอบเขต API/worker/sandbox เดิม เหมาะกับขนาดทีม |
| โครงสร้างโค้ด | 68 | 72 | 5% | เพิ่ม service/query/alerts และ ADR; โมดูลใหญ่ยังต้องทยอยแยก |
| อ่าน Excel/CSV | 80 | 80 | 10% | รอบนี้ไม่ได้ขยาย parser หรือวัด real ERP ใหม่ |
| Mapping/unknown format | 74 | 74 | 12% | ไม่เพิ่มคะแนนความฉลาดจาก mock test |
| Queue/worker/recovery | 82 | 88 | 10% | kill/recovery gate และ repro ARQ ผ่าน; ยังไม่มี cross-host failover |
| วงจรไฟล์ | 58 | 86 | 8% | ปิดช่อง cancelled/stale mutations และตรวจ race |
| แก้ JSON/เวอร์ชัน | 50 | 88 | 12% | commit, head lock, unique index และ persistent selection มี regression |
| ความรู้/หลักฐาน | 78 | 85 | 6% | retention pin และ recheck ป้องกันหลักฐานที่ยังใช้ |
| ผู้ช่วย | 78 | 78 | 5% | ไม่เปลี่ยน scope หรือวัดคำตอบผู้ช่วยเพิ่ม |
| Security | 70 | 72 | 5% | restore path/integrity และ isolation ดีขึ้น; ยังไม่ได้ pentest |
| Deploy/upgrade | 60 | 80 | 7% | profile/config gates + isolated migration/restart/restore; ยังไม่ใช่ server rollout |
| Backup/restore | 45 | 86 | 4% | ยืนยัน checksum และคืนข้อมูล/ไฟล์/checkpoint ได้จริง; ยังต้อง off-host drill |
| Logs/alerts | 73 | 82 | 3% | actionable alerts + rule validation; receiver ยังต้องตั้ง |
| UX | 68 | 76 | 3% | stale edit, back link และหน่วยชัดขึ้น; ไม่ได้เก็บทุกหน้า/ทุกขนาดจอ |
| Tests/CI | 68 | 86 | 2% | gates ผ่านและมี regression/PG repro; browser checks ยังเป็น manual |
| **รวมถ่วงน้ำหนัก** | **69.19 → 69** | **81.22 → 81** | **100%** | **พร้อมเริ่ม pilot แบบควบคุม เมื่อทำ pre-deploy ครบ** |

## ก่อนเริ่ม pilot จริง

1. สำรองข้อมูลเดิมและซ้อม migration; ตรวจ duplicate versions หากพบให้ตัดสินใจแก้รายกรณี ไม่ข้าม migration guard
2. ใช้ production database/storage/Redis แยก; ตั้ง OIDC/TLS, secret ที่จัดเก็บปลอดภัย, capacity profile ตาม server และ import เฉพาะความรู้ที่อนุมัติ
3. ตั้ง backup ไปนอกเครื่อง, retention, Prometheus/Alertmanager ผู้รับจริง และทดสอบรับ alert
4. เริ่ม ERP จริงที่ปกปิดข้อมูลแล้วจำนวนเล็ก ๆ โดยมี JSON schema และผู้เชี่ยวชาญยืนยันผล เก็บ holdout format ใหม่โดยไม่ใช้เป็นข้อมูลเรียนก่อนวัด
5. ใช้ human review สำหรับความหมายที่ไม่ชัด; วัด wrong-but-confident, คำถามที่ถามเกินจำเป็น, เวลา และต้นทุนจริง ก่อนค่อยเพิ่มจำนวนไฟล์

ข้อจำกัดที่ยังไม่ควรตีความว่าปิดแล้ว: legacy override ที่ไม่มี schema binding, backup SQLite ที่มี workflow sidecar แยกต้องตรวจความครบถ้วนเพิ่มก่อนใช้เป็น DR หลัก, retention ยังไม่ใช่ atomic DB+storage deletion, external notification delivery, large-dataset transformations, tenant security review และ real-model/real-ERP holdout

## ความปลอดภัยของข้อมูลและสถานะ Git

ไม่ได้ใช้ API เสียเงิน ไม่ย้ายความรู้เข้าหรือออก production ไม่ลบข้อมูล dev และไม่ได้ restart/ปรับทรัพยากร dev ตรวจ container IDs เดิมทั้ง 9 ตัวตรงกับก่อนเริ่ม และ `/ready` ของ dev ยัง `ok`, โมเดลเดิมยัง `deepseek-flash`

หลังตรวจ flow และหน้าเว็บแล้วหยุดเฉพาะ `forma-repro-auditfix` เพื่อคืนทรัพยากร ข้อมูลและ volume ทดลองยังเก็บไว้ให้เปิดทดสอบซ้ำได้

Git/release: **ผ่านเกณฑ์สำหรับ commit/push ชุดนี้; ผล hosted CI หลัง push ต้องตรวจจาก GitHub Actions แยกจาก local gates ในรายงานนี้**
