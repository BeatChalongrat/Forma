# Testing strategy and quality gates

> **สรุปภาษาไทย** — ใช้ `tools/quality.py` เป็นคำสั่งกลางสำหรับ static checks, unit/integration,
> browser E2E, multi-sheet evaluation และ Docker runtime/load/recovery โดยเก็บผลกับ exit code สำหรับ pipeline
> ทุกชุดเป็นข้อมูลทดลองและไม่ใช้ API เสียเงิน ชุดจำลองพิสูจน์กลไกของระบบ ไม่ใช่คะแนนความแม่นของ DeepSeek จริง

## Automated entrypoints (Windows / CI)

รันจาก repo root หลังติดตั้ง backend dev dependencies และ `npm ci` ใน `frontend` แล้ว:

```powershell
# เตรียม Chromium ครั้งแรก
cd frontend
npx playwright install chromium
cd ..

backend\.venv\Scripts\python tools/quality.py backend
backend\.venv\Scripts\python tools/quality.py frontend
backend\.venv\Scripts\python tools/quality.py eval
backend\.venv\Scripts\python tools/quality.py browser
backend\.venv\Scripts\python tools/quality.py critical

# ต้องเปิด Docker Desktop: สร้างและลบเฉพาะ stack ทดลอง qa-<random>
backend\.venv\Scripts\python tools/quality.py runtime --jobs 300

# ทุกชุดตามลำดับ หยุดเมื่อพบด่านที่ไม่ผ่าน
backend\.venv\Scripts\python tools/quality.py release
```

Linux ใช้ `backend/.venv/bin/python` แทน หรือ `make qa-release`.
บน Linux ติดตั้ง browser dependencies ด้วย `npx playwright install --with-deps chromium`.
ผลอยู่ใน `test-results/<timestamp>-<suite>/` หรือระบุ `--out <โฟลเดอร์ใหม่>`;
ไม่เขียนทับผลเก่า มี `summary.json`, stdout/stderr logs, pytest JUnit, coverage XML,
browser screenshots/Playwright traces และ downloaded JSON สำหรับตรวจค่าจริง.
เปิด trace ด้วย `npx playwright show-trace <path-to-trace.zip>` จาก `frontend`.

| คำสั่ง | สิ่งที่ตรวจ / เกณฑ์ผ่าน |
|---|---|
| `backend` | ruff, format, mypy และ pytest ทั้งชุด; line ≥ 85% และ branch ≥ 80% แยกกัน; concurrent requests, ownership, remembered answers, stale versions, durable jobs และ migration contracts |
| `frontend` | lint, Vitest/V8 (รวมไฟล์ production ที่ยังไม่มีเทส), component/hook/pure-function tests, coverage floors และ production build |
| `critical` | ชุดความเสี่ยงสำคัญจาก catalogue → frontend coverage → Chromium → ตรวจหลักฐานทุก flow; ไม่มีรายงาน, skipped, failed หรือรายงานซ้ำ ให้ไม่ผ่าน |
| `eval` | synthetic baseline ต้องไม่ถอย; multi-sheet, question/knowledge, cold/warm/drift, graph roles และ relationship regression ด้วย scripted providers |
| `browser` | ใช้ Chromium กับ API/เว็บชั่วคราว: chat drafts, เปลี่ยนหน้า, offline/reconnect, stop, branching, แนบไฟล์บางส่วน; review → reload/back → confirm → download ตรวจ 12 SKU และ VAT ทุกแถว |
| `renderer` | ใช้ sandbox image ที่ build แล้ว (`--report-image`) ตรวจ 11 กรณี Office/PDF จริง รวมตัวเลขติดลบ/ทศนิยมใหญ่/ภาษาไทย/หน้าว่าง/ข้อมูลไม่ครบ; Docker ไม่มี network |
| `runtime` | PostgreSQL + Redis/arq + MinIO + sandbox จริง: upload → review → แข่งแก้ข้อมูล → approve → download → restart → backup/restore ไปฐาน/bucket ใหม่; 300 งาน, kill worker ขณะทำจริง, ตรวจจำนวน/ค่า/ไม่มี mapping ซ้ำ; render DOCX/PPTX/PDF และตรวจ activity receipt |
| `release` | รวมทั้งหมดข้างบน; Docker image build เป็นส่วนหนึ่งของ runtime provisioning |

