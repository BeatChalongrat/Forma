"""Adversarial input fixtures with exact OOXML caches/styles, independent of the synth generator."""

from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import polars as pl
import pytest

from forma.core.errors import UnsupportedFile
from forma.ingestion.readers import read_meta, read_raw
from forma.profiling.tables import detect_tables, extract_table


def test_sheet_coordinates_survive_leading_empty_rows_and_columns(tmp_path: Path) -> None:
    p = workbook_xml(
        tmp_path / "offset.xlsx",
        f'<row r="5">{text_cell("C5", "SKU")}{text_cell("D5", "Price")}</row><row r="6">{text_cell("C6", "000123")}<c r="D6"><v>107</v></c></row>',
    )
    raw = read_raw(p)
    assert raw.row(4) == (None, None, "SKU", "Price")
    assert raw.row(5) == (None, None, "000123", "107")
    assert detect_tables(raw)[0].header_row == 4


def test_cached_formula_zero_and_empty_string_are_valid(tmp_path: Path) -> None:
    rows = '<row r="1"><c r="A1"><f>1-1</f><v>0</v></c><c r="B1" t="str"><f>IF(TRUE,"","x")</f><v/></c></row>'
    p = workbook_xml(tmp_path / "cached.xlsx", rows)
    assert read_meta(p).sheets[0].formula_without_cache == 0
    assert read_raw(p)["c0"][0] == "0"


