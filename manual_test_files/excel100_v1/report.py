"""Produce an auditable Thai report from saved evidence; no network or model calls."""
import csv
import hashlib
import json
import statistics
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RUN=ROOT/'run'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
manifest=read(ROOT/'manifest.json')
specs=manifest['files'];results={s['id']:read(RUN/s['id']/'result.json') for s in specs}
diags={p.stem:read(p) for p in (RUN/'diagnostics').glob('*.json')}
downloads={d['id']:d for d in read(RUN/'download.json')}
dm=read(RUN/'download-manifest.json')
jobs=read(RUN/'final-jobs.json')
stats=read(RUN/'provider-stats-final.json')
qa=read(ROOT/'qa/fixtures.json')
hashes={q['id']:q['sha256'] for q in qa}
source_hashes_ok=all(e['uploaded_sha256']==hashes[Path(e['uploaded_name']).stem] and
    all(s['sha256']==hashes[Path(e['uploaded_name']).stem] for s in e['sources']) for e in dm['files'])

def projected_match(a,b):
    if isinstance(b,dict):return isinstance(a,dict) and all(k in a and projected_match(a[k],v) for k,v in b.items())
    if isinstance(b,list):
        return isinstance(a,list) and len(a)==len(b) and all(projected_match(x,y) for x,y in zip(
            sorted(a,key=lambda x:str(x.get('warehouse_code'))),sorted(b,key=lambda x:str(x.get('warehouse_code')))))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return abs(a-b)<1e-6
    return a==b

preview_audits=[];before_checks=[];case_rows=[];model_cost=0
for s in specs:
    cid=s['id'];r=results[cid];b=read(RUN/cid/'before.json');m=b['mapping'];policy=m['policy'] if m else {}
    verify=policy.get('verification',{})
    full=r.get('after_score') or diags.get(cid,{}).get('full_source_score')
    out=downloads.get(cid,{})
    if s['family']<9:
        p=RUN/cid/'answer-preview.json'
        if p.exists():
            ap=read(p);gold={x['sku']:x for x in read(ROOT/'oracle'/f'{cid}.json')['rows']}
            bad=[x.get('sku') for x in ap['preview'].get('records',[]) if not projected_match(x,gold.get(x.get('sku'),{}))]
            preview_audits.append({'id':cid,'ok':ap['preview']['ok'],'wrong_displayed_records':len(bad),'sample':bad[:5],
                                   'full_validation':ap['comparison']['validation'].get('passed'),
                                   'sources':ap['preview'].get('stats',{}).get('sources')})
    for log in m.get('agent_log',[]) if m else []:
        model_cost+=log.get('usage',{}).get('cost_usd',0) or 0
    sc=policy.get('self_check',{})
    qtypes=Counter(q['kind'] for q in r.get('questions',[]))
    row={'file':cid+'.xlsx','family':s['family'],'expected':s['expected_action'],'rows_expected':s['expected_rows'],
         'rows_full_preview':r['before_score']['rows_got'],'normal_flow':bool(r.get('flow_pass')),
         'guard_pass':r.get('guard_pass'),'specific_unit_question':r.get('ask_pass'),
         'question_count':r.get('question_count',0),'question_types':dict(qtypes),
         'verification_passed':verify.get('passed'),'fix_rounds':sc.get('fix_rounds',0),
         'grounded':sc.get('grounded',0),'python_turns':sc.get('python_turns',0),
         'exported':out.get('included',False),'export_accuracy':out.get('score',{}).get('field_accuracy'),
         'normal_flow_error':r.get('error'),'diagnostic_backend_pass':diags.get(cid,{}).get('backend_pass'),
         'review_harness_elapsed_s':r.get('elapsed_s')}
    case_rows.append(row)
