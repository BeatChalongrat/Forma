"""Files the way SAP, NetSuite and Oracle write them when a user presses "export" (not real customer files).

aCommerce's integration directory names these three systems; which client uses which, and which report, is
not public (``manual_test_files/acommerce_global_v1/RESEARCH.md``). Each file here imitates one export
*mechanism* of one system and holds the same six products, so a difference in the result comes from the
mechanism. Column labels are the systems' standard ones as far as I know them; a customer's own report will
differ. No model is called.

What each mechanism writes, and where that is documented:

* SAP, ALV list > Spreadsheet > "Excel (in Office 2007 XLSX format)": a plain sheet, labels in row 1.
* SAP, List > Export > Local File > "Text with Tabs": a date and title line, then tab-separated lines that
  start with a tab. Users save it as ``.xls`` (SAP KBA 2193144, 3413729).
* SAP, Local File > "Unconverted": the list as printed, cells between ``|`` and rules of ``-``
  (help.sap.com, "Save list in file": Unconverted, Spreadsheet, Rich Text Format, HTML Format).
* SAP, Spreadsheet > "Excel (in MHTML format)": a MIME web archive holding an HTML table.
* NetSuite, saved search > Export - CSV, and Export - Microsoft Excel: "Only XLS files are supported"
  (docs.oracle.com, Exporting Search Results); the ``.xls`` holds XML Spreadsheet 2003 with every cell a
  string (Oracle NetSuite community 4255454).
* Oracle OTBI, analysis > Export > Data > CSV: every column of the criteria, values repeated; > Excel 2007+:
  the view as shown, where a repeated value in a leading column is suppressed and its cells merged; > Web
  Archive (.mht) (docs.oracle.com, export menu; Oracle Analytics community on value suppression).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import openpyxl
import pytest
from fastapi.testclient import TestClient

N = 6
SKU = [f"A{i:03d}" for i in range(1, N + 1)]
NAME = [f"สินค้า {i}" for i in range(1, N + 1)]
PRICE = [float(i * 107) for i in range(1, N + 1)]
ORGS = ("BKK", "CNX")
STOCK = {org: [float((k + 1) * 3 * i) for i in range(1, N + 1)] for k, org in enumerate(ORGS)}
FIRST_TIME = "first time this customer/format is seen"

Rows = list[list[Any]]


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _xlsx(rows: Rows, *, merges: list[str] | None = None, sheet: str = "Sheet1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet
    for row in rows:
        ws.append(row)
    for cells in merges or []:
        ws.merge_cells(cells)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ---- SAP: a pricing report (condition records), the same list through four export mechanisms -------
SAP_LABELS = ["CnTy", "Material", "Description", "Amount", "Unit", "per", "UoM", "Valid From", "Valid to"]


def _sap_rows(*, text: bool) -> Rows:
    """``Unit`` is the currency of the amount and ``UoM`` the unit of measure: SAP's own labels."""
    return [
        [
            "ZPR0",
            SKU[i],
            NAME[i],
            f"{PRICE[i]:,.2f}" if text else PRICE[i],
            "THB",
            1,
            "PC",
            "01.10.2026",
            "31.12.9999",
        ]
        for i in range(N)
    ]


def sap_alv_xlsx() -> bytes:
    return _xlsx([SAP_LABELS, *_sap_rows(text=False)])


