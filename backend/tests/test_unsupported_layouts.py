"""Sheet layouts the profiler was not written for: what a person is told when FORMA reads one.

Every file holds the same six products. A layout passes when the review card says something beyond "first
time this customer is seen", or when the records are right. The tests marked ``xfail`` are layouts where
FORMA drops or misplaces data and the card says nothing: they fail the day that is fixed, and the mark goes.
No model is called; this is the deterministic path a file takes before, and without, an AI answer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.worksheet.worksheet import Worksheet
from tests.test_api import _run, _upload

N = 6
SKU = [f"A{i:03d}" for i in range(1, N + 1)]
NAME = [f"สินค้า {i}" for i in range(1, N + 1)]
PRICE = [float(i * 107) for i in range(1, N + 1)]
STOCK = [float(3 * i) for i in range(1, N + 1)]
PRODUCT_HEADER = ["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย (รวม VAT)"]
STOCK_HEADER = ["รหัสสินค้า", "คลัง", "ยอดคงเหลือ"]
FIRST_TIME = "first time this customer/format is seen"

Layout = Callable[[Worksheet, openpyxl.Workbook], None]


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _products(ws: Worksheet) -> None:
    ws.append(PRODUCT_HEADER)
    for i in range(N):
        ws.append([SKU[i], NAME[i], PRICE[i]])


def _stock(ws: Worksheet) -> None:
    ws.append(STOCK_HEADER)
    for i in range(N):
        ws.append([SKU[i], "BKK", STOCK[i]])


# ---- layouts FORMA reads --------------------------------------------------------------------------
def two_blank_rows(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Two tables on one sheet with two blank rows between them."""
    _products(ws)
    ws.append([None])
    ws.append([None])
    _stock(ws)


