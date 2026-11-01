"""Files that used to lose data, change it, or stop a round with nothing to act on (ADR-055)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import polars as pl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run

from forma.ai.heuristics import HeuristicProposal, propose_mapping
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import profile_column, profile_workbook
from forma.profiling.tables import detect_tables, extract_table
from forma.schema_registry import TargetSchema

CONTRACT = Path(__file__).parents[2] / "target_schema" / "product.v1.json"
HEADER = ["รหัสสินค้า", "ชื่อสินค้า", "ยี่ห้อ", "ราคาขาย (รวม VAT)"]
ROWS = [[f"10{i:04d}", f"สินค้า {i}", "บัว", str(10 + i)] for i in range(1, 9)]
TITLE = ["บริษัท ตัวอย่าง จำกัด", None, None, None]
BLANK: list[Any] = [None, None, None, None]


def _schema() -> TargetSchema:
    return TargetSchema("product", 1, json.loads(CONTRACT.read_text(encoding="utf-8")))


def _book(path: Path, sheets: dict[str, list[list[Any]]], hidden: tuple[str, ...] = ()) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        ws.sheet_state = "hidden" if name in hidden else "visible"
    wb.save(path)
    return path


def _propose(path: Path) -> HeuristicProposal:
    return propose_mapping(profile_workbook(path), _schema())


def _grid(rows: list[list[Any]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={f"c{i}": pl.String for i in range(4)}, orient="row")


def _table(rows: list[list[Any]], **options: Any) -> pl.DataFrame:
    df = _grid(rows)
    # as a mapping reads it: the row count changes every round, so the end of the table is left open
    spec = detect_tables(df)[0].to_spec().model_copy(update={"data_end": None, **options})
    return extract_table(df, spec)


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _published_rows(c: TestClient, path: Path, code: str) -> list[dict[str, Any]]:
    customer = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    rid = c.post("/api/rounds", json={"customer_id": customer}).json()["id"]
    up = c.post(
        f"/api/rounds/{rid}/files",
        files=[("files", (path.name, path.read_bytes(), "application/octet-stream"))],
    )
    assert up.status_code == 201, up.text
    _run(c, rid)
    r = c.get(f"/api/rounds/{rid}").json()
    assert r["status"] == "review", r["summary"]
    assert c.post(f"/api/mappings/{r['mappings'][0]['id']}/approve", json={}).status_code == 200
    c.post(f"/api/rounds/{rid}/transform", params={"sync": True})
    dataset = c.get(f"/api/rounds/{rid}").json()["datasets"][-1]["id"]
    return c.get(f"/api/datasets/{dataset}/rows", params={"limit": 50}).json()["rows"]  # type: ignore[no-any-return]


# ---- a printed report --------------------------------------------------------------------------------------


@pytest.mark.parametrize("gap", [1, 2, 3])
def test_a_printed_report_keeps_every_page(gap: int) -> None:
    page_two = [TITLE, ["หน้า 2", None, None, None], BLANK, HEADER, *ROWS[4:]]
    rows = [TITLE, BLANK, HEADER, *ROWS[:4], *[BLANK] * gap, *page_two]
    assert len(detect_tables(_grid(rows))) == 1
    assert _table(rows)["รหัสสินค้า"].to_list() == [r[0] for r in ROWS]


def test_the_first_page_is_kept_when_it_is_short() -> None:
    rows = [TITLE, BLANK, HEADER, *ROWS[:1], TITLE, HEADER, *ROWS[1:]]
    assert _table(rows)["รหัสสินค้า"].to_list() == [r[0] for r in ROWS]


def test_a_row_that_only_starts_like_the_header_is_data() -> None:
    odd = ["รหัสสินค้า", "สินค้าที่ตั้งชื่อแปลก", "บัว", "12"]
    assert odd[0] in _table([HEADER, *ROWS, odd])["รหัสสินค้า"].to_list()


def test_a_one_cell_line_stays_unless_a_page_break_follows_it() -> None:
    note = ["หมายเหตุ", None, None, None]
    assert "หมายเหตุ" in _table([HEADER, *ROWS[:4], note, *ROWS[4:]])["รหัสสินค้า"].to_list()
    assert "หมายเหตุ" not in _table([HEADER, *ROWS[:4], note, HEADER, *ROWS[4:]])["รหัสสินค้า"].to_list()


def test_page_breaks_can_be_kept_on_purpose() -> None:
    rows = [HEADER, *ROWS[:4], HEADER, *ROWS[4:]]
    assert "รหัสสินค้า" in _table(rows, drop_repeated_headers=False)["รหัสสินค้า"].to_list()


def test_a_report_with_pages_reaches_the_json_whole(client: TestClient, isolated_env: Path) -> None:
    rows = [TITLE, BLANK, HEADER, *ROWS[:3], TITLE, BLANK, HEADER, *ROWS[3:]]
    path = _book(isolated_env / "report.xlsx", {"สินค้า": rows})
    records = _published_rows(client, path, "PAGES")
    assert sorted(r["sku"] for r in records) == sorted(r[0] for r in ROWS)


# ---- a second source for one collection ------------------------------------------------------------------

COST = [["รหัสสินค้า", "ราคาทุน", "หมายเหตุ"], *[[r[0], 5.5, "ลับ"] for r in ROWS]]


def test_a_hidden_cost_sheet_is_left_out_and_said_so(client: TestClient, isolated_env: Path) -> None:
    path = _book(isolated_env / "h.xlsx", {"สินค้า": [HEADER, *ROWS], "_ทุน": COST}, hidden=("_ทุน",))
    proposal = _propose(path)
    assert not any("ทุน" in (s.sheet.name or "") for s in proposal.mapping.sources if s.sheet)
    assert any("hidden" in q for q in proposal.open_questions)
    records = _published_rows(client, path, "HIDDEN")
    assert sorted(r["prices"][0]["amount"] for r in records) == [float(r[3]) for r in ROWS]


def test_a_second_price_sheet_is_a_question_not_a_crash(isolated_env: Path) -> None:
    prices = [
        ["รหัสสินค้า", "ประเภทราคา", "ราคา", "วันที่มีผล"],
        *[[r[0], "ส่ง", 5.5, "1/10/2569"] for r in ROWS],
    ]
    path = _book(isolated_env / "two.xlsx", {"สินค้า": [HEADER, *ROWS], "ราคา": prices})
    proposal = _propose(path)
    assert [c.target for c in proposal.mapping.collections].count("/prices") == 1
    assert any("left out" in q for q in proposal.open_questions)
    assert validate_mapping(proposal.mapping, _schema()).ok


def test_a_document_cannot_map_a_collection_twice(isolated_env: Path) -> None:
    doc = _propose(_book(isolated_env / "one.xlsx", {"สินค้า": [HEADER, *ROWS]})).mapping
    twice = doc.model_copy(update={"collections": [*doc.collections, doc.collections[0]]})
    report = validate_mapping(twice, _schema())
    assert not report.ok and "duplicate_collection" in {e.code for e in report.issues}


# ---- currency ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "code"), [("USD", "USD"), ("usd", "USD"), ("บาท", "THB"), ("ดอลลาร์", "USD"), ("EUR", "EUR")]
)
def test_a_currency_column_is_read(client: TestClient, isolated_env: Path, written: str, code: str) -> None:
    rows = [[*HEADER, "สกุลเงิน"], *[[*r, written] for r in ROWS]]
    records = _published_rows(
        client, _book(isolated_env / "c.xlsx", {"สินค้า": rows}), f"C{code}{len(written)}"
    )
    assert {r["currency"] for r in records} == {code}


def test_an_unknown_currency_stays_as_written_and_breaks_the_contract(
    client: TestClient, isolated_env: Path
) -> None:
    rows = [[*HEADER, "สกุลเงิน"], *[[*r, "ไม่ทราบ"] for r in ROWS]]
    records = _published_rows(client, _book(isolated_env / "c.xlsx", {"สินค้า": rows}), "CUNK")
    assert {r["currency"] for r in records} == {"ไม่ทราบ"}


def test_no_currency_column_keeps_the_default_quietly(isolated_env: Path) -> None:
    proposal = _propose(_book(isolated_env / "c.xlsx", {"สินค้า": [HEADER, *ROWS]}))
    assert not any("currency" in q for q in proposal.open_questions)
    field = next(f for f in proposal.mapping.fields if f.target == "/currency")
    assert field.expr.model_dump()["value"] == "THB"


def test_a_price_header_naming_a_currency_is_a_question(isolated_env: Path) -> None:
    header = [*HEADER[:3], "ราคาขาย (USD)"]
    proposal = _propose(_book(isolated_env / "c.xlsx", {"สินค้า": [header, *ROWS]}))
    assert any("names a currency" in q for q in proposal.open_questions)


def test_a_currency_on_another_sheet_is_a_question(isolated_env: Path) -> None:
    stock = [["รหัสสินค้า", "คลัง", "ยอดคงเหลือ", "สกุลเงิน"], *[[r[0], "BKK", 5, "USD"] for r in ROWS]]
    proposal = _propose(_book(isolated_env / "c.xlsx", {"สินค้า": [HEADER, *ROWS], "สต็อก": stock}))
    assert any("currency column" in q for q in proposal.open_questions)


# ---- which comes first in 03/04/2026 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["31/10/2026", "15/10/2026"], {"day_first"}),
        (["10/31/2026", "10/01/2026"], {"month_first"}),
        (["10/01/2026", "11/02/2026"], {"ambiguous_date"}),
        (["1/10/2569", "2/10/2569"], {"day_first"}),
        (["1/10/69", "2/10/69"], {"day_first"}),
        (["๓๑/๑๐/๒๕๖๙", "๑/๑๐/๒๕๖๙"], {"day_first"}),
        (["31/10/2026", "10/31/2026"], {"day_first", "month_first"}),
        (["2026-10-01", "2026-10-31"], set()),
    ],
)
def test_a_column_says_which_way_its_dates_run(values: list[str], expected: set[str]) -> None:
    hints = set(profile_column(pl.Series("วันที่", values), 0).hints)
    assert hints & {"day_first", "month_first", "ambiguous_date"} == expected


def _dated(path: Path, start: str, end: str) -> Path:
    promos = [
        ["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"],
        *[[r[0], f"P{i}", "ลด%", 10, start, end] for i, r in enumerate(ROWS)],
    ]
    return _book(path, {"สินค้า": [HEADER, *ROWS], "โปรโมชั่น": promos})


def test_one_column_settles_the_order_for_the_next(client: TestClient, isolated_env: Path) -> None:
    records = _published_rows(client, _dated(isolated_env / "d.xlsx", "10/01/2026", "10/31/2026"), "USDATE")
    assert {(p["start_date"], p["end_date"]) for r in records for p in r["promotions"]} == {
        ("2026-10-01", "2026-10-31")
    }


def test_a_thai_file_still_reads_day_first(client: TestClient, isolated_env: Path) -> None:
    records = _published_rows(client, _dated(isolated_env / "d.xlsx", "1/10/2569", "31/10/2569"), "THDATE")
    assert {p["start_date"] for r in records for p in r["promotions"]} == {"2026-10-01"}


def test_mixed_dates_are_a_question(isolated_env: Path) -> None:
    proposal = _propose(_dated(isolated_env / "d.xlsx", "31/10/2026", "10/31/2026"))
    assert any("day-first" in q and "month-first" in q for q in proposal.open_questions)


def test_dates_that_could_be_either_are_a_question(isolated_env: Path) -> None:
    proposal = _propose(_dated(isolated_env / "d.xlsx", "10/01/2026", "11/02/2026"))
    assert any("could be day-first or month-first" in q for q in proposal.open_questions)
