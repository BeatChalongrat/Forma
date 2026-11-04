# FORMA — ทดสอบความต่อเนื่องของงานด้วยโมเดลจำลอง

วันที่ 4 พฤศจิกายน 2026 · ไม่เรียก DeepSeek หรือ API ที่คิดเงิน · ยังไม่ commit

## ขอบเขต

ทดสอบการรับงาน การกู้คิว การถือสิทธิ์ประมวลผล การหยุดงาน และการจำกัดคำขอ AI
ด้วย PostgreSQL 16, Redis 7, ARQ และ LangGraph จริงใน Docker แยกจากฐานข้อมูลใช้งาน
โมเดลเป็น HTTP server ภายในที่คืน draft แบบกำหนดตายตัว ไม่ใช่การวัดความฉลาดหรือความแม่นยำของ DeepSeek
ไฟล์ชุดหลักเป็น CSV 300 ไฟล์ ไฟล์ละ 12 แถว ใช้ลูกค้าแยกกันเพื่อให้แต่ละงานผ่านทางเสนอ mapping
storage ชุดนี้เป็น local volume ไม่ได้ทดสอบ MinIO ล่มหรือ Excel ขนาดใหญ่

## ก่อน–หลัง

คอลัมน์ “ก่อน” มาจากการตรวจโค้ดเดิม ไม่ใช่ผล benchmark 300 งานแบบควบคุมเงื่อนไขเท่ากัน

| เรื่อง | ก่อน | หลัง |
|---|---|---|
| รู้ว่า worker ยังทำงานอยู่หรือไม่ | ดูอายุงานจากเวลาเริ่ม งานช้าอาจถูกมองว่าตาย | heartbeat + lease แยกจากเพดานเวลาทำงาน |
| กู้ worker ที่หาย | ตรวจงานค้างตอนเริ่มบริการเป็นหลัก | ตรวจเป็นระยะ กู้เฉพาะ lease ที่หมด และจำกัดจำนวนครั้ง |
| ข้อความในคิวหาย/ส่ง Redis ไม่สำเร็จ | การส่งกับการบันทึกงานยังมีช่องว่าง ต้องอาศัย startup ช่วย | Job ใน DB เป็น outbox; ส่งซ้ำด้วย delivery ID เดิมของ attempt และซ่อม queue membership |
| ผลจาก worker เก่า | มี claim งาน แต่ยังไม่มี token ตรวจสิทธิ์ก่อน business commit | token + expiry fencing; worker ที่หมดสิทธิ์บันทึกผลไม่ได้ |
| crash ระหว่างเก็บผลกับเก็บขั้นตอน | stage receipt เขียนอีกธุรกรรม | ผลและ stage receipt commit พร้อมกัน |
| หยุด/หมดเวลา | ARQ รอ thread; ยกเลิก async task ไม่ได้ฆ่า thread ที่ค้าง | child process ต่อหนึ่งงาน, terminate แล้ว kill ถ้ายังไม่หยุด |
| ทำต่อหลังหยุด | ไม่มีสถานะหยุดและทำต่อครบวงจร | เก็บไฟล์/ขั้นตอนที่เสร็จแล้ว; กดทำต่อได้ ยกเว้นไฟล์ถูกแทนที่/ยกเลิก/ย้าย |
| หลาย worker เรียก AI | ขีดจำกัดระดับโปรเซสไม่เท่ากับขีดจำกัดรวม | Redis รวม concurrency/RPM/cooldown ของ endpoint + credential ทั้ง streaming และ structured |
| checkpoint พร้อมกัน | setup อาจรอ business transaction; saver และ ledger ใช้ connection ร่วมกัน | setup ก่อนรับงาน และแยก ledger connection จาก background checkpoint writer |

## ค่าที่ตั้งและความหมาย

ค่าปกติ: lease 90 วินาที, heartbeat ทุก 30 วินาที, ตรวจ recovery ทุก 15 วินาที,
ส่งคิวซ้ำหลัง 30 วินาที, เวลาทำงาน 1,800 วินาที ไม่รวมเวลาที่รอคิว
เพดานร่วม AI เริ่มต้น 4 คำขอพร้อมกัน และ 60 คำขอต่อนาที ต้องปรับตาม quota จริง
หาก Redis สำหรับควบคุมคำขอใช้ไม่ได้ ระบบไม่ข้ามตัวจำกัดเพื่อส่งคำขอไม่จำกัด

