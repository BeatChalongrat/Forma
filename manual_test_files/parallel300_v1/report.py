import json
import statistics
from pathlib import Path
from datetime import datetime

root=Path(__file__).resolve().parent
summary={}
for label in ('before','after'):
    r=json.loads((root/label/'result.json').read_text(encoding='utf-8'))
    waits=[(datetime.fromisoformat(j['started_at'])-datetime.fromisoformat(j['created_at'])).total_seconds() for j in r['jobs']]
    summary[label]={'jobs':r['counts'],'seconds':r['elapsed_s'],'jobs_per_min':round(18000/r['elapsed_s'],2),
        'api_p95_s':r['api_latency_p95_s'],'queue_p95_s':sorted(waits)[284],
        'provider_requests':r['fake_provider']['requests'],'peak_requests':r['fake_provider']['max_active'],
        'retries':len(r['manual_retries']),'unique_mappings':len({m['mapping_id'] for m in r['mappings']}),
        'twelve_rows':sum(m['policy']['simulation']['rows_out']==12 for m in r['mappings']),
        'verification_passed':sum(bool(m['policy'].get('verification',{}).get('passed')) for m in r['mappings']),
        'fallbacks':sum(bool(m['policy']['checks'].get('llm_fallback')) for m in r['mappings'])}
f=json.loads((root/'fault/result.json').read_text(encoding='utf-8'))
summary['recovery']={'done':sum(j['status']=='done' for j in f['jobs']),'killed_jobs':len(f['killed_jobs']),
    'seconds':f['recovery_s'],'duplicate_mappings':sum(n!=1 for n in f['round_mapping_counts']), 'faults':f['provider']['faults']}
summary['speedup']=summary['before']['seconds']/summary['after']['seconds']
samples=[json.loads(s) for s in (root/'resources.jsonl').read_text(encoding='utf-8').splitlines()]
def memory_mib(value):
    import re
    amount,unit=re.match(r'([\d.]+)(\w+)',value.split('/')[0].strip()).groups()
    return float(amount)*{'MiB':1,'GiB':1024,'KiB':1/1024,'B':1/1048576}[unit]
summary['resources']={label:{'peak_worker_cpu_percent':max(float(s['CPUPerc'].rstrip('%')) for s in samples if s['phase']==label and 'worker' in s['Name']),
    'peak_worker_memory_mib':max(memory_mib(s['MemUsage']) for s in samples if s['phase']==label and 'worker' in s['Name'])} for label in ('before','after')}
