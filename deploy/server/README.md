# เตรียม FORMA สำหรับ server โดยไม่เปลี่ยนเครื่อง dev

เครื่องมือชุดนี้อ่านความจุหรือรับสเปกที่ระบุ แล้วสร้าง **capacity report + Compose override** เพื่อให้ตรวจทานก่อน deploy ไม่แก้ `.env.production`, `.wslconfig`, โค้ดแอป, key หรือคอนเทนเนอร์ที่กำลังทำงาน ไม่เรียก LLM และไม่สั่ง `up` เอง

นี่เป็น capacity planning สำหรับ **Linux server เครื่องเดียว** ไม่ใช่ load balancer, autoscaler หรือระบบ HA การกระจายงานยังใช้ Redis/ARQ เดิม Worker รับงานจากคิวร่วม ส่วน mapping Python ใช้ pool ของ sandbox ที่แยกคอนเทนเนอร์ ถ้าจะใช้หลาย host ต้องออกแบบ sandbox socket ที่อยู่แต่ละ host, shared storage, DB/Redis availability และการ route เว็บใหม่ ไม่ควรนำไฟล์นี้ไปใช้ข้ามเครื่องโดยตรง

## วิธีเลือก config

จาก repo root ใช้ Python 3.10+ (Windows dev ใช้ `backend\.venv\Scripts\python` แทน `python3`):

```bash
# จำลอง server ที่กำลังพิจารณา ซื้อเครื่องตามผลนี้ทันทีไม่ได้ ต้องปรับสมมติฐานไฟล์ด้วย
python3 deploy/server/plan.py --cpus 16 --ram-gib 48 \
  --disk-total-gib 1000 --disk-free-gib 900 \
  --files 300 --average-file-mib 5 --retained-batches 7 \
  --output-multiplier 1 --backup-copies 15 --database-gib 5 \
  --out deploy/server/generated/first-plan

# เมื่อมี server จริง: อ่านทรัพยากรที่ Docker เห็นและ filesystem เก็บข้อมูล
python3 deploy/server/plan.py --probe --data-path /var/lib/docker \
  --files 300 --average-file-mib 20 --retained-batches 7 \
  --out deploy/server/generated/measured-plan
```

`--probe` ใช้เฉพาะบน Linux server ที่ Docker daemon อยู่ในเครื่องเดียวกัน ต้องระบุ data path ของ filesystem ที่เก็บ volumes จริง ตรวจด้วย `docker info --format '{{.DockerRootDir}}'` ถ้าใช้ rootless Docker/custom mounts ให้ระบุ path ให้ตรง การ probe ไม่ได้วัด peak ของแอปอื่น: ใส่ `--other-ram-gib` และ `--other-cpus` ตาม peak ที่ต้องเผื่อไว้ หาก DB, backups, object storage อยู่คนละดิสก์ ให้ประเมินแต่ละ mount เพิ่มเติม ชุดนี้ยังไม่สร้าง topology แบบ external services

ค่าความจุใช้ **GiB/MiB** ไม่ใช่ GB/TB ตามฉลากดิสก์ RAM/CPU ควรใช้ค่าที่ Docker/VM/cgroup ได้รับจริง ไม่ใช่สเปกทั้ง host ถ้า WSL มีเพดาน 12 GiB ต้องใช้ 12 ไม่ใช่ RAM เครื่อง 32 GiB

หากไม่ระบุ `--out` จะพิมพ์รายงานอย่างเดียว หากระบุ จะสร้าง directory ใหม่เท่านั้น ไม่เขียนทับแผนเก่า:

- `capacity.json`: สมมติฐาน ส่วนเผื่อ ผลทุกขนาด และขนาดที่แนะนำ
- `compose.capacity.json`: สร้างเฉพาะเมื่อ CPU, RAM และดิสก์ผ่านพร้อมกัน
- exit code `2`: ทรัพยากรไม่พอ/ข้อมูลไม่ถูกต้อง **ไม่มี config ให้ฝืน deploy**

ระบบเลือกได้ 2, 4, 8, 16 หรือ 32 งาน ไม่เกิน `--max-jobs` ซึ่งค่าเริ่มต้นเป็น **8** ต้องระบุ `--max-jobs 16` หรือ `--max-jobs 32` จึงจะเลือกขนาดใหญ่ขึ้นได้ ลดขนาดได้ในแผนเท่านั้น ไม่ไปเปลี่ยนค่าที่ dev ใช้อยู่ คง shared RPM ที่ 60 ไม่ปรับ provider/key/model/timeout/กฎอนุมัติ โปรไฟล์ server ใช้ `FORMA_KNOWLEDGE_POLICY=strict` เพื่อเรียกใช้เฉพาะความรู้ที่ตรวจและอนุญาตแล้ว

