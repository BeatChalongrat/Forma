"""The files customers actually send, end to end through the API with the deterministic mapper (mock LLM),
and the AI branches with a fake provider: Thai CSVs, Buddhist dates, currency symbols, heading and total
rows, leading zeros, two files in one round, renamed files and columns, missing prices, empty sheets,
ambiguous sheet patterns, business checks in a custom contract, typed clarifications."""

from __future__ import annotations

import copy
import csv
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _FakeProvider, _run, _upload, make_workbook

FIXTURES = Path(__file__).parent / "fixtures" / "harness"


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def _customer(c: TestClient, code: str) -> str:
    return str(c.post("/api/customers", json={"code": code, "name": code}).json()["id"])


def _round(c: TestClient, cust: str, path: Path, name: str | None = None) -> dict[str, Any]:
    r = c.post("/api/rounds", json={"customer_id": cust}).json()
    _upload(c, r["id"], path, name or path.name)
    _run(c, r["id"])
    return c.get(f"/api/rounds/{r['id']}").json()  # type: ignore[no-any-return]


def _mapping(c: TestClient, r: dict[str, Any]) -> dict[str, Any]:
    return c.get(f"/api/mappings/{r['summary']['mapping_version_id']}").json()  # type: ignore[no-any-return]


def _field(doc: dict[str, Any], suffix: str) -> dict[str, Any] | None:
    fields = [*doc["fields"], *(f for coll in doc["collections"] for f in coll["fields"])]
    return next((f for f in fields if f["target"].endswith(suffix)), None)


def _approve_and_transform(c: TestClient, r: dict[str, Any], *, publish: bool = True) -> list[dict[str, Any]]:
    """What the operator does when the mapping is right: approve, transform, approve the data, export."""
    mv = r["summary"]["mapping_version_id"]
    m = c.get(f"/api/mappings/{mv}").json()
    if m["status"] == "proposed":
        res = c.post(f"/api/mappings/{mv}/approve", json={})
        assert res.status_code == 200, res.text
    job = c.post(f"/api/rounds/{r['id']}/transform", params={"sync": "true"}).json()
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    ds = c.get(f"/api/rounds/{r['id']}").json()["datasets"][-1]["id"]
    if publish:
        approved = c.post(f"/api/datasets/{ds}/approve", json={"acknowledge_anomalies": True})
        assert approved.status_code == 200, approved.text
        assert c.post(f"/api/datasets/{ds}/export", params={"fmt": "json"}).status_code == 201
    return c.get(f"/api/datasets/{ds}/rows", params={"limit": 500}).json()["rows"]  # type: ignore[no-any-return]


def _profile(c: TestClient, r: dict[str, Any]) -> dict[str, Any]:
    f = r["files"][0]
    return c.get(f"/api/rounds/{r['id']}/files/{f['id']}").json()["profile"]  # type: ignore[no-any-return]


def _sheet(path: Path, rows: list[list[Any]], *, title: str = "สินค้า") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = title
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


HEADER = ["รหัสสินค้า", "ชื่อสินค้า", "หมวด", "ราคาขาย", "สถานะ"]


def _products(n: int = 6) -> list[list[Any]]:
    return [
        [f"A{i:03d}", f"สินค้า {i}", "เครื่องดื่ม" if i % 2 else "ของใช้", i * 100, "ขาย"] for i in range(1, n + 1)
    ]


# --------------------------------------------------------------------------------------------
# 1. the shapes of real files
# --------------------------------------------------------------------------------------------
def test_thai_csv_in_cp874_with_semicolons(client: TestClient, isolated_env: Path) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["รหัสสินค้า", "ชื่อสินค้า", "ราคาขาย", "สถานะ"])
    for i in range(1, 6):
        w.writerow([f"P{i:03d}", f"น้ำดื่ม {i}", f"{i * 1250:,}.50", "ขาย"])
    path = isolated_env / "export.csv"
    path.write_bytes(buf.getvalue().encode("cp874"))
    r = _round(client, _customer(client, "CSV1"), path)
    assert r["status"] in ("review", "auto_approved"), r["summary"]
    assert _profile(client, r)["meta"]["csv_delimiter"] == ";"
    rows = _approve_and_transform(client, r)
    assert len(rows) == 5 and rows[0]["sku"] == "P001" and rows[0]["product_name"] == "น้ำดื่ม 1"
    assert rows[0]["prices"][0]["amount"] == 1250.5 and rows[4]["prices"][0]["amount"] == 6250.5


