# แชตผู้ช่วย: การทำงานต่อและกู้คืนงาน

งานนี้เปลี่ยนแชตให้ส่งงานเข้าคิวที่บันทึกในฐานข้อมูล เว็บเป็นผู้ติดตามผล ไม่ใช่เจ้าของอายุงาน การปิดหน้าเว็บจึงไม่ใช่คำสั่งหยุดงาน ต้องกด **หยุดคำตอบ** จึงจะยกเลิก

## ก่อนและหลัง

| ส่วน | ก่อน | หลัง |
|---|---|---|
| งานผู้ช่วย | อยู่กับโปรเซส API | มี `assistant_message` ใน durable outbox และคิว `arq:assistant` แยกจาก mapping/report |
| เซิร์ฟเวอร์หยุด | ประวัติอยู่ แต่งานในหน่วยความจำอาจหาย | lease หมดอายุแล้ว worker ใหม่รับต่อจาก LangGraph และใบรับผลของแต่ละขั้น |
| Provider หลุด | คำตอบอาจขาด | เก็บขั้นที่สำเร็จไว้ ลองเฉพาะขั้นที่ยังไม่เสร็จภายในงบเดิม |
| เน็ตผู้ใช้หลุด | ต้องแยกให้ออกว่างานยังอยู่หรือไม่ | อ่านคำขอเดิมด้วย request ID และแทนข้อความด้วย snapshot ไม่ส่งคำถามซ้ำ |
| ร่าง/คำถามกลับ | ส่วนหนึ่งอยู่ใน component | เก็บร่างและตัวเลือก พร้อม revision ป้องกันแท็บหนึ่งเขียนทับอีกแท็บ |
| แก้คำถาม/ตอบใหม่ | เสี่ยงเสียบริบทเดิมหรือทำงานซ้ำ | เปิดบทสนทนาทางเลือก เก็บต้นฉบับและไฟล์เดิม ตอบใหม่จากหลักฐานเดิมโดยไม่เรียกเครื่องมือทำรายการซ้ำ |
| บทสนทนายาว | เห็นเฉพาะช่วงล่าสุด | แนบคำถามเก่าแบบข้อความต้นฉบับในขอบเขตจำกัด และใช้ `conversation_history` ค้นรายละเอียดที่ไม่ได้แนบ |
| การแสดงผล | ข้อความทั่วไปและสถานะในหน้าแชต | Markdown/ตาราง, เวลาและปุ่มใต้คำถาม, ผลงาน/แหล่งอ้างอิงแยกส่วน, ป้ายงานเบื้องหลังและคำตอบใหม่ |
| Feedback | ไม่มีช่องทางเฉพาะรายคำตอบ | บันทึกมีประโยชน์/ไม่ถูกต้อง/เข้าใจยาก พร้อมหมายเหตุ ไม่แก้ความรู้ mapping อัตโนมัติ |

## Recovery หกส่วน

1. **คิวถาวรแยกงาน:** API บันทึกคำถาม, turn, job และ run ใน transaction เดียวแล้วจึงส่ง Redis; outbox ส่งใหม่ได้เมื่อ Redis สะดุด
2. **Checkpoint:** LangGraph บันทึกสถานะกราฟ; `AssistantRun.state.steps` บันทึกชื่อขั้น, fingerprint ของ arguments, จำนวนครั้ง และผลลัพธ์ การ replay ใช้ผลเดิมของขั้นที่สำเร็จ
3. **ป้องกันผลซ้ำ:** ผลฐานข้อมูลของเครื่องมือกับ receipt commit ร่วมกัน มี lease token ป้องกัน worker เก่าบันทึกหลังเสียสิทธิ์ claim; request ID เดิมไม่สร้างคำถามใหม่
4. **แยกเหตุขัดข้อง:** browser หลุดให้ติดตามต่อ, provider หลุดให้ลองขั้นค้างใหม่, worker ตายให้ stale-job recovery รับต่อ; `Stop` ยกเลิก job และสิทธิ์ commit
5. **ขีดจำกัดเดิมข้าม restart:** เก็บจำนวน request, attempts, retry time, วันหมดอายุ และยอดใช้โดยประมาณใน DB; workflow ที่ไม่เข้ากันให้พักแทนการ replay ฝืน
6. **Fault injection:** มีการทดสอบฆ่าโปรเซสหลังสร้างไฟล์แล้วเริ่มโปรเซสใหม่, worker แข่งกันรับงาน, provider หลุด, stop ระหว่าง retry, budget, draft conflict, ownership และ browser reconnect

## ขอบเขตที่ต้องเข้าใจ