ชุด 300 งานเร่ง fault detection: lease 9 วินาที, recovery ทุก 2 วินาที, dispatch ทุก 8 วินาที,
2 worker containers × 2 งาน, provider concurrency 2, RPM 6,000, timeout งาน 120 วินาที
ทดสอบ RPM จริงด้วย probe แยกที่ตั้งเพดาน 5 ไม่ใช้ผลชุด 300 งานอ้างว่าทดสอบ 60 RPM

## ผลชุด 300 งาน

| ตัววัด | ผลจริง |
|---|---:|
| งานจบ / งานค้าง / งานล้มเหลวสุดท้าย | 300 / 0 / 0 |
| งานที่ใช้ 1 attempt / 2 attempts | 296 / 4 |
| ต้องกด retry เอง | 0 |
| Mapping ที่สร้าง | 300, ไม่พบมากกว่าหนึ่ง mapping ต่อ round จาก SQL |
| ผลจำลอง 12 แถว | 300/300 |
| verification.passed | 300/300 |
| ตกไปใช้ LLM fallback | 0 |
| analyst / mapper / execute / reviewer จบ | ทุกบทบาท 300/300 |
| เวลารวมจนเก็บผลครบ | 2,029.73 วินาที (33 นาที 49.73 วินาที) |
| API list-jobs latency P95 | 0.152 วินาที เฉพาะคำขอที่สำเร็จ |
| 429 / 503 / จำลอง timeout | 84 / 67 / 26 |
| HTTP คำขอถึง fake provider | 1,077 |
| ค่า API จริง | $0 |

Mapping ทั้ง 300 อยู่สถานะ proposed / review เพราะเป็นรูปแบบแรกและมีคำถาม VAT;
ไม่ได้อนุมัติหรือเผยแพร่ JSON ข้าม human review เพื่อให้ตัวเลขดูสำเร็จ
เกณฑ์สำเร็จของงานรอบนี้คือประมวลผลและส่งผลเข้า review ได้ครบตาม policy

Faults ที่ทำจริง: kill worker ขณะกำลัง mapping, restart Redis, restart PostgreSQL,
ลบสมาชิก `arq:queue` ใน Redis ของชุดทดสอบ แล้วสังเกตการเติมคิวกลับ
4 งานที่จับไว้ตอน kill กลับเข้าคิวใน 15.94 วินาที และจบด้วย attempt 2 ทั้งหมด
การ restart Redis หลังปรับ restart policy ไม่ต้องสั่ง start worker เอง

Fake server วัด peak handler ที่ยังทำงานอยู่ 3 แม้ตั้ง client concurrency 2:
handler ที่ถูกตัดการเชื่อมต่อระหว่าง fault อาจยังทำงานจนจบอยู่ฝั่ง provider
จึงใช้ probe ปกติแยกเพื่อยืนยัน distributed admission สูงสุด 2 โดยไม่อ้างว่าควบคุมงาน
ที่ยังทำอยู่ภายในผู้ให้บริการหลังตัดการเชื่อมต่อได้

## ผลตรวจเพิ่มเติมที่ยืนยันแล้ว

- regression backend รอบแรก: ผ่าน 902, ข้าม 5, coverage 90.28%
- regression รอบถัดมา: ผ่าน 912, ข้าม 5, ไม่นับ slow tests 2, coverage 90.14%;
  การปรับ stop/lifecycle ท้ายรอบตรวจซ้ำด้วยชุดเฉพาะทาง 41 เทสตามรายการด้านล่าง