def sap_text_with_tabs() -> bytes:
    lines = [f"15.10.2026{' ' * 30}Pricing Report{' ' * 30}1", "", "\t" + "\t".join(SAP_LABELS), ""]
    lines += ["\t" + "\t".join(str(v) for v in row) for row in _sap_rows(text=True)]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def sap_unconverted() -> bytes:
    rows = [[str(v) for v in row] for row in _sap_rows(text=True)]
    width = [max(len(r[j]) for r in [SAP_LABELS, *rows]) for j in range(len(SAP_LABELS))]
    right = {3, 5}  # numbers are right-aligned in a list

    def line(cells: list[str]) -> str:
        return (
            "|"
            + "|".join(c.rjust(width[j]) if j in right else c.ljust(width[j]) for j, c in enumerate(cells))
            + "|"
        )

    rule = "-" * len(line(SAP_LABELS))
    out = [f"15.10.2026{' ' * 20}Pricing Report{' ' * 20}1", rule, line(SAP_LABELS), rule]
    out += [line(r) for r in rows]
    out.append(rule)
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def sap_mhtml() -> bytes:
    cells = "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>"
        for row in [SAP_LABELS, *_sap_rows(text=True)]
    )
    return (
        "MIME-Version: 1.0\r\nX-Document-Type: Workbook\r\n"
        'Content-Type: multipart/related; boundary="----=_NextPart_ALV"\r\n\r\n'
        "------=_NextPart_ALV\r\nContent-Location: file:///C:/Mappe1.htm\r\n"
        'Content-Transfer-Encoding: 8bit\r\nContent-Type: text/html; charset="utf-8"\r\n\r\n'
        '<html xmlns:x="urn:schemas-microsoft-com:office:excel"><body><table>'
        f"{cells}</table></body></html>\r\n------=_NextPart_ALV--\r\n"
    ).encode()


# ---- NetSuite: an item saved search ---------------------------------------------------------------
NETSUITE_LABELS = [
    "Internal ID",
    "Name",
    "Display Name",
    "Type",
    "Base Price",
    "Primary Sale Unit",
    "Inactive",
]


def _netsuite_rows() -> Rows:
    """``Name`` is the item number and ``Display Name`` the product's name."""
    return [
        [str(1200 + i), SKU[i], NAME[i], "Inventory Item", f"{PRICE[i]:.2f}", "Each", "No"] for i in range(N)
    ]


def netsuite_csv() -> bytes:
    lines = [",".join(f'"{v}"' for v in row) for row in [NETSUITE_LABELS, *_netsuite_rows()]]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def netsuite_excel() -> bytes:
    rows = "".join(
        "<Row>" + "".join(f'<Cell><Data ss:Type="String">{escape(v)}</Data></Cell>' for v in row) + "</Row>"
        for row in [NETSUITE_LABELS, *_netsuite_rows()]
    )
    return (
        '<?xml version="1.0"?>\r\n<?mso-application progid="Excel.Sheet"?>\r\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
        'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        f'<Worksheet ss:Name="ItemSearchResults"><Table>{rows}</Table></Worksheet></Workbook>\r\n'
    ).encode()


# ---- Oracle OTBI: one analysis of items, their list price and on-hand quantity per organization ------
OTBI_LABELS = [
    "Item Name",
    "Item Description",
    "Primary UOM Code",
    "List Price",
    "Currency Code",
    "Organization Code",
    "On-Hand Quantity",
]


def _otbi_rows(*, suppress: bool) -> Rows:
    rows: Rows = []
    for i in range(N):
        for k, org in enumerate(ORGS):
            item = [None] * 5 if suppress and k else [SKU[i], NAME[i], "Ea", PRICE[i], "THB"]
            rows.append([*item, org, STOCK[org][i]])
    return rows


def otbi_data_csv() -> bytes:
    lines = [",".join(str(v) for v in row) for row in [OTBI_LABELS, *_otbi_rows(suppress=False)]]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def otbi_excel() -> bytes:
    """The view as shown: a title, and each item's own columns written once and merged down its rows."""
    rows: Rows = [["Item Price and On-Hand"], [None], OTBI_LABELS, *_otbi_rows(suppress=True)]
    merges = [f"{col}{4 + 2 * i}:{col}{5 + 2 * i}" for i in range(N) for col in "ABCDE"]
    return _xlsx(rows, merges=merges)


def otbi_web_archive() -> bytes:
    return sap_mhtml().replace(b"Mappe1", b"analysis")


