"""The reader for "Excel" exports that are text inside: XML Spreadsheet 2003 and HTML tables (ADR-074)."""

from __future__ import annotations

import quopri
from pathlib import Path

import pytest

from forma.core.errors import UnsupportedFile
from forma.engine.transform import BoundSource, TransformEngine
from forma.ingestion import markup
from forma.ingestion.readers import file_kind, read_meta, read_raw
from forma.ingestion.sniff import sniff_kind
from forma.mapping_dsl.models import (
    FieldMapping,
    MappingDocument,
    MappingMeta,
    RecordSpec,
    SheetSelect,
    SourceSpec,
    TableSpec,
    call,
    col,
)
from forma.schema_registry import TargetSchema

XMLSS = (
    '<?xml version="1.0"?>\n<?mso-application progid="Excel.Sheet"?>\n'
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
    'xmlns:x="urn:schemas-microsoft-com:office:excel" '
    'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet" xmlns:html="http://www.w3.org/TR/REC-html40">'
    "{body}</Workbook>"
)


def _cell(value: str, kind: str = "String", attrs: str = "") -> str:
    return f'<Cell{attrs}><Data ss:Type="{kind}">{value}</Data></Cell>'


def _row(*cells: str, attrs: str = "") -> str:
    return f"<Row{attrs}>{''.join(cells)}</Row>"


def _sheet(name: str, *rows: str, extra: str = "") -> str:
    return f'<Worksheet ss:Name="{name}"><Table>{"".join(rows)}</Table>{extra}</Worksheet>'