def test_buddhist_dates_thai_months_and_two_digit_years_become_iso(
    client: TestClient, isolated_env: Path
) -> None:
    r = _round(client, _customer(client, "DATE1"), make_workbook(isolated_env / FILE_NAME))
    rows = _approve_and_transform(client, r)
    promos = {p["promo_code"]: p for row in rows for p in row.get("promotions", [])}
    assert (
        promos["PR1"]["start_date"] == "2026-10-01" and promos["PR1"]["end_date"] == "2026-10-31"
    )  # 1/10/2569, 31 ต.ค. 69
    assert (
        promos["PR2"]["start_date"] == "2026-10-05" and promos["PR2"]["end_date"] == "2026-10-15"
    )  # 15/10/69
    assert promos["PR1"]["discount_percent"] == 10 and promos["PR2"]["discount_percent"] == 5  # "10%" and "5"


def test_currency_symbols_thai_digits_and_percent_signs_parse(client: TestClient, isolated_env: Path) -> None:
    rows = _products(4)
    rows[0][3], rows[1][3], rows[2][3], rows[3][3] = "฿1,250.50", "๑๒๕๐", "1.250,00", " 990 บาท"
    path = _sheet(isolated_env / "symbols.xlsx", [HEADER, *rows])
    r = _round(client, _customer(client, "SYM1"), path)
    out = {
        row["sku"]: row["prices"][0]["amount"]
        for row in _approve_and_transform(client, r)
        if row.get("prices")
    }
    assert out["A001"] == 1250.5 and out["A002"] == 1250 and out["A004"] == 990
    assert out["A003"] in (1250.0, 1.25)  # a European thousands separator is ambiguous; it must not crash


def test_heading_rows_and_a_total_row_are_not_products(client: TestClient, isolated_env: Path) -> None:
    body: list[list[Any]] = [["รหัส", "ชื่อ", "ราคา", "คงเหลือ"]]
    for section, items in (("เครื่องดื่ม", 3), ("ของใช้", 3), ("อาหารแห้ง", 2)):
        body.append([section, None, None, None])
        for i in range(items):
            body.append([f"{section[:1]}{i}", f"{section} {i}", 10 + i, 5])
    body.append(["รวม", None, 100, 40])
    r = _round(client, _customer(client, "HAND1"), _sheet(isolated_env / "hand.xlsx", body, title="Sheet1"))
    profile = _profile(client, r)["tables"][0]
    assert profile["table"]["section_rows"] >= 3 and any(c["name"] == "section" for c in profile["columns"])
    rows = _approve_and_transform(client, r)
    skus = {row["sku"] for row in rows}
    assert len(rows) == 8 and "รวม" not in skus and "เครื่องดื่ม" not in skus
    seen = {
        c
        for row in rows
        for c in (row.get("category") if isinstance(row.get("category"), list) else [row.get("category")])
    }
    assert seen >= {"เครื่องดื่ม", "ของใช้", "อาหารแห้ง"}


def test_leading_zeros_survive_and_duplicate_codes_are_reported(
    client: TestClient, isolated_env: Path
) -> None:
    rows = [
        ["000123", "A", "ของใช้", 10, "ขาย"],
        ["000124", "B", "ของใช้", 20, "ขาย"],
        ["000123", "A again", "ของใช้", 30, "ขาย"],
    ]
    r = _round(client, _customer(client, "ZERO1"), _sheet(isolated_env / "zeros.xlsx", [HEADER, *rows]))
    m = _mapping(client, r)
    # Conflicting alternatives must now be resolved before approval (ADR-062).
    rejected = client.post(f"/api/mappings/{m['id']}/approve", json={})
    assert rejected.status_code == 422
    out = client.post(f"/api/rounds/{r['id']}/mapping-preview", json={"document": m["document"]}).json()[
        "records"
    ]
    assert {row["sku"] for row in out} == {"000123", "000124"}  # text codes keep their zeros
    sim = m["policy"]["simulation"]
    assert sim["rows_out"] == 2 and sim["rows_in"] == 3
    findings = json.dumps(m["policy"].get("verification", {})) + json.dumps(r.get("review_tasks", []))
    assert "duplicate" in findings.lower() or any("dedupe" in str(t) for t in r.get("review_tasks", []))