`runtime` เรียก `renderer` โดยใช้ image ที่เพิ่ง build ของ stack ทดลองให้เอง ไม่ต้องเตรียม image แยก.
พอร์ต 8081, 19000 และ 19001 ต้องว่างสำหรับ repro; runner จะปฏิเสธเมื่อพอร์ตหรือทรัพยากรไม่พร้อม.
ชุด runtime นี้ใช้ repro ที่ทำงานพร้อมกัน 2 งาน และ workbook สังเคราะห์ 12 รายการต่อไฟล์
เพื่อพิสูจน์ queue/recovery และตรวจค่าทุกไฟล์ ไม่ใช่ benchmark 8 workers หรือการคาดเวลา DeepSeek จริง.
Docker ต้องมี RAM ให้ stack ประมาณ 8 GiB รวมกับพื้นที่สำรอง 1.5 GiB และหน่วยความจำที่ container อื่นใช้อยู่;
runner ตรวจความพร้อมก่อนสร้าง stack. เครื่อง CI ที่มี RAM 7 GiB ต้องเลือก runner ที่ใหญ่ขึ้น.

### CI และ release pipeline

`.github/workflows/ci.yml` รันทุก PR/push main: backend (รวม Postgres/MinIO แยก), frontend,
browser, eval, migration/config guards, Docker builds และ renderer จริงแบบไม่มี network.
Job `quality-gate` จะผ่านต่อเมื่อทุก job ผ่านจริง รวมกรณี dependency ถูก skip ด้วย.
Job `critical-flows` อ่าน JUnit และ browser receipts จาก artifacts ของ CI run เดียวกัน
โดยไม่เรียกโมเดลซ้ำ และเป็น dependency ของ `quality-gate`.
ตั้ง required status check เป็น `quality-gate` ใน GitHub branch protection เมื่อต้องการบังคับก่อน merge
(ไฟล์ workflow ไม่สามารถเปิด branch protection ให้เอง).

### Coverage และ critical flows: อ่านตัวเลขให้ถูก

ค่าขั้นต่ำอยู่ใน [`quality/coverage-policy.json`](../quality/coverage-policy.json).
Backend ใช้ coverage.py แบบ branch และตรวจ **line กับ branch แยกกัน** ผ่าน
`tools/coverage_gate.py`; ค่า combined ของ pytest-cov ใช้แทนสองค่านี้ไม่ได้.
รายงานมี XML, JSON และ HTML (`backend-coverage/index.html`).

Frontend เปลี่ยนจาก Node test runner เป็น Vitest โดยเก็บ assertion เดิม และเพิ่ม React DOM/hook tests.
`vitest.config.ts` รวม `src/**/*.ts` และ `src/**/*.tsx` ทั้งหมด ยกเว้น type declaration;
ไม่ตัด pages ที่ยังไม่มีเทสออกเพื่อทำให้ตัวเลขสูงขึ้น. รายงาน V8 มี JSON, LCOV และ HTML
(`coverage/index.html`) ส่วน browser E2E แยกหลักฐานออกมา ไม่เอามาบวกเป็นเปอร์เซ็นต์ V8.

Frontend ทั้งระบบยังมีช่องว่าง: floor line 29%, branch 22% เป็น **เกณฑ์กันถอยชั่วคราว**
ไม่ใช่เป้าหมายความพร้อม production. ส่วน auth, draft store/hook, chat recovery, review navigation,
value input และ explicit confirmation มี floor รายไฟล์ line 90%, branch 85%.
หน้า upload, data editor, review journey, delivery และ assistant มี floor แยกรายไฟล์ตาม
coverage ที่วัดได้จริงใน policy เพื่อไม่ให้เทสหน้าอื่นชดเชยส่วนสำคัญที่ถอยลง.
เมื่อเพิ่ม coverage ให้ยก floor ตาม ห้ามลด floor เพื่อทำให้ CI เขียว.

### Frontend page journeys