(RUN/'case-metrics.json').write_text(json.dumps(case_rows,ensure_ascii=False,indent=2),encoding='utf-8')
(RUN/'preview-audit.json').write_text(json.dumps(preview_audits,ensure_ascii=False,indent=2),encoding='utf-8')
with (ROOT/'case-results.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(case_rows[0]));w.writeheader();w.writerows(case_rows)

included=[d for d in downloads.values() if d.get('included')]
valid_fields=sum(d['score']['total_fields'] for d in included)
correct_fields=sum(d['score']['correct_fields'] for d in included)
normal=sum(bool(r.get('flow_pass')) for r in results.values())
guards=sum(bool(r.get('guard_pass')) for r in results.values())
questions=sum(bool(r.get('ask_pass')) for r in results.values())
wrong_preview=[p for p in preview_audits if p['wrong_displayed_records']]
wrong_ok=[p for p in wrong_preview if p['ok']]
failed_jobs=[j for j in jobs if j['status']!='done']
durations=[j['duration_ms']/1000 for j in jobs if j.get('duration_ms') is not None]
waits=[(datetime.fromisoformat(j['started_at'])-datetime.fromisoformat(j['created_at'])).total_seconds() for j in jobs if j.get('started_at')]
job_start=min(j['created_at'] for j in jobs);job_end=max(j['finished_at'] for j in jobs if j.get('finished_at'))
wall=(datetime.fromisoformat(job_end)-datetime.fromisoformat(job_start)).total_seconds()
summary={'files':100,'sheets':sum(q['sheets'] for q in qa),'physical_rows':sum(q['physical_rows'] for q in qa),
 'input_bytes':sum(q['bytes'] for q in qa),'normal_flow_pass':normal,'normal_preview_blocked':len(diags),
 'expected_guard_pass':guards,'unit_question_pass':questions,'final_exported':len(included),
 'final_exact_exports':sum(d['score']['exact'] for d in included),'export_rows':sum(d['score']['rows_got'] for d in included),
 'correct_fields':correct_fields,'total_fields':valid_fields,'wrong_previews':len(wrong_preview),'wrong_but_ok_previews':len(wrong_ok),
 'normal_flow_with_correct_preview':normal-len(wrong_ok),
 'jobs':dict(Counter(j['status'] for j in jobs)),'source_hashes_ok':source_hashes_ok,'cost_usd':model_cost,
 'observed_wall_s_including_harness_and_diagnostics':wall,'job_median_s':statistics.median(durations),
 'job_max_s':max(durations),'queue_wait_max_s':max(waits),'fake_requests':stats['requests']}
(RUN/'verified-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

lines=[
'# ผลทดสอบ Excel 100 ไฟล์ผ่าน FORMA — โมเดลจำลอง ไม่เสียค่า API', '',
'ทดสอบวันที่ 5 พฤศจิกายน 2026 (Asia/Bangkok), โค้ด commit `1e5a559eb28864c05491282d0731d2ae3cb0c599`',
'Docker image `forma-flow-final` / `sha256:7a02fe31ea5b61e7281e4e97369f3631fbf64457451f1d0c4a448738a812695c`', '',
'## ข้อสรุป', '',
f'การประมวลผลข้อมูลเต็มไฟล์และการสร้าง JSON ผ่าน แต่ flow ตรวจคำตอบยังมีบั๊กที่ควรแก้ก่อนใช้จริง: ใน 80 ไฟล์ที่ควรทำสำเร็จ มี {normal} ไฟล์ผ่านขั้นตอนปกติ และ {len(diags)} ไฟล์ติดหน้า preview แม้ข้อมูลเต็มถูกต้อง อีก 20 ไฟล์ตั้งใจใส่ปัญหาและถูกระงับครบ {guards}/20.', '',
f'การตรวจแยกผ่าน API หลังยืนยันข้อมูลเต็ม (ข้ามเฉพาะข้อจำกัดของ preview UI สำหรับวินิจฉัย ไม่แก้โค้ด) ทำให้ดาวน์โหลดได้ {len(included)} ไฟล์ รวม {summary["export_rows"]:,} สินค้า; เทียบค่าที่กำหนดไว้ตรง {correct_fields:,}/{valid_fields:,} ค่าที่ตรวจ ไม่มีแถวเพิ่ม/ซ้ำหรือยอดคลังหายจาก JSON ที่ส่งออก.', '',
f'คำว่า “ผ่าน flow ปกติ {normal} ไฟล์” หมายถึงสามารถกดบันทึก/อนุมัติตามเงื่อนไข UI ได้ ไม่ได้หมายความว่าตัวอย่างบนหน้าจอถูกต้องทั้งหมด: ในกลุ่มนี้ {len(wrong_ok)} ไฟล์ยังมีตัวอย่างผิด ดังนั้นผ่านทั้งขั้นตอนและแสดงตัวอย่างถูกต้องเพียง {normal-len(wrong_ok)}/80 ไฟล์ที่ควรสำเร็จ ต้องแก้ preview ก่อนให้ผู้ใช้อ้างอิงเพื่อตัดสินใจ.', '',
'**ผลนี้ไม่ใช่ accuracy ของ DeepSeek หรือคะแนนความสามารถอ่าน unknown format ของ AI**: fake model คืน mapping DSL ที่เตรียมไว้ ระบบ FORMA เป็นผู้อ่าน Excel และคำนวณค่าจริง ผู้ตรวจอ่านเฉลยแยกต่างหาก ไม่ส่งเฉลย JSON ให้โมเดลจำลอง.', '',
'## สิ่งที่ทดสอบ', '',
f'- 100 XLSX, {summary["sheets"]} ชีต, {summary["physical_rows"]:,} แถวจริงใน worksheet, ขนาดรวม {summary["input_bytes"]:,} bytes (~1 MB); ไฟล์ละ 12–48 สินค้า จึงยังไม่ใช่การทดสอบ Excel ขนาดใหญ่',
'- 10 ประเภท × 10 ตัวแปร: join ข้ามชีต, composite key, รวมล็อต, unpivot คลัง, ช่วงเวลา/สกุลเงิน, merged header/TOTAL, สูตรข้ามชีต, หน่วยกำกวม, ราคาขัดกัน, สูตรไม่มี cache',
'- อัปโหลดผ่าน HTTP API เป็น chunk ไม่เกิน 10 ไฟล์, PostgreSQL + Redis + ARQ workers 2 ตัว × 2 slots, LangGraph analyst/mapper/reviewer กับ fake HTTP provider',
'- ยืนยันลูกค้าเมื่อรูปแบบชนกัน → preview → ตอบคำถาม → ปฏิเสธ digest เก่า → apply สร้าง mapping version → อนุมัติ → transform/export → ดาวน์โหลด ZIP',
'- ทดสอบ HTTP flow และเงื่อนไขใน UI จากโค้ด ไม่ได้คลิกเบราว์เซอร์ 100 รอบ',
'- Docker internal network ปิดทางออกอินเทอร์เน็ต ใช้เฉพาะ `fake-excel100` / `http://fake:8001/v1`; fake container mount เฉพาะ provider/ ไม่มี oracle/',
'- ไม่มีการเปลี่ยนโค้ด production, การตั้งค่า DeepSeek หรือข้อมูล localhost จริง และไม่ได้ commit งานรอบนี้', '',
'## ผลแยกตามประเภท', '',
'| ประเภท | ไฟล์ | ผ่าน flow ปกติ | ติด preview | JSON ถูกต้องหลังวินิจฉัย | ระงับถูกต้อง |',
'|---|---:|---:|---:|---:|---:|',
]
for family,title in manifest['families'].items():
    group=[r for r in case_rows if r['family']==int(family)]
    lines.append(f'| {family}. {title} | {len(group)} | {sum(r["normal_flow"] for r in group)} | {sum(r["normal_flow_error"]=="Answer preview invalid" for r in group)} | {sum(r["exported"] and r["export_accuracy"]==1 for r in group)} | {sum(bool(r["guard_pass"]) for r in group)} |')
lines += ['', '## จุดที่ต้องแก้ก่อนลองไฟล์จริง', '',
'### 1. Preview ตัดแถวก่อน join/aggregate จึงแสดงผลผิด — ควรแก้ก่อน pilot', '',
f'พบ {len(wrong_preview)}/80 ไฟล์ที่ตัวอย่างหลังตอบคำถามมีข้อมูลไม่ตรงผลเต็ม รวม {len(wrong_ok)} ไฟล์ที่ preview ยังรายงาน `ok=true`; และ {len(diags)} ไฟล์รายงาน invalid ทำให้ปุ่มบันทึกคำตอบใน UI ถูก disable.',
'สาเหตุ: `review_evidence.answer_impact` ใช้ SAMPLE_LIMIT=50 และ `preview_mapping` ส่ง limit ไปจำกัดทุก source ก่อนเชื่อมตาราง/รวมล็อต ส่วน Mapping Studio ส่ง limit=40 เช่นกัน ตัวอย่างจึงอาจไม่มีแถวราคาที่ต้องใช้ หรือได้สต็อกเพียงบางล็อต.',
'ตัวอย่าง X0310: full execution อ่าน stock 432 แถวและได้ข้อมูลครบ 48 สินค้า แต่ answer-preview อ่าน stock แค่ 50 แถว ทำให้บาง SKU ไม่มี inventory และบางยอดคลังต่ำกว่าจริง ทั้งที่ full validation ผ่าน.',
'แนวทางแก้: เลือก SKU ตัวอย่างที่ root แล้วอ่านแถวลูก/lookup ที่สัมพันธ์กับ SKU เหล่านั้นให้ครบ หรือประมวลผลครบก่อนจำกัด JSON สำหรับแสดงผล ต้องระบุขอบเขตตัวอย่างและใช้ผลตรวจเต็มเป็นตัวตัดสินการบันทึก แยกกรณีจริงผิดออกจากตัวอย่างไม่ครบ.',
'เพิ่ม regression test สำหรับ 3 คลัง × หลายล็อต, shuffled lookup, price-history และกรณีที่ preview.ok=true แต่ยอดถูกตัด ห้ามแก้ด้วยการปิด validation.', '',
'### 2. ลดคำถามยืนยันที่เกิดจากรูปแบบ expression ต่างกัน', '',
'พบคำถาม contested เรื่อง Currency/Full unit/Price แม้โมเดลจำลองชี้คอลัมน์ถูกและผลเต็มตรงทั้งหมด เช่น matcher ใช้ค่าคงที่ THB แต่ mapping ใช้คอลัมน์ Currency; หรือ expression แปลงตัวเลขต่างรูปแบบกัน การถามครั้งแรกเป็นเรื่องเหมาะสม แต่ควรไม่รบกวนผู้ใช้ซ้ำเมื่อหลักฐานเท่ากัน.',
'แนวทางปรับ: เปรียบเทียบตัวตนของ source และผล/ความหมายภายใต้ schema ไม่ใช้ความต่างของชื่อ source หรือรูป expression เพียงอย่างเดียว และคงคำถามที่ยังมีความหมายทางธุรกิจไม่ชัดเจนไว้.', '',
'### 3. ยืนยันด้วยไฟล์จริงชุดเล็กก่อนเพิ่มปริมาณ', '',
'หลังแก้ preview ให้รันชุดนี้ซ้ำแบบฟรี แล้วทดลองไฟล์จริงที่ปกปิดข้อมูล 5–10 ไฟล์ รวมไฟล์หลายชีต/สูตร/ตัวเลข locale/รอบแก้ไข พร้อม JSON ที่ทีมปลายทางยืนยัน ชุดนี้ยังไม่ได้พิสูจน์การตัดสินใจของโมเดลจริงหรือไฟล์ทุก ERP.', '',
'## การถามและการหยุด', '',
f'- หน่วย ZQ: มีคำถามเฉพาะจุดครบ {questions}/10; ใช้คำตอบผู้ทดสอบ BOTTLE ผ่านเส้นทาง preview/apply และตรวจว่าฟิลด์อื่นไม่เปลี่ยน นี่ตรวจการส่งต่อคำถามจาก fake ไม่ใช่ความสามารถที่โมเดลจริงจะรู้ว่าควรถามเอง',
'- ราคาขัดกัน 10 ไฟล์: ไม่เดาแถวแรก และ POST approve ถูกปฏิเสธ HTTP 422 เพราะ join 1:1 พบคีย์ซ้ำ',
'- สูตรไม่มี cached result 10 ไฟล์: ไม่แทนด้วย 0 และ approve ถูกปฏิเสธ HTTP 422 พร้อมตำแหน่งเซลล์ที่ต้องแก้',
'- ลูกค้าที่รูปแบบคล้ายกัน: ขอการยืนยันเจ้าของไฟล์ก่อนเดินต่อ ผู้ทดสอบตอบตามเจ้าของ fixture ที่กำหนดไว้ ไม่ข้ามหรือปิดตัวตรวจนี้', '',
'## งานเบื้องหลัง ดาวน์โหลด และต้นทุน', '',
f'- Jobs ทั้งหมดรวม pilot/การตรวจเพิ่ม: {len(jobs)}; สถานะ {dict(Counter(j["status"] for j in jobs))}; งานค้าง/ล้มเหลว ณ สิ้นสุด {len(failed_jobs)}',
f'- ระยะเวลางานแต่ละ job: median {statistics.median(durations):.2f}s, สูงสุด {max(durations):.2f}s; queue wait สูงสุด {max(waits):.2f}s',
f'- ช่วงเวลาเริ่ม job แรกถึงเสร็จ job สุดท้าย {wall/60:.1f} นาที รวมการตรวจ harness, pilot และวินิจฉัยซ้ำ จึงไม่ใช่เวลารัน 100 ไฟล์แบบต่อเนื่องครั้งเดียว และห้ามใช้คูณเป็นเวลาของ DeepSeek จริง',
f'- Fake HTTP calls {stats["requests"]}, cost_usd ใน mapping agent logs ที่ตรวจ {model_cost:.6f}, ค่า API ภายนอกจริง $0 (network isolation)',
f'- ZIP สุดท้าย: {dm["exported_files"]} exported / {dm["omitted_files"]} omitted / {dm["not_received_files"]} not received; complete={str(dm["complete"]).lower()} ถูกต้องเพราะ 20 ไฟล์ต้องระงับ',
f'- source hash ตรงต้นฉบับทั้งหมด: {source_hashes_ok}; JSON SHA256, จำนวนแถวและ mapping/dataset version ตรวจใน run/download.json',
'- มีหลักฐานงานพร้อมดาวน์โหลดพร้อมกับงานที่ยัง processing ใน run/in-progress.json; การตอบคำถามไม่ต้องรอไฟล์ทั้งชุดจบ แต่ job อนุมัติ/ส่งออกอาจรอคิวงานเดิม', '',
'## ข้อจำกัดและความโปร่งใส', '',
'- Fake model ได้สัญญา mapping ที่ถูกกำหนดไว้ ไม่ได้ค้นหาความสัมพันธ์เอง ไม่มี Python sandbox reasoning จริงและไม่มีการวัด DeepSeek/ChatGPT/Claude',
'- ใช้ local test storage ไม่ใช่ MinIO/S3; ไม่ครอบคลุมไฟล์ระดับหลายสิบ MB, 300 Excel จริง, restart/recovery, login/RBAC หรือ cross-round learning ในรอบนี้',
'- Pilot แรกพบ DSL contract ของชุดทดสอบใช้ pointer collection ไม่เต็ม จึงแก้เฉพาะ prepare.py/contracts และเสนอ mapping ใหม่ เก็บหลักฐานไว้ใน pilot-invalid-contract/; ไม่รวมเป็นบั๊ก production',
'- Harness รุ่นแรกยังไม่จัดการ choice ที่หุ้ม parse_number และกรณีขอยืนยันลูกค้าหลังคิวเดิน จึงปรับ harness แล้วทำขั้นตอนที่ค้างต่อ หลักฐานก่อนปรับอยู่ใน run/harness-first-pass.json',
'- ไฟล์ที่ติด preview มีการทดสอบผ่าน API แยกใน run/diagnostics/ หลังตรวจข้อมูลเต็มแล้วเท่านั้น ยังถือว่า flow UI ปกติไม่ผ่าน ไม่ได้อ้างว่าบั๊กถูกแก้', '',
'## ผลต่อไฟล์', '',
'ตารางนี้แสดงผลเต็มและผลปลายทาง; “วินิจฉัย” คือผ่าน backend หลังข้ามข้อจำกัด preview UI ในชุดทดสอบ ไม่ใช่ normal UI pass.', '',
'| ไฟล์ | สินค้า | คำถาม | ชนิดคำถาม | Full verify | Flow ปกติ | JSON เทียบเฉลย | หมายเหตุ |',
'|---|---:|---:|---|---|---|---|---|',
]
for r in case_rows:
    accuracy='—' if r['export_accuracy'] is None else f'{r["export_accuracy"]*100:.1f}%'
    note='ระงับถูกต้อง' if r['guard_pass'] else 'วินิจฉัยผ่าน API' if r['diagnostic_backend_pass'] else 'ผ่าน' if r['normal_flow'] else r['normal_flow_error'] or 'ตรวจเพิ่ม'
    lines.append(f'| {r["file"]} | {r["rows_expected"]} | {r["question_count"]} | {r["question_types"]} | {r["verification_passed"]} | {"ผ่าน" if r["normal_flow"] else "—" if r["expected"]=="block" else "ติด preview"} | {accuracy} | {note} |')
lines+=['','ข้อมูลตัวเลขสำหรับกรองต่อ: case-results.csv และ run/case-metrics.json; หลักฐาน preview: run/preview-audit.json.','']
(ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
with zipfile.ZipFile(ROOT/'Excel100-for-manual-test.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted((ROOT/'inputs').glob('*.xlsx')):z.write(p,str(p.relative_to(ROOT)))
    for p in sorted((ROOT/'schemas').glob('*.json')):z.write(p,str(p.relative_to(ROOT)))
    for name in ['README.md','manifest.json']:z.write(ROOT/name,name)
print(json.dumps(summary,ensure_ascii=False,indent=2))