- **ต่อจากขั้นที่บันทึกแล้ว ไม่ใช่ต่อ token ภายในโมเดลตัวเดิม** ถ้า provider stream ขาด ขั้นเขียนคำตอบอาจต้องเรียกใหม่ ข้อความบางส่วนอาจเปลี่ยน แต่ขั้นค้นข้อมูล/สร้างไฟล์ที่มี receipt แล้วไม่ต้องทำซ้ำ
- ไม่อ้างว่า provider รับ request แบบ exactly-once หากตอบแล้วแต่การเชื่อมต่อขาดก่อนรับผล อาจมีค่าใช้จ่ายของ request ที่ไม่ทราบผล การจองจำนวน call จะไม่คืนยอดให้กรณีนี้
- Cost ใช้ usage ที่ได้รับกับอัตราที่ระบบรู้ **ไม่ใช่เพดาน billing ของผู้ให้บริการ** จำนวน calls, เวลา และ attempts เป็นขีดจำกัดเสริม ต้องดูยอด provider เมื่อนำไปใช้จริง
- ไม่ย้อนกลับการอนุมัติหรือไฟล์ที่ส่งออกสำเร็จแล้วเมื่อกด Stop หรือแตกบทสนทนา และไม่ให้ feedback ไปเปลี่ยนกฎการจับคู่อัตโนมัติ
- สิทธิ์งานมาจาก principal ตอนรับคำขอ และตรวจเจ้าของ/ขอบเขตแชตตอนทำต่อ; ยังไม่มีการ introspect การเพิกถอนสิทธิ์จาก IdP ระหว่างงานแบบทันที
- เก็บร่างคำถามที่ยังไม่ส่งในเครื่องได้เฉพาะแชตปกติ ไฟล์ที่ยังไม่อัปโหลดต้องเลือกใหม่หลังรีโหลด ส่วนไฟล์ที่อัปโหลดแล้วเก็บ ID ไว้ ไม่อัปโหลดซ้ำโดยตั้งใจ
- Incognito ไม่เขียนร่างลง localStorage; ฝั่ง server ใช้วงจรล้างแชต incognito เดิม รวม draft/feedback/run/job ที่เพิ่มใหม่ ไม่ได้หมายถึงไม่มีข้อมูลชั่วคราวระหว่างกำลังทำงาน
- ข้อความเก่าที่แนบอัตโนมัติจำกัด 200 คำถาม/16,000 ตัวอักษร บวก 8 คู่ล่าสุด พร้อมบอกว่าละอะไรไป ข้อมูลเก่าเป็นประวัติ ไม่ใช่หลักฐานว่าข้อมูลปัจจุบันยังเท่าเดิม
- การ kill ระหว่างเขียน object และ commit อาจเหลือ object ที่ไม่มี DB อ้างอิง ต้องใช้นโยบาย storage cleanup; ไม่แสดง object นั้นเป็นผลสำเร็จให้ผู้ใช้

## การนำขึ้นระบบ

1. สำรองฐานข้อมูลและ storage ตาม runbook เดิม
2. รัน migration ถึง `daa012345680` เพิ่ม `assistant_runs`, `assistant_chat_state`, `assistant_feedback`
3. Build/deploy API และ frontend พร้อม `assistant-worker` จาก Compose ชุดเดียวกัน ใช้ DB/Redis/storage ของ environment เดียวกัน
4. ตรวจ health ของ `arq:assistant` และส่งข้อความทดสอบด้วย mock ก่อนเปิด provider จริง
5. ปิดแท็บระหว่างงาน เปิดใหม่ ตรวจคำตอบและประวัติงาน; ทดสอบหยุด worker ใน staging แล้วตรวจว่ารับต่อได้

การแก้ไฟล์ครั้งนี้ไม่สั่ง migrate/rebuild dev ที่กำลังใช้อยู่ ไม่เปลี่ยน key และไม่ลด mapping concurrency 8 งาน

ค่าตั้งต้น:

| ตัวแปร | ค่า | ความหมาย |
|---|---:|---|
| `FORMA_ASSISTANT_WORKERS` | 2 | งานแชตพร้อมกันต่อ assistant worker container |
| `FORMA_ASSISTANT_RUN_MAX_CALLS` | 48 | จำนวน model requests ต่อข้อความ รวม retry |
| `FORMA_ASSISTANT_STEP_ATTEMPTS` | 3 | ครั้งสูงสุดต่อขั้นที่ยังไม่สำเร็จ |
| `FORMA_ASSISTANT_RUN_MAX_COST_USD` | 2 | หยุดเรียกเพิ่มเมื่อยอดประมาณที่บันทึกถึงค่านี้ |
| `FORMA_ASSISTANT_RUN_TIMEOUT_S` | 1800 | อายุคำขอนับรวมเวลารอคิวและ recovery |

