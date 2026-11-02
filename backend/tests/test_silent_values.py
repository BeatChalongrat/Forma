"""Values that used to change quietly: a default filled in, a number read another way, a duplicate chosen,
the wrong version exported, a reused mapping never checked (ADR-056)."""

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

from forma.ai.heuristics import propose_mapping
from forma.engine import thai
from forma.engine.ops import CompileContext, compile_expr
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import call, col
from forma.profiling.profile import profile_workbook
from forma.schema_registry import TargetSchema

CONTRACT = Path(__file__).parents[2] / "target_schema" / "product.v1.json"
HEADER = ["รหัสสินค้า", "ชื่อสินค้า", "ยี่ห้อ", "ราคาขาย (รวม VAT)", "สถานะ"]
ROWS = [[f"10{i:04d}", f"สินค้า {i}", "บัว", 100.0 + i, "ขาย"] for i in range(1, 9)]


def _schema() -> TargetSchema:
    return TargetSchema("product", 1, json.loads(CONTRACT.read_text(encoding="utf-8")))


def _book(path: Path, rows: list[list[Any]], promos: list[list[Any]] | None = None) -> Path:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("สินค้า")
    for row in rows:
        ws.append(row)
    if promos:
        ps = wb.create_sheet("โปรโมชั่น")
        for row in promos:
            ps.append(row)
    wb.save(path)
    return path


def _transform(path: Path) -> Any:
    schema = _schema()
    doc = propose_mapping(profile_workbook(path), schema).mapping
    bindings = {s.id: BoundSource(path, None, "f1", path.name) for s in doc.sources}
    return TransformEngine(schema).run(doc, bindings)


# ---- A4: numbers -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("189.-", 189.0),
        ("1,289.-", 1289.0),
        ("200.00-", -200.0),
        ("1.500,00", 1500.0),
        ("1.500.000,50", 1500000.5),
        ("1500,50", 1500.5),
        ("1,500.00", 1500.0),
        ("1,500", 1500.0),
        ("1.500", 1.5),
        ("(200)", -200.0),
        ("฿ 1,284.50", 1284.5),
        ("๑๒๐.-", 120.0),
        ("12.5%", 12.5),
        ("-", None),
    ],
)
def test_a_written_amount_is_read_the_same_way_everywhere(text: str, value: float | None) -> None:
    assert thai.parse_number_th_py(text) == value
    frame = pl.DataFrame({"s.v": [text]})
    ctx = CompileContext(default_source="s", columns=set(frame.columns))
    out = frame.select(compile_expr(call("parse_number_th", col("v")), ctx).alias("n"))["n"][0]
    assert out == value


# ---- A6: units ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "unit", "pack"), [("1x24", "CARTON", 24), ("1 X 12", "CARTON", 12), ("12's", "PACK", 12)]
)
def test_a_bare_packing_figure_names_its_unit(text: str, unit: str, pack: int) -> None:
    assert thai.unit_normalize_py(text) == unit and thai.pack_size_py(text) == pack
    frame = pl.DataFrame({"s.v": [text]})
    ctx = CompileContext(default_source="s", columns=set(frame.columns))
    assert frame.select(compile_expr(call("unit_normalize", col("v")), ctx).alias("u"))["u"][0] == unit


# ---- A3: no default on an enum field ---------------------------------------------------------------------------


def test_an_unknown_status_breaks_the_contract_instead_of_becoming_active(tmp_path: Path) -> None:
    rows = [list(r) for r in ROWS]
    rows[0][4], rows[1][4], rows[2][4] = "พักการขาย", None, "รอของ"
    res = _transform(_book(tmp_path / "s.xlsx", [HEADER, *rows]))
    by = {r["sku"]: r for r in res.records}
    assert by["100001"]["status"] == "พักการขาย" and by["100003"]["status"] == "รอของ"
    assert by["100002"]["status"] == "active"  # a blank takes the contract's own default, not FORMA's guess
    assert res.stats["invalid_records"] == 2
    assert by["100004"]["status"] == "active"


def test_an_unknown_promotion_kind_is_not_a_percentage(tmp_path: Path) -> None:
    promos = [
        ["รหัสสินค้า", "รหัสโปร", "ประเภทโปร", "ส่วนลด %", "เริ่ม", "สิ้นสุด"],
        ["100001", "P1", "Flash Sale", 10, "1/10/2569", "31/10/2569"],
        ["100002", "P2", "ลด%", 10, "1/10/2569", "31/10/2569"],
    ]
    res = _transform(_book(tmp_path / "p.xlsx", [HEADER, *ROWS], promos))
    by = {r["sku"]: r for r in res.records}
    assert by["100001"]["promotions"][0]["type"] == "Flash Sale"
    assert by["100002"]["promotions"][0]["type"] == "percent_off"
    assert res.stats["invalid_records"] == 1


# ---- A2: duplicates --------------------------------------------------------------------------------------------


