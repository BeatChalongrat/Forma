"""Build the Thai before/after report from frozen snapshots, without changing predictions."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def pct(value):
    return f'{value * 100:.2f}%'


def compact_check(row):
    c = row.get('self_check') or {}
    return '/'.join(str(c.get(k, '—')) for k in ('fix_rounds', 'grounded', 'python_turns'))


def main():
    old = read(ROOT / 'runs/rel-20261103-b/summary.json')
    new = read(ROOT / 'runs/rel-20261104-fixed2/summary.json')
    by_old = {r['case']: r for r in old}
    total = sum(r['score']['expected_fields'] for r in new)
    old_right = sum(r['score']['correct_fields'] for r in old)
    new_right = sum(r['score']['correct_fields'] for r in new)
    assisted = {}
    for row in new:
        p = ROOT / 'runs/rel-20261104-fixed2' / (row['case'] + '_r1') / 'score-after-human.json'
        if p.exists():
            assisted[row['case']] = read(p)
    assisted_right = sum(assisted.get(r['case'], r['score'])['correct_fields'] for r in new)
    comparable = {r['case'] for r in old if r['score']['rows_got'] > 0}
    common_old = [r for r in old if r['case'] in comparable]
    common_new = [r for r in new if r['case'] in comparable]
    common_total = sum(r['score']['expected_fields'] for r in common_old)
    lines = [
        '# ผลก่อน–หลังแก้ FORMA: ไฟล์ที่มีความสัมพันธ์ซับซ้อน',
        '',
        'วันที่ 4 พฤศจิกายน 2026 · โมเดล deepseek-flash · ไม่ commit · ใช้ Docker stack จริง',
        '',
        '## วิธีอ่านผล',
        '',
        'ทดสอบ 12 กรณี / 14 ไฟล์ Excel / 30 ชีต / กรณีละ 12 สินค้า โดยใช้ไฟล์และ schema เดิมที่ตรวจ SHA-256 แล้ว '
        'แต่สร้างลูกค้าและรอบใหม่ แยกคำทำนายก่อนคนช่วยออกจากหลังคนตอบ ระบบเห็นเฉพาะไฟล์ โจทย์ และ schema '
        'ไม่ส่ง expected JSON หรือคำตอบที่ซ่อนไว้ให้โมเดล ไม่มีการอนุมัติหรือส่งออกชุดทดสอบนี้เป็นข้อมูลใช้งาน',
        '',
        '**ตัวเลขนี้เป็นผลชุดจำลอง ไม่ใช่เปอร์เซ็นต์ความพร้อมใช้จริงหรือหลักฐานว่าดีกว่า ChatGPT/Claude** '
        'ฐานก่อนแก้มี API 503/วงจรพัก provider หลายกรณี ดังนั้นส่วนที่ดีขึ้นเกิดจากทั้งการแก้ระบบและรอบ provider ที่สำเร็จ '
        'ไม่สามารถยกทั้งหมดเป็นความฉลาดของโมเดลที่เพิ่มขึ้นได้',
        '',
        f'- Field accuracy แบบนับทุกค่ารวมกัน ก่อนแก้: **{pct(old_right/total)}** ({old_right}/{total})',
        f'- หลังแก้ ก่อนคนช่วย: **{pct(new_right/total)}** ({new_right}/{total})',
        f'- หลังตอบคำถามจำลองที่ตรวจแล้ว: **{pct(assisted_right/total)}** ({assisted_right}/{total})',
        f'- เฉพาะ {len(comparable)} กรณีที่ฐานเดิมสร้างผลได้แล้ว เปรียบเทียบก่อนคนช่วย: '
        f"{pct(sum(r['score']['correct_fields'] for r in common_old)/common_total)} → "
        f"{pct(sum(r['score']['correct_fields'] for r in common_new)/common_total)}. "
        'ความก้าวหน้าหลักรอบนี้คือทำกรณีที่เคยไปต่อไม่ได้ให้ตรวจสอบและดำเนินการได้ ไม่ใช่หลักฐานว่าเปลี่ยนโมเดลให้ฉลาดขึ้นสองเท่า',
        '- การเก็บ null และขอไฟล์ที่หายตามเฉลยของ T08 นับเป็นการหยุดอย่างถูกต้อง ไม่ใช่ได้จำนวนสินค้าที่พร้อมใช้งาน',
        '',
        '## 6 เรื่องที่แก้',
        '',
        '| เรื่อง | ก่อน | หลัง |',
        '|---|---|---|',
        '| วันที่มีผลของราคา | comparison ส่ง Date ผ่านตัวแปลงตัวเลข ทำให้กรองผิด | เทียบ Date/Datetime ตามชนิดจริง พร้อมทดสอบวันที่และตัวเลขแยกกัน |',
        '| สูตร Excel | สูตรไม่มี cache แม้ไม่ใช้งานอาจบล็อกทั้งชีต | ตรวจคอลัมน์ที่ใช้จริง; คอลัมน์ที่ใช้แล้วมี error/ไม่มี cache/อ้างไฟล์ภายนอกยังบล็อกพร้อมตำแหน่งเซลล์; AI สร้าง DSL จากตัวตั้งที่ครบได้ |',
        '| คำถามค้าง/ความเห็นไม่ตรง | ชื่อ alias ใหม่ถูกมองเป็นคนละคอลัมน์ และคำถาม heuristic ค้าง | เปรียบเทียบชื่อจริงหลัง rename และปิดเฉพาะคำถามโครงสร้างที่พิสูจน์ได้ ไม่ปิดเรื่อง VAT/ความหมายรหัสเพราะมั่นใจเฉย ๆ |',
        '| Provider และเวลารอ | response ผิดรูปแบบ/keepalive อาจรอนานหรือผิดพลาดไม่ชัด | ตรวจรูปแบบ response, wall-clock timeout ต่อ call และงบเวลารวม, แสดงขั้นตอนที่ worker กำลังทำ |',
        '| การอนุมัติ | คนอาจยืนยันข้าม verification ที่ล้มเหลว | ปฏิเสธทั้ง API และ UI เมื่อ verification ไม่ผ่าน/ไม่ครบ แม้ส่ง override; ขอไฟล์ต้นทางไม่ใช่กรอกค่าทับสูตร |',
        '| ความสัมพันธ์และการตัดสินใจ | กรณีราคาชน/สต๊อกขาด/รวมล็อตเคยถูก provider บัง | ทดสอบครบ; เพิ่ม aggregate แบบระบุ group+sum, รักษาแถวต้นทางทุกล็อต, ราคาแข่งขันกันให้เลือกแถว ไม่รวมราคาและไม่ทับทุก SKU |',
        '',
        '## ผลรายกรณี ก่อนคนช่วย',
        '',
        'การ์ด/หมายเหตุ = จำนวนคำถามบนการ์ด / open_questions; self-check = fix_rounds / grounded / python_turns; '
        'เวลาเป็นวินาทีตั้งแต่เริ่มกรณีจน job propose เสร็จ รวมอัปโหลด/profile/คิว ไม่ใช่เฉพาะเวลาโมเดล',
        '',
        '| กรณี | ก่อน | หลัง | แถว ก่อน→หลัง | การ์ด/หมายเหตุ ก่อน→หลัง | verify หลัง | self-check หลัง | เวลา ก่อน→หลัง (s) |',
        '|---|---:|---:|---|---|---|---|---|',
    ]
    for r in new:
        b = by_old[r['case']]
        lines.append(f"| {r['case']} {r['title']} | {pct(b['score']['field_accuracy'])} | {pct(r['score']['field_accuracy'])} | "
                     f"{b['score']['rows_got']}→{r['score']['rows_got']} | {len(b['questions'])}/{len(b['open_questions'])}→{len(r['questions'])}/{len(r['open_questions'])} | "
                     f"{(r.get('verification') or {}).get('passed')} | {compact_check(r)} | {b['elapsed_seconds']}→{r['elapsed_seconds']} |")
    lines += ['', '## คำถาม ความเสี่ยง และรายละเอียดคำตอบ', '']
    for r in new:
        c = r['case']
        lines += [f'### {c}: {r["title"]}', '',
                  f"- decision: `{r['decision']}` · disagree: {r['disagree_count']} · flags: {', '.join(r['flags']) or 'ไม่มี'}",
                  f"- งาน profile/propose: {r.get('profile_job', {}).get('status')}/{r.get('propose_job', {}).get('status')}"]
        for q in r['questions']:
            text = q.get('question') or q.get('title') or q['id']
            lines.append(f"- คำถาม `{q['id']}`: {text} · kind={q['kind']} · action={q['action']} · contested={q.get('contested')}")
            if q.get('note'):
                lines.append(f"  - หลักฐาน/เหตุผล: {q['note']}")
        for q in r['open_questions']:
            lines.append(f'- หมายเหตุค้าง: {q}')
        for problem in (r.get('verification') or {}).get('problems', []):
            lines.append(f'- ไม่ผ่านการตรวจ: {problem}')
        if r['score']['wrong']:
            lines += ['- ค่าที่ยังไม่ตรงเฉลยก่อนคนช่วย:', '```json', json.dumps(r['score']['wrong'], ensure_ascii=False, indent=2), '```']
        if c in assisted:
            s = assisted[c]
            snap = read(ROOT / 'runs/rel-20261104-fixed2' / (c + '_r1') / 'after-human.json')
            lines.append(f"- หลังตอบจริงผ่าน preview/apply: {pct(s['field_accuracy'])}, {s['rows_got']} แถว, "
                         f"verification={snap['mapping']['policy']['verification']['passed']}, "
                         f"การ์ดเหลือ {len(snap['questions']['questions'])}, หมายเหตุเหลือ {len(snap['questions']['open_questions'])}")
        lines += [f"- หลักฐาน: [snapshot](runs/rel-20261104-fixed2/{c}_r1/before.json) · [score](runs/rel-20261104-fixed2/{c}_r1/score-reviewed.json)", '']
    lines += ['## ต้นทุนและขอบเขต', '',
              f"- cost_usd ที่บันทึกใน mapping.agent_log รอบแก้ครั้งแรก: ${sum(r['cost_usd'] for r in read(ROOT / 'runs/rel-20261104-fixed1/summary.json')):.6f}",
              f"- cost_usd ที่บันทึกใน mapping.agent_log รอบยืนยัน: ${sum(r['cost_usd'] for r in new):.6f}",
              '- ตัวเลขเป็นประมาณการจาก usage ที่ระบบบันทึก ไม่ใช่ใบแจ้งหนี้; call ที่ล้มเหลวก่อนส่ง usage อาจมีต้นทุนที่ไม่ปรากฏ '
              'และ ledger รวม schema/agent อื่นจึงอาจต่างจาก mapping.agent_log',
              '- `source-repair-guard.json` บันทึกการลอง confirm ข้อมูลต้นทางที่หาย: API ต้องตอบ 422 ไม่สร้างเวอร์ชันใหม่',
              '- ชุดทดสอบไม่มีไฟล์ลูกค้าจริง จึงยังไม่ครอบคลุม Excel ทุก format, macro, encrypted workbooks, ขนาดหลายล้านแถว '
              'หรือ network/provider outage ทุกแบบ',
              '- ไม่ใช่เครื่องคำนวณสูตร Excel ทุกชนิด: ถ้าค่าไม่ครบหรือสูตรแปลงเป็น DSL ที่รองรับไม่ได้ ต้องซ่อม/คำนวณต้นทางแล้วส่งใหม่',
              '- Legacy mapping ที่ไม่มี verification snapshot ยังมีเส้นทาง compatibility เดิม ต้องทดสอบ/ตรวจใหม่ก่อนนำไปใช้เป็นหลักฐานความถูกต้อง',
              '', '## การตรวจโค้ดและข้อจำกัดคงเหลือ', '',
              'ส่วนนี้ให้เติมผลตรวจรอบสุดท้ายและสิ่งที่พบจากการตรวจด้วยคนก่อนส่งรายงาน', '']
    (ROOT / 'REPORT-BEFORE-AFTER.md').write_text('\n'.join(lines), encoding='utf-8')
    print('Wrote REPORT-BEFORE-AFTER.md', old_right, new_right, assisted_right, total)


if __name__ == '__main__':
    main()