`frontend/tests/*.page.test.tsx` mount หน้าจอ production พร้อม React Query, router,
draft hooks และองค์ประกอบลูกจริง แล้วจำลองเฉพาะ API boundary. `pageHarness.tsx` ปิดการเรียก
`fetch` จริงและให้เทสล้มเหลวถ้ามี request หลุดออกมา; ไม่ต้องเปิด dev และไม่อ่านข้อมูล dev.
เฉพาะ DOM scrolling/viewport ใช้ตัวแทนของ jsdom; pixel/layout และการเชื่อม backend จริง
ต้องดู browser E2E แยก ไม่ควรอ้างว่า component tests พิสูจน์ server recovery ได้ด้วยตัวเอง.

| ไฟล์เทส | พฤติกรรมที่ต้องรักษา |
|---|---|
| `Upload.page.test.tsx` | ไฟล์ไม่รองรับ, วันที่ว่าง, แบ่งชุดส่ง, ป้องกันกดซ้ำ, idempotency หลัง response หาย, เพิ่มต่อรอบเดิม, ปุ่มกลับ |
| `UploadStatus.page.test.tsx` | จำนวนรับเข้าแยกจากประมวลผล, action ต่อไฟล์, ZIP เฉพาะไฟล์พร้อม, resume งานค้าง, สิทธิ์เลือกลูกค้า |
| `ReviewJourney.page.test.tsx` | เก็บคำตอบ/ย้อนกลับ/พักแล้วเปิดต่อ, ยังไม่ทราบไม่ใช่อนุมัติ, draft ชนกัน, preview ผิด/ล่ม, สิทธิ์, เปลี่ยนคำตอบต้องตรวจใหม่ |
| `DataEditor.page.test.tsx` | แก้/วางตัวเลขตาม locale, preview ก่อนบันทึก, validation, retry, 403/เวอร์ชันชน, เก็บ draft ในหน้า, audit note, กลับหน้าตรวจ, published อ่านอย่างเดียว |
| `ReviewDelivery.page.test.tsx` | ยืนยันข้อมูลก่อน export, reviewer/approver แยกกัน, ข้อมูลผิดห้ามส่งออก, sample โหลดไม่ได้, dataset ใหม่ต้องยืนยันใหม่, retry job เดิม |
| `Assistant.page.test.tsx` | stream/reconnect/reset, ออกจากหน้าไม่ยกเลิก server job, stop/retry request เดิม, draft ใหม่ไม่ถูกล้าง, attachment ล้มเหลว, archived/403, branching, clarification |

รันเฉพาะหน้าได้จาก `frontend`: `npx vitest run tests/Upload.page.test.tsx`
หรือรันทุกหน้าและวัดทั้งโปรเจกต์ด้วย `npm run test:coverage`.

[`quality/critical-flows.json`](../quality/critical-flows.json) ผูกความเสี่ยงกับชื่อเทสจริง:
transaction rollback/commit acknowledgement, concurrent edits, invalid edits, tenant/role isolation,
OIDC/PKCE, worker kill/restart, provider retry/budget/stop, stale preview, explicit approval,
draft persistence, browser navigation/reconnect และตรวจค่าของ JSON ที่ดาวน์โหลด.
`critical-flows.json` ในผลรันทดสอบบอกว่า flow ใดผ่านและอ้างอิง test ใด;
31/31 หมายถึง **31 flow ที่กำหนดไว้** ไม่ใช่ครอบคลุมความเป็นไปได้ทุกแบบ 100%.
ตัวตรวจหลักฐานมี regression tests ป้องกันผลเขียวปลอมเมื่อข้อมูลหาย/ซ้ำ/skip/ล้มเหลว.

เพิ่ม feature หรือแก้บั๊ก: เพิ่ม regression ที่ล้มเหลวก่อนแก้ → ผูก flow ใน catalogue ถ้าเป็นความเสี่ยงสำคัญ
→ รัน `critical` → รัน full suite ของส่วนที่แก้ → แนบ artifacts. ก่อน release ยังคงต้องผ่าน
`runtime --jobs 300` ใน Docker แยก ซึ่งทดสอบ PostgreSQL/Redis/MinIO, backup/restore และ renderer จริง.
ชุด critical ไม่ทดแทน load test และโมเดลจำลองไม่ใช่การวัดความแม่นของโมเดลจริง.