def test_duplicates_that_disagree_are_an_error_and_identical_ones_a_warning(tmp_path: Path) -> None:
    same = [HEADER, *ROWS, list(ROWS[0])]
    res = _transform(_book(tmp_path / "same.xlsx", same))
    f = next(x for x in res.findings if x.kind == "dedupe")
    assert f.severity == "warning" and "identical rows" in f.message and len(res.records) == len(ROWS)

    conflict = [HEADER, *ROWS, [*ROWS[0][:3], 999.0, "ขาย"]]
    res = _transform(_book(tmp_path / "conflict.xlsx", conflict))
    f = next(x for x in res.findings if x.kind == "dedupe" and x.severity == "error")
    assert "conflicting values" in f.message and f.target == "/prices"
    assert f.details["conflicting"] == 1
    assert len(res.records[0]["prices"]) == 2  # Both alternatives remain available to the reviewer.


# ---- A1 and A5 through the API --------------------------------------------------------------------------------


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _round(c: TestClient, cust: str, path: Path) -> dict[str, Any]:
    rid = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    up = c.post(
        f"/api/rounds/{rid}/files",
        files=[("files", (path.name, path.read_bytes(), "application/octet-stream"))],
    )
    assert up.status_code == 201, up.text
    _run(c, rid)
    return c.get(f"/api/rounds/{rid}").json()  # type: ignore[no-any-return]


def _publish(c: TestClient, r: dict[str, Any]) -> str:
    if r["status"] in ("review", "blocked"):
        assert c.post(f"/api/mappings/{r['mappings'][0]['id']}/approve", json={}).status_code == 200
    r = c.get(f"/api/rounds/{r['id']}").json()
    if not r.get("datasets"):
        c.post(f"/api/rounds/{r['id']}/transform", params={"sync": True})
        r = c.get(f"/api/rounds/{r['id']}").json()
    d = r["datasets"][-1]["id"]
    assert c.post(f"/api/datasets/{d}/approve", json={"acknowledge_anomalies": True}).status_code == 200
    assert c.post(f"/api/datasets/{d}/export", json={"format": "json"}).status_code == 201
    return str(d)


def _verdict(c: TestClient, rid: str) -> dict[str, Any]:
    v = c.get(f"/api/rounds/{rid}/verdict").json()
    return v.get("verdict", v)  # type: ignore[no-any-return]


def test_a_reused_mapping_is_still_checked_on_the_data(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "VAT", "name": "vat"}).json()["id"]
    first = _round(c, cust, _book(isolated_env / "r1.xlsx", [HEADER, *ROWS]))
    _publish(c, first)

    net = [[*r[:3], round(r[3] / 1.07, 2), r[4]] for r in ROWS]  # the same layout, VAT taken out
    second = _round(c, cust, _book(isolated_env / "r2.xlsx", [HEADER, *net]))
    assert second["status"] == "review" and second["summary"]["drift"] == "identical"
    v = _verdict(c, second["id"])
    assert "vat_shift" in v["rules_fired"] and v["outcome"] == "answer"

    satang = [[*r[:3], r[3] * 100, r[4]] for r in ROWS]
    third = _round(c, cust, _book(isolated_env / "r3.xlsx", [HEADER, *satang]))
    assert third["status"] == "review" and "scaled" in _verdict(c, third["id"])["rules_fired"]

    # a new price list is a data change, not a mapping doubt: the round passes on its own
    prices = [[*r[:3], r[3] + 5 * (i % 2), r[4]] for i, r in enumerate(ROWS)]
    fourth = _round(c, cust, _book(isolated_env / "r4.xlsx", [HEADER, *prices]))
    assert fourth["status"] in ("transformed", "auto_approved", "approved")


def test_a_conflicting_duplicate_holds_a_returning_customer(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "DUP", "name": "dup"}).json()["id"]
    _publish(c, _round(c, cust, _book(isolated_env / "d1.xlsx", [HEADER, *ROWS])))
    second = _round(c, cust, _book(isolated_env / "d2.xlsx", [HEADER, *ROWS, [*ROWS[0][:3], 999.0, "ขาย"]]))
    assert second["status"] == "review"
    reasons = " ".join(c.get(f"/api/mappings/{second['mappings'][0]['id']}").json()["policy"]["reasons"])
    assert "engine/validation errors" in reasons


def test_publishing_exports_the_version_a_person_approved(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import ImportRound
    from forma.db.session import session_scope
    from forma.services import publishing
    from forma.services.context import get_context

    c = client
    cust = c.post("/api/customers", json={"code": "PUB", "name": "pub"}).json()["id"]
    r = _round(c, cust, _book(isolated_env / "p1.xlsx", [HEADER, *ROWS]))
    v1 = _publish(c, r)
    with session_scope() as s:
        row = s.get(ImportRound, r["id"])
        assert row is not None
        row.summary = {**(row.summary or {}), "auto_publish": True}
    edit = c.post(
        f"/api/datasets/{v1}/changes",
        json={
            "operations": [
                {"kind": "set_cell", "identity": "100001", "target": "/product_name", "value": "แก้แล้ว"}
            ]
        },
    )
    assert edit.status_code == 201, edit.text
    v2 = edit.json()["dataset"]["id"]
    assert c.post(f"/api/datasets/{v2}/approve", json={}).status_code == 200
    with session_scope() as s:
        out = publishing.auto_publish(get_context(), s, r["id"])
    assert out["decision"] == "publish"
    assert c.get(f"/api/datasets/{v2}").json()["status"] == "published", out
    assert c.get(f"/api/datasets/{v1}").json()["status"] == "superseded"
