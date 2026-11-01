"""Writes the guide, one note per case and the checklist from cases/index.json, so they cannot disagree."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from catalogue import records  # noqa: E402

GROUPS = {
    "A": ("ไฟล์เปิดไม่ได้", "สร้างลูกค้า `MTFILE` ครั้งเดียว แล้วใช้กับทุกเคสในกลุ่มนี้ ไปที่ อัปโหลดไฟล์ > รวมไฟล์ของลูกค้า เลือก `MTFILE` แล้วอัปโหลดไฟล์ของเคส"),
    "L": ("รูปแบบ ERP แต่ละแบบ", "สร้างลูกค้าตามรหัสของเคส (เช่น `MTL01`) ไปที่ อัปโหลดไฟล์ > รวมไฟล์ของลูกค้า แล้วอัปโหลดทุกไฟล์ในโฟลเดอร์ ยกเว้น `expected.json` และ `ผลที่ควรได้.md` จากนั้นตรวจ อนุมัติ และดาวน์โหลด JSON มาเทียบ"),
    "P": ("ค่าที่อ่านยาก", "สร้างลูกค้าตามรหัสของเคส (เช่น `MTP01`) แล้วอัปโหลดเหมือนกลุ่ม L"),
    "Q": ("ข้อมูลผิด", "สร้างลูกค้าตามรหัสของเคส (เช่น `MTQ01`) แล้วอัปโหลดเหมือนกลุ่ม L ดูว่าระบบเตือนตรงตามเคสหรือไม่"),
    "R": ("ลูกค้าเดิม รอบถัดไป", "ทำ `R00` ให้จบก่อน จนดาวน์โหลด JSON ได้ จากนั้นอัปโหลดแต่ละเคสให้ลูกค้า `MTEXP` ทีละเคส ดูผลแล้วเปิดเมนูของรอบ เลือก ยกเลิกรอบ โดยไม่ต้องกดยอมรับ เว้นแต่เคสบอกให้ทำ รอบที่ยกเลิกจะไม่กระทบเคสถัดไป"),
    "B": ("อัปโหลดหลายไฟล์", "ต้องทำ `R00` ก่อน ไปที่หน้า อัปโหลดไฟล์ แล้วลากไฟล์ทั้งหมดของเคสลงไปพร้อมกัน ดูการ์ดสีกรมท่าและสถานะของแต่ละไฟล์"),
    "S": ("รูปแบบ JSON ปลายทาง", "ไปที่ ตั้งค่า > Target formats"),
}
LABELS = {
    "stage": "สถานะรอบ",
    "decision": "การตัดสินของ policy",
    "verdict": "ข้อสรุป",
    "outcome": "สิ่งที่ระบบให้ทำ",
    "drift": "ชนิดการเปลี่ยนรูปแบบ",
    "records": "จำนวนรายการ",
    "invalid": "รายการผิดสัญญา JSON",
    "data_verdict": "ข้อสรุปขั้นข้อมูล",
    "questions": "ต้องมีคำถามเรื่อง",
    "anomalies": "ต้องมีคำเตือนเรื่อง",
    "findings": "ต้องมีคำเตือนเรื่อง",
    "message": "ข้อความต้องมีคำว่า",
    "upload": "ตอนอัปโหลด",
    "schema": "ตอนลงทะเบียน",
    "duplicate": "ระบบบอกว่าไฟล์ซ้ำ",
    "skipped": "ไฟล์ที่ถูกข้าม",
}
SKIP = {"json", "bulk", "replace"}
KNOWN = {
    "P04": "1x24 ได้ sell_unit เป็น 1X24 แทน CARTON ไม่ตรงกับ THAI_CONTEXT_RULES",
    "L06": "โปรโมชั่น 4 รายการหาย มีคำเตือนว่าจับคู่สินค้าไม่ได้",
    "R03": "หัวตารางเลื่อนถูกรายงานเป็น identical ไม่ใช่ header_moved (ข้อมูลยังถูก)",
    "R05": "เปลี่ยนชื่อคอลัมน์ภาษาไทยถูกรายงานเป็น columns_removed ไม่ใช่ columns_renamed (คำตัดสิน rename_only ยังถูก)",
    "R13": "ได้ other_customer_unconfirmed แทน wrong_customer เพราะรหัสสินค้าของสองรายไม่ซ้อนกันจึงเทียบค่าไม่ได้ ผลที่ให้ทำยังเป็นย้ายลูกค้าเหมือนกัน",
    "R15": "รอบล้มเหลวด้วย 'profile contains no tables' แทนที่จะถูกระงับด้วยเหตุผลไม่มีข้อมูล",
    "R17": "ถูกระงับโดย sandbox (รายการซ้ำเพิ่ม 4) ก่อนถึงขั้นข้อมูล จึงไม่เห็น catalogue_replaced",
    "R19": "สต็อกลด 95% เป็นความผิดปกติระดับกลาง จึงถูกพับไว้และรอบผ่านเอง",
    "R21": "รอบแรกผ่านเองแล้วเตือนราคาที่ขั้นข้อมูล ไม่ได้ให้คนตรวจที่ขั้น mapping ส่วนการแทนไฟล์ทำงานถูก",
    "P11": "แถว 'รวม 12 รายการ' กลายเป็นรายการที่ 13 ระบบจับได้ว่าผิดสัญญา แต่ต้องลบเอง",
    "P13": "แถวคั่นและแถวรวมหมวดกลายเป็น 5 รายการผิดสัญญา ระบบจับได้ แต่ต้องลบเอง",
    "P14": "ราคาส่งไปอยู่ใน attributes แทนที่จะเป็นราคาแบบที่สอง",
}


def facts(expect: dict[str, Any]) -> list[str]:
    out = []
    for key, value in expect.items():
        if key in SKIP or value == "":
            continue
        shown = ", ".join(value) if isinstance(value, list) else value
        out.append(f"{LABELS.get(key, key)}: `{shown}`")
    for name, state in (expect.get("bulk") or {}).items():
        out.append(f"`{name}`: `{state}`")
    return out


def case_note(c: dict[str, Any]) -> str:
    lines = [f"# {c['id']} {c['title']}", "", f"**สถานการณ์:** {c['why']}", ""]
    if c["customer"]:
        lines.append(f"**ลูกค้า:** `{c['customer']}`")
    if c["baseline"]:
        lines.append(f"**ต้องทำก่อน:** `{c['baseline']}`")
    lines += ["", "**ไฟล์:**", *[f"- `{name}`" for name in c["files"]], "", "## วิธีทำ", "", c["steps"] or GROUPS[c["group"]][1], "", "## ผลที่ควรได้", "", *[f"- {s}" for s in c["should"]]]
    shown = facts(c["expect"])
    if shown:
        lines += ["", "ค่าที่ควรเห็นในระบบ:", "", *[f"- {s}" for s in shown]]
    lines += ["", "## JSON", "", "เทียบไฟล์ที่ดาวน์โหลดกับ `expected.json` โดยไม่ต้องเทียบ `source_meta`" if c["has_json"] else "เคสนี้ไม่ควรมี JSON ที่ถูกต้องออกมาจนกว่าจะแก้ปัญหา", ""]
    if c["id"] in KNOWN:
        lines += ["## ปัญหาที่รู้แล้ว", "", f"ตอนนี้ระบบยังไม่เป็นตามผลที่ควรได้: {KNOWN[c['id']]}", ""]
    return "\n".join(lines)


def guide(cases: list[dict[str, Any]]) -> str:
    lines = [
        "# ไฟล์สำหรับทดสอบ FORMA ด้วยมือ",
        "",
        f"ชุดนี้มี {len(cases)} เคส จำลองไฟล์จาก ERP แบบต่าง ๆ และความผิดพลาดที่พบได้จริง ทุกเคสบอกไว้ว่าควรเกิดอะไรขึ้น",
        "",
        "> **สถานะ:** 33 เคสที่มี `expected.json` ในกลุ่ม L, P, Q และ `R00` เทียบกับระบบจริงแล้วหนึ่งรอบแบบไม่มี AI ตรง 29 เคส",
        "> อีก 4 เคสอยู่ในหัวข้อปัญหาที่รู้แล้ว เคสที่เหลือ เช่น `R01` ขึ้นไป และกลุ่ม A, B, S ยังไม่ได้รันกับระบบจริง",
        "> ผลที่ควรได้ของเคสเหล่านั้นเขียนจากกฎของระบบ ถ้าไม่ตรง อาจเป็นบั๊กของระบบ หรือคำตอบในเคสอาจผิดก็ได้",
        "",
        "## ในโฟลเดอร์นี้มีอะไร",
        "",
        "| ที่อยู่ | ใช้ทำอะไร |",
        "|---|---|",
        "| `เช็กลิสต์ทดสอบ.xlsx` | รายการทุกเคส พร้อมช่องจดผลจริงและผ่าน/ไม่ผ่าน |",
        "| `format/` | รูปแบบ JSON ที่อยากได้: สัญญา JSON และตัวอย่างผลลัพธ์เต็ม |",
        "| `cases/<เคส>/` | ไฟล์ทดสอบ, `ผลที่ควรได้.md` และ `expected.json` ถ้าเคสนั้นควรได้ JSON |",
        "| `tools/` | โปรแกรมที่สร้างไฟล์ชุดนี้ |",
        "",
        "## ก่อนเริ่ม",
        "",
        "1. เปิด http://localhost",
        "2. ลูกค้าทดสอบใช้รหัสขึ้นต้นด้วย `MT` จะได้แยกจากข้อมูลตัวอย่าง ลูกค้าเหล่านี้จะค้างอยู่ในระบบหลังทดสอบ",
        "3. ถ้าเปิด AI ไว้ รอบแรกอาจเรียก AI และมีค่าใช้จ่ายเล็กน้อย คำถามตอนจับคู่อาจต่างจากที่เขียนไว้ แต่ข้อสรุปหลักควรเหมือนเดิม",
        "4. สินค้าใน `expected.json` เรียงตามข้อมูลตั้งต้น ถ้าค่าเป็น null หรือรายการว่าง ถือว่าเหมือนกับไม่มีฟิลด์นั้น",
        "5. ไม่ต้องเทียบ `source_meta` เพราะเป็นบันทึกที่มาของระบบ และเปลี่ยนทุกครั้งที่รัน",
        "",
        "## ปัญหาที่รู้แล้ว",
        "",
        "เคสเหล่านี้ผลจริงยังไม่ตรงกับผลที่ควรได้ ไม่ใช่ความผิดของไฟล์ทดสอบ",
        "",
        "| เคส | สิ่งที่เกิดขึ้นตอนนี้ |",
        "|---|---|",
        *[f"| {key} | {text} |" for key, text in KNOWN.items()],
        "",
        "## ทำตามลำดับนี้",
        "",
        "ทำกลุ่ม A, L, P และ Q สลับกันได้ แต่กลุ่ม R ต้องเริ่มที่ `R00` และกลุ่ม B ต้องทำหลัง `R00`",
        "",
    ]
    for key, (title, how) in GROUPS.items():
        group = [c for c in cases if c["group"] == key]
        lines += [f"## กลุ่ม {key}: {title} ({len(group)} เคส)", "", how, "", "| เคส | ทดสอบอะไร | ผลหลักที่ควรได้ | JSON |", "|---|---|---|---|"]
        for c in group:
            lines.append(f"| [{c['id']}](cases/{c['folder']}/ผลที่ควรได้.md) | {c['title']} | {c['should'][0]} | {'มี' if c['has_json'] else '–'} |")
        lines.append("")
    lines += [
        "## ชุดนี้ยังไม่ครอบคลุม",
        "",
        "- ไฟล์ `.xlsm` ที่มี macro",
        "- ไฟล์เกินเพดาน 200 MB และไฟล์ขนาดใหญ่ระดับแสนแถว",
        "- AI ล่มกลางงาน และ worker หยุดกลางงาน",
        "- ผู้ช่วย ทั้งการแนบไฟล์ รันโค้ด ผลงาน และความจำ",
        "",
        "## สร้างไฟล์ใหม่",
        "",
        "```bash",
        "backend/.venv/Scripts/python manual_test_files/tools/generate.py",
        "```",
        "",
        "ไฟล์ใส่รหัสผ่านต้องมี `msoffcrypto-tool` และ `.xls` ต้องมี Docker image `forma-sandbox` ถ้าไม่มี ระบบจะเก็บไฟล์เดิมไว้",
        "",
    ]
    return "\n".join(lines)


def checklist(cases: list[dict[str, Any]], path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "เช็กลิสต์"
    columns = [("เคส", 8), ("กลุ่ม", 20), ("ทดสอบอะไร", 38), ("ลูกค้า", 10), ("ทำก่อน", 8), ("ไฟล์", 34), ("ผลที่ควรได้", 60), ("ค่าที่ควรเห็น", 34), ("เทียบ JSON", 10), ("ผลจริง", 34), ("ผ่านไหม", 10), ("หมายเหตุ", 28)]
    ws.append([c[0] for c in columns])
    for i, (_, width) in enumerate(columns, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = width
        cell = ws.cell(1, i)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="203E71")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for c in cases:
        ws.append([c["id"], GROUPS[c["group"]][0], c["title"], c["customer"] or "", c["baseline"] or "", "\n".join(c["files"]), "\n".join(c["should"]), "\n".join(s.replace("`", "") for s in facts(c["expect"])), "เทียบ" if c["has_json"] else "–", None, None, f"รู้แล้วว่ายังไม่ผ่าน: {KNOWN[c['id']]}" if c["id"] in KNOWN else None])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "D2"
    ws.auto_filter.ref = ws.dimensions
    verdict = DataValidation(type="list", formula1='"ผ่าน,ไม่ผ่าน,ข้าม"', allow_blank=True)
    ws.add_data_validation(verdict)
    verdict.add(f"K2:K{len(cases) + 1}")
    wb.save(path)


def main() -> None:
    cases = json.loads((ROOT / "cases" / "index.json").read_text(encoding="utf-8"))
    for c in cases:
        (ROOT / "cases" / c["folder"] / "ผลที่ควรได้.md").write_text(case_note(c), encoding="utf-8")
    (ROOT / "README.md").write_text(guide(cases), encoding="utf-8")
    checklist(cases, ROOT / "เช็กลิสต์ทดสอบ.xlsx")
    fmt = ROOT / "format"
    fmt.mkdir(exist_ok=True)
    shutil.copy(ROOT.parent / "target_schema" / "product.v1.json", fmt / "product.v1.schema.json")
    sample = json.dumps(records(), ensure_ascii=False, indent=2)
    (fmt / "ตัวอย่าง_json_ที่อยากได้.json").write_text(sample + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