`.github/workflows/runtime.yml` เป็นชุดหนัก กด **Run workflow** ก่อน release หรือเรียกผ่าน `workflow_call`
จาก deployment pipeline ได้ เป็น 300 งานเสมอและคืน exit code nonzero เมื่อขาดงาน/ข้อมูลผิด/recovery ไม่ถูกทดสอบ.
ไม่เปิด schedule หรือ deployment อัตโนมัติ. การรัน local `--jobs` ต่ำกว่า 300 ใช้ debug ได้ แต่ไม่แทน release gate.
ช่อง `runner` ใน Run workflow / workflow_call เลือก label ของ runner ที่มี Docker และ RAM เพียงพอได้
(ค่าเริ่มต้น `ubuntu-latest`; ตรวจขนาด runner ตามสิทธิ์ของ repository ก่อนใช้งาน).
CI สร้าง MinIO/mc จาก source ทางการที่ pin commit ใน `deploy/testing/Minio.Dockerfile`
เพราะ image เดิมถูกถอนจาก public registry; การสร้างนี้อยู่เฉพาะ runner ไม่เปลี่ยน storage ของ dev.
การบังคับ required checks บน private repository ต้องมี GitHub plan ที่รองรับ branch protection/rulesets.

### การแยกข้อมูลและค่า API

- Runner ไม่รับ database/storage/provider/key ของ dev จาก environment และสร้าง temp state ใหม่;
  browser ใช้พอร์ต 18171/15174 ถ้าไม่ว่างจะหยุด ไม่ไปใช้ server ที่มีอยู่.
- Unit/integration ต้องทดสอบทั้ง mock และการเลือก provider จึงใช้ fake transports ร่วมกับ
  `FORMA_TEST_BLOCK_EXTERNAL=1`: บล็อก external TCP ผ่าน Python socket ซึ่งเป็นเส้นทาง HTTP ของ LLM;
  loopback และ Unix socket ใช้สำหรับ test services (ตัว guard นี้ไม่ใช่ firewall ของระบบปฏิบัติการ).
  Runtime/browser ใช้ mock-only lock ของระบบ ไม่มีการถอดรหัส key หรือเรียกโมเดลจริง.
- CI Postgres/MinIO เป็น service ชั่วคราว หากส่ง `FORMA_TEST_PG_URL` / `FORMA_TEST_S3_ENDPOINT` ให้ runner เอง
  ต้องยืนยัน `FORMA_TEST_SERVICES_DISPOSABLE=1`. **ห้ามชี้ฐาน dev/prod**: integration fixture ล้าง public schema.
  ถ้าไม่กำหนด service URL เทสเหล่านี้จะ skip อย่างชัดเจน; Docker runtime และ CI เป็นด่านตรวจของจริง.
- Runtime ใช้ repro ownership token/labels และ volume/network แยก `forma-repro-qa-*`; เก็บ log ก่อน destroy แม้เทสล้ม.
  `--capacity ci` เป็นเพียง resource cap ของ stack ทดลอง ไม่แก้ config dev/server และยังตรวจ RAM ที่เหลือก่อนเริ่ม.
  หากเครื่องไม่พอให้รันบน CI/server ทดสอบ ไม่ลดจำนวน assertions เพื่อให้ผ่าน.
- ไม่ใช้ผลโมเดลจำลองยืนยันความสามารถเข้าใจ unknown ERP. Paid live evaluation เดิมยังต้องสั่งแยก
  ด้วย corpus/schema ที่มีเฉลยตรวจโดยคน ดู EVALUATION.md; ไม่อยู่ใน default CI หรือ release runner นี้.

### Multi-sheet และ upgrade regression