| งานพร้อมกัน | Worker containers × slots | Sandbox | ตัวอย่าง CPU / RAM | ยอด RAM limits รวม |
|---|---|---|---|---|
| 8 (ค่าเริ่มต้น) | 2 × 4 | 2 | 16 vCPU / 48 GiB | 28.125 GiB |
| 16 (เลือกเพิ่ม) | 4 × 4 | 4 | 24 vCPU / 64 GiB | 49.125 GiB |
| 32 (เลือกเพิ่ม) | 8 × 4 | 8 | 48 vCPU / 128 GiB | 91.125 GiB |

ตัวเลขเป็นสมมติฐาน capacity ของไฟล์ซับซ้อน ไม่ใช่ผล load test หรือการรับประกัน throughput การเพิ่มงานไม่ได้เพิ่มโควตาของผู้ให้บริการ LLM อัตโนมัติ เมื่อชน RPM/TPM หรือ sandbox จะยังต้องรอ ตัวอย่างที่สร้างไว้ตรวจได้ใน `examples/server-48g`, `examples/server-64g-16jobs` และ `examples/server-128g-32jobs`

**เริ่ม production ใหม่และ deploy ครั้งต่อไป:** ใช้ [คู่มือแยกฐานและย้ายความรู้](KNOWLEDGE.md) ซึ่งเพิ่ม `compose.isolated.yml` และ env เฉพาะ server ห้ามใช้ฐาน dev ทั้งก้อนเป็นจุดเริ่ม production

## สูตรคำนวณและส่วนเผื่อ

เป็นนโยบายตั้งต้นของ FORMA ไม่ใช่ค่ามาตรฐานที่ผู้ให้บริการรับประกัน:

- RAM กันไว้ `max(3 GiB, 20% ของ RAM)` แล้วหัก peak RAM ของแอปอื่น
- CPU กันไว้ `max(1 vCPU, 12.5% ของ CPU)` แล้วหัก CPU ของแอปอื่น
- รวม **hard limit ทุก replica** ต้องไม่เกินส่วนที่เหลือ ไม่คิดว่า swap คือ RAM เพิ่ม
- Worker เผื่อฐาน 0.5 GiB + `slots × --ram-per-job-gib` (เริ่มต้น **1.5 GiB/งาน**) และ 0.5 CPU/slot
- API 1.5 GiB, PostgreSQL 2 GiB, Redis 0.5 GiB, MinIO 1 GiB, เว็บ 0.25 GiB, backup 0.75 GiB รวมถึง migration 1 GiB และ init 0.125 GiB เพื่อเผื่อช่วงเริ่มระบบด้วย
- Sandbox ตัวละ 4 GiB / 2 CPU; 2–4 งานใช้หนึ่งตัว, 8/16/32 งานใช้ 2/4/8 ตัว Python ยังคง virtual-address limit 2560 MiB เดิม ส่วนที่เพิ่มเผื่อ tmpfs/runner/ผลลัพธ์ ไม่ได้ทำให้โปรแกรมใช้ RAM 4 GiB ได้ทั้งหมด
- `FORMA_FILE_WORKERS=1` และจำกัด thread ของไลบรารีคำนวณ เพื่อลดการซ้อนขนานภายในแต่ละงาน ยังไม่มี memory-aware scheduler ที่กันทุก job ให้โดยอัตโนมัติ

ภายใต้สมมติฐานนี้ ตัวอย่าง 16 vCPU / 48 GiB เลือก 8 งาน ใช้ยอดเพดาน RAM รวม **28.125 GiB**, CPU รวม **11.5 vCPU** เหลือจาก capacity ทั้งหมด 19.875 GiB / 4.5 vCPU (รวมส่วนเผื่อแล้ว อย่าหักซ้ำ) เครื่อง 32 GiB อาจแนะนำ 4 งานในนโยบายนี้ แม้เคยรัน 8 งานเล็กได้ เพราะกัน RAM ต่อไฟล์ซับซ้อนไว้มากกว่าเดิม ไม่ใช่ข้อพิสูจน์ว่า 32 GiB ใช้ 8 งานไม่ได้