def test_blank_columns_and_a_merged_title_row(client: TestClient, isolated_env: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Export"
    ws.append(["รายงานสินค้า ประจำเดือน"])
    ws.merge_cells("A1:H1")
    ws.append([])
    ws.append(["รหัสสินค้า", None, "ชื่อสินค้า", None, "ราคาขาย", "สถานะ", None, None])
    for i in range(1, 6):
        ws.append([f"B{i:03d}", None, f"ชื่อ {i}", None, i * 10, "ขาย", None, None])
    wb.save(isolated_env / "blank_cols.xlsx")
    r = _round(client, _customer(client, "BLANK1"), isolated_env / "blank_cols.xlsx")
    rows = _approve_and_transform(client, r)
    assert len(rows) == 5 and rows[0]["sku"] == "B001" and rows[0]["product_name"] == "ชื่อ 1"


def test_empty_and_header_only_sheets_are_ignored(client: TestClient, isolated_env: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Empty"
    header_only = wb.create_sheet("Notes")
    header_only.append(["หมายเหตุ", "ผู้ส่ง"])
    data = wb.create_sheet("สินค้า")
    for row in [HEADER, *_products(5)]:
        data.append(row)
    wb.save(isolated_env / "sparse.xlsx")
    r = _round(client, _customer(client, "SPARSE1"), isolated_env / "sparse.xlsx")
    tables = _profile(client, r)["tables"]
    assert [t["sheet"] for t in tables if t["n_rows"] > 0] == ["สินค้า"]
    assert len(_approve_and_transform(client, r)) == 5


def test_two_files_in_one_round_bind_by_name(client: TestClient, isolated_env: Path) -> None:
    products = _sheet(isolated_env / "products.xlsx", [HEADER, *_products(4)])
    stock = isolated_env / "stock.csv"
    stock.write_text("รหัสสินค้า,คลัง,ยอดคงเหลือ\nA001,BKK,7\nA002,BKK,3\nA001,CNX,1\n", encoding="utf-8")
    cust = _customer(client, "TWO1")
    r = client.post("/api/rounds", json={"customer_id": cust}).json()
    _upload(client, r["id"], products, "products.xlsx")
    _upload(client, r["id"], stock, "stock.csv")
    _run(client, r["id"])
    r = client.get(f"/api/rounds/{r['id']}").json()
    doc = _mapping(client, r)["document"]
    patterns = {s["id"]: s["file"].get("name_pattern") for s in doc["sources"]}
    assert len(doc["sources"]) >= 2 and all(patterns.values()), patterns  # every source names its file
    rows = _approve_and_transform(client, r)
    by_sku = {row["sku"]: row for row in rows}
    assert (
        len(rows) == 4
        and len(by_sku["A001"].get("inventory", [])) == 2
        and by_sku["A003"].get("inventory", []) == []
    )


def test_a_file_without_prices_cannot_be_approved_automatically(
    client: TestClient, isolated_env: Path
) -> None:
    rows = [[r[0], r[1], r[2], r[4]] for r in _products(5)]
    r = _round(
        client,
        _customer(client, "NOPRICE"),
        _sheet(isolated_env / "noprice.xlsx", [["รหัสสินค้า", "ชื่อสินค้า", "หมวด", "สถานะ"], *rows]),
    )
    m = _mapping(client, r)
    assert r["status"] in ("review", "blocked") and m["policy"]["decision"] != "auto_approve"
    assert _field(m["document"], "/amount") is None  # nothing was invented
    coverage = m["policy"]["verification"]["coverage"]
    assert (
        coverage["collections"].get("/prices", 0) == 0 and coverage["fields"]["/sku"]["records_filled"] == 5
    )
    rows = _approve_and_transform(client, r, publish=False)
    assert len(rows) == 5 and all(not row.get("prices") for row in rows)


# --------------------------------------------------------------------------------------------
# 2. the second and third delivery: memory, renamed files, renamed and moved columns, VAT shifts
# --------------------------------------------------------------------------------------------
def _approved_first_round(c: TestClient, root: Path, code: str) -> tuple[str, dict[str, Any]]:
    cust = _customer(c, code)
    r1 = _round(c, cust, make_workbook(root / f"{code}_r1.xlsx"), FILE_NAME)
    _approve_and_transform(c, r1)
    return cust, r1


def test_a_renamed_file_with_the_same_layout_reuses_the_approved_mapping(
    client: TestClient, isolated_env: Path
) -> None:
    cust, r1 = _approved_first_round(client, isolated_env, "RENAME1")
    r2 = _round(client, cust, make_workbook(isolated_env / "export_v2.xlsx"), "export_v2.xlsx")
    m2 = _mapping(client, r2)
    assert r2["summary"]["drift"] == "identical" and m2["policy"]["source"] == "memory"
    assert m2["policy"]["reused_from"] == r1["summary"]["mapping_version_id"]
    assert all(s["file"]["name_pattern"] for s in m2["document"]["sources"])  # rebound to the new file name
    assert m2["policy"]["verification"]["complete"] and m2["policy"]["verification"]["passed"], m2["policy"][
        "verification"
    ]
    assert r2["summary"]["decision"] == "auto_approve", m2["policy"]["reasons"]
    rows = _approve_and_transform(client, r2)
    assert len(rows) == 12


def test_a_renamed_column_is_a_drift_that_a_person_reviews(client: TestClient, isolated_env: Path) -> None:
    cust, _ = _approved_first_round(client, isolated_env, "DRIFT1")
    r2 = _round(client, cust, make_workbook(isolated_env / "r2.xlsx", rename_qty=True), FILE_NAME)
    m2 = _mapping(client, r2)
    assert r2["summary"]["drift"] == "columns_renamed" and r2["status"] == "review"
    assert m2["policy"]["source"] == "heuristic" and any("drift" in x for x in m2["policy"]["reasons"])
    qty = _field(m2["document"], "/qty")
    assert qty is not None and "จำนวนคงเหลือ" in json.dumps(qty["expr"], ensure_ascii=False)
    review = m2["policy"]["field_review"]
    assert review and any(ch["pointer"].endswith("/qty") for ch in review["fields"])


def test_a_column_that_moves_to_another_sheet_is_not_reused_silently(
    client: TestClient, isolated_env: Path
) -> None:
    cust, _ = _approved_first_round(client, isolated_env, "MOVE1")
    wb = openpyxl.load_workbook(isolated_env / "MOVE1_r1.xlsx")
    ws = wb["สินค้า"]
    prices = wb.create_sheet("Prices")
    prices.append(["รหัสสินค้า", "ราคาขาย (รวม VAT)"])
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row[0] and row[0] != "รวม":
            prices.append([row[0], row[6]])
    ws.delete_cols(7)
    wb.save(isolated_env / "moved.xlsx")
    r2 = _round(client, cust, isolated_env / "moved.xlsx", FILE_NAME)
    m2 = _mapping(client, r2)
    assert (
        r2["summary"]["drift"] not in (None, "identical", "header_moved")
        and m2["policy"]["source"] != "memory"
    )
    assert r2["status"] in ("review", "blocked")


def test_prices_that_move_by_the_vat_rate_ask_a_yes_no_question(
    client: TestClient, isolated_env: Path
) -> None:
    cust, _ = _approved_first_round(client, isolated_env, "VAT1")
    wb = openpyxl.load_workbook(isolated_env / "VAT1_r1.xlsx")
    ws = wb["สินค้า"]
    for row in ws.iter_rows(min_row=4):
        cell = row[6]
        if (
            isinstance(cell.value, str)
            and cell.value.replace(",", "").replace(".", "").isdigit()
            and row[0].value != "รวม"
        ):
            cell.value = f"{float(cell.value.replace(',', '')) * 1.07:,.2f}"
    wb.save(isolated_env / "vat_r2.xlsx")
    r2 = _round(client, cust, isolated_env / "vat_r2.xlsx", FILE_NAME)
    m2 = _mapping(client, r2)
    checks = (m2["policy"].get("field_review") or {}).get("checks") or []
    assert any(ch["verdict"] in ("vat_added", "vat_removed") for ch in checks), checks
    qs = client.get(f"/api/mappings/{m2['id']}/questions").json()["questions"]
    vat = [q for q in qs if q["field"]["semantic"] == "price.vat_included"]
    assert vat and vat[0]["kind"] == "boolean" and r2["status"] == "review"


# --------------------------------------------------------------------------------------------
# 3. the engine refuses ambiguity; business checks live in the contract
# --------------------------------------------------------------------------------------------
def test_an_ambiguous_sheet_pattern_is_refused_not_guessed(client: TestClient, isolated_env: Path) -> None:
    wb = openpyxl.Workbook()
    first = wb.active
    assert first is not None
    first.title = "Stock BKK"
    second = wb.create_sheet("Stock CNX")
    for ws in (first, second):
        for row in [HEADER, *_products(3)]:
            ws.append(row)
    wb.save(isolated_env / "two_stocks.xlsx")
    r = _round(client, _customer(client, "AMBIG1"), isolated_env / "two_stocks.xlsx")
    doc = copy.deepcopy(_mapping(client, r)["document"])
    doc["sources"][0]["sheet"] = {"match": "Stock"}
    preview = client.post(
        f"/api/rounds/{r['id']}/mapping-preview", json={"document": doc, "limit": 10}
    ).json()
    text = json.dumps(preview, ensure_ascii=False)
    assert "matches 2 sheets" in text and "name one sheet" in text and preview["records"] == []
    doc["sources"][0]["sheet"] = {"match": "BKK"}
    narrowed = client.post(
        f"/api/rounds/{r['id']}/mapping-preview", json={"document": doc, "limit": 10}
    ).json()
    assert narrowed["ok"] is True and len(narrowed["records"]) == 3


def test_business_checks_in_a_custom_contract_gate_records(client: TestClient, isolated_env: Path) -> None:
    document = json.loads((FIXTURES / "schema.json").read_text(encoding="utf-8"))
    preview = client.post("/api/schemas/preview", json={"document": document})
    assert preview.status_code == 200, preview.text
    broken = copy.deepcopy(document)
    broken["x-forma"]["checks"].append(
        {"id": "declared-matches-invoice", "rule": "equals", "left": "/a", "right": "/b"}
    )
    refused = client.post("/api/schemas/preview", json={"document": broken})
    assert refused.status_code == 422 and "unique" in refused.text
    published = client.post("/api/schemas", json={"document": document})
    assert published.status_code == 201, published.text
    ref = (
        published.json()["ref"]
        if "ref" in published.json()
        else f"{published.json()['schema_id']}@{published.json()['version']}"
    )
    cust = client.post(
        "/api/customers", json={"code": "DISP1", "name": "DISP1", "default_target_schema": ref}
    )
    assert cust.status_code in (200, 201), cust.text
    bad = isolated_env / "dispatch_bad.csv"
    bad.write_text(
        "Dispatch Ref,Destination,Cartons,Declared THB,Invoice THB\n0001,BKK,2,1200.25,1000.00\n0002,CNX,4,3000,3000\n",
        encoding="utf-8",
    )
    r = client.post("/api/rounds", json={"customer_id": cust.json()["id"], "target_schema": ref}).json()
    _upload(client, r["id"], bad, bad.name)
    _run(client, r["id"])
    r = client.get(f"/api/rounds/{r['id']}").json()
    m = _mapping(client, r)
    assert m["target_schema"] == ref
    verification = m["policy"]["verification"]
    assert verification["passed"] is False, verification
    assert any("declared-matches-invoice" in p for p in verification["problems"]), (
        verification["problems"],
        m["document"]["fields"],
    )
    assert r["summary"]["decision"] != "auto_approve" and m["policy"]["checks"]["full_verification"] is False


# --------------------------------------------------------------------------------------------
# 4. the AI branches with a fake provider: typed clarifications, and an open VAT question
# --------------------------------------------------------------------------------------------
def _ai_round(
    c: TestClient, root: Path, code: str, answers: list[dict[str, Any]], build: Any = make_workbook
) -> dict[str, Any]:
    """A fresh customer whose first round is proposed with the fake model; ``answers`` follow the schema agent."""
    from forma.ai.llm import LLMClient
    from forma.services.context import get_context

    cust = _customer(c, code)
    r = c.post("/api/rounds", json={"customer_id": cust}).json()
    _upload(c, r["id"], build(root / f"{code}.xlsx"), FILE_NAME)
    pjob = c.post(f"/api/rounds/{r['id']}/profile", params={"sync": "true"}).json()["job_id"]
    assert c.get(f"/api/jobs/{pjob}").json()["status"] == "done"
    fake = _FakeProvider()
    fake.answers = [{"tables": [{"table_index": 0, "role": "products"}]}, *answers]
    get_context().llm = LLMClient(fake, model="fake-model")  # type: ignore[arg-type]
    job_id = c.post(f"/api/rounds/{r['id']}/propose", json={"sync": True, "use_llm": True}).json()["job_id"]
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job["error"]
    return c.get(f"/api/rounds/{r['id']}").json()  # type: ignore[no-any-return]


def _heuristic_doc(
    c: TestClient, root: Path, code: str, build: Any = make_workbook
) -> tuple[dict[str, Any], list[str]]:
    r = _round(c, _customer(c, f"{code}-draft"), build(root / f"{code}_draft.xlsx"), FILE_NAME)
    m = _mapping(c, r)
    return m["document"], list(m["open_questions"])


def _no_vat_marker(path: Path) -> Path:
    """A price column whose header says nothing about VAT: the matcher assumes and asks."""
    return _sheet(path, [HEADER, *_products(6)])


def test_a_typed_clarification_becomes_a_control_and_its_answer_a_constant(
    client: TestClient, isolated_env: Path
) -> None:
    doc, _ = _heuristic_doc(client, isolated_env, "CLAR1")
    for f in doc["fields"] + [f for coll in doc["collections"] for f in coll["fields"]]:
        f["confidence"] = 0.97
    clarification = {
        "target": "/currency",
        "question": "Which currency are these prices in?",
        "why": "The file states no currency and the customer trades in two",
        "kind": "enum",
        "options": ["THB", "USD"],
    }
    answer = {"mapping": doc, "confidence": 0.9, "clarifications": [clarification], "changes_from_draft": []}
    r = _ai_round(client, isolated_env, "CLAR1", [answer, answer, answer])
    m = _mapping(client, r)
    assert m["created_by"] == "ai" and clarification["question"] in m["open_questions"]
    assert m["policy"]["clarifications"] == [clarification] and r["status"] == "review"
    qs = client.get(f"/api/mappings/{m['id']}/questions").json()["questions"]
    q = next(x for x in qs if x["id"] == "/currency")
    assert q["kind"] == "text" and q["allow_custom"] and q["scope"] == "constant_for_this_file"
    assert (
        q["question"] == clarification["question"]
        and q["note"] == clarification["why"]
        and q["contested"] is True
    )
    verdict = client.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
    decision = next(d for d in verdict["must_decide"] if d["id"] == "/currency")
    assert (
        decision["kind"] == "text"
        and decision["options"] == ["THB", "USD"]
        and decision["scope"] == "constant_for_this_file"
    )
    extra = client.post(f"/api/mappings/{m['id']}/answers/preview", json={"answers": {"/currency": "EUR"}})
    assert (
        extra.status_code == 200 and extra.json()["preview"]["ok"]
    )  # The schema permits it; suggestions are not an enum.
    preview = client.post(f"/api/mappings/{m['id']}/answers/preview", json={"answers": {"/currency": "USD"}})
    assert preview.status_code == 200, preview.text
    assert clarification["question"] not in preview.json()["remaining_questions"]
    applied = client.post(
        f"/api/mappings/{m['id']}/answers/apply",
        json={"answers": {"/currency": "USD"}, "digest": preview.json()["digest"]},
    )
    assert applied.status_code in (200, 201), applied.text
    new = client.get(f"/api/mappings/{applied.json()['id']}").json()
    assert _field(new["document"], "/currency")["expr"] == {"op": "const", "value": "USD"}  # type: ignore[index]
    assert clarification["question"] not in new["open_questions"] and new["policy"]["clarifications"] == []
    assert new["policy"]["verification"]["complete"] is True
    assert "/currency" not in {
        x["id"] for x in client.get(f"/api/mappings/{new['id']}/questions").json()["questions"]
    }


def test_an_open_vat_assumption_always_has_a_control_to_answer_it(
    client: TestClient, isolated_env: Path
) -> None:
    """The heuristic asks "assumed VAT-inclusive — confirm". A model's confidence no longer retires it (ADR-060),
    so the card must still offer the yes/no control, however sure the model was; otherwise the question can
    never be answered and the round never leaves review."""
    doc, draft_questions = _heuristic_doc(client, isolated_env, "VATQ1", _no_vat_marker)
    assert any("VAT" in q and "confirm" in q for q in draft_questions), draft_questions
    for f in doc["fields"] + [f for coll in doc["collections"] for f in coll["fields"]]:
        f["confidence"] = 0.97
    vat = _field(doc, "/vat_included")
    assert vat is not None and vat["expr"]["op"] == "const"
    vat["evidence"] = ["the model is sure the prices include VAT"]
    answer = {"mapping": doc, "confidence": 0.95, "open_questions": [], "changes_from_draft": []}
    r = _ai_round(client, isolated_env, "VATQ1", [answer, answer, answer], _no_vat_marker)
    m = _mapping(client, r)
    open_vat = [q for q in m["open_questions"] if "VAT" in q and "confirm" in q]
    assert open_vat, m["open_questions"]  # the heuristic's assumption is still open
    qs = client.get(f"/api/mappings/{m['id']}/questions").json()["questions"]
    controls = [q for q in qs if q["field"]["semantic"] == "price.vat_included" and q["kind"] == "boolean"]
    assert controls, [q["id"] for q in qs]  # a person can answer it on the card
    preview = client.post(
        f"/api/mappings/{m['id']}/answers/preview", json={"answers": {controls[0]["id"]: True}}
    )
    assert preview.status_code == 200 and not [
        q for q in preview.json()["remaining_questions"] if "VAT" in q and "confirm" in q
    ]


def test_the_model_cannot_invent_a_clarification_for_an_unmapped_field(
    client: TestClient, isolated_env: Path
) -> None:
    doc, _ = _heuristic_doc(client, isolated_env, "CLAR2")
    bogus = {"target": "/nope", "question": "?", "why": "?", "kind": "text"}
    good = {"mapping": doc, "confidence": 0.9, "changes_from_draft": []}
    # the first answer is rejected by validation and sent back; the fake provider then answers without it
    r = _ai_round(client, isolated_env, "CLAR2", [{**good, "clarifications": [bogus]}, good, good, good])
    m = _mapping(client, r)
    assert m["created_by"] == "ai" and m["policy"]["clarifications"] == [] and "?" not in m["open_questions"]
    mapping_entry = next(e for e in m["agent_log"] if e["agent"] == "mapping")
    assert mapping_entry["attempts"] >= 2


def test_contract_checks_are_rerun_after_a_data_edit(client: TestClient, isolated_env: Path) -> None:
    """A business invariant lives in the contract: an edit that breaks it shows up on the new version."""
    document = json.loads((FIXTURES / "schema.json").read_text(encoding="utf-8"))
    ref = client.post("/api/schemas", json={"document": document}).json()["ref"]
    cust = client.post("/api/customers", json={"code": "DISP2", "name": "DISP2"}).json()["id"]
    good = isolated_env / "dispatch_good.csv"
    good.write_text(
        "Dispatch Ref,Destination,Cartons,Declared THB,Invoice THB\n0001,BKK,2,1200.25,1200.25\n0002,CNX,4,3000,3000\n",
        encoding="utf-8",
    )
    r = client.post("/api/rounds", json={"customer_id": cust, "target_schema": ref}).json()
    _upload(client, r["id"], good, good.name)
    _run(client, r["id"])
    r = client.get(f"/api/rounds/{r['id']}").json()
    assert _mapping(client, r)["policy"]["verification"]["passed"], _mapping(client, r)["policy"][
        "verification"
    ]
    _approve_and_transform(client, r, publish=False)
    ds = client.get(f"/api/rounds/{r['id']}").json()["datasets"][-1]["id"]
    assert not any(
        f["kind"] == "contract_check" for f in client.get(f"/api/datasets/{ds}").json()["findings"]
    )
    edit = client.post(
        f"/api/datasets/{ds}/changes",
        json={
            "operations": [
                {
                    "kind": "set_field",
                    "target": "/invoice_value",
                    "value": 1.0,
                    "where": {
                        "op": "eq",
                        "args": [{"op": "col", "name": "/dispatch_id"}, {"op": "const", "value": "0001"}],
                    },
                }
            ]
        },
    )
    assert edit.status_code in (200, 201), edit.text
    new_id = edit.json()["dataset"]["id"]
    findings = client.get(f"/api/datasets/{new_id}").json()["findings"]
    broken = [f for f in findings if f["kind"] == "contract_check"]
    assert broken and "declared-matches-invoice" in broken[0]["message"] and broken[0]["severity"] == "error"
