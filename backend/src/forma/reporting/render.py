"""Trusted renderer executed ONLY inside the no-network report sandbox.

No provider is called. Every table and native chart reads the frozen report contract.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import subprocess
import textwrap
import zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def run() -> None:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor as PPTColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Inches as PI
    from pptx.util import Pt as PP
    from weasyprint import HTML

    spec = json.loads(Path("inputs/report.json").read_text(encoding="utf-8"))
    root = Path("outputs")
    root.mkdir(exist_ok=True)
    temp = Path("tmp")
    temp.mkdir(exist_ok=True)
    th = spec["language"] == "th"
    brand = spec["brand"]
    font, ink = brand["font"], brand["ink"]
    installed = subprocess.run(["fc-list", ":", "family"], check=True, capture_output=True, text=True).stdout
    if font.casefold() not in installed.casefold():
        raise ValueError("Required brand/Thai font is unavailable; install it before rendering")

    def tr(en: str, thai: str) -> str:
        return thai if th else en

    def label(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get(spec["language"]) or value.get("en") or "")
        if value is None:
            return tr("Unknown", "ไม่ทราบ")
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

    blocks: list[tuple[str, list[str], list[list[str]], str]] = []
    numeric: list[str] = []
    for source in spec["sources"]:
        report = source["report"]
        cols = report["columns"]
        for offset in range(1, max(2, len(cols)), 3):
            selected = [cols[0], *cols[offset : offset + 3]]
            rows = [[label(row.get(c["key"])) for c in selected] for row in report["rows"]]
            scope = label(report["scope"])
            if not report["complete"]:
                scope += " · " + tr("PARTIAL DATA — not a full population", "ข้อมูลบางส่วน — ไม่ใช่ภาพรวมทั้งหมด")
            blocks.append((label(report["title"]), [label(c["label"]) for c in selected], rows, scope))
        numeric.extend(
            label(row.get(c["key"]))
            for row in report["rows"]
            for c in cols
            if c["type"] == "number" and row.get(c["key"]) is not None
        )
    if spec.get("flow"):
        flow = spec["flow"]["content"]
        names = {n["id"]: n["label"] for n in flow["nodes"]}
        blocks.append(
            (
                flow["title"],
                [tr("Step", "ขั้นตอน"), tr("Details", "รายละเอียด")],
                [[n["label"], n.get("detail", "")] for n in flow["nodes"]],
                tr("Authored process — not a runtime event log", "แผนผังที่จัดทำขึ้น — ไม่ใช่บันทึกเหตุการณ์การทำงานจริง"),
            )
        )
        blocks.append(
            (
                tr("Connections", "เส้นทางการทำงาน"),
                [tr("From", "จาก"), tr("To", "ไปยัง"), tr("Condition", "เงื่อนไข")],
                [[names[e["from"]], names[e["to"]], e.get("label", "")] for e in flow["edges"]],
                tr("All branches preserved", "แสดงเส้นทางครบทุกเงื่อนไข"),
            )
        )
    notes = [label(n) for src in spec["sources"] for n in src["report"]["notes"]]
    # Check glyph support before rendering; PDF CID text extraction is not a glyph test.
    from fontTools.ttLib import TTFont

    supported: set[int] = set()
    for family in (font, "Noto Sans"):
        font_path = subprocess.run(
            ["fc-match", "-f", "%{file}", family], check=True, capture_output=True, text=True
        ).stdout
        with TTFont(font_path) as font_file:
            supported.update(font_file.getBestCmap() or {})
    authored_text = json.dumps(spec, ensure_ascii=False)
    missing_glyphs = {
        ord(ch)
        for ch in authored_text
        if (32 <= ord(ch) <= 126 or 0x0E01 <= ord(ch) <= 0x0E5B) and ord(ch) not in supported
    }
    if missing_glyphs:
        raise ValueError(f"Brand fonts lack required Thai/Latin glyphs: {sorted(missing_glyphs)}")
    provenance = [
        f"{s['report']['id']} · {s['report']['generated_at']}\n"
        + json.dumps(s["query"], ensure_ascii=False, sort_keys=True)
        for s in spec["sources"]
    ]
    provenance.extend(
        f"{label(link['label'])}: {link['url']}" for s in spec["sources"] for link in s["report"]["links"]
    )
    if spec.get("flow"):
        provenance.append(f"Flow {spec['flow']['id']} v{spec['flow']['version']} · authored process")
    note = tr(
        "Automatic checks do not confirm business meaning. Review before distribution.",
        "ผลตรวจอัตโนมัติไม่ยืนยันความหมายทางธุรกิจ โปรดตรวจทานก่อนเผยแพร่",
    )
    created = datetime.fromisoformat(spec["created_at"]).astimezone(ZoneInfo(spec["timezone"]))
    meta = f"{created:%d/%m/%Y %H:%M} · {spec['timezone']} · {spec['template']} v{spec['template_version']}"
    title = spec["title"]
    esc = html.escape
    style = f"""@page {{size:A4; margin:20mm 17mm 19mm;
@bottom-left {{content:'FORMA · {spec["snapshot_id"][:12]}'; font-size:8pt; color:#{brand["muted"]};}}
@bottom-right {{content:counter(page); font-size:9pt;}}}}
body {{font:10pt '{font}', sans-serif; color:#{ink}; line-height:1.65;}}
h1 {{font-size:25pt; margin:0 0 8mm;}}
h2 {{font-size:15pt; margin:9mm 0 3mm; break-after:avoid;}}
p {{overflow-wrap:anywhere;}}
table {{width:100%; border-collapse:collapse; table-layout:fixed; font-size:9pt;}}
th {{background:#{ink}; color:white; text-align:left;}}
th,td {{padding:7px; border-bottom:1px solid #dce2eb; overflow-wrap:anywhere; vertical-align:top;}}
tr {{break-inside:avoid;}}
thead {{display:table-header-group;}}
.meta {{color:#{brand["muted"]}; font-size:9pt;}}
.brand {{font-size:12pt; color:#{brand["muted"]}; margin-bottom:12mm;}}
.sources {{break-before:page;}}"""
    body = (
        f"<div class='brand'>{esc(brand['name'])}</div><h1>{esc(title)}</h1><p class='meta'>{esc(meta)}</p>"
    )
    company = spec.get("company_template", {})
    doc = Document("inputs/company.docx") if company.get("format") == "docx" else Document()
    if company.get("format") == "docx":
        for element in list(doc.element.body):
            if element.tag != qn("w:sectPr"):
                doc.element.body.remove(element)
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(0.75)
    section.left_margin = section.right_margin = Inches(0.7)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        s = doc.styles[name]
        s.font.name = "Noto Sans"
        s.font.color.rgb = RGBColor.from_string(ink)
        s.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), font)
        fonts = s.element.get_or_add_rPr().get_or_add_rFonts()
        for key in list(fonts.attrib):
            if "Theme" in key:
                del fonts.attrib[key]
        for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(qn("w:" + slot), font if slot == "cs" else "Noto Sans")
    for node in list(doc.styles.element.iter(qn("w:pBdr"))):
        node.getparent().remove(node)
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    if company.get("format") != "docx":
        section.header.paragraphs[0].text = brand["name"]
    footer = section.footer.paragraphs[0]
    footer.add_run(f" · FORMA · {spec['snapshot_id'][:12]} · ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    doc.add_heading(title, 0)
    doc.add_paragraph(meta)
    deck = Presentation("inputs/company.pptx") if company.get("format") == "pptx" else Presentation()
    if company.get("format") == "pptx":
        for slide_id in list(deck.slides._sldIdLst):
            deck.part.drop_rel(slide_id.rId)
            deck.slides._sldIdLst.remove(slide_id)
    deck.slide_width, deck.slide_height = PI(13.333), PI(7.5)

    def box(slide: Any, value: str, x: float, y: float, w: float, h: float, size: int = 18) -> None:
        shape = slide.shapes.add_textbox(PI(x), PI(y), PI(w), PI(h))
        shape.text_frame.word_wrap = True
        for i, line in enumerate(value.splitlines() or [""]):
            p = shape.text_frame.paragraphs[0] if i == 0 else shape.text_frame.add_paragraph()
            p.text = line
            p.font.name, p.font.size = font, PP(size)
            p.font.color.rgb = PPTColor.from_string(ink)
            p.space_after = PP(5)

    def slide(title_text: str, subtitle: str = "") -> Any:
        blank = next(
            (
                layout
                for layout in deck.slide_layouts
                if not any(p.placeholder_format.type not in (13, 15, 16) for p in layout.placeholders)
            ),
            None,
        )
        if blank is None:
            raise ValueError("Company template requires a blank layout without body placeholders")
        s = deck.slides.add_slide(blank)
        box(s, brand["name"], 0.6, 0.2, 11.9, 0.35, 11)
        box(s, title_text, 0.6, 0.75, 12, 0.9, 25)
        if subtitle:
            box(s, subtitle, 0.6, 1.65, 12, 0.9, 12)
        box(s, f"{spec['snapshot_id'][:12]} · {len(deck.slides)}", 0.6, 7.05, 12, 0.24, 9)
        return s

    cover = slide(title, meta)
    box(cover, tr("Evidence-backed report", "รายงานจากข้อมูลที่ตรวจสอบย้อนกลับได้"), 0.6, 3.2, 11.8, 0.75, 24)
    box(cover, note, 0.6, 5.3, 11.8, 1, 15)
    if spec.get("brief"):
        brief = spec["brief"]
        doc.add_paragraph(tr("Prepared for: ", "สำหรับ: ") + brief["audience"])
        doc.add_paragraph(brief["purpose"])
        body += f"<p class='meta'>{esc(brief['audience'])}</p><p>{esc(brief['purpose'])}</p>"
        box(cover, brief["audience"] + "\n" + brief["purpose"], 0.6, 4.15, 11.8, 1, 15)
    if company.get("format") != "pptx":
        cover.background.fill.solid()
        cover.background.fill.fore_color.rgb = PPTColor.from_string(ink)
        for shape in cover.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    paragraph.font.color.rgb = PPTColor.from_string("FFFFFF")
    # Four editorial layouts precede the full, unchanged data appendix.
    from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE

    for editorial in spec.get("sections", []):
        heading, points, layout = editorial["title"], editorial["points"], editorial["layout"]
        kind = (
            tr("Proposed next steps", "ข้อเสนอแนะ — ยังไม่ใช่การตัดสินใจ")
            if layout == "actions"
            else tr("Analysis — review interpretation", "บทวิเคราะห์ — โปรดตรวจความหมาย")
            if editorial.get("interpretation") == "analysis_requires_review"
            else tr("Source summary", "สรุปจากข้อมูลต้นทาง")
        )
        citation = tr("Evidence ", "ข้อมูลอ้างอิง ") + ", ".join(str(i) for i in editorial["evidence_ids"])
        doc.add_heading(heading, 1)
        doc.add_paragraph(kind)
        body += f"<section><h2>{esc(heading)}</h2><p class='meta'>{esc(kind)}</p>"
        for point in points:
            doc.add_paragraph(point, style="List Bullet")
            body += f"<p>{esc(point)}</p>"
        body += f"<p class='meta'>{esc(citation)}</p></section>"
        doc.add_paragraph(citation)
        s = slide(heading, kind)
        if any(len(point) > 140 for point in points):
            for start in range(0, len(points), 2):
                if start:
                    s = slide(heading, kind)
                for i, point in enumerate(points[start : start + 2]):
                    box(s, point, 0.8, 2.8 + i * 1.65, 11.5, 1.5, 18)
                box(s, citation, 0.6, 6.65, 12, 0.25, 10)
            continue
        if layout in ("summary", "comparison"):
            for i, point in enumerate(points):
                x, y = 0.6 + (i % 2) * 6.1, 2.7 + (i // 2) * 1.85
                width = 11.8 if len(points) == 1 else 5.85
                shape = s.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, PI(x), PI(y), PI(width), PI(1.65))
                shape.fill.solid()
                shape.fill.fore_color.rgb = PPTColor.from_string("F2F5FA")
                shape.line.fill.background()
                from pptx.oxml.xmlchemy import OxmlElement as PPTElement

                shape._element.spPr.append(PPTElement("a:effectLst"))
                for effect in shape._element.xpath("./p:style/a:effectRef"):
                    effect.set("idx", "0")
                box(s, point, x + 0.25, y + 0.22, width - 0.5, 1.2, 20 if len(points) == 1 else 18)
        else:
            for i, point in enumerate(points):
                y = 2.7 + i * 0.87
                box(s, str(i + 1).zfill(2), 0.6, y, 0.55, 0.5, 20)
                box(s, point, 1.35, y, 11.2, 0.8, 17)
        box(s, citation, 0.6, 6.65, 12, 0.25, 10)
    purpose = {
        "executive": tr("Management overview", "ภาพรวมสำหรับผู้บริหาร"),
        "monthly": tr("Reporting period and coverage", "ช่วงเวลาและขอบเขตรายงาน"),
        "presentation": tr("Presentation scope", "ขอบเขตการนำเสนอ"),
        "incident": tr("Observed findings; cause requires confirmation", "ผลที่ตรวจพบ โดยสาเหตุยังต้องยืนยัน"),
        "process": tr("Process scope and decision points", "ขอบเขตขั้นตอนและจุดตัดสินใจ"),
    }[spec["template"]]
    if spec["template"] in ("executive", "monthly", "incident"):
        doc.add_heading(purpose, 1)
        body += f"<h2>{esc(purpose)}</h2>"
        for src in spec["sources"]:
            coverage = label(src["report"]["scope"])
            doc.add_paragraph(coverage)
            body += f"<p>{esc(coverage)}</p>"
        if spec["template"] == "incident":
            pending = tr(
                "Confirm cause, owner and corrective action before closing the incident.",
                "ยืนยันสาเหตุ ผู้รับผิดชอบ และวิธีแก้ก่อนสรุปว่าปัญหานี้ได้รับการแก้ไขแล้ว",
            )
            doc.add_paragraph(pending)
            body += f"<p>{esc(pending)}</p>"
    expected_rows = Counter(tuple(row) for _, _, rows, _ in blocks for row in rows)
    for heading, headers, rows, scope in blocks:
        body += (
            f"<h2>{esc(heading)}</h2><p class='meta'>{esc(scope)}</p><table><thead><tr>"
            + "".join(f"<th>{esc(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
        )
        doc.add_heading(heading, 1)
        doc.add_paragraph(scope)
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        repeat = OxmlElement("w:tblHeader")
        table.rows[0]._tr.get_or_add_trPr().append(repeat)
        for cell, value in zip(table.rows[0].cells, headers, strict=True):
            cell.text = value
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), ink)
            cell._tc.get_or_add_tcPr().append(shade)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.color.rgb = RGBColor.from_string("FFFFFF")
                    r.font.bold = True
        for row in rows:
            body += "<tr>" + "".join(f"<td>{esc(v)}</td>" for v in row) + "</tr>"
            for cell, value in zip(table.add_row().cells, row, strict=True):
                cell.text = value
        if not rows:
            empty = tr("No matching records", "ไม่พบรายการที่ตรงเงื่อนไข")
            body += f"<tr><td colspan='{len(headers)}'>{empty}</td></tr>"
            doc.add_paragraph(tr("No matching records", "ไม่พบรายการที่ตรงเงื่อนไข"))
        body += "</tbody></table>"
        # Bound content by character budget, not by shrinking typography or clipping rows.
        pages: list[list[list[str]]] = []
        page: list[list[str]] = []
        used = 0.0
        for row in rows:
            height = max(
                0.55, 0.30 * max(max(1, math.ceil(len(v) / (28 if len(headers) > 2 else 48))) for v in row)
            )
            if height > 3.5:
                raise ValueError("A cell is too long for a readable slide; choose a narrower report")
            if page and used + height > 3.6:
                pages.append(page)
                page, used = [], 0
            page.append(row)
            used += height
        pages.append(page)
        for page in pages:
            s = slide(heading, scope)
            if not page:
                box(s, tr("No matching records", "ไม่พบรายการที่ตรงเงื่อนไข"), 0.6, 3, 12, 1)
                continue
            shapes = s.shapes.add_table(len(page) + 1, len(headers), PI(0.6), PI(2.7), PI(12), PI(3.7))
            t = shapes.table
            t.rows[0].height = PI(0.6)
            for i, row in enumerate([headers, *page]):
                if i:
                    t.rows[i].height = PI(
                        max(
                            0.55,
                            0.30
                            * max(max(1, math.ceil(len(v) / (28 if len(headers) > 2 else 48))) for v in row),
                        )
                    )
                for j, value in enumerate(row):
                    cell = t.cell(i, j)
                    cell.text = value
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = PPTColor.from_string(ink if i == 0 else "F4F6FA")
                    for p in cell.text_frame.paragraphs:
                        p.font.name, p.font.size = font, PP(15)
                        p.font.color.rgb = PPTColor.from_string("FFFFFF" if i == 0 else ink)

    # Native chart series use the unchanged source values; unknown cells remain gaps.
    for src in spec["sources"]:
        r = src["report"]
        if "bar" not in r["views"] or not r["rows"] or len(r["rows"]) > 12:
            continue
        for key in r["series"][:4]:
            vals = [row.get(key) for row in r["rows"]]
            if any(v is None or abs(Decimal(str(v))) > Decimal("1e15") for v in vals):
                continue
            if any(len(str(row.get("label", ""))) > 50 for row in r["rows"]):
                continue  # full exact table is retained; do not clip chart labels
            col = next(c for c in r["columns"] if c["key"] == key)
            chart_title = label(r["title"]) + " · " + label(col["label"])
            data = CategoryChartData()
            data.categories = [label(row.get("label")) for row in r["rows"]]
            data.add_series(label(col["label"]), [float(v) for v in vals])
            s = slide(chart_title, label(r["scope"]))
            chart = s.shapes.add_chart(
                XL_CHART_TYPE.BAR_CLUSTERED, PI(0.7), PI(2.7), PI(11.8), PI(3.95), data
            ).chart
            chart.has_legend = False
            chart.has_title = False
            chart.category_axis.tick_labels.font.name = font
            chart.category_axis.tick_labels.font.size = PP(12)
            chart.value_axis.tick_labels.font.name = font
            chart.plots[0].series[0].format.fill.solid()
            chart.plots[0].series[0].format.fill.fore_color.rgb = PPTColor.from_string(ink)
            # Plot signed values on a shared zero baseline; do not approximate bars with table cells.
            import matplotlib

            matplotlib.use("Agg")
            import warnings

            import matplotlib.pyplot as plt

            plt.rcParams["font.family"] = ["Noto Sans", font]
            warnings.filterwarnings("error", message="Glyph .* missing from font")
            fig, ax = plt.subplots(figsize=(8, max(2.1, len(vals) * 0.45 + 1)), layout="constrained")
            values = [float(v) for v in vals]
            bars = ax.barh(range(len(values)), values, color="#" + ink, height=0.55)
            ax.set_yticks(range(len(values)), [label(row.get("label")) for row in r["rows"]])
            ax.invert_yaxis()
            ax.axvline(0, color="#" + ink, linewidth=0.7)
            ax.grid(axis="x", alpha=0.15)
            ax.set_axisbelow(True)
            ax.ticklabel_format(axis="x", style="plain", useOffset=False)
            ax.margins(x=0.25)
            for spine in ax.spines.values():
                spine.set_visible(False)
            ax.bar_label(bars, labels=[label(v) for v in vals], padding=4, fontsize=10)
            for bar, numeric_value in zip(bars, values, strict=True):
                if bar.get_width() != numeric_value:
                    raise ValueError("Chart geometry does not match its source values")
            image = str(temp / f"chart-{len(deck.slides)}.png")
            fig.savefig(image, dpi=220)
            plt.close(fig)
            doc.add_heading(chart_title, 1)
            doc.add_picture(image, width=Inches(6.5))
            body += (
                f"<h2>{esc(chart_title)}</h2><p class='meta'>{esc(label(r['scope']))}</p>"
                f"<img style='width:100%' src='{Path(image).resolve().as_uri()}'>"
            )

    if spec.get("flow"):
        import graphviz

        flow = spec["flow"]["content"]
        g = graphviz.Digraph(
            graph_attr={"rankdir": "TB", "bgcolor": "white"},
            node_attr={"shape": "box", "style": "rounded", "fontname": font},
            edge_attr={"fontname": font},
        )
        for n in flow["nodes"]:
            g.node(n["id"], n["label"])
        for e in flow["edges"]:
            g.edge(e["from"], e["to"], label=e.get("label", ""))
        path = g.render(str(temp / "flow"), format="png", cleanup=True)
        doc.add_heading(flow["title"], 1)
        doc.add_picture(path, width=Inches(5.8))
        body += (
            f"<h2>{esc(flow['title'])}</h2>"
            f"<img style='max-width:100%;max-height:230mm' src='{Path(path).resolve().as_uri()}'>"
        )
        # Editable diagram on slides for a bounded set; larger flows keep editable node/edge tables.
        if len(flow["nodes"]) <= 8:
            from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE

            s = slide(flow["title"])
            from pptx.oxml.xmlchemy import OxmlElement as PPTElement

            layout = json.loads(g.pipe(format="json"))
            bounds = [float(v) for v in layout["bb"].split(",")]
            scale = min(11.8 / bounds[2], 4.4 / bounds[3])
            origin_x = (13.333 - bounds[2] * scale) / 2

            def point(value: str) -> tuple[float, float]:
                x, y = map(float, value.split(","))
                return origin_x + x * scale, 2.1 + (bounds[3] - y) * scale

            for n in layout["objects"]:
                if "pos" not in n:
                    continue
                x, y = point(n["pos"])
                width, height = float(n["width"]) * 72 * scale, float(n["height"]) * 72 * scale
                shape = s.shapes.add_shape(
                    MSO_SHAPE.ROUNDED_RECTANGLE, PI(x - width / 2), PI(y - height / 2), PI(width), PI(height)
                )
                shape.text = n["label"]
                shape.fill.solid()
                shape.fill.fore_color.rgb = PPTColor.from_string(ink)
                for p in shape.text_frame.paragraphs:
                    p.font.name, p.font.size = font, PP(12)
                    p.font.color.rgb = PPTColor.from_string("FFFFFF")
            for edge in layout.get("edges", []):
                positions = edge["pos"].split()
                end = positions[0].removeprefix("e,")
                start = positions[1]
                x, y = point(start)
                xx, yy = point(end)
                connector = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, PI(x), PI(y), PI(xx), PI(yy))
                arrow = PPTElement("a:tailEnd")
                arrow.set("type", "triangle")
                connector._element.spPr.get_or_add_ln().append(arrow)
                if edge.get("label") and edge.get("lp"):
                    x, y = point(edge["lp"])
                    box(s, edge["label"], x - 0.6, y - 0.15, 1.5, 0.35, 10)

    doc.add_page_break()
    doc.add_heading(tr("Sources and limitations", "แหล่งข้อมูลและข้อจำกัด"), 1)
    body += "<div class='sources'><h2>" + tr("Sources and limitations", "แหล่งข้อมูลและข้อจำกัด") + "</h2>"
    for paragraph in [note, *notes, *provenance, "Snapshot SHA-256: " + spec["snapshot_id"]]:
        doc.add_paragraph(paragraph)
        body += f"<p class='meta'>{esc(paragraph)}</p>"
    body += "</div>"
    source_lines = [
        line for paragraph in [note, *notes, *provenance] for line in textwrap.wrap(paragraph, 110)
    ]
    for start in range(0, len(source_lines), 12):
        s = slide(tr("Sources and limitations", "แหล่งข้อมูลและข้อจำกัด"))
        box(s, "\n".join(source_lines[start : start + 12]), 0.6, 2, 12, 4.7, 13)
    if len(deck.slides) > 60:
        raise ValueError("Report exceeds 60 slides; narrow the source scope")
    # Check competing content shapes, excluding intentional background panels and flow connectors.
    overlaps = []
    for page_no, s in enumerate(deck.slides, 1):
        regions = [
            shape
            for shape in s.shapes
            if shape.has_table or shape.has_chart or (shape.has_text_frame and shape.text.strip())
        ]
        for i, first in enumerate(regions):
            for second in regions[i + 1 :]:
                dx = min(first.left + first.width, second.left + second.width) - max(first.left, second.left)
                dy = min(first.top + first.height, second.top + second.height) - max(first.top, second.top)
                if dx > PI(0.03) and dy > PI(0.03):
                    overlaps.append(page_no)
    if overlaps and not spec.get("flow"):
        raise ValueError(f"Competing slide content overlaps on pages {sorted(set(overlaps))}")
    paths = []
    if "docx" in spec["formats"]:
        doc.save(root / "report.docx")
        actual = Counter(
            tuple(c.text for c in row.cells)
            for t in Document(root / "report.docx").tables
            for row in list(t.rows)[1:]
        )
        if actual != expected_rows:
            raise ValueError("Word table cells do not match the source snapshot")
        paths.append(root / "report.docx")
    if "pptx" in spec["formats"]:
        deck.save(root / "report.pptx")
        actual = Counter(
            tuple(c.text for c in row.cells)
            for s in Presentation(root / "report.pptx").slides
            for shape in s.shapes
            if shape.has_table
            for row in list(shape.table.rows)[1:]
        )
        if actual != expected_rows:
            raise ValueError("PowerPoint table cells do not match the source snapshot")
        paths.append(root / "report.pptx")
    if "pdf" in spec["formats"]:
        from bs4 import BeautifulSoup

        markup = BeautifulSoup(body, "html.parser")
        actual = Counter(
            tuple(c.get_text() for c in row.find_all("td"))
            for row in markup.select("tbody tr")
            if not row.select("td[colspan]")
        )
        if actual != expected_rows:
            raise ValueError("PDF source table cells do not match the source snapshot")
        HTML(
            string=(
                f"<html lang='{spec['language']}'><meta charset='utf-8'><style>{style}</style>{body}</html>"
            ),
            base_url=str(Path.cwd()),
        ).write_pdf(root / "report.pdf")
        paths.append(root / "report.pdf")

    # Validation operates on actual generated files. Preview every page, never a selected sample.
    import pdfplumber

    checks = {}
    with zipfile.ZipFile(root / "previews.zip", "w", zipfile.ZIP_DEFLATED) as previews:
        for path in paths:
            folder = temp / path.suffix[1:]
            folder.mkdir()
            pdf = path
            if path.suffix != ".pdf":
                subprocess.run(
                    [
                        "soffice",
                        "--headless",
                        "-env:UserInstallation=file:///tmp/forma-report-lo",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(folder),
                        str(path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=90,
                )
                pdf = folder / "report.pdf"
            with pdfplumber.open(pdf) as opened:
                if not 1 <= len(opened.pages) <= 60:
                    raise ValueError("Rendered document must contain 1 to 60 pages")
                text = "".join(p.extract_text() or "" for p in opened.pages)
                missing = sorted({v for v in numeric if v.replace(" ", "") not in text.replace(" ", "")})
                if missing:
                    raise ValueError(f"Rendered document lost numerical values: {missing[:6]}")
                if any(not p.chars for p in opened.pages):
                    raise ValueError("Rendered document has an empty text page")
                outside = sum(
                    1
                    for p in opened.pages
                    for c in p.chars
                    if c["x0"] < -1 or c["x1"] > p.width + 1 or c["top"] < -1 or c["bottom"] > p.height + 1
                )
                if outside:
                    raise ValueError("Rendered text extends outside a page")
                extraction_warning = "\ufffd" in text or "(cid:" in text
                page_count = len(opened.pages)
            subprocess.run(
                ["pdftoppm", "-png", "-scale-to", "1000", str(pdf), str(folder / "page")],
                check=True,
                capture_output=True,
                timeout=90,
            )
            images = sorted(folder.glob("page-*.png"))
            if len(images) != page_count:
                raise ValueError("Not every page has a preview")
            for preview_image in images:
                previews.write(preview_image, f"{path.suffix[1:]}/{preview_image.name}")
            previews.write(pdf, f"{path.suffix[1:]}/document.pdf")
            checks[path.name] = {
                "pages": page_count,
                "rendered_pages": len(images),
                "numeric_values_checked": len(numeric),
                "table_cells": "exact_snapshot_match",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rendered": True,
                "page_bounds": "passed",
                "required_font": "installed",
                "thai_latin_glyph_coverage": "passed",
                "text_extraction": "font_encoding_requires_visual_review"
                if extraction_warning
                else "readable",
                "content_overlap": "passed"
                if path.suffix == ".pptx" and not spec.get("flow")
                else "human_review_required",
            }
    receipt = {
        "snapshot_id": spec["snapshot_id"],
        "status": "automatic_checks_passed",
        "visual_review": "human_required",
        "business_review": "human_required",
        "files": checks,
        "limitations": [
            "No model visual review was performed.",
            "Office may paginate differently from LibreOffice.",
            "Numerical presence checks do not prove business interpretation or detect all overlap.",
        ],
    }
    (root / "quality.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "snapshot.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({"snapshot_id": spec["snapshot_id"], "files": list(checks), "status": receipt["status"]})
    )


if __name__ == "__main__":
    run()