คิวผู้ช่วยยังใช้ provider rate limit ร่วมกับงานอื่น การแยก worker ไม่ได้เพิ่ม RPM/TPM ของ key

เมื่อแก้ลำดับหรือความหมายของ journal steps ใน deploy ถัดไป ต้องทบทวน `PROTOCOL` และ compatibility; งานที่เก่ากว่าและไม่รองรับควรพัก พร้อมให้ผู้ใช้เริ่มคำขอใหม่หลังตรวจผลงานที่มีแล้ว

## คำสั่งตรวจด้วยโมเดลจำลอง

จาก `backend`:

```powershell
.venv/Scripts/python -m pytest tests/test_assistant_continuity.py tests/test_chat_stream.py tests/test_conversations.py tests/test_inspection_center.py --no-cov
```

จาก `frontend`: `npm test`, `npm run build`, `npm run lint`

Browser regression ใช้ `backend/tests/assistant_browser_server.py` ซึ่งสร้างฐานและ storage ชั่วคราวและบังคับ mock จากนั้นเปิด Vite ที่ port 15174 โดยตั้ง `FORMA_DEV_API=http://127.0.0.1:18171` และใช้ `frontend/tests/chat-continuity.browser.cjs` กับ Playwright/Chromium ที่ติดตั้งในเครื่อง ชุดนี้ไม่เปิดข้อมูล dev

การทดสอบ restart ของโปรเซสในชุดนี้ใช้ SQLite และ filesystem ชั่วคราว ส่วน PostgreSQL/Redis/ARQ จริงควรทำ deployment smoke test ใน staging ก่อนเปิดให้ผู้ใช้จริง ไม่ถือว่าการทดสอบ mock ยืนยันคุณภาพคำตอบของ DeepSeek

## ผลตรวจวันที่ 7 พฤศจิกายน 2026

- Recovery/continuity ใหม่ 14 กรณีผ่าน รวมการฆ่าโปรเซสจริงแล้วเปิดใหม่, worker แข่งกัน, provider หลุด, หยุดระหว่าง retry, budget และสิทธิ์เจ้าของแชต
- Browser 12 สถานการณ์ผ่านด้วย Edge + backend จำลองแยกฐานข้อมูล: ร่างหลัง reload, ออกจากหน้าแล้วงานทำต่อ, เน็ตหลุดแล้วติดตามคำขอเดิม, stop, คำถามกลับ, แตกบทสนทนา, feedback และอัปโหลดไฟล์สำเร็จบางส่วนแล้วกลับมาส่งต่อ ไม่พบ console error
- Frontend unit tests 48 กรณีผ่าน; production build ผ่าน; lint ไม่มี error แต่ยังมี warning เดิม และ build เตือนขนาด bundle
- ชุดแชต/รายงาน/ตรวจย้อนหลัง 54 กรณีผ่าน 8 skipped ตามเงื่อนไข environment
- Regression การถามกลับ, ค่าราคา, healing และ format memory/context 51 กรณีผ่าน; mapping workflow/harness/policy/learning/verdict/self-check อีก 75 กรณีผ่าน
- Recovery พร้อม relationship regression 40 กรณีผ่าน; ทดสอบการแตกบทสนทนาซ้ำและย้อนก่อนคำตอบเพิ่มแล้วผ่าน
- Deployment capacity/Compose/repro safety 25 กรณีผ่าน; Ruff และ formatting ผ่าน; mypy ผ่านทั้ง 168 source files; `git diff --check` ผ่าน

ชุด backend ทั้งหมดมี 1,057 กรณี รันตรวจเป็นหลายช่วง: ช่วงแรกหยุดที่ 4 failures (713 passed, 13 skipped), ช่วงที่เหลือ 342 passed, 2 failures จากนั้นแก้และรันชุดที่เกี่ยวข้องซ้ำจนผ่าน ผลข้างต้นมีกรณีซ้ำระหว่างชุด จึงไม่ควรบวกจำนวนเป็นยอดรวมใหม่ ไม่ได้อ้างว่าเป็น full-suite clean run ครั้งเดียวหลังแก้ทุกไฟล์

Coverage จากการรันเพียงช่วงท้ายอยู่ที่ 61.24% และไม่ผ่าน threshold 80% เพราะไม่ได้มี coverage ของช่วงแรกอยู่ในผลชุดนั้น จึงยังไม่ยืนยัน full-suite coverage gate; ไม่ได้ลด threshold หรือปิด gate ของ repository