- ตรวจส่วนล็อก/งาน/วงจรไฟล์หลังแก้ล่าสุด: ผ่าน 48 เทส
- mypy: ผ่าน 143 source files; frontend build ผ่าน มีคำเตือนขนาด bundle เดิม
- 8 โปรเซสแย่ง quota ร่วม: ส่งจริง 5, ไม่ได้รับสิทธิ์ 3, พร้อมกันสูงสุด 2
- child ที่ไม่ยอมรับ SIGTERM: หลังยกเลิกถูกปิดจริงใน 3.63 วินาที บน release image สุดท้าย
- hard timeout: ตั้ง execution 10 วินาที + startup allowance 10 + kill grace 3; ปิดโปรเซสและหมด lease ใน 23.04 วินาที
- PostgreSQL: progress อัปเดตระหว่างธุรกรรมหลักล็อก round ได้ใน 0.068 วินาที; หลังถอน token การ commit ผลเก่าถูกปฏิเสธ
- แทนที่ไฟล์ระหว่าง map ผ่าน API จริง: 0.828 วินาที; งานเดิม cancelled, รอบเดิม superseded, งานไฟล์ใหม่ done
- หยุดงานระหว่าง map: API ตอบใน 0.031 วินาที; รอ parent คืนสถานะ source แล้วกดทำต่อ จบด้วย attempt 2
- ตรวจเสริมหลังปรับ savepoint/write tracking: 19 ผ่าน; หลังปรับ stop/lifecycle: 41 ผ่าน

## ข้อค้นพบระหว่างทำ ไม่ใช่ผลที่ซ่อนไว้

1. การทดลองแรกหยุดเพราะ setup checkpoint index รอธุรกรรม mapping: แก้ให้ setup ตอนเริ่มบริการ
2. การทดลองถัดมาพบ `another command is already in progress`: แยก ledger connection แล้วทดสอบใหม่
3. compose ของชุดทดสอบรอบ 300 แรกขาด restart policy ของ worker ซึ่ง production มีอยู่แล้ว:
   เพิ่ม `unless-stopped` ให้ตรงกัน และทดสอบ Redis restart ซ้ำโดยไม่สั่ง start worker เอง
4. การ kill ผ่านคำสั่ง Docker โดยตรงถือเป็นการหยุดโดยผู้ดูแล จึงมีคำสั่ง start ตามมา;
   อย่านำผลนี้ไปอ้างว่า Docker auto-restart หลังผู้ดูแลสั่งหยุดเอง
5. probe ครั้งแรกตั้งเวลางานสั้นเกิน startup ขณะโหลดหนัก จึงแยก timeout ของการตรวจ cancellation
   และการตรวจ hard deadline แล้วรันใหม่ ข้อมูลผลสำเร็จอยู่ในไฟล์ probe logs
6. lifecycle probe พบว่า read-only session ไม่ควรล็อก round และ stop ไม่ควรรอ round lock
   ของธุรกรรม mapping: แยกการถอนสิทธิ์งานออกมาก่อน และตรวจซ้ำด้วยไฟล์ชุดใหม่จนผ่าน

## ข้อจำกัดที่ยังต้องรู้

- ยังไม่ใช่ high availability หลายเครื่อง; PostgreSQL/Redis/storage ถ้าหายถาวรยังต้อง backup/restore
- ไม่รับประกัน exactly-once ของ HTTP หรือค่า API: ผู้ให้บริการอาจทำงานต่อแม้ฝั่งเราตัดการเชื่อมต่อ
- commit fencing ป้องกันผลที่ถูกเผยแพร่ผ่าน DB; อาจมี storage object ที่ไม่ได้อ้างอิงเหลือหลัง rollback
- hard kill ใช้กับ ARQ; dev thread/process backend ใช้ cooperative cancellation และ commit fencing
- child process เพิ่ม overhead ตอนเริ่มงาน แลกกับการหยุด native code ที่ค้างได้
- เวลาของ fake model ใช้ทำนายเวลา DeepSeek สำหรับ ERP จริง 300 ไฟล์ไม่ได้
- งานจบการประมวลผลไม่ได้แปลว่าอนุมัติ/เผยแพร่แล้ว; งานที่ต้องถามผู้ใช้ยังต้องผ่าน review ตามเดิม

## Artifact และความตรงกันของรุ่น

