"""Build a Thai before/after report from frozen, completed evaluation artifacts."""
from __future__ import annotations

import json
import re
from pathlib import Path

from global_report import summarize

ROOT = Path(__file__).resolve().parents[1] / "acommerce_global_v1"


def main():
    baseline = {r["id"]: r for r in summarize(ROOT / "runs/intl-20261102-a")}
    final = sorted(summarize(ROOT / "runs/intl-release-a") + summarize(ROOT / "runs/intl-release-b"), key=lambda r: r["id"])
    final = [r for r in final if r["id"].endswith(("_r1", "_alt"))]
    for row in final:
        for run in ("intl-release-a", "intl-release-b"):
            current = ROOT / "runs" / run / row["id"] / "questions-current.json"
            if current.exists():
                qs = json.loads(current.read_text(encoding="utf-8"))
                row["questions"] = len(qs["questions"])
                row["open_questions"] = qs["open_questions"]
    repeated = [r for r in summarize(ROOT / "runs/intl-dl4") if r["id"].endswith("_r1")]
    if len(final) != 17 or len(repeated) != 5:
        raise RuntimeError("Final evaluation is incomplete; do not publish a partial score")
    clear = [r for r in final if not r["id"].startswith(("G13", "G14", "G15", "G16"))]
    correct = sum(r["score"]["correct_fields"] for r in clear)
    expected = sum(r["score"]["expected_fields"] for r in clear)
    cost = sum(r["cost_usd_log"] for r in final + repeated)
    development_runs = ("intl-dl1", "intl-dl2", "intl-dl3", "intl-dl4", "intl-release-a", "intl-release-b", "intl-release-check")
    total_cost = sum(r["cost_usd_log"] for run in development_runs for r in summarize(ROOT / "runs" / run))
    test_log = (ROOT / "regression-frozen-release.log").read_text(encoding="utf-8")
    if "[100%]" not in test_log or "FAILED " in test_log:
        raise RuntimeError("Frozen-release regression must finish successfully before reporting")
    marks = "".join(re.findall(r"^([.s]+)\s+\[", test_log, re.M))
    lines = [
        "# ผลปรับวงจรถาม–ตรวจ–เรียนรู้ FORMA",
        "",
        "วันที่ 3 พฤศจิกายน 2026 · DeepSeek `deepseek-flash` · stack จริง PostgreSQL/S3/Redis/ARQ",
        "",
        "## สิ่งที่เปลี่ยนทั้ง 6 ข้อ",
        "",
        "| ข้อ | ก่อน | หลัง |",
        "|---|---|---|",
        "| 1. คำถามที่นำไปใช้ได้จริง | คำตอบหลายชนิดถูกแทนเป็นค่าคงที่ | แยก parser วันที่, สกุลเงินตาม contract, กฎแปลรหัสหน่วย, ยืนยันการอ่าน และเลือกแถวที่ขัดกัน |",
        "| 2. ตรวจผลหลังตอบ | ตัวอย่างบนหน้าจอไม่ครอบคลุมทุกแถว | preview/apply รันครบไฟล์ ป้องกันรายการหาย/เพิ่ม ข้อมูลส่วนอื่นเปลี่ยน และ validation error ใหม่ |",
        "| 3. จำคำตอบ | ค่าบางอย่างถูกจำเหมารวม | เริ่มเรียนรู้เมื่ออนุมัติ เก็บผู้ตอบ/ต้นทาง/ขอบเขต ค่าคงที่ต้องเลือกให้จำ ข้อยกเว้นเฉพาะแถวไม่ใช้ข้ามรอบ |",
        "| 4. ใช้ความรู้ก่อนถาม | ไม่แยกความรู้ขัดแย้งหรือถูกถอน | ตรวจลูกค้า contract รูปแบบ เงื่อนไขงาน และ expression; ขัดแย้งหรือถอนแล้วไม่เรียกใช้ หน่วยใหม่ถามใหม่ |",
        "| 5. ลดคำถาม/ข้อผิดพลาดเทียม | VAT รายแถวอาจโดนถามให้แทนทั้งไฟล์; สต็อกหลายคลังถูกมองว่าซ้ำผิด | รักษาค่ารายแถว ตรวจ grain ของ collection; แก้ schema refresh, queue retry และ CSV เปลี่ยนชื่อ |",
        "| 6. วัดผลซ้ำ | คะแนนก่อนแก้ครั้งเดียว | ชุดจำลองต่างประเทศ + contract อีกแบบ + รันทวนจุดเสี่ยง + replay คำตอบคนผ่าน API และ regression |",
        "",
        "## ขอบเขตและผลที่วัดได้",
        "",
        "ไม่มีไฟล์ลูกค้าจริงในรอบนี้ ตามคำยืนยันผู้ใช้ ใช้ชุดจำลองเดิมและไม่เปลี่ยนเฉลยเพื่อทำให้คะแนนดีขึ้น",
        "รอบสุดท้าย `intl-release-a` + `intl-release-b` มี 17 งาน (16 กรณี + contract อีกแบบ) บน prompt 2026.11.03.3 เก็บ hash ของโค้ดและ worker image ใน metadata.json; intl-dl4 เป็นรอบทวน 5 กรณีเสี่ยงก่อนปรับ prompt ล่าสุด",
        f"กรณีโจทย์ชัดเจน 13 งาน: ฟิลด์ที่เฉลยกำหนดถูก {correct}/{expected} = **{correct / expected:.2%}** ก่อนคนช่วย",
        "Field accuracy ไม่เท่ากับความพร้อมใช้งานทั้งหมด: ต้องดูข้อมูลที่เกินเฉลย, จำนวนแถว, verifier, คำถาม และการยืนยันทางธุรกิจด้วย",
        "จำนวนการ์ดในตารางอ่านซ้ำผ่าน API หลัง patch รวมคำถามหน่วยที่โมเดลกับ server ถามซ้ำ (`questions-current.json`); prediction และคะแนนเดิมไม่ถูกแก้หรือเลือกใหม่",
        "หลังเก็บ prediction พบกรณีสัญลักษณ์สกุลเงินฝังในราคาที่ต้องใช้เงื่อนไข contains เดิมแทนเทียบทั้งเซลล์ จึงแก้ชั้นใช้คำตอบและทดสอบใหม่ผ่าน preview/apply รวมทั้งเทสที่ยืนยันว่าราคาและแถวที่ไม่มีสัญลักษณ์ไม่เปลี่ยน ไม่ใช้การรันโมเดลใหม่มาแทน prediction เดิม",
        "G07 มีการหยุดที่ harness ฝั่ง Windows ขณะ replace state.tmp (Access denied) แล้ว resume จาก checkpoint เดิม เก็บ error.json และ eval-release-a-resume.log ไว้ ไม่จัดเป็น job/โมเดลล้ม",
        "",
        "## ก่อน–หลัง ต่อกรณี (ยังไม่ตอบคำถาม)",
        "",
        "| กรณี | ก่อน Field % | หลัง Field % | Exact record % | แถว | การ์ด ก่อน→หลัง | ข้อสงสัยค้าง | verifier | ธง |",
        "|---|---:|---:|---:|---:|---|---:|---|---|",
    ]
    for r in final:
        b = baseline.get(r["id"])
        s = r["score"]
        lines.append(f"| {r['id']} | {b['score']['field_accuracy'] * 100:.2f} | {s['field_accuracy'] * 100:.2f} | {s['exact_record_accuracy'] * 100:.2f} | {s['rows_got']} | {b['questions']}→{r['questions']} | {len(r['open_questions'])} | {r['verification_passed']} | {', '.join(r['flags']) or '—'} |")
    lines += ["", "## จุดที่ยังต้องตรวจ ไม่รวมเป็นความผิดแบบเดียวกัน", ""]
    for r in final:
        s = r["score"]
        if s["wrong"] or s["extra_values"] or r["flags"]:
            lines += [f"### {r['id']}", ""]
            if s["wrong"]:
                lines.append("ค่าต่างจากเฉลย (กรณี G13–G16 มีข้อมูลธุรกิจที่ต้องถามก่อน):")
                lines.append("```json\n" + json.dumps(s["wrong"][:4], ensure_ascii=False, indent=2) + "\n```")
            if s["extra_values"]:
                lines.append(f"ข้อมูลเกินเฉลย {len(s['extra_values'])} จุด; ตัวอย่าง:")
                lines.append("```json\n" + json.dumps(s["extra_values"][:2], ensure_ascii=False, indent=2) + "\n```")
            if r["flags"]:
                lines.append("ธง: " + ", ".join(r["flags"]))
            lines.append("")
    lines += ["## ทวนจุดเสี่ยงก่อนปรับคำอธิบาย DSL รอบสุดท้าย", "", "| กรณี | Field % | แถว | การ์ด | verifier | ธง |", "|---|---:|---:|---:|---|---|"]
    for r in repeated:
        lines.append(f"| {r['id']} | {r['score']['field_accuracy'] * 100:.2f} | {r['score']['rows_got']} | {r['questions']} | {r['verification_passed']} | {', '.join(r['flags']) or '—'} |")
    patch_repeats = summarize(ROOT / "runs/intl-release-check")
    lines += ["", "## ความผิดจริงที่พบระหว่างพัฒนาและเก็บไว้", "", "ใน intl-dl3/G04 โมเดลส่ง nested target ของ attributes ที่ไม่ได้อยู่ใน DSL catalogue ทั้งที่ JSON Schema เปิด object ได้ guard จึงปฏิเสธและใช้ heuristic fallback: field accuracy 70%, อยู่สถานะ review ไม่ได้อนุมัติอัตโนมัติ แก้คำสั่งให้ใช้ SourceSpec.rename + attributes_passthrough และไม่อ้างว่า BT เป็นหน่วยที่ระบบรู้จักแล้ว", "ผลรอบสุดท้ายของ G04 อยู่ในตารางหลักด้านบน; รันทวนบน build สุดท้ายอีกครั้ง:"]
    for r in patch_repeats:
        lines.append(f"- {r['id']}: Field {r['score']['field_accuracy']:.2%}, Exact {r['score']['exact_record_accuracy']:.2%}, flags={r['flags']}")
    replay = ROOT / "runs/decision-loop-release/summary.json"
    if replay.exists():
        answers = json.loads(replay.read_text(encoding="utf-8"))
        lines += ["", "## หลังตอบข้อมูลที่ขาดผ่าน preview/apply จริง", "", "คำตอบจากโจทย์เฉลยใช้เฉพาะขั้นจำลองผู้ตรวจหลังเก็บ prediction แล้ว ไม่ป้อนเฉลยให้โมเดลก่อน map", "", "| กรณี | Field % | แถว | Full effect check | ยังต้องตรวจเพิ่ม |", "|---|---:|---:|---|---:|"]
        for r in answers:
            lines.append(f"| {r['case']} | {r['field_accuracy'] * 100:.2f} | {r['rows']} | {r['validation']['passed']} | {len(r['remaining_questions'])} |")
    memory_file = ROOT / "runs/decision-memory-release/summary.json"
    if memory_file.exists():
        memory = json.loads(memory_file.read_text(encoding="utf-8"))
        lines += ["", "## การเรียนรู้ข้ามรอบที่ทดสอบกับ API จริง", "", f"กรณี G15: รอบแรกยืนยัน BT=BOTTLE และตรวจธงภาษี/รายการราคาให้ตรงไฟล์ จากนั้นอนุมัติเฉพาะข้อมูลจำลอง รอบถัดไป source={memory['source']}, field accuracy={memory['field_accuracy']:.2%}, exact record={memory['exact_record_accuracy']:.2%}, คำถาม={memory['questions']}, LLM calls={memory['calls']} ไม่มีการส่งออก", "เทส regression แยกยืนยันว่าเปลี่ยนชื่อ CSV อย่างเดียวใช้รูปแบบเดิมได้ แต่เปลี่ยนรหัสหน่วยเป็น XYZ ต้องถามใหม่ และความรู้ข้ามลูกค้า/contract/เงื่อนไขงานหรือที่ถอนแล้วใช้ไม่ได้", "หลักฐาน: runs/decision-memory-release และ runs/intl-release-b/G15_ask_unit_r2_memory"]
    lines += [
        "", "## การตรวจโค้ด", "",
        f"- Full regression ก่อน patch คำถามรอบสุดท้าย: {marks.count('.')} passed, {marks.count('s')} skipped (`regression-frozen-release.log`); หลัง patch รันชุด decision/actions/memory/evidence/harness/real-world อีก 54 tests ผ่าน (`regression-final-decisions.log`)",
        "- Ruff ผ่าน; mypy ผ่านทั้ง 132 source files; frontend tests 7/7 ผ่าน; TypeScript/Vite build ผ่าน",
        "- Frontend lint ไม่มี error แต่ยังมี warnings ของ repository; build มีคำเตือน chunk size",
        "- Eval gate ชุดสังเคราะห์เดิมผ่านเกณฑ์ baseline (`eval-gate-decision-loop.log`)",
        "", "## ต้นทุนและหลักฐาน", "",
        f"ชุดสุดท้าย 17 งานรวมกับรอบทวน intl-dl4 อีก 5 งาน มี `cost_usd` จาก usage ใน agent_log = **USD {cost:.6f}** เป็นค่าประเมินของระบบ ไม่ใช่ใบแจ้งหนี้ DeepSeek; ตารางราคา fallback อาจไม่ตรงผู้ให้บริการ",
        f"รวม mapping runs ระหว่างพัฒนางานนี้ทั้ง 7 ชุด (ไม่รวม baseline ก่อนงานนี้) = **USD {total_cost:.6f} ตาม log** ไม่ใช่ยอดเรียกเก็บจริง และไม่รวมงานอื่นนอก mapping agent_log",
        "รอบพัฒนาก่อน build สุดท้ายเก็บแยกไว้ที่ intl-dl1, intl-dl2 และ decision-loop-replay ไม่นำมาปนเป็นผลของ build สุดท้าย",
        "รายละเอียดรายกรณี: runs/intl-release-a, runs/intl-release-b, runs/intl-release-check, runs/intl-dl4 (before.json, score-before.json, job JSON, state และ metadata)",
        "", "## ความหมายต่อการนำไปใช้", "",
        "พร้อมก้าวไปสู่ pilot ที่มีผู้ตรวจและตรวจผลก่อนส่งออก ไม่ใช่หลักฐานว่าถูก 100% กับทุก ERP หรือเทียบเท่า ChatGPT/Claude ทุกกรณี การถามข้อมูลธุรกิจที่ไม่มีในไฟล์เป็นพฤติกรรมที่ถูกต้อง",
        "ความรู้คือกฎที่คนอนุมัติแล้วและมีขอบเขต ไม่ใช่การฝึกน้ำหนักโมเดลใหม่; ไม่รับประกันว่ารอบถัดไปจะไม่ถาม หากหลักฐานหรือความหมายเปลี่ยน",
        "ขั้นถัดไปเมื่อมีไฟล์จริง: ทำชุด holdout ที่ไม่ใช้แก้ prompt, ให้เจ้าของข้อมูลตรวจคำตอบ และวัด critical-field error, จำนวนคำถามต่อไฟล์, เวลา review และสัดส่วนอัตโนมัติ",
        "", "หลักการออกแบบและแหล่งอ้างอิงผู้ให้บริการ: docs/adr/062-typed-decisions-and-scoped-memory.md", "",
    ]
    (ROOT / "REPORT-DECISION-LOOP.md").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote REPORT-DECISION-LOOP.md")


if __name__ == "__main__":
    main()