def _send(c: TestClient, name: str, data: bytes) -> dict[str, Any]:
    """Send one file as a new customer's first round. Returns where it stopped and what a person is told."""
    code = "".join(ch for ch in name.upper() if ch.isalnum())[:20]
    customer = c.post("/api/customers", json={"code": code, "name": name}).json()
    rid = c.post("/api/rounds", json={"customer_id": customer["id"]}).json()["id"]
    up = c.post(f"/api/rounds/{rid}/files", files=[("files", (name, data, "application/octet-stream"))])
    if up.status_code != 201:
        return {"stage": "upload", "status": up.status_code, "message": up.text}
    job_id = c.post(f"/api/rounds/{rid}/run", json={"sync": True}).json()["job_id"]
    job = c.get(f"/api/jobs/{job_id}").json()
    if job["status"] != "done":
        return {"stage": "run", "status": job["status"], "message": str(job["error"])}
    mapping = c.get(f"/api/mappings/{job['result']['mapping_version_id']}").json()
    policy = mapping["policy"]
    return {
        "stage": "card",
        "decision": policy["decision"],
        "reasons": policy["reasons"],
        "said": [r for r in policy["reasons"] if r != FIRST_TIME] + list(mapping["open_questions"]),
        "records": mapping["sample"],
        "document": mapping["document"],
    }


def _products(records: list[dict[str, Any]]) -> dict[str, tuple[Any, ...]]:
    return {r["sku"]: (r.get("product_name"), [p["amount"] for p in r.get("prices", [])]) for r in records}


def _products_right(records: list[dict[str, Any]]) -> bool:
    """Every record shown is a product of the file under its own code, with its name and price."""
    truth = {SKU[i]: (NAME[i], [PRICE[i]]) for i in range(N)}
    read = _products(records)
    return bool(read) and all(truth.get(sku) == values for sku, values in read.items())


def _names_and_prices_right(records: list[dict[str, Any]]) -> bool:
    """The rows shown are the file's rows in order, whatever was taken for the code."""
    names = [r.get("product_name") for r in records]
    prices = [[p["amount"] for p in r.get("prices", [])] for r in records]
    return bool(records) and names == NAME[: len(records)] and prices == [[v] for v in PRICE[: len(records)]]


# ---- the file itself: does it get in (ADR-074) -------------------------------------------------
TEXT_INSIDE = [
    pytest.param("zpricing.xls", sap_text_with_tabs, id="sap-text-with-tabs-saved-as-xls"),
    pytest.param("zpricing_alv.xls", sap_mhtml, id="sap-mhtml-saved-as-xls"),
    pytest.param("zpricing_alv.mhtml", sap_mhtml, id="sap-mhtml"),
    pytest.param("ItemSearchResults.xls", netsuite_excel, id="netsuite-export-microsoft-excel"),
    pytest.param("analysis.mht", otbi_web_archive, id="otbi-web-archive"),
]


@pytest.mark.parametrize(("name", "make"), TEXT_INSIDE)
def test_what_the_export_button_writes_is_read(client: TestClient, name: str, make: Any) -> None:
    out = _send(client, name, make())
    assert out["stage"] == "card", out.get("message")
    assert _names_and_prices_right(out["records"]), _products(out["records"])


def test_the_same_list_reads_the_same_through_every_sap_mechanism(client: TestClient) -> None:
    files = [
        ("a.xlsx", sap_alv_xlsx()),
        ("b.txt", sap_text_with_tabs()),
        ("c.xls", sap_text_with_tabs()),
        ("d.mhtml", sap_mhtml()),
    ]
    read = []
    for name, data in files:
        out = _send(client, name, data)
        for record in out["records"]:
            record.pop("source_meta", None)
        read.append(out["records"])
    assert all(records == read[0] for records in read[1:])


STILL_REFUSED = [
    pytest.param(
        "notes.xls", b"just a note, no table\r\n", "is not an Excel workbook", id="plain-text-as-xls"
    ),
    pytest.param("list.xlsx", sap_text_with_tabs(), "is not an Excel workbook", id="tab-text-as-xlsx"),
    pytest.param("page.mhtml", b"%PDF-1.7 not a web archive", "is not a web archive", id="pdf-as-mhtml"),
    pytest.param(
        "bomb.xls",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">]>'
        b'<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet><Table><Row><Cell>'
        b"<Data>&a;</Data></Cell></Row></Table></Worksheet></Workbook>",
        "declares a DTD or entities",
        id="xml-with-entities",
    ),
]