- `verified/result.json`: ผลรายงาน/งาน/ความคืบหน้าทั้ง 300
- `verified/summary.json`: ตัวเลขสรุปที่สร้างซ้ำได้ด้วย `summarize.py`
- `verified/fault-recheck.json`: จับงานก่อน kill และเวลาที่กลับมารอคิว
- `verified/stack.log`: log stack จำลอง
- `limiter-probe.log`, `process-probe.log`, `locking-probe.log`: probe เฉพาะทาง
- `probes/lifecycle.json`: การแทนที่ไฟล์ / หยุด / ทำต่อ บน release image สุดท้าย

ชุด 300 ใช้ image `forma-stability-code` SHA256
`718a77ab797eed7e3301f7d92fa2c9bf56a7973e1b133eb30390f4daa4890749`
ซึ่งรวมกลไกหลัก heartbeat/outbox/fencing/process isolation/provider limits แล้ว
หลังจากนั้นปรับเพิ่มการทำต่อจากสถานะหยุดและการล็อกของ cancellation/read-only sessions;
ตรวจด้วย regression และ lifecycle/locking probes บน release image แยก ไม่ใช่อ้างว่าได้รัน
300 งานซ้ำกับทุกการแก้ท้ายงาน ผลเวลา 33:50 จึงอ้างอิง image ของชุด 300 เท่านั้น

Release image ที่ผ่าน lifecycle, process และ locking probes:
`191731e1504fb6e015fad95737e5c5441ebcb31c79bef1276ae5702377a46204`

Image ที่ติดตั้งบน localhost:
`404c7ccb64c09bbc0a0a24c1c51a4f95fbaca657038b3b1e6dbdaffa4658cc2d`
ต่างจาก image probe ข้างบนเฉพาะการจัดรูปแบบ exception handling ให้ผ่าน lint
(`except Conflict: pass` เป็น `suppress(Conflict)`) และไฟล์ประกอบทดสอบ

## สถานะหลังติดตั้งบน localhost

- migration `a4c6e8f01234` สำเร็จ เพิ่ม 4 คอลัมน์ใน jobs ไม่มีการลบข้อมูล
- API, worker, web healthy; `/ready` เป็น `ok`; หน้า/endpoint สถานะระบบอ่าน schema ใหม่ได้
- คิวจริงรอ 0 / กำลังทำ 0; failed 5 รายการเดิมไม่ได้สั่ง retry
- ยังคง provider `openai_compatible`, model `deepseek-flash` เดิม
- อัปเดต image ของ scheduled backup ให้รองรับ migration ใหม่ด้วย
- หยุด stack จำลองทั้งหมด เก็บ volume และ artifact ไว้ตรวจย้อนกลับ
- ไม่ commit; HEAD ยังเป็น `bee0ddc`

## ทำซ้ำ

รันจาก repo root; compose ทั้งหมดในโฟลเดอร์นี้ใช้ข้อมูลทดสอบและ fake credential เท่านั้น
สร้าง image `forma-stability-code` จาก `backend/Dockerfile`, ใช้ project
`forma-stability-verified` กับ `compose.yml`, เปิด worker 2 containers แล้วรัน
`backend/.venv/Scripts/python manual_test_files/stability_v1/run.py`
คำสั่งอ่าน artifact อย่างเดียวคือ
`backend/.venv/Scripts/python manual_test_files/stability_v1/summarize.py`

`run.py` ใช้ manifest เดิมเมื่อมีอยู่; ถ้าต้องการโหลดใหม่จริงให้ใช้ project/volume และ manifest
ใหม่ ไม่ลบฐานข้อมูลใช้งานจริงเพื่อเริ่มเทสใหม่
Probe ใช้ project `forma-stability-probes` กับ `compose.yml`, `release.yml`, `probes.yml`
และ image `forma-stability-release`; ต้องหยุด API/worker ของ project นี้ก่อนรัน `process_probe.py`
เพื่อไม่ให้ scheduler รับ handler ที่ตั้งใจให้ค้าง แล้วเปิด API/worker อีกครั้งสำหรับ lifecycle probe

ดูงานจริงได้ที่ **สถานะระบบ**: งานกำลังทำพร้อม heartbeat/ปุ่มหยุด, งานที่หยุดพร้อมปุ่มทำต่อ,
และงานล้มเหลวพร้อมปุ่มลองใหม่ การกดทำต่อใช้ไฟล์และขั้นตอนที่บันทึกไว้เดิม