(root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
b,a=summary['before'],summary['after']
lines=['# ผลเทียบ 4 กับ 8 งานพร้อมกัน — 5 พฤศจิกายน 2026','',
'ใช้โมเดลจำลอง HTTP เท่านั้น ค่า API $0 ทั้งสองชุดแยก PostgreSQL/Redis/storage และปิด egress ด้วย Docker internal network ใช้ key ปลอมเดียวกันและ image forma-api เดียวกัน รันตามลำดับไม่แย่งทรัพยากรกัน','',
'ข้อมูลเดิม 300 CSV × 12 แถว แยกลูกค้าและรอบ 300 ชุด โมเดลจำลองส่ง mapping draft กลับและหน่วง 2 วินาทีต่อ request จึงวัดระบบคิว/worker/limiter/การตรวจจริง ไม่วัดความฉลาดหรือเวลาของ DeepSeek และไม่ใช่ไฟล์ Excel ขนาดใหญ่','',
'งาน done หมายถึงอ่านไฟล์ สร้าง mapping และตรวจผลจำลองเสร็จแล้ว ไม่ได้แปลว่าอนุมัติธุรกิจอัตโนมัติทุกไฟล์ รอบนี้ทดสอบ 300 jobs ตามคำขอ ส่วน flow ตอบคำถามจนดาวน์โหลดเคยตรวจแยกใน excel100_v1','',
'| ตัวชี้วัด | ก่อน: 4 slots | หลัง: 8 slots |','|---|---:|---:|',
f'| เวลางานทั้งชุด | {b["seconds"]/60:.2f} นาที | {a["seconds"]/60:.2f} นาที |',
f'| งานต่อนาที | {b["jobs_per_min"]} | {a["jobs_per_min"]} |',
f'| สำเร็จ | {b["jobs"]} | {a["jobs"]} |',
f'| Request พร้อมกันสูงสุด | {b["peak_requests"]} | {a["peak_requests"]} |',
f'| Request รวม | {b["provider_requests"]} | {a["provider_requests"]} |',
f'| API latency P95 | {b["api_p95_s"]:.3f} วินาที | {a["api_p95_s"]:.3f} วินาที |',
f'| รอคิว P95 | {b["queue_p95_s"]:.1f} วินาที | {a["queue_p95_s"]:.1f} วินาที |',
f'| Mapping ไม่ซ้ำ / ได้ 12 แถว | {b["unique_mappings"]} / {b["twelve_rows"]} | {a["unique_mappings"]} / {a["twelve_rows"]} |',
f'| Verification ผ่าน / fallback | {b["verification_passed"]} / {b["fallbacks"]} | {a["verification_passed"]} / {a["fallbacks"]} |','',
f'RAM ต่อ worker container สูงสุดที่สุ่มเก็บทุก ~20 วินาที: ก่อน {summary["resources"]["before"]["peak_worker_memory_mib"]:.1f} MiB; หลัง {summary["resources"]["after"]["peak_worker_memory_mib"]:.1f} MiB ต่อ container (หลังมี 2 containers) ไม่ใช่ high-water mark ที่จับได้ทุกขณะ','',
f'เร็วขึ้น {summary["speedup"]:.2f} เท่า ลดเวลารวม {(1-a["seconds"]/b["seconds"])*100:.1f}% โดยคงเพดาน 60 requests/minute เท่ากันทั้งคู่ เวลาเริ่มจาก enqueue งานแรกจนทั้งหมดเสร็จ รวมเวลา enqueue/poll ไม่รวมเตรียมไฟล์และตรวจรายงานหลังจบ','',
f'อัตรา request เฉลี่ยก่อน {b["provider_requests"]*60/b["seconds"]:.1f}/นาที หลัง {a["provider_requests"]*60/a["seconds"]:.1f}/นาที หลังจึงอยู่ใกล้เพดาน 60 RPM แล้ว นี่เป็นข้อจำกัดหนึ่งที่ทำให้เพิ่ม slots สองเท่าแต่เวลาไม่ลดครึ่งหนึ่ง','',
'ทดสอบความเสถียรเพิ่มอีก 16 งาน นอกเวลาที่ใช้เทียบ: บังคับ kill worker ขณะกำลังรอโมเดล จากนั้น start container ให้ supervisor กู้คืนเอง ไม่มี manual retry ราย job พร้อมฉีด 429/503/หน่วงเวลา','',
json.dumps(summary['recovery'],ensure_ascii=False),'',
'การตั้งค่าใช้งาน: worker 2 containers × FORMA_WORKERS=4, FORMA_LLM_SHARED_CONCURRENCY=8, FORMA_LLM_SHARED_RPM=60 ใช้ key เดิม งานที่รอคนยังแยกจากงานประมวลผล ไม่ลดการตรวจข้อมูลเพื่อเพิ่มความเร็ว','',
'ข้อจำกัด: 60 RPM เป็นเพดานร่วม แม้เพิ่มจำนวนงานก็อาจรอโควตา; งานจริงยังขึ้นกับ model latency/token length/จำนวนรอบตรวจ/CPU/RAM ผลนี้ห้ามใช้ยืนยันว่า DeepSeek จะเร็วขึ้นเท่ากัน ยังไม่ได้ทดสอบ paid API และยังไม่ commit']
(root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,indent=2))