@pytest.mark.parametrize(("name", "data", "reason"), STILL_REFUSED)
def test_text_that_is_not_one_of_those_exports_is_still_refused_with_the_reason(
    client: TestClient, name: str, data: bytes, reason: str
) -> None:
    out = _send(client, name, data)
    assert (out["stage"], out["status"]) == ("upload", 415), out
    assert reason in out["message"], out["message"]


# ---- SAP ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("zpricing.xlsx", sap_alv_xlsx),
        ("zpricing.txt", sap_text_with_tabs),
        ("zpricing_list.txt", sap_unconverted),
    ],
    ids=["alv-xlsx", "text-with-tabs", "unconverted"],
)
def test_a_sap_pricing_list_is_read_and_its_currency_column_is_asked_about(
    client: TestClient, name: str, make: Any
) -> None:
    out = _send(client, name, make())
    assert out["stage"] == "card" and out["decision"] == "review"
    assert _products_right(out["records"]), _products(out["records"])
    # SAP labels the currency of the amount "Unit" and the unit of measure "UoM". FORMA takes "Unit" for
    # the selling unit, and asks what the unit THB means: wrong, and not silent.
    assert {r["unit"]["sell_unit"] for r in out["records"]} == {"THB"}
    assert any("THB" in line for line in out["said"]), out["said"]
    assert all(r["attributes"]["UoM"] == "PC" for r in out["records"])


def test_the_rules_of_an_unconverted_list_are_not_rows(client: TestClient) -> None:
    """The lines of dashes SAP draws above and below the header and at the end hold nothing (ADR-075)."""
    out = _send(client, "zpricing_list.txt", sap_unconverted())
    assert not any("null identity" in line or "unknown root" in line for line in out["said"]), out["said"]
    assert not any("engine/validation errors" in line for line in out["said"]), out["said"]


# ---- NetSuite ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "make"),
    [("ItemSearchResults.csv", netsuite_csv), ("ItemSearchResults.xls", netsuite_excel)],
    ids=["csv", "excel"],
)
def test_netsuite_name_is_the_item_number_and_the_card_asks(client: TestClient, name: str, make: Any) -> None:
    """NetSuite calls the item number ``Name`` and gives every record an ``Internal ID``. Both hold one
    code per row. FORMA takes the one that is not a system's record id, and asks (ADR-075)."""
    out = _send(client, name, make())
    assert out["stage"] == "card" and out["decision"] == "review"
    assert _products_right(out["records"]), _products(out["records"])
    assert any("'Name' is used as the product code and 'Internal ID'" in line for line in out["said"])
    assert any("low confidence on critical fields: /sku" in line for line in out["said"]), out["said"]
    assert [r["attributes"]["Internal ID"] for r in out["records"]] == [
        "1200",
        "1201",
        "1202",
        "1203",
        "1204",
    ]


# ---- Oracle OTBI ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "make", "merged"),
    [("analysis.csv", otbi_data_csv, False), ("analysis.xlsx", otbi_excel, True)],
    ids=["data-csv", "excel-view"],
)
def test_otbi_rows_per_organization_give_one_product_with_stock_in_each(
    client: TestClient, name: str, make: Any, merged: bool
) -> None:
    """One row per item and organization. In the Excel view the item's own cells are written once and
    merged down; FORMA reads them on each row. Either way: one product, its price once, stock per
    organization, and a question about the column taken for the warehouse (ADR-075)."""
    out = _send(client, name, make())
    assert out["stage"] == "card" and out["decision"] == "review"
    assert _products_right(out["records"]), _products(out["records"])
    for record in out["records"]:
        i = SKU.index(record["sku"])
        stock = {s["warehouse_code"]: s["qty"] for s in record["inventory"]}
        assert stock == {org: STOCK[org][i] for org in ORGS}, record
        assert "attributes" not in record  # nothing of the row was left over
    assert out["document"]["sources"][0]["table"]["fill_merged"] is merged
    assert any("is given per 'Organization Code'" in line for line in out["said"]), out["said"]
    assert not any("engine/validation errors" in line for line in out["said"]), out["said"]