def _write(tmp_path: Path, name: str, text: str | bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return path


def _grid(path: Path, sheet: int | str = 0) -> list[tuple[str | None, ...]]:
    return read_raw(path, sheet).rows()


# ---- XML Spreadsheet 2003 --------------------------------------------------------------------------
def test_xml_spreadsheet_sheets_cells_and_gaps(tmp_path: Path) -> None:
    body = _sheet(
        "Items",
        _row(_cell("Code"), _cell("Name"), _cell("Price")),
        _row(_cell("00123"), _cell("<html:B>น้ำ</html:B>ดื่ม &amp; โซดา"), _cell("107.5", "Number")),
        # a cell and a row that say where they are: what lies between is empty
        _row(_cell("A2"), _cell("12", "Number", ' ss:Index="3"'), attrs=' ss:Index="5"'),
    ) + _sheet(
        "Dates",
        _row(_cell("2026-10-01T00:00:00.000", "DateTime"), _cell("2026-10-01T13:05:00.000", "DateTime")),
    )
    path = _write(tmp_path, "export.xls", XMLSS.format(body=body))
    meta = read_meta(path)
    assert meta.kind == "excel" and [(s.name, s.n_rows, s.n_cols) for s in meta.sheets] == [
        ("Items", 5, 3),
        ("Dates", 1, 2),
    ]
    assert _grid(path, "Items") == [
        ("Code", "Name", "Price"),
        ("00123", "น้ำดื่ม & โซดา", "107.5"),  # a code keeps its zeros; markup inside a cell is its text
        (None, None, None),
        (None, None, None),
        ("A2", None, "12"),
    ]
    assert _grid(path, 1) == [("2026-10-01", "2026-10-01 13:05:00.000")]
    assert read_raw(path, "Items", n_rows=2).height == 2


def test_xml_spreadsheet_reports_what_the_ooxml_scan_reports(tmp_path: Path) -> None:
    hidden = "<x:WorksheetOptions><x:Visible>SheetHidden</x:Visible></x:WorksheetOptions>"
    body = _sheet(
        "Cost",
        _row(_cell("Code"), _cell("Group", attrs=' ss:MergeAcross="1"')),
        _row(_cell("A1"), _cell("7", "Number", ' ss:Formula="=3+4"'), attrs=' ss:Hidden="1"'),
        extra=hidden,
    )
    sheet = read_meta(_write(tmp_path, "export.xls", XMLSS.format(body=body))).sheets[0]
    assert (sheet.hidden, sheet.hidden_rows, sheet.formula_cells, sheet.merged_ranges) == (
        True,
        1,
        1,
        ["B1:C1"],
    )
    assert sheet.structure_scan_complete and sheet.error_cells == 0


def test_an_error_cell_in_an_xml_spreadsheet_is_refused_not_read_as_a_value(tmp_path: Path) -> None:
    body = _sheet("Items", _row(_cell("Code"), _cell("Price")), _row(_cell("A1"), _cell("#N/A", "Error")))
    path = _write(tmp_path, "export.xls", XMLSS.format(body=body))
    assert read_meta(path).sheets[0].error_locations == ["B2: #N/A"]
    with pytest.raises(UnsupportedFile, match="1 Excel error cells"):
        read_raw(path, 0)
    assert read_raw(path, 0, validate_cells=False).rows()[1] == ("A1", "#N/A")  # profiling still describes it


def test_the_engine_refuses_an_error_cell_in_a_column_it_maps(tmp_path: Path, schema: TargetSchema) -> None:
    body = _sheet(
        "Items",
        _row(_cell("Code"), _cell("Name"), _cell("Cost")),
        _row(_cell("A1"), _cell("น้ำดื่ม"), _cell("#REF!", "Error")),
    )
    path = _write(tmp_path, "export.xls", XMLSS.format(body=body))

    def run(*targets: tuple[str, str]) -> list[str]:
        doc = MappingDocument(
            meta=MappingMeta(target_schema="product@1"),
            sources=[SourceSpec(id="p", sheet=SheetSelect(name="Items"), table=TableSpec(header_row=0))],
            record=RecordSpec(root_source="p"),
            fields=[FieldMapping(target=t, expr=call("trim", col(c))) for t, c in targets],
            attributes_passthrough=False,
        )
        result = TransformEngine(schema).run(doc, {"p": BoundSource(path)})
        return [f.message for f in result.findings if f.severity == "error"]

    assert not any("unavailable cells" in m for m in run(("/sku", "Code"), ("/product_name", "Name")))
    assert any("C2: Excel error #REF!" in m for m in run(("/sku", "Code"), ("/brand", "Cost")))


@pytest.mark.parametrize(
    "prolog",
    ['<!DOCTYPE x [<!ENTITY a "aaaa">]>', '<!DOCTYPE x SYSTEM "http://example.invalid/x.dtd">'],
)
def test_an_xml_spreadsheet_with_a_dtd_is_refused_before_anything_is_expanded(
    tmp_path: Path, prolog: str
) -> None:
    text = XMLSS.format(body=_sheet("S", _row(_cell("x")))).replace("<Workbook", prolog + "<Workbook", 1)
    with pytest.raises(UnsupportedFile, match="DTD or entities"):
        sniff_kind(text.encode(), "export.xls")
    with pytest.raises(UnsupportedFile, match="DTD or entities"):  # and by the parser, whoever calls it
        read_meta(_write(tmp_path, "export.xls", text))


def test_a_damaged_xml_spreadsheet_says_so(tmp_path: Path) -> None:
    text = XMLSS.format(body=_sheet("S", _row(_cell("x")))).replace("</Workbook>", "")
    with pytest.raises(UnsupportedFile, match="XML spreadsheet is damaged"):
        read_meta(_write(tmp_path, "export.xls", text))


# ---- HTML tables -----------------------------------------------------------------------------------
def test_html_table_spans_entities_and_cells_left_open(tmp_path: Path) -> None:
    html = (
        "<html><head><style>td{color:red}</style><title>Report</title></head><body>"
        "<table><tr><th colspan=2>Item</th><th rowspan='2'>Price</th>"
        "<tr><td>Code<td>Name"  # no </td>, no </tr>: HTML allows it
        "<tr><td>A1</td><td>น้ำดื่ม&nbsp;&amp;<br>โซดา</td><td> 1,070.00 </td></tr>"
        "<tr><td>A2</td><td><script>alert(1)</script></td><td>5</td></tr>"
        "</table></body></html>"
    )
    path = _write(tmp_path, "report.xls", html)
    assert _grid(path) == [
        ("Item", None, "Price"),
        ("Code", "Name", None),  # the price header spans down: nothing is shifted under it
        ("A1", "น้ำดื่ม & โซดา", "1,070.00"),
        ("A2", None, "5"),
    ]
    assert read_meta(path).sheets[0].merged_ranges == ["A1:B1", "C1:C2"]


def test_a_table_that_lays_out_the_page_is_not_data_and_each_data_table_is_a_sheet(tmp_path: Path) -> None:
    html = (
        "<table><tr><td>Company report</td></tr><tr><td>"
        "<table><tr><td>Code</td><td>Qty</td></tr><tr><td>A1</td><td>3</td></tr></table>"
        "</td><td>"
        "<table><tr><td>Plant</td></tr><tr><td>BKK</td></tr></table>"
        "</td></tr></table>"
    )
    path = _write(tmp_path, "report.xls", html)
    assert [s.name for s in read_meta(path).sheets] == ["Sheet1", "Sheet2"]
    assert _grid(path, "Sheet1") == [("Code", "Qty"), ("A1", "3")]
    assert _grid(path, 1) == [("Plant",), ("BKK",)]


def test_a_web_archive_part_is_decoded_as_it_declares(tmp_path: Path) -> None:
    page = "<html><body><table><tr><td>รหัส</td><td>ชื่อ</td></tr><tr><td>A1</td><td>น้ำดื่ม</td></tr></table></body></html>"
    encoded = quopri.encodestring(page.encode("cp874")).decode("ascii")
    archive = (
        'MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary="B"\r\n\r\n'
        '--B\r\nContent-Type: text/html; charset="windows-874"\r\n'
        "Content-Transfer-Encoding: quoted-printable\r\n\r\n" + encoded + "\r\n"
        "--B\r\nContent-Type: image/gif\r\nContent-Transfer-Encoding: base64\r\n\r\nR0lGODlhAQABAAAAACw=\r\n--B--\r\n"
    )
    for name in ("list.mhtml", "list.mht", "list.xls"):
        path = _write(tmp_path, name, archive)
        assert sniff_kind(path.read_bytes(), name) == "excel" and file_kind(path) == "excel"
        assert _grid(path) == [("รหัส", "ชื่อ"), ("A1", "น้ำดื่ม")]


def test_an_export_with_no_table_has_no_sheets(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.mhtml", "<html><body><p>No data found</p></body></html>")
    assert read_meta(path).sheets == []


# ---- what the bytes are decides ----------------------------------------------------------------------
def test_tab_text_named_xls_is_read_as_delimited_text(tmp_path: Path) -> None:
    text = "15.10.2026\tPricing Report\r\n\r\n\tMaterial\tAmount\r\n\tA1\t107.00\r\n"
    assert sniff_kind(text.encode(), "list.xls") == "csv"
    path = _write(tmp_path, "list.xls", text)
    assert file_kind(path) == "csv" and markup.kind(path) == "text"
    meta = read_meta(path)
    assert (meta.kind, meta.csv_delimiter) == ("csv", "\t")
    assert _grid(path)[-1] == (None, "A1", "107.00")
    utf16 = _write(tmp_path, "list16.xls", text.encode("utf-16"))
    assert sniff_kind(utf16.read_bytes(), "list16.xls") == "csv" and _grid(utf16)[-1] == (
        None,
        "A1",
        "107.00",
    )


def test_a_real_workbook_is_not_touched_by_any_of_this(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["urn:schemas-microsoft-com:office:spreadsheet", "<table>"])  # type: ignore[union-attr]
    path = tmp_path / "real.xlsx"
    wb.save(path)
    assert markup.kind(path) is None and file_kind(path) == "excel"
    assert markup.container_of(path.read_bytes()) == "zip"
    assert _grid(path) == [("urn:schemas-microsoft-com:office:spreadsheet", "<table>")]


def test_a_text_export_past_the_size_limit_is_refused_with_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "big.xls", "<table><tr><td>a</td><td>b</td></tr></table>")
    monkeypatch.setattr(markup, "MAX_BYTES", 10)
    with pytest.raises(UnsupportedFile, match="export it as CSV or XLSX"):
        read_meta(path)