ดิสก์คำนวณจากข้อมูลใหม่ที่จะเก็บเพิ่ม:

```text
source = จำนวนไฟล์ × ขนาดเฉลี่ย / 1024
retained_source = source × จำนวนรอบที่จะเก็บ
live_data = retained_source × (1 + output_multiplier) + database_gib
persistent = live_data × (1 + backup_copies) + retained_source สำหรับ local cache
scratch = max(10 GiB, source × 2) + live_data × 2 สำหรับสร้าง backup/บีบอัด
required_growth = persistent + scratch + 15 GiB สำหรับ images/logs
ผ่านเมื่อ free_now - required_growth >= max(20 GiB, 20% ของดิสก์ทั้งหมด)
```

**ขนาด JSON/versions อาจใหญ่กว่า XLSX ที่บีบอัดหลายเท่า** ปรับ `--output-multiplier` จากข้อมูลจริง ค่า 1 เป็นสมมติฐาน ไม่ใช่การรับประกัน สคริปต์ไม่สมมติว่า backup บีบอัดแล้วเล็กลง ค่าเริ่มต้น `backup-copies=15` สอดคล้องกับงาน nightly เดิมที่ล้างหลัง 14 วันและเผื่อขอบเขตรอบใหม่; ลดค่านี้ได้ต่อเมื่อเปลี่ยน retention/offsite policy จริงด้วย

ตัวอย่าง 300 ไฟล์เฉลี่ย 5 MiB เก็บ 7 รอบ, output เท่าต้นทาง, DB เพิ่ม 5 GiB, local backup 15 ชุด ต้องเผื่อเพิ่มประมาณ **494.4 GiB** บนดิสก์ 1000 GiB ว่าง 900 GiB จะเหลือประมาณ 405.6 GiB มากกว่าส่วนเผื่อ 200 GiB ตัวเลขมากเพราะนับ full local backups ไม่ใช่ไฟล์ต้นทางอย่างเดียว

ผลเป็นงบสำหรับ planning horizon ที่ระบุ ไม่ใช่การคาดการณ์ตลอดอายุระบบ ไม่มีการเปิด retention หรือลบไฟล์อัตโนมัติ MinIO versioning เดิมเก็บเวอร์ชันเพิ่มได้ ต้องรวมไว้ใน multiplier และทบทวนจาก disk growth จริง เก็บ backup offsite เพิ่มด้วย เพราะ backup บนเครื่องเดียวไม่ป้องกันเครื่อง/ดิสก์สูญหาย

## ตรวจและนำไปใช้เมื่ออยู่บน server จริง

1. ใช้ checkout/config/secrets ของ server แยกจาก dev เตรียม `deploy/server/generated/server.env` ตาม [คู่มือ production ใหม่](KNOWLEDGE.md): DB/object-store credentials, encryption key, OIDC, URLs และ TLS ห้ามใช้ default credentials หรือ dev auth เพียงเพราะ capacity report ผ่าน
2. สร้างแผนจากสเปกจริงและ workload ที่จะเก็บ ตรวจ `capacity.json` หากมีแอปอื่นบนเครื่องต้องกัน peak ของแอปนั้นก่อน
3. จาก repo root ตรวจ Compose ที่ merge แล้ว **โดยไม่ start**:

```bash
docker compose --env-file deploy/server/generated/server.env \
  -f docker-compose.prod.yml \
  -f deploy/server/generated/measured-plan/compose.capacity.json \
  -f deploy/server/compose.isolated.yml config --quiet
```

4. Build images ก่อนรับโหลดจริง แล้วทดสอบ fake model บน stack แยก ทั้ง RAM peak, sandbox busy/timeout/OOM, สูตร/หลายชีต/joins, queue recovery และ flow จนดาวน์โหลด ห้ามตีความว่าทดสอบตัว planner ผ่านเท่ากับผ่าน workload นี้
5. เมื่อจะนำไปใช้จริง รอคิวเดิมว่างและสำรองข้อมูลก่อน ใช้คำสั่ง Compose เดียวกันข้างบนเปลี่ยน `config --quiet` เป็น `up -d --build` **เฉพาะบน server** ไม่ใส่ `--scale worker` เพราะจะทับจำนวน replica ที่คำนวณไว้ หากเคยมี sandbox-2 แล้วลด profile ให้ตรวจ orphan containers และจัดการตอนคิวว่าง ไม่ปล่อยตัวเก่ากิน RAM นอกงบ
6. ตรวจ `/ready`, health ของทั้ง sandbox และ runtime settings ทั้ง API/worker ค่าใน DB อาจ override environment ตรวจ worker slots, `file_workers`, shared concurrency/RPM และ pool socket จริง กรณี recreate API ให้ตรวจ nginx resolution และ reload เมื่อจำเป็น

