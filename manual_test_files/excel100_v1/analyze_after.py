"""Audit the isolated rerun and compare saved evidence without model calls."""
import csv
import hashlib
import json
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'run-after'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(name, value):
    (RUN / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'sum_s': round(sum(values), 3),
            'median_s': round(statistics.median(values), 3),
            'p95_s': round(values[max(0, int(len(values) * .95) - 1)], 3),
            'max_s': round(max(values), 3)}


def main():
    baseline = read(ROOT / 'run/verified-summary.json')
    manifest = read(ROOT / 'manifest.json')
    results = [read(RUN / s['id'] / 'result.json') for s in manifest['files']]
    exports = read(RUN / 'download.json')
    included = [e for e in exports if e.get('included')]
    jobs = read(RUN / 'jobs.json')
    archive = read(RUN / 'download-manifest.json')
    hashes = {q['id']: q['sha256'] for q in read(ROOT / 'qa/fixtures.json')}
    intact = all(
        e['uploaded_sha256'] == hashes[Path(e['uploaded_name']).stem]
        and all(s['sha256'] == hashes[Path(e['uploaded_name']).stem] for s in e['sources'])
        for e in archive['files'])
    fixture_intact = all(hashlib.sha256((ROOT / s['path']).read_bytes()).hexdigest() == hashes[s['id']]
                         for s in manifest['files'])
    stages = defaultdict(list)
    stage_rows = []
    for job in jobs:
        for stage, item in (job.get('result', {}).get('stages') or {}).items():
            if item.get('duration_ms') is not None:
                stages[stage].append(item['duration_ms'] / 1000)
                stage_rows.append({'job': job['id'], 'kind': job['kind'], 'stage': stage,
                                   'seconds': item['duration_ms'] / 1000})
    wait = [(datetime.fromisoformat(j['started_at']) - datetime.fromisoformat(j['created_at'])).total_seconds()
            for j in jobs if j.get('started_at')]
    runtime = [j['duration_ms'] / 1000 for j in jobs if j.get('duration_ms') is not None]
    wall = (datetime.fromisoformat(max(j['finished_at'] for j in jobs if j.get('finished_at')))
            - datetime.fromisoformat(min(j['created_at'] for j in jobs))).total_seconds()
    # ARQ duration includes child startup/exit; DB duration starts after Python imports.
    repo = ROOT.parents[1]
    save('code-state.json', {
        'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        'uncommitted': True,
        'tested_image': subprocess.check_output(['docker', 'inspect', 'forma-excel100-after-api-1',
                                                '--format', '{{.Image}}'], text=True).strip(),
        'source_sha256': {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            repo / 'backend/src/forma/services/mapping.py', repo / 'backend/src/forma/services/review_evidence.py',
            repo / 'backend/src/forma/ai/evidence.py']}})
    log = subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compose.yml'), '-f',
                          str(ROOT / 'compose.after.yml'), 'logs', '--no-color', 'worker'],
                         cwd=repo, capture_output=True, text=True, encoding='utf-8', check=True).stdout
    (RUN / 'worker.log').write_text(log, encoding='utf-8')
    arq = {job: float(seconds) for seconds, job in re.findall(
        r'\s([\d.]+)s ← (job_\w+):\d+:arq_run_job', log)}
    matched = [{'id': j['id'], 'arq_s': arq[j['id']], 'db_s': j['duration_ms'] / 1000,
                'startup_exit_overhead_s': max(0, arq[j['id']] - j['duration_ms'] / 1000)}
               for j in jobs if j['id'] in arq and j.get('duration_ms') is not None]
    timing = {'wall_s': wall, 'queue_wait': distribution(wait), 'db_job': distribution(runtime),
              'arq_job': distribution(list(arq.values())),
              'startup_exit_overhead': distribution([j['startup_exit_overhead_s'] for j in matched]),
              'stages': {k: distribution(v) for k, v in stages.items()}, 'matched_jobs': matched,
              'stage_jobs': stage_rows}
    if (RUN / 'startup-probe.json').exists():
        timing['cold_import_probe'] = read(RUN / 'startup-probe.json')
    save('timing.json', timing)
    before_q = sum(read(ROOT / 'run' / s['id'] / 'result.json').get('question_count', 0)
                   for s in manifest['files'])
    evidence = read(RUN / 'evidence-audit.json')
    questions = sum(r.get('question_count', 0) for r in results)
    cost = 0
    resolved = Counter()
    metrics = []
    for r in results:
        before = read(RUN / r['id'] / 'before.json')
        mapping = before.get('mapping') or {}
        policy = mapping.get('policy') or {}
        resolved.update((policy.get('agreement') or {}).get('resolved_by_evidence', {}).keys())
        for log_entry in mapping.get('agent_log', []):
            cost += (log_entry.get('usage') or {}).get('cost_usd', 0) or 0
        old = read(ROOT / 'run' / r['id'] / 'result.json')
        out = next(e for e in exports if e['id'] == r['id'])
        metrics.append({'file': r['id'], 'family': r['family'], 'expected': r['expected_action'],
                        'questions_before': old.get('question_count', 0),
                        'questions_after': r.get('question_count', 0),
                        'question_types': dict(Counter(q['kind'] for q in r.get('questions', []))),
                        'flow_pass': r.get('flow_pass', False), 'guard_pass': r.get('guard_pass'),
                        'unit_asked': r.get('ask_pass'), 'display_preview_exact': r.get('display_preview_exact'),
                        'answer_preview_exact': r.get('preview_exact'),
                        'stale_preview_rejected': r.get('stale_preview_rejected'),
                        'export_rows': out.get('score', {}).get('rows_got'),
                        'export_accuracy': out.get('score', {}).get('field_accuracy'), 'error': r.get('error')})
    save('case-metrics.json', metrics)
    with (RUN / 'case-results.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    summary = {'files': len(results), 'normal_flow_pass': sum(bool(r.get('flow_pass')) for r in results),
               'correct_display_previews': sum(bool(r.get('display_preview_exact')) for r in results),
               'correct_answer_previews': sum(bool(r.get('preview_exact')) for r in results),
               'stale_previews_rejected': sum(bool(r.get('stale_preview_rejected')) for r in results),
               'expected_guard_pass': sum(bool(r.get('guard_pass')) for r in results),
               'unit_question_pass': sum(bool(r.get('ask_pass')) for r in results),
               'final_exported': len(included), 'final_exact_exports': sum(e['score']['exact'] for e in included),
               'export_rows': sum(e['score']['rows_got'] for e in included),
               'correct_fields': sum(e['score']['correct_fields'] for e in included),
               'total_fields': sum(e['score']['total_fields'] for e in included),
               'question_count_before': before_q, 'question_count_after': questions,
               'resolved_evidence': dict(resolved), 'cost_usd': cost,
               'jobs': dict(Counter(j['status'] for j in jobs)),
               'source_hashes_ok': intact and fixture_intact,
               'export_hashes_ok': all(e['hash_ok'] and e['versions_present'] for e in included),
               'errors': {r['id']: r['error'] for r in results if r.get('error')},
               'evidence_examples': len(evidence), 'evidence_passed': sum(e['passed'] for e in evidence),
               'wall_s': wall, 'fake_requests': read(RUN / 'provider-stats.json')['requests']}
    save('verified-summary.json', summary)
    lines = [
        '# ผลแก้ไขและทดสอบซ้ำ Excel 100 ไฟล์ — 5 พฤศจิกายน 2026', '',
        'ใช้ไฟล์เดิม 100 XLSX / 520 ชีต / 17,654 แถวจริง ชุดใหม่อยู่ในฐานข้อมูลแยก ใช้โมเดลจำลองและ Docker internal network ค่า API $0 ไม่มีการส่งไฟล์ไป DeepSeek', '',
        '## ก่อน–หลัง', '',
        '| สิ่งที่ตรวจ | ก่อน | หลัง |', '|---|---:|---:|',
        f'| ผ่าน flow ปกติใน 80 ไฟล์ที่ควรสำเร็จ | {baseline["normal_flow_pass"]}/80 | {summary["normal_flow_pass"]}/80 |',
        f'| ผ่าน flow พร้อม preview ถูกต้อง | {baseline["normal_flow_with_correct_preview"]}/80 | {summary["correct_display_previews"]}/80 |',
        f'| ระงับไฟล์ราคาซ้ำ/สูตรไม่มี cache | {baseline["expected_guard_pass"]}/20 | {summary["expected_guard_pass"]}/20 |',
        f'| ถามหน่วย ZQ ก่อนเผยแพร่ | {baseline["unit_question_pass"]}/10 | {summary["unit_question_pass"]}/10 |',
        f'| คำถามแบบเลือก/ยืนยัน/กรอกบนการ์ด รวมทุกไฟล์ | {before_q} | {questions} |',
        f'| JSON ส่งออกตรงเฉลย | {baseline["final_exact_exports"]}/80 หลังวินิจฉัยข้าม preview 37 ไฟล์ | {summary["final_exact_exports"]}/80 ผ่านขั้นตอนปกติ |', '',
        f'ผลหลังแก้: {summary["export_rows"]:,} สินค้า, ถูก {summary["correct_fields"]:,}/{summary["total_fields"]:,} ค่า; ตรวจ hash ไฟล์ต้นทางและ ZIP พร้อม mapping/dataset version: {summary["source_hashes_ok"] and summary["export_hashes_ok"]}. งาน: {summary["jobs"]}.', '',
        '## สิ่งที่แก้', '',
        '1. Preview และหลักฐานคำถามประมวลผล source ครบก่อนจำกัดรายการแสดง จึงไม่ตัด lookup หรือบางล็อตออกจากยอดรวม ตรวจความถูกต้องทั้งไฟล์ รวมแถวที่ไม่ได้แสดงบนหน้าจอ',
        '2. Answer preview ใช้ผลคำนวณก่อน/หลังร่วมกับตัวตรวจ แทนการรันซ้ำ 4 ครั้ง เหลือ 2 ครั้งต่อคำขอ Apply ยังคำนวณใหม่และตรวจ digest เพื่อกันผลล้าสมัย',
        '3. ยอมรับช่วงคอลัมน์ที่เท่ากันเมื่อ metadata พิสูจน์ว่าอ่านเต็มชีต และยุติข้อขัดแย้งสกุลเงินเฉพาะเมื่อชื่อคอลัมน์ตรง schema และค่าทั้งไฟล์ตรงกัน ไม่ยกเลิกคำถามที่หน่วย/ราคา/ความหมายยังคลุมเครือ',
        '4. แก้ข้อความ UI ให้แยกผลตรวจเต็มไฟล์กับตัวอย่างรายการแสดง และทดสอบซ้ำผ่าน upload → mapping → questions → preview → apply/version → approval → export → ZIP', '',
        f'ยืนยัน preview คำตอบ {summary["correct_answer_previews"]} ไฟล์ และปฏิเสธ digest เก่า {summary["stale_previews_rejected"]} ครั้ง; Mapping Studio preview ขนาด 40 รายการตรวจครบทุกไฟล์ที่ควรผ่าน แม้ไม่มีคำถาม.', '',
        f'ตรวจหลักฐานเซลล์จริงผ่าน API ของไฟล์รวมล็อตทั้ง 10 ไฟล์: {summary["evidence_passed"]}/{summary["evidence_examples"]} ตัวอย่างมีเซลล์ครบ 3 ล็อตและรวมได้ตรงค่าที่แสดง ไม่ชี้แค่บางแถว.', '',
        '## คอขวดความเร็ว', '',
        f'รอบนี้ตั้ง worker 2 ตัว × 2 slots: เวลาตั้งแต่งานแรกเข้าคิวจนงานสุดท้ายเสร็จ {wall:.1f} วินาที ({wall/60:.1f} นาที) รวมเวลาผู้ทดสอบอัตโนมัติเรียก preview/ตอบ/อนุมัติ ไม่ใช่เวลา AI ล้วน',
        '', '| ส่วนงาน | ค่ากลาง (วินาที) | P95 (วินาที) | สูงสุด (วินาที) |', '|---|---:|---:|---:|',
        *[f'| {label} | {timing[key]["median_s"]:.3f} | {timing[key]["p95_s"]:.3f} | {timing[key]["max_s"]:.3f} |'
          for label, key in [('ระยะทำงานที่ DB บันทึก', 'db_job'), ('ระยะถือ slot ของ ARQ', 'arq_job'),
                             ('ส่วนต่างเริ่ม/ปิด process และงานรอบนอก', 'startup_exit_overhead'),
                             ('รอก่อนเริ่มงาน รวม import ก่อน claim', 'queue_wait')]],
        *[f'| ขั้น {stage} | {v["median_s"]:.3f} | {v["p95_s"]:.3f} | {v["max_s"]:.3f} |'
          for stage, v in timing['stages'].items()], '',
        f'วัด import Python ใหม่โดยไม่รันงาน/โมเดลอีก 3 ครั้ง ได้ {timing.get("cold_import_probe", {}).get("samples_s")} วินาที; ค่ากลาง {timing.get("cold_import_probe", {}).get("median_s")} วินาที', '',
        'ARQ เปิด Python process ใหม่ทุกงานเพื่อให้หยุดงานค้างได้จริง ส่วนต่างระหว่าง ARQ/DB จึงบอก overhead รอบงาน ไม่ใช่เวลาคำนวณ Excel เพียงอย่างเดียว คิวรับไฟล์และคิวประมวลผล/ส่งออกใช้ slots ร่วมกัน', '',
        'หลักฐาน DeepSeek เดิม (workflow_v1/wf-after-summary.json): 10 กรณีซับซ้อนรวม 2,187.62 วินาที / 69 model calls (~219 วินาทีต่อกรณีรวม flow) รอบนี้ไม่ได้เรียกซ้ำ จึงไม่ใช้เวลาของ fake ทำนายความเร็ว DeepSeek หรืออ้างว่า AI เร็วขึ้นเท่านี้',
        'แนวทางเร่งต่อ: ลดการอ่าน/ส่งบริบท/เรียกโมเดลซ้ำอย่างมีหลักฐาน, ใช้ mapping ที่อนุมัติแล้วกับ format ที่เข้ากันได้, วัดและแยกคิวรับไฟล์กับคิวส่งออก และพิจารณา worker process ที่อุ่นไว้แต่ยังหยุดแยกงานได้ การเปลี่ยน worker ต้องทดสอบ timeout/cancel/recovery ใหม่', '',
        '## ขอบเขตการพิสูจน์', '',
        'fake provider คืน DSL ที่เตรียมไว้และมองไม่เห็น oracle ระบบจริงเป็นผู้อ่าน Excel/เชื่อมชีต/รวมข้อมูลและสร้าง JSON; การตรวจ oracle ทำใน test harness หลังได้รับผลเท่านั้น คะแนนนี้พิสูจน์ความถูกต้องของ execution และ flow ไม่ใช่ความฉลาดของ DeepSeek ต่อ unknown format',
        'ไฟล์รวมประมาณ 1 MB และมี 12–48 สินค้าต่อไฟล์ จึงยังไม่พิสูจน์หน่วยความจำหรือเวลา Excel ขนาดใหญ่มาก ยังไม่มีไฟล์ ERP จริงตามคำยืนยันผู้ใช้ จึงยังต้อง pilot 5–10 ไฟล์จริงพร้อมผลที่ทีมปลายทางตรวจ เมื่อมีข้อมูล',
        'ทดสอบผ่าน HTTP/API ตามเงื่อนไข UI ไม่ได้คลิกเบราว์เซอร์ 100 ครั้ง ไม่ได้ commit งานรอบนี้', '',
        'การตรวจโค้ด: regression tests ที่เกี่ยวข้อง 100 ข้อผ่าน (preview/evidence, mapping harness, relationship, decisions/memory, lineage, custom schema และ review queue), Ruff และ mypy ผ่านสำหรับ backend ที่แก้, frontend TypeScript/Vite build ผ่าน มีคำเตือน bundle ขนาดใหญ่เดิม', '',
        'อัปเดต API, worker และเว็บที่ localhost แล้ว ตรวจ /ready ผ่าน และยืนยันข้อความ preview ใหม่ใน frontend; รุ่นที่ตั้งไว้ยังเป็น deepseek-flash ไม่มีการรันงานผ่านโมเดลจริง ดูหลักฐาน run-after/localhost-check.json', '',
        '## ผลรายไฟล์', '',
        '| ไฟล์ | กลุ่ม | คำถามก่อน→หลัง | ผ่าน flow | Preview | ระงับถูกจุด | จำนวน JSON | ถูกต้อง |',
        '|---|---:|---:|---|---|---|---:|---:|',
    ]
    for r in metrics:
        lines.append(f'| {r["file"]} | {r["family"]} | {r["questions_before"]}→{r["questions_after"]} | {r["flow_pass"]} | {r["display_preview_exact"]} | {r["guard_pass"]} | {r["export_rows"]} | {r["export_accuracy"]} |')
    (ROOT / 'REPORT-AFTER.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert summary['normal_flow_pass'] == summary['correct_display_previews'] == summary['final_exact_exports'] == 80
    assert summary['expected_guard_pass'] == 20 and summary['unit_question_pass'] == 10
    assert summary['evidence_passed'] == summary['evidence_examples'] == 50
    assert not summary['errors'] and summary['source_hashes_ok'] and summary['export_hashes_ok']
    assert set(summary['jobs']) == {'done'} and summary['cost_usd'] == 0


if __name__ == '__main__':
    main()