def test_chart_only_sheet_does_not_shift_data_sheet_inspection(tmp_path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference

    book = Workbook()
    data = book.active
    data.title = "Data"
    data.append(["SKU", "Count"])
    data.append(["0001", 2])
    chart = BarChart()
    chart.add_data(Reference(data, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    book.create_chartsheet("Chart", 0).add_chart(chart)
    path = tmp_path / "chart.xlsx"
    book.save(path)
    meta = read_meta(path)
    assert [s.name for s in meta.sheets] == ["Chart", "Data"]
    assert read_raw(path, 1).row(1) == ("0001", "2")


def test_utf8_sniff_boundary_does_not_misdetect_encoding(tmp_path: Path) -> None:
    p = tmp_path / "boundary.csv"
    prefix = "SKU,Name\n" + "a,b\n" * 65530
    prefix += "000123,"
    text = prefix + "a" * (256 * 1024 - len(prefix) - 1) + "ก\n"
    p.write_text(text, encoding="utf-8")
    assert read_raw(p)["c1"][-1].endswith("ก")


def test_binary_damage_is_a_clear_input_error(tmp_path: Path) -> None:
    p = tmp_path / "broken.xlsx"
    p.write_bytes(b"PKbroken")
    with pytest.raises(UnsupportedFile, match=r"workbook|Workbook"):
        read_raw(p)


def test_meta_round_trip_keeps_inspection_results(tmp_path: Path) -> None:
    from forma.profiling.profile import WorkbookProfile, profile_workbook

    p = workbook_xml(
        tmp_path / "formula.xlsx",
        f'<row r="1">{text_cell("A1", "SKU")}{text_cell("B1", "Price")}</row><row r="2">{text_cell("A2", "0001")}<c r="B2"><f>1+1</f><v/></c></row>',
    )
    prof = profile_workbook(p)
    restored = WorkbookProfile.from_dict(prof.to_dict())
    assert restored.meta.sheets[0].formula_without_cache == 1
    assert restored.meta.sheets[0].formula_locations == ["B2"]
    assert restored.meta.sheets[0].structure_scan_complete


def test_complex_numeric_mask_is_reported_not_guessed(tmp_path: Path) -> None:
    styles = '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="000-000"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'
    p = workbook_xml(
        tmp_path / "custom.xlsx", '<row r="1"><c r="A1" s="1"><v>123</v></c></row>', styles=styles
    )
    assert read_meta(p).sheets[0].display_format_warnings == ["000-000"]
    assert read_raw(p)["c0"][0] == "123"


def test_display_replacements_keep_coordinates_with_offset_and_slice(tmp_path: Path) -> None:
    from forma.ingestion.readers import read_excel_sheet_raw

    styles = '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="000000"/></numFmts><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'
    p = workbook_xml(
        tmp_path / "offset_mask.xlsx",
        f'<row r="5">{text_cell("C5", "SKU")}</row><row r="6"><c r="C6" s="1"><v>123</v></c></row>',
        styles=styles,
    )
    assert read_raw(p).row(5) == (None, None, "000123")
    assert read_excel_sheet_raw(p, 0, skip_rows=5, n_rows=1).row(0) == (None, None, "000123")


def workbook_xml(path: Path, rows: str, *, styles: str = "") -> Path:
    """Hand-built package to test missing caches/errors without a writer repairing the fixture."""
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with ZipFile(path, "w", ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Products" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{rows}</sheetData></worksheet>'
        )
        z.writestr(
            "xl/styles.xml",
            styles
            or f'<styleSheet xmlns="{ns}"><cellXfs count="1"><xf numFmtId="0"/></cellXfs></styleSheet>',
        )
    return path


def text_cell(ref: str, value: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig", "cp874"])
def test_csv_encoding_and_identifiers(tmp_path: Path, encoding: str) -> None:
    p = tmp_path / "thai.csv"
    p.write_bytes('รหัสสินค้า,ชื่อสินค้า,ราคา\n000123,"น้ำ,ดื่ม",107\n'.encode(encoding))
    df = read_raw(p)
    assert df.row(0) == ("รหัสสินค้า", "ชื่อสินค้า", "ราคา")
    assert df.row(1) == ("000123", "น้ำ,ดื่ม", "107")


def test_csv_explicit_wrong_encoding_never_replaces_data(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_bytes(b"SKU,Name\n0001,\xff\n")
    with pytest.raises(UnsupportedFile, match="encod"):
        read_raw(p, csv_encoding="utf-8")


def test_unclosed_csv_quote_is_not_silently_accepted(tmp_path: Path) -> None:
    p = tmp_path / "bad.csv"
    p.write_text('SKU,Name\n0001,"unfinished\n0002,Other\n', encoding="utf-8")
    with pytest.raises(UnsupportedFile, match="CSV"):
        read_raw(p)


def test_formula_after_row_400_is_reported(tmp_path: Path) -> None:
    rows = "".join(f'<row r="{i}">{text_cell(f"A{i}", str(i))}</row>' for i in range(1, 451))
    rows += '<row r="451"><c r="A451"><f>1+1</f><v/></c></row>'
    p = workbook_xml(tmp_path / "late_formula.xlsx", rows)
    sh = read_meta(p).sheets[0]
    assert sh.formula_cells == 1
    assert sh.formula_without_cache == 1


def test_uncached_formula_cannot_be_transformed_as_a_blank(tmp_path: Path) -> None:
    p = workbook_xml(tmp_path / "formula.xlsx", '<row r="1"><c r="A1"><f>1+1</f><v/></c></row>')
    with pytest.raises(UnsupportedFile, match="cached"):
        read_raw(p)


def test_excel_error_cell_cannot_be_transformed_as_a_blank(tmp_path: Path) -> None:
    p = workbook_xml(tmp_path / "error.xlsx", '<row r="1"><c r="A1" t="e"><v>#REF!</v></c></row>')
    with pytest.raises(UnsupportedFile, match="error"):
        read_raw(p)


def test_display_zero_padding_and_percent_keep_their_meaning(tmp_path: Path) -> None:
    styles = '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="000000"/></numFmts><cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="9"/></cellXfs></styleSheet>'
    rows = f'<row r="1">{text_cell("A1", "SKU")}{text_cell("B1", "Discount")}</row><row r="2"><c r="A2" s="1"><v>123</v></c><c r="B2" s="2"><v>0.15</v></c></row>'
    p = workbook_xml(tmp_path / "format.xlsx", rows, styles=styles)
    assert read_raw(p).row(1) == ("000123", "15%")


def test_table_after_5000_rows_is_discovered() -> None:
    rows = [["SKU", "Price"]] + [[f"A{i}", "10"] for i in range(6000)]
    rows += [[None, None], [None, None], ["Warehouse", "Qty"], ["BKK", "7"], ["CNX", "9"]]
    raw = pl.DataFrame({"c0": [r[0] for r in rows], "c1": [r[1] for r in rows]})
    tables = detect_tables(raw)
    assert len(tables) == 2
    assert extract_table(raw, tables[0].to_spec()).height == 6000
    assert tables[1].headers == ["Warehouse", "Qty"]


def test_long_preamble_does_not_become_header() -> None:
    rows = [[f"report note {i}", None, None] for i in range(25)]
    rows += [["SKU", "Name", "Price"], ["001", "Tea", "107"], ["002", "Milk", "32"]]
    raw = pl.DataFrame({f"c{i}": [r[i] for r in rows] for i in range(3)})
    tables = detect_tables(raw)
    assert len(tables) == 1 and tables[0].header_row == 25
    assert extract_table(raw, tables[0].to_spec()).height == 2