ไฟล์ override เพิ่ม sandbox ตามจำนวนในแผน โดยไม่ส่ง secrets/env_file เข้าไป ไม่มี network, root filesystem read-only และใช้ socket คนละชื่อ มี healthcheck ชี้ socket ให้ตรง จำกัด admitted request หนึ่งงานต่อตัวเพื่อไม่สะสมไฟล์ที่รอใน tmpfs ถ้าทุกตัวเต็มจะคืน busy ให้ caller จัดการตามโค้ดเดิม **ยังไม่ใช่คิวรอ sandbox ใหม่หรือ least-loaded balancer** ผู้ช่วยทั่วไปยังใช้ sandbox ตัวแรก ส่วน mapping ใช้ pool

Redis ใช้ `noeviction` เพราะถือคิวงาน ไม่ใช่ cache ที่ทิ้งได้ เมื่อเต็มจะปฏิเสธ write ต้องเฝ้าดู/เพิ่มงบ ไม่ใช่ล้างคิวทิ้ง ค่าขีดจำกัดคือรั้วป้องกัน ไม่รับประกันว่าแต่ละ process จะพอดี: ตรวจ peak จริงแล้วแก้งบก่อนใช้ข้อมูลจริง

## สิ่งที่ต้องเฝ้าดูหลังขึ้น server

- RAM working set/peak และ `memory.events`/OOM ของแต่ละ container, host available memory และ swap activity
- queued/running/failed, อายุงานเก่าสุด, retry, latency ของหน้าเว็บ
- sandbox busy/timeout, workspace เต็ม, ระยะรอโมเดลและ limiter
- ดิสก์ของทุก volume: เตือนที่ว่างต่ำกว่า 25%, หยุดรับ batch ใหม่เมื่อคาดว่าจะต่ำกว่า reserve 20% (เป็น operational policy ที่ต้องติดตั้ง monitoring/admission เพิ่ม ยังไม่มี automation ใน config นี้)
- ใช้ผลช่วงโหลดจริงปรับ `ram-per-job-gib`, output multiplier, retention และ backup budget ทีละค่า ไม่เพิ่ม worker อัตโนมัติจาก CPU ว่างอย่างเดียว

## ทดสอบเครื่องมือฟรี

```bash
python3 deploy/server/test_plan.py
python3 deploy/server/test_compose.py
```

ชุดนี้ตรวจเลขงบทุก replica, insufficient capacity, disk reserve, backup staging, isolation และ CLI โดยไม่รัน API ค่าใช้จ่าย การ merge กับ Compose จริงตรวจได้ด้วย `config --quiet` ไม่ต้องเปิด containers

ผลตรวจตอนเตรียมชุดนี้: unit tests 7 ข้อผ่าน (รวมไล่สเปก CPU/RAM 35 คู่), และ Compose merge test ผ่านทั้ง 2/4/8/16/32 งาน โดยใช้ env จำลองแยกต่างหาก ยืนยันชื่อ project/volumes แยก, strict knowledge, OIDC, replica, socket/healthcheck และ sandbox ไม่ได้รับ secrets ของแอป ตัวอย่างทั้งหมดใช้สมมติฐานดิสก์ 1000 GiB ว่าง 900 GiB / ไฟล์เฉลี่ย 5 MiB ไม่ได้ start หรือ deploy config นี้ และยังไม่ได้ทำ load test บน server

แนวคิดอ้างอิง: [Kubernetes Node Allocatable / reserve](https://kubernetes.io/docs/tasks/administer-cluster/reserve-compute-resources/), [Docker memory/CPU limits](https://docs.docker.com/engine/containers/resource_constraints/), [tmpfs นับเป็น RAM ของ container](https://docs.docker.com/engine/storage/tmpfs/), [Compose override merge](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/). นำหลัก reserve-before-scheduling มาใช้กับ Compose แบบคงที่ ไม่ได้อ้างว่าได้ระบบ scheduler ของ Kubernetes มาแล้ว