def two_blank_columns(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Two tables side by side with two blank columns between them; the stock rows are in another order."""
    ws.append([*PRODUCT_HEADER, None, None, *STOCK_HEADER])
    for i in range(N):
        j = N - 1 - i
        ws.append([SKU[i], NAME[i], PRICE[i], None, None, SKU[j], "BKK", STOCK[j]])


# ---- layouts FORMA does not read -----------------------------------------------------------------
def three_row_header(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """A header of three rows: FORMA takes the third as the header and loses the names above it."""
    ws.append(["ข้อมูลสินค้า", None, "ราคา", None])
    ws.append(["รหัสสินค้า", "ชื่อสินค้า", "ราคาปลีก", "ราคาส่ง"])
    ws.append(["(รหัส)", "(ไทย)", "รวม VAT", "ไม่รวม VAT"])
    for i in range(N):
        ws.append([SKU[i], NAME[i], PRICE[i], PRICE[i] - 7])


def one_blank_row(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Two tables with one blank row between them: the first becomes lines above the second's header."""
    _products(ws)
    ws.append([None])
    _stock(ws)


def late_header(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """A header below sheet row 60: the search for it stops before it gets there."""
    for k in range(64):
        ws.append([f"เงื่อนไขข้อ {k + 1}", "รายละเอียดของเงื่อนไขการขายและการส่งสินค้า"])
    _products(ws)


def transposed(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Field names down the first column and one product per column."""
    ws.append(["รหัสสินค้า", *SKU])
    ws.append(["ชื่อสินค้า", *NAME])
    ws.append(["ราคาขาย (รวม VAT)", *PRICE])
    ws.append(["หน่วย", *(["ชิ้น"] * N)])


def key_value_form(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """One product per block of label and value lines."""
    for i in range(N):
        ws.append(["รหัสสินค้า", SKU[i]])
        ws.append(["ชื่อสินค้า", NAME[i]])
        ws.append(["ราคาขาย (รวม VAT)", PRICE[i]])
        ws.append([None])
        ws.append([None])


def one_blank_column(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Two tables side by side with one blank column between them: FORMA reads them as one table."""
    ws.append([*PRODUCT_HEADER, None, *STOCK_HEADER])
    for i in range(N):
        j = N - 1 - i
        ws.append([SKU[i], NAME[i], PRICE[i], None, SKU[j], "BKK", STOCK[j]])


def stock_by_warehouse_and_month(ws: Worksheet, wb: openpyxl.Workbook) -> None:
    """Stock in a grid of warehouse by month under a two-row header, on its own sheet."""
    _products(ws)
    stock = wb.create_sheet("Stock")
    stock.append([None, "BKK", None, "CNX", None])
    stock.append(["รหัสสินค้า", "ม.ค.", "ก.พ.", "ม.ค.", "ก.พ."])
    for i in range(N):
        stock.append([SKU[i], 10 + i, 20 + i, 30 + i, 40 + i])


def _card(c: TestClient, root: Path, layout: Layout) -> dict[str, Any]:
    """Upload the layout as a new customer's first file and return what its review card holds."""
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "สินค้า"
    layout(ws, wb)
    path = root / f"{layout.__name__}.xlsx"
    wb.save(path)
    customer = c.post("/api/customers", json={"code": layout.__name__[:20].upper(), "name": "layout"}).json()
    rid = c.post("/api/rounds", json={"customer_id": customer["id"]}).json()["id"]
    _upload(c, rid, path, path.name)
    mapping = c.get(f"/api/mappings/{_run(c, rid)['result']['mapping_version_id']}").json()
    policy = mapping["policy"]
    return {
        "decision": policy["decision"],
        "reasons": policy["reasons"],
        "said": [r for r in policy["reasons"] if r != FIRST_TIME] + list(mapping["open_questions"]),
        "records": mapping["sample"],
        "tables": c.get(f"/api/rounds/{rid}").json()["files"][0]["tables"],
    }


def _read(records: list[dict[str, Any]]) -> dict[str, tuple[Any, ...]]:
    return {
        r["sku"]: (
            r.get("product_name"),
            [p["amount"] for p in r.get("prices", [])],
            [i["qty"] for i in r.get("inventory", [])],
        )
        for r in records
    }


WITH_STOCK = {SKU[i]: (NAME[i], [PRICE[i]], [STOCK[i]]) for i in range(N)}


def _right(records: list[dict[str, Any]]) -> bool:
    """Every record shown is a product of the file with its name, price and stock (the card shows a few)."""
    read = _read(records)
    return bool(read) and all(WITH_STOCK.get(sku) == values for sku, values in read.items())


@pytest.mark.parametrize("layout", [two_blank_rows, two_blank_columns])
def test_two_tables_on_one_sheet_are_read_when_the_gap_is_two(
    client: TestClient, isolated_env: Path, layout: Layout
) -> None:
    card = _card(client, isolated_env, layout)
    assert card["tables"] == 2 and card["decision"] == "review"
    assert _right(card["records"]), _read(card["records"])  # stock joined by code, not by sheet row


@pytest.mark.xfail(strict=True, reason="rows read as the second table are reported as rows nobody read")
def test_rows_read_as_another_table_are_not_reported_as_unread(
    client: TestClient, isolated_env: Path
) -> None:
    card = _card(client, isolated_env, two_blank_rows)
    assert card["said"] == [], card["said"]


@pytest.mark.parametrize(
    ("layout", "decision", "says"),
    [
        (three_row_header, "block", "required target /product_name is not mapped"),
        (one_blank_row, "block", "required target /product_name is not mapped"),
        (late_header, "block", "required target /product_name is not mapped"),
        (transposed, "review", "No SKU-like header found in root table"),
        (key_value_form, "review", "rows that look like data follow a blank gap"),
    ],
)
def test_a_layout_forma_cannot_read_is_stopped_or_questioned(
    client: TestClient, isolated_env: Path, layout: Layout, decision: str, says: str
) -> None:
    card = _card(client, isolated_env, layout)
    assert card["decision"] == decision, card["reasons"]
    assert any(says in line for line in card["said"]), card["said"]
    assert not _right(card["records"])  # FORMA did not read it: stopping is the right answer


def test_a_transposed_sheet_passes_the_contract_with_field_names_as_codes(
    client: TestClient, isolated_env: Path
) -> None:
    """The contract cannot tell: only the low confidence on the identity keeps these records from going out."""
    card = _card(client, isolated_env, transposed)
    assert {r["sku"] for r in card["records"]} == {"ราคาขาย (รวม VAT)", "หน่วย"}
    assert not any("validation error" in line for line in card["reasons"])
    assert any("low confidence on critical fields: /sku" in line for line in card["reasons"])


def test_two_tables_one_blank_column_apart_are_reported(client: TestClient, isolated_env: Path) -> None:
    """FORMA reads them as one table. The code's header a second time gives it away: the first table is
    read, the columns beside the second header are not, and the card says which (ADR-075)."""
    card = _card(client, isolated_env, one_blank_column)
    assert any("has the header 'รหัสสินค้า' twice" in line for line in card["said"]), card["said"]
    assert {r["sku"]: r["product_name"] for r in card["records"]} == dict(zip(SKU[:5], NAME[:5], strict=True))
    assert not any(r.get("inventory") for r in card["records"])  # stock of another row is not this product's


def test_a_record_never_carries_another_products_code(client: TestClient, isolated_env: Path) -> None:
    card = _card(client, isolated_env, one_blank_column)
    for record in card["records"]:
        others = set(SKU) - {record["sku"]}
        assert not others & {str(v) for v in record.get("attributes", {}).values()}, record


@pytest.mark.xfail(strict=True, reason="a sheet no field reads is not mentioned; its group header is lost")
def test_a_stock_sheet_nothing_reads_is_reported(client: TestClient, isolated_env: Path) -> None:
    card = _card(client, isolated_env, stock_by_warehouse_and_month)
    assert card["tables"] == 2
    assert card["said"] or all(r.get("inventory") for r in card["records"]), card["reasons"]