`test_multisheet_contract.py` ตรวจ workbook เดิมเมื่อสลับลำดับชีต เปลี่ยนชื่อชีต เพิ่ม Notes/ชีตว่าง
จนเป็นสิบชีต และซ่อน Stock: ทุกกรณีตรวจ SKU/ราคา/จำนวนแถว/ความสัมพันธ์คลัง ไม่ใช่แค่ API ตอบ 200.
**ชีตซ่อนที่อาจจำเป็นต้องมีคำถามให้ตรวจ ไม่ถือว่าใช้ได้เองเสมอ**; เนื้อหาใน Notes ไม่ใช่คำสั่งให้โมเดล.
กรณี ambiguous joins, stale/cross-round knowledge และ semantic conflicts อยู่ใน regression files ที่ eval gate เรียกด้วย.
`test_upgrade_preservation.py` เริ่มจาก schema revision ก่อนหน้า ใส่ลูกค้า รอบ ไฟล์ mapping คำถาม/log
แล้ว upgrade head สองครั้ง ตรวจค่าทุกคอลัมน์เดิมและ byte ของไฟล์ต้นทางยังเหมือนเดิม.

รายงาน PDF/PPTX/DOCX ตรวจเลข ตาราง หน้า และการ render จริงอัตโนมัติได้ แต่ความสวยและความหมายทางธุรกิจ
ยังต้องมี visual/human review; artifacts เก็บภาพไว้ให้ตรวจ ไม่อ้างว่าคนตรวจแล้วโดยอัตโนมัติ.

## 1. Layers

| Layer | Tool | Gate | Where |
|---|---|---|---|
| Static | ruff (lint + format), mypy strict, oxlint, tsc strict | zero errors | `make lint typecheck`, CI |
| Unit | pytest | pass | `backend/tests/test_thai.py`, `test_engine.py`, `test_dsl.py`, `test_fingerprint.py`, `test_heuristics*.py`, `test_policy.py`, `test_verdict.py`, `test_healing.py`, `test_anomalies.py`, … |
| API / integration | pytest + FastAPI `TestClient` with `sync=true` jobs on an isolated SQLite + data dir (`isolated_env`) | pass; quality runner/CI: line ≥ 85%, branch ≥ 80%; direct pytest: combined ≥ 80% | `test_api.py`, `test_batches.py`, `test_workspace.py`, `test_learning.py`, `test_calibration.py`, `test_foresight.py`, `test_schema_gate.py`, `test_sandbox.py`, … |
| Infrastructure | real Postgres and MinIO in CI (`FORMA_TEST_PG_URL`, `FORMA_TEST_S3_ENDPOINT`) | pass | `test_postgres.py`, `test_minio.py` |
| Migrations | upgrade/downgrade from scratch; models vs Alembic agree | pass | `test_migrations.py` |
| Failure injection | dead worker, storage error, DB error, LLM timeout, breaker | nothing partial visible; fallback routes to review | `test_failure_injection.py`, `test_job_recovery.py` |
| Accuracy | `forma eval` on the synthetic corpus vs `data/eval_baseline.json` | identity 1.00, field ≥ 0.996, critical 1.00, drift 9/9, no failed variants | `make eval-gate`, CI |
| Input fidelity | adversarial OOXML/CSV fixtures | pass | `test_ingestion_readiness.py`, `test_contract_readiness.py` |
| Configuration drift | `.env.production.example` regenerated and compared | identical | CI |
| Performance | `forma bench --files 6 --rows 2000` in CI; full 300 × 100K locally | reported; the full run is documented in PERFORMANCE.md | CI, `make bench` |
| Build | both Docker images, compose config | build | CI |
| Frontend | tsc (app + tests), oxlint, vite build, Vitest/V8, React DOM/hook tests | zero errors; coverage floors in `quality/coverage-policy.json` (warnings tracked) | `make qa-frontend`, CI |

## 2. Conventions

* Tests live in `backend/tests/`; `conftest.py` isolates the database and data directory per test via
  environment variables and clears every `lru_cache` (`isolated_env`). Reuse it.
* API tests build workbooks with openpyxl in `tmp_path` (`tests/test_api.py::make_workbook`) and run jobs with
  `sync=true`; never depend on wall-clock ordering.
* Thai parsing is table-driven (`test_thai.py`); add a row when touching `engine/thai.py`.
* A fake provider (`_FakeProvider` with `complete_json`) exercises the real LLM branches without network;
  the guard and the fallback are tested with injected output.
* Every bug fix starts with a test that reproduces it (the regression test must fail without the fix).
* Every new policy rule, verdict rule, learning rule or proposal has a unit test from fabricated reports and
  at least one end-to-end API test.