สิ่งที่พบระหว่าง regression:

1. ปรับ test ของ stream ให้ตรวจ `started → snapshot → done` และเนื้อหาคำถามกลับที่บันทึกจริง แทนการคาด event `delta` ของ API รุ่นเดิม
2. แก้ fixture การอนุมัติให้ใช้ฐานข้อมูลแยกจริง แทน `session=None` เพื่อให้ตรวจ knowledge gate และ execution failure ได้ครบ โดยไม่ลดเงื่อนไขอนุมัติ
3. แก้ช่องว่างเดิมเมื่อ format context เปลี่ยนแล้วเสนอ mapping ใหม่แต่ได้ expression เดิม: ต้องตรวจค่าเทียบข้อมูลที่เผยแพร่ก่อนหน้าด้วย จึงไม่ข้ามสัญญาณ VAT/การเปลี่ยนหน่วยราคา; กรณีเปลี่ยนราคาทั่วไปยังผ่านตาม policy ได้

ไม่มีการเรียก provider เสียเงิน, commit, push, migrate ฐาน dev หรือ rebuild dev ในงานนี้ ปิด test servers ชั่วคราวแล้ว

## ตรวจ deployment หลัง migrate และ rebuild dev

ตรวจเพิ่มวันที่ 7 พฤศจิกายน 2026 บนเว็บ, API, PostgreSQL, Redis/ARQ และ S3 ของ dev จริง โดยใช้ผู้ใช้ทดสอบแยกและโมเดลจำลองเฉพาะงานของผู้ใช้นั้น ไม่เปลี่ยน API key หรือ provider settings:

| เหตุการณ์ | ผล |
|---|---|
| ออกจากแชตไปหน้าคู่มือ | งานจบในเบื้องหลัง แจ้งคำตอบใหม่ และกลับมาอ่านได้ |
| ตัด stream และการเข้าถึง API ของแท็บทดสอบ | worker ทำต่อจนจบ เมื่อเชื่อมต่อกลับ เว็บรับคำตอบเดิมเอง ไม่ส่งคำถามซ้ำ |
| SIGKILL assistant-worker และ restart API กลางคำตอบ | lease/recovery ปกติรับ job เดิมต่อ ขั้นอ่านและสร้างไฟล์ทำครั้งเดียว ขั้นเขียนคำตอบลองใหม่ แล้วจบ |
| reload หลัง recovery | คำตอบสำเร็จยังอยู่ ไม่มี turn หรือไฟล์เพิ่ม |

ทั้งสามงานมีหนึ่ง turn และหนึ่ง output ต่อคำถาม; งาน restart มี job attempts 2 และ step attempts อ่าน/สร้างไฟล์/เขียนคำตอบเท่ากับ 1/1/2 ทุกงานมี provider calls 0 และ cost 0 โดย log การเรียก LLM เดิมไม่เปลี่ยน

หลังทดสอบลบเฉพาะแชตและ output ของชุดทดลอง คืน worker config ปกติ ปิด proxy และตรวจ `/ready` ได้ `ok` ทุก service ที่มี healthcheck กลับมา healthy ตรวจ checksum ยืนยันข้อมูลธุรกิจ แชตเดิม ความจำ และ app settings ไม่เปลี่ยน เหลือ audit/activity/checkpoint ของการทดลองเพื่อการตรวจย้อนหลัง

นี่เป็นการทดสอบสามงานด้วย mock ไม่ใช่ load test, provider outage จริง หรือ host/database crash และยังคงข้อจำกัดเรื่องการกู้เป็นขั้นงาน ไม่ใช่ token-level resume

## ตรวจรวมก่อน commit

รัน backend ทั้งชุดครั้งเดียวหลังแก้ครบแล้วด้วย `pytest --cov=forma --cov-report=term-missing:skip-covered --cov-fail-under=85`: **1,044 passed, 13 skipped, 0 failures** ใน 1,078.15 วินาที Coverage **89.40%** ผ่านเกณฑ์ CI 85% ผลนี้ปิดช่องว่าง full-suite/coverage ของการตรวจแยกช่วงข้างต้น แต่ไม่ถือว่ากรณีที่ skipped ถูกทดสอบแล้ว

Frontend 48 tests ผ่าน, production build ผ่าน, lint ไม่มี error (ยังมี warnings เรื่อง React/Fast Refresh และขนาด bundle) Backend Ruff/format ผ่านทั้ง 292 ไฟล์ และ mypy ผ่าน 173 source files; deployment Ruff/format, capacity 8 tests, Compose 1 test, config example drift และ `git diff --check` ผ่าน ไม่มีการเรียก provider เสียเงิน
