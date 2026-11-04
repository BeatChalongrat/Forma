"""Render the frozen A/B data and the separately retained corrective retest."""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def question_types(row):
    counts = Counter(q['kind'] for q in row['questions'])
    return ', '.join(f'{k}:{v}' for k, v in sorted(counts.items())) or '0'


def main():
    before, after, fix = [read(f'{run}-summary.json') for run in ['wf-before', 'wf-after', 'wf-evidence-fix']]
    assert before['completed'] == after['completed'] == 10 and fix['completed'] >= 1
    b = {r['case']: r for r in before['cases']}
    a = {r['case']: r for r in after['cases']}
    f = {r['case']: r for r in fix['cases']}
    total_cost = sum(s['usage']['cost_usd'] for s in [before, after, fix])
    total_calls = sum(s['usage']['calls'] for s in [before, after, fix])
    text = [
        '# ผลทดสอบ LangGraph + 3 roles กับ DeepSeek เดิม — 4 พฤศจิกายน 2026',
        '',
        '## สรุปผลที่วัดได้',
        '',
        f"- ก่อนเพิ่ม graph: **{before['weighted_accuracy']:.2%}** ({before['correct']}/{before['total']} ค่าที่ตรวจ)",
        f"- หลังเพิ่ม graph รอบเปรียบเทียบครบชุด: **{after['weighted_accuracy']:.2%}** ({after['correct']}/{after['total']})",
        '- คะแนนนี้เป็นความตรงของค่ากับเฉลย ก่อนคนตอบคำถาม ไม่ใช่เปอร์เซ็นต์ความพร้อมใช้จริง',
        '- T09 และ T10 เป็นเรื่องที่ต้องให้คนตัดสิน ตรวจคำถามและทดสอบ preview/apply แยกจากคะแนนดิบ',
        '- กรณี T08 ไม่มีไฟล์อ้างอิง: เฉลยยอมรับค่า null จึงได้คะแนนเต็ม แต่ผลยังไม่พร้อมใช้งานและต้องบล็อกไว้',
        '- ผลชุดนี้ยังไม่พิสูจน์ว่าเพิ่ม agent แล้ว map แม่นขึ้น หรือแม่นกว่า ChatGPT/Claude',
        '',
        '## วิธีทดสอบ',
        '',
        'รันผ่าน API → PostgreSQL/Redis/ARQ worker → DeepSeek → เครื่องมืออ่านไฟล์/sandbox → engine ตรวจข้อมูลจริง '
        'บน stack ในเครื่อง ใช้ `deepseek-flash` และไม่มี fallback model ทั้งสองฝั่ง '
        'รันเรียงทีละกรณี ลูกค้าแยกกัน ไม่อนุมัติหรือส่งออกข้อมูล ใช้ไฟล์และ JSON contract ชุดเดียวกัน '
        'ตรวจ hash ก่อนรันและเก็บ proposal ก่อนอ่านเฉลย',
        '',
        'เลือก 10 กรณี รอบ r1: key หลายคอลัมน์, merged headers, สูตรค่าค้าง, สูตรไม่มี cache, '
        'อ้าง workbook ที่หายไป, หน่วย BT, ราคาขัดกัน, หลายชีต/ล็อต/ช่วงวันที่, CSV อินโดนีเซีย และ TSV ภาษาญี่ปุ่น UTF-16 '
        'ทุกกรณีมีสินค้า 12 รายการ ชุดนี้เป็น regression ที่เคยใช้พัฒนาระบบ ไม่ใช่ holdout ใหม่ '
        'ไม่ได้รันครบ 25 กรณีหรือทุก r2/r3 และไม่ได้ทดสอบโหลดพร้อมกัน 300 ไฟล์',
        '',
        'ก่อน = `wf-before` โหมด legacy หลัง = `wf-after` graph รุ่น `2026.11.04.1` '
        'ส่วน `wf-evidence-fix` เป็นการรันซ้ำเฉพาะกรณีหลังแก้การส่งต่อหลักฐาน รุ่น `2026.11.04.2` '
        'เก็บผลรอบแรกไว้ทั้งหมด ไม่แทนที่ผลที่พบปัญหา ไม่ควรเรียกผลแบบผสมนี้ว่าการรันครบชุดบนรุ่นล่าสุด',
        '',
        '## ความถูกต้องและคำถามต่อกรณี',
        '',
        '| กรณี | ก่อน | หลังรอบแรก | แถวหลัง | คำถามก่อน → หลัง (ชนิด:จำนวน) | Verification หลัง |',
        '|---|---:|---:|---:|---|---|',
    ]
    for case in sorted(a):
        x, y = b[case], a[case]
        text.append(f"| {case} | {x['accuracy']:.2%} | {y['accuracy']:.2%} | {y['rows']} | {question_types(x)} → {question_types(y)} | {(y.get('verification') or {}).get('passed')} |")
    text += ['', '## เวลาและต้นทุน', '',
             '| กรณี | วินาทีก่อน → หลัง | calls ที่มี usage ก่อน → หลัง | cost_usd ก่อน → หลัง |',
             '|---|---:|---:|---:|']
    for case in sorted(a):
        x, y = b[case], a[case]
        text.append(f"| {case} | {x['seconds']:.1f} → {y['seconds']:.1f} | {x['usage']['calls']} → {y['usage']['calls']} | {x['usage']['cost_usd']:.4f} → {y['usage']['cost_usd']:.4f} |")
    text += ['',
             f"รวมรอบเปรียบเทียบ: {before['seconds']/60:.2f} → {after['seconds']/60:.2f} นาที; "
             f"{before['usage']['calls']} → {after['usage']['calls']} calls; "
             f"cost_usd {before['usage']['cost_usd']:.4f} → {after['usage']['cost_usd']:.4f} "
             f"({after['usage']['cost_usd']/before['usage']['cost_usd']:.2f} เท่า)", '',
             f"รวมการทดลองทั้งก่อน หลัง และรอบแก้เพิ่มเติม: **{total_calls} calls ที่มี usage; cost_usd {total_cost:.4f}**", '',
             '**cost_usd ไม่ใช่ยอดเรียกเก็บจริงจาก DeepSeek:** โมเดลนี้ไม่มีราคาในตาราง PRICING เดิม '
             'จึงใช้ fallback $1/input 1M tokens และ $4/output 1M tokens; ไม่ได้หัก cache discount '
             'งบใน runner เป็น checkpoint หยุดระหว่างกรณี ไม่ใช่เพดานค่าใช้จ่ายจากผู้ให้บริการ '
             'กรณี T12 ฝั่ง graph มี 1 attempt ที่ไม่มี usage ตอบกลับ จึงไม่ทราบว่าคิดเงินเท่าใด '
             'ตัวเลขรวมไม่รวมต้นทุนที่ไม่ทราบนี้ และ legacy ไม่มี ledger เทียบ failed attempts ได้ครบ '
             'เวลาเป็น wall-clock จากการทดลองครั้งเดียว รวม API/งานในเครื่องและความผันผวนของโมเดล ไม่ใช่ latency benchmark',
             '', '## สิ่งที่พบและแก้ระหว่างทดสอบ', '',
             'T06 mapper เปิดสูตรใน sandbox และคำนวณใหม่ถูกแล้ว แต่ reviewer ไม่ได้รับ tool receipt '
             'จึงถามให้เลือก cached value กับผลคำนวณใหม่ ทั้งที่สูตรมีหลักฐานอยู่จริง '
             'แก้โดยส่งผล inspection และ code/output ของ sandbox พร้อม source IDs/hashes ให้ reviewer '
             'รวมทั้งส่ง scoped operator decisions เพื่อไม่ทำให้ความรู้ที่เคยยืนยันหายไประหว่าง roles '
             'ยังไม่ส่ง confidence หรือคำสรุปของ mapper ให้เป็นเหตุผลในการผ่านตรวจ '
             'เพิ่ม validation ป้องกัน confirm card ที่เสนอทางเลือกซึ่ง action นั้นเปลี่ยนค่าให้ไม่ได้',
             '', '| กรณีรันทวนรุ่น .2 | ความถูกต้อง | คำถาม | เวลา (วินาที) | calls | cost_usd |',
             '|---|---:|---|---:|---:|---:|']
    for case, row in sorted(f.items()):
        text.append(f"| {case} | {row['accuracy']:.2%} | {question_types(row)} | {row['seconds']:.1f} | {row['usage']['calls']} | {row['usage']['cost_usd']:.4f} |")
    sensitivity = read('sensitivity-result.json')
    text += ['', '## การตรวจว่า mapping ตอบสนองต่อข้อมูลรอบใหม่จริง', '',
             'ใน T12 reviewer พบว่า candidate ฉบับแรกฝัง pack size เป็น literal lookup แม้ผลรอบนี้ถูก '
             'จึงให้แก้เป็น join กับชีต Pack sizes แล้วตรวจซ้ำ ผลปลายทางยังถูกครบ '
             'ทดสอบเพิ่มเติมโดยใช้ engine เดิมและเปลี่ยน pack size ของ SKU 000001 จาก 12 เป็น 18 ในหน่วยความจำ '
             'ไม่แก้ fixture และไม่เรียกโมเดล:', '',
             f"- ค่าที่ควรได้จากจำนวนในแต่ละล็อต: `{sensitivity['expected_from_lot_rows']}`",
             f"- Candidate ก่อน reviewer แก้: `{sensitivity['actual']['graph_initial']}` — ไม่เปลี่ยนตามต้นทาง",
             f"- หลัง reviewer แก้: `{sensitivity['actual']['wf-after']}` — เปลี่ยนถูกต้อง",
             f"- Legacy ในรอบ baseline: `{sensitivity['actual']['wf-before']}` — ทำถูกอยู่แล้วเช่นกัน", '',
             'นี่เป็นหลักฐานว่า reviewer จับข้อผิดพลาดเชิงการนำ mapping กลับมาใช้ได้ใน candidate หนึ่ง '
             'ไม่ได้พิสูจน์ว่า graph ชนะ legacy โดยรวม หรือรับประกันการเปลี่ยนข้อมูลทุกแบบ '
             'ไฟล์ตรวจซ้ำ: `sensitivity.py`, `sensitivity-result.json`, `t12-initial-mapping.json`',
             '', '## ทดสอบ human in the loop', '',
             '| ฝั่ง | กรณี | หลังตอบ | แถว | การตรวจผลใหม่ | คำถามคงเหลือ |',
             '|---|---|---:|---:|---|---:|']
    for arm in ['wf-before', 'wf-after']:
        for case in ['T09_r1', 'T10_r1']:
            score = read(f'runs/{arm}/{case}/score-after-human.json')
            snap = read(f'runs/{arm}/{case}/after-human.json')
            text.append(f"| {arm} | {case} | {score['field_accuracy']:.2%} | {score['rows_got']} | {snap['mapping']['policy']['verification']['passed']} | {len(snap['questions']['questions'])} |")
    text += ['',
             'T09 ตอบ BT = BOTTLE แบบ value_map; T10 เลือกแถวราคา 152 เฉพาะ SKU 000004 แบบ select_rows '
             'ไม่ตั้งราคาทุกสินค้าเป็น 152 ใช้ preview + digest + apply จริง สร้าง mapping version ใหม่ '
             'ไม่มี API call ไปยังโมเดลในขั้นตอนตอบ และไม่มีการ approve/export',
             '',
             'ยังพบคำถามราคาอีกหนึ่งคำถามค้างหลังเลือกแถว T10 ทั้งสองฝั่ง แม้ข้อมูลและ verification ถูกแล้ว '
             'เป็นงานลดคำถามซ้ำที่ยังต้องปรับ ไม่ควรแก้โดยล้างทุกคำถามของฟิลด์นั้น เพราะอาจลบปัญหาของสินค้าอื่น',
             '', '## ธงแดงและขอบเขตของผล', '']
    for s in [before, after, fix]:
        rows = [r for r in s['cases'] if 'accuracy' in r]
        flags = {
            'job_failed': [r['case'] for r in s['cases'] if r.get('failed_stage')],
            'rows_mismatch': [r['case'] for r in rows if r['rows'] != r['expected_rows']],
            'extra_rows': [r['case'] for r in rows if r['extra_rows']],
            'llm_failed': [r['case'] for r in rows if r['llm_failed']],
            'questions_over_8': [r['case'] for r in rows if len(r['questions']) > 8],
            'verification_false': [r['case'] for r in rows if not (r.get('verification') or {}).get('passed')],
            'unknown_usage_attempts': None if s['run'] == 'wf-before' else sum((r.get('workflow') or {}).get('usage_unknown_attempts', 0) for r in rows),
        }
        text.append(f"- `{s['run']}`: `{json.dumps(flags, ensure_ascii=False)}`")
    text += ['',
             'Verification false ของไฟล์อ้างอิงหายและราคาขัดกันเป็นการบล็อกที่จำเป็น '
             'workflow.status เป็นสถานะของ proposal ตอนนั้น ส่วนสถานะหลังคนตอบต้องดู verification ของ mapping version ใหม่ '
             'ทุกเคสชุดนี้อยู่ใน review จึงไม่ได้พิสูจน์ความปลอดภัยของ auto-approval ทุกสถานการณ์',
             '', '## สิ่งที่เพิ่มในระบบ', '',
             '1. LangGraph จริง: Evidence Analyst → Mapping Builder → engine ตรวจทั้งไฟล์ → Semantic Reviewer; '
             'ย้อนแก้ได้สูงสุด 1 รอบเมื่อพบ mapping error',
             '2. Checkpoint PostgreSQL สำหรับ production / SQLite สำหรับ dev; snapshot เปลี่ยนตามไฟล์ schema '
             'คำสั่ง ความรู้ โมเดลและเวอร์ชัน; มี lock กัน workflow เดียวทำซ้อน',
             '3. เพดาน 18 provider attempts และ deadline ถาวร; สำรองจำนวนครั้งก่อนเรียกจริง '
             'เก็บ usage แม้ JSON ไม่ผ่าน validation และระบุ usage ที่ไม่ทราบจากคำขอที่ขัดจังหวะ',
             '4. หลักฐานและเวลาแต่ละ role อยู่ใน agent_log / policy.workflow / inspection trace; '
             'คำถามใช้ระบบ preview/apply/versioning เดิม ไม่ได้เพิ่ม LangGraph interrupt สำหรับคนตอบ',
             '5. Format เดิมที่อนุมัติแล้วใช้ fast path เดิม ไม่เรียก 3 roles ซ้ำ; มี integration test ยืนยันว่า API calls ไม่เพิ่ม',
             '', '## การตรวจโค้ด', '',
             '- Backend regression: 890 passed, 5 skipped, 2 deselected; coverage 91.24% (เกณฑ์ 80%)',
             '- หลังแก้ handoff เพิ่มเติม: workflow tests 19 passed; mypy ผ่าน 140 source files; Ruff ผ่านไฟล์ที่เพิ่ม',
             '- PostgreSQL smoke test: checkpoint/attempt/usage อยู่ครบเมื่อปิดแล้วเปิด connection ใหม่ ไม่มี model call',
             '- Tests ครอบคลุม crash/resume, ไม่เรียกซ้ำเมื่อจบแล้ว, เปลี่ยน snapshot, budget/deadline หลัง restart, '
             'invalid evidence, bounded repair, failed execution, actionable questions และ upload/profile/propose API',
             '', '## ข้อสรุปสำหรับใช้งาน', '',
             'โครงสร้างพร้อมให้ทดลองแบบมีคนตรวจมากขึ้น: ดูย้อนหลัง แยกหน้าที่ ตรวจซ้ำ และจำกัดการลองได้ '
             'แต่ผลรอบนี้ยังไม่รองรับคำกล่าวว่าความแม่นยำสูงขึ้นจาก LangGraph และมีต้นทุน/เวลาสูงขึ้น '
             'ควรใช้กับ format ใหม่หรือเปลี่ยนที่ต้องตรวจละเอียด และรักษา fast path สำหรับ format ที่อนุมัติแล้ว',
             '',
             'งานที่ยังควรทำ: ลดคำถามซ้ำหลังแก้ปัญหา, เพิ่มเครื่องมืออ่านสูตรโดยตรงให้ reviewer ตรวจเองได้ง่าย, '
             'ตั้ง retention สำหรับ checkpoint ที่เก็บข้อมูลลูกค้า และเก็บ holdout/ไฟล์ ERP จริงที่ปกปิดข้อมูล '
             'ก่อนสรุปความพร้อม production การใช้โมเดลเดียวทุก role ยังอาจผิดแบบเดียวกันได้ '
             'ไม่ได้ทดสอบเหนือกว่า ChatGPT/Claude และไม่รับประกัน format ใหม่ทุกชนิด',
             '',
             'รายละเอียดสถาปัตยกรรม: [ADR-067](../../docs/adr/067-mapping-role-workflow.md). '
             'อ้างอิง [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) '
             'และ [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).',
             '', '**ยังไม่ commit และไม่ push**', '']
    (ROOT / 'REPORT.md').write_text('\n'.join(text), encoding='utf-8')


if __name__ == '__main__':
    main()