* Coverage is a floor, not a target: untested branches in services are not accepted.

## 3. What the evaluation harness measures

| Mode | Command | Measures | Gate |
|---|---|---|---|
| Heuristic | `forma eval --corpus ../data/synthetic --baseline ../data/eval_baseline.json` | mapper + engine vs ground truth on 30 synthetic variants and 3 sequences | CI, must not drop |
| AI-refined | `forma eval --llm` | the draft refined by the configured provider; fallbacks counted | report (needs a key) |
| Correction replay | `forma eval --replay` | later rounds of a sequence scored without and with the alias learned from round 0 | report |
| Real files | `forma eval --corpus ../data/real --index` | the same metrics on reviewed real exports | pending files |

Details: [EVALUATION.md](EVALUATION.md), [adr/041](adr/041-evaluation-on-real-files-and-with-ai.md).

## 4. Manual / exploratory checks before a release

[manual_test_files/](../manual_test_files/README.md) holds ERP-style files for every failure the system should catch, with
the JSON each should produce and what a person should see.

1. Seeded demo: Home lists C900–C902 sequences; C902 round 4 shows the wrong-customer verdict; a renamed-layout
   round shows the one-click accept; accepting it approves and audits.
2. Bulk upload of 5 mixed files: known customers publish, an unknown layout waits, assign continues it, the
   zip contains one JSON per file.
3. Review page: answer a VAT question, preview the impact, save, see the new version re-evaluated.
4. Data Editor: fix an invalid record, see the gate open; roll back; export.
5. Settings: paste a key, test it, add a backup, break the primary, see the backup take over; edit the policy;
   apply a calibration proposal; promote a learned alias.
6. Assistant: ask about a file, get cited evidence; ask for a work-round comparison chart; ask to accept a
   verdict and confirm the card; attach a picture and a file; have code make a chart and a document, pick part of it
   and change it, bring back the old version; tell it something to remember, then find it in a new chat.
7. A round's life: upload and watch the navy card across pages; cancel a round and reopen it; move one to another
   customer; replace one with a corrected file and check the zip; park a question and bring it back.
8. System health: storage used, last retention pass and last backup show real values. After changing Postgres or
   the image, restore a backup into an empty stack (RUNBOOK, ADR-054).
9. Both themes, 390 px width, Thai and English.

## 5. Definition of done (per change)

`make check` green · `make eval-gate` green · tests added for new behaviour · docs/ADR updated in the same
commit · `.env.production.example` regenerated when a setting changed · migration shipped when a model
changed · frontend `tsc` + `oxlint` + `npm test` + `build` green · conventional commit message.


### Release qualification และการกู้หน้าเมื่อโหลดไม่สำเร็จ

เรียก `.github/workflows/release.yml` ก่อนปล่อยรุ่นจริง: บังคับ CI + runtime บน commit เดียวกัน
แล้ว `tools/release_gate.py` ตรวจผล mock ทุกชุด, run/attempt, working tree ที่ไม่เปลี่ยน และบันทึก image IDs
พร้อม hash หลักฐานใน manifest ไม่มี deploy อัตโนมัติ ดู [checklist](RELEASE_CHECKLIST.md).
`tools/quality.py` เก็บ source ก่อน–หลังทุกชุด; local dirty checkout ทดสอบได้ แต่ใช้ผ่าน release gate ไม่ได้.

Regression เพิ่ม: initial 403/404/503, rows โหลดไม่ได้ต้องไม่ดูเหมือนไฟล์ว่าง, refetch ล้มแต่ร่างยังอยู่,
retry ไม่เปลี่ยนหน้า, ปุ่มกลับยังชี้ไฟล์เดิม, account/mapping/round/settings โหลดล้ม และไม่เรียก mutation แทน retry.
คำสถานะหน้าแก้ข้อมูลแยก “ยังไม่บันทึก”, “ข้อมูลเวอร์ชันที่บันทึกแล้ว”, “อนุมัติแล้ว—พร้อมดาวน์โหลด”.
`frontend-load-recovery` และ `release-evidence-integrity` อยู่ใน critical catalogue แล้ว.
