"""CSV templates share the JSON registry, mapping, review and export pipeline; no paid API."""

from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.core.errors import ValidationFailed
from forma.export.exporter import export_bytes
from forma.schema_registry import SchemaRegistry
from forma.schema_registry.csv_template import (
    CsvColumnIn,
    CsvContractIn,
    CsvTemplateIn,
    analyze_csv,
    build_csv_contract,
)


def contract(content="SKU,Product Name,Price,Note\n00123,น้ำดื่ม,12.50,\n00124,ขนม,15.00,ok"):
    return CsvContractIn(
        content=content,
        delimiter=",",
        schema_id="csv_catalog",
        title="CSV catalogue",
        columns=[
            CsvColumnIn(
                name="SKU",
                data_type="string",
                required=True,
                identity=True,
                semantic="sku",
                aliases=["รหัสสินค้า"],
                confirmed=True,
            ),
            CsvColumnIn(
                name="Product Name",
                data_type="string",
                required=True,
                semantic="product_name",
                aliases=["ชื่อสินค้า"],
                confirmed=True,
            ),
            CsvColumnIn(
                name="Price",
                data_type="number",
                required=True,
                semantic="price.amount",
                aliases=["ราคาขาย"],
                confirmed=True,
            ),
            CsvColumnIn(
                name="Note", data_type="string", required=False, description="Optional notes", confirmed=True
            ),
        ],
    )


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_read_quotes_bom_multiline_thai_and_delimiters(delimiter):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter)
    w.writerows([["รหัส", "คำอธิบาย"], ["00123", 'น้ำ,ดื่ม; "ขวด"\nใหม่'], ["00124", "ขนม"]])
    result = analyze_csv(CsvTemplateIn(content="\ufeff" + buf.getvalue(), delimiter=delimiter))
    assert result["row_count"] == 2
    assert result["columns"][0]["suggested_type"] == "string"
    assert result["columns"][1]["examples"][0] == 'น้ำ,ดื่ม; "ขวด"\nใหม่'
    assert (
        analyze_csv(CsvTemplateIn(content=f"sku{delimiter}price\nA{delimiter}1\nB{delimiter}2"))["delimiter"]
        == delimiter
    )


@pytest.mark.parametrize(
    "content",
    [
        "",
        " ,x\n1,2",
        "x,X\n1,2",
        'a,b\n1,"unclosed',
        "a,b\n1,2,3",
        "a,b\n1",
        "a,\x00b",
        "a,\ufffdb",
        ",".join(str(i) for i in range(101)),
        "a\n" + "1\n" * 2001,
    ],
)
def test_reject_invalid_templates(content):
    with pytest.raises((ValidationFailed, ValueError)):
        analyze_csv(CsvTemplateIn(content=content, delimiter=","))


def test_header_only_and_conservative_suggestions():
    a = analyze_csv(
        CsvTemplateIn(
            content='Code,Date,Amount,Flag\n00123,02/03/2026,"1.234,56",1\n00124,22/09/2026,2.345,0',
            delimiter=",",
        )
    )
    assert [c["suggested_type"] for c in a["columns"]] == ["string", "string", "string", "integer"]
    assert analyze_csv(CsvTemplateIn(content="SKU"))["row_count"] == 0
    assert (
        analyze_csv(CsvTemplateIn(content="a,b\n2026-02-30,12345678901234567", delimiter=","))["columns"][0][
            "suggested_type"
        ]
        == "string"
    )
    assert (
        analyze_csv(CsvTemplateIn(content="a,b\n2026-02-28,true", delimiter=","))["columns"][0][
            "suggested_type"
        ]
        == "date"
    )


@pytest.mark.parametrize(
    "change",
    [
        "unconfirmed",
        "identity",
        "meaning",
        "number",
        "required",
        "headers",
        "duplicate",
        "date",
        "boolean",
        "collection",
    ],
)
def test_reject_unconfirmed_or_contradictory_decisions(change):
    body = contract()
    if change == "unconfirmed":
        body.columns[0].confirmed = False
    if change == "identity":
        body.columns[0].identity = False
    if change == "meaning":
        body.columns[3].description = ""
    if change == "number":
        body.columns[0].data_type = "integer"
    if change == "required":
        body.columns[3].required = True
    if change == "headers":
        body.columns.reverse()
    if change == "duplicate":
        body.content = body.content.replace("00124", "00123")
    if change == "date":
        body.columns[3].data_type = "date"
    if change == "boolean":
        body.columns[3].data_type = "boolean"
    if change == "collection":
        body.columns[3].semantic = "inventory"
    with pytest.raises(ValidationFailed):
        build_csv_contract(body)


def test_registry_validation_export_order_and_json_parity(tmp_path):
    registry = SchemaRegistry(tmp_path)
    doc = build_csv_contract(contract())
    target = registry.prepare(doc)
    good = {"Note": None, "Price": 12.5, "SKU": "00123", "Product Name": "น้ำ,ดื่ม"}
    assert not target.validate_records([good])
    for bad in [
        {**good, "Price": "12.5"},
        {**good, "SKU": ""},
        {**good, "SKU": None},
        {**good, "SKU": "   "},
        {**good, "extra": 1},
    ]:
        assert target.validate_records([bad])
    output = list(csv.reader(io.StringIO(export_bytes([good], "csv", target).decode("utf-8-sig"))))
    assert output == [["SKU", "Product Name", "Price", "Note"], ["00123", "น้ำ,ดื่ม", "12.5", ""]]
    assert json.loads(export_bytes([good], "json", target)) == [good]
    assert export_bytes([], "csv", target).decode("utf-8-sig") == "SKU,Product Name,Price,Note\r\n"
    # Registry version allocation / reload keeps the confirmation valid.
    registry.register(doc, persist=False)
    second = registry.prepare(doc)
    assert second.version == 2
    assert export_bytes([good], "csv", second)
    altered = copy.deepcopy(doc)
    altered["properties"]["Price"]["type"] = "string"
    with pytest.raises(ValidationFailed, match="ยืนยัน"):
        registry.prepare(altered)


def test_literal_headers_special_keys_and_composite_identity(tmp_path):
    headers = ["SKU/id", "คลัง.ชื่อ", "source_meta", "csv_column_1", "Note,quoted"]
    buf = io.StringIO()
    csv.writer(buf, delimiter=";").writerows(
        [headers, ["001", "A", "text", "x", "first"], ["001", "B", "text", "y", "second"]]
    )
    body = CsvContractIn(
        content=buf.getvalue(),
        delimiter=";",
        schema_id="literal",
        title="Literal",
        columns=[
            CsvColumnIn(
                name=h,
                data_type="string",
                required=True,
                identity=i < 2,
                description="Literal test field",
                confirmed=True,
            )
            for i, h in enumerate(headers)
        ],
    )
    schema = SchemaRegistry(tmp_path).prepare(build_csv_contract(body))
    fields = list(schema.document["properties"])
    assert len(set(fields)) == 5 and "source_meta" not in fields
    record = dict(zip(fields, ["001", "A", "text", "x", "first"], strict=True))
    record["source_meta"] = {"lineage": "internal"}
    result = list(
        csv.reader(io.StringIO(export_bytes([record], "csv", schema).decode("utf-8-sig")), delimiter=";")
    )
    assert result == [headers, ["001", "A", "text", "x", "first"]]


def test_date_boolean_integer_and_header_only_contracts(tmp_path):
    body = CsvContractIn(
        content="ID,Date,Enabled\n1,2026-02-28,true\n2,2026-03-01,false",
        delimiter=",",
        schema_id="dated",
        title="Dated records",
        columns=[
            CsvColumnIn(
                name="ID",
                data_type="integer",
                identity=True,
                required=True,
                description="Identifier",
                confirmed=True,
            ),
            CsvColumnIn(
                name="Date",
                data_type="date",
                required=True,
                description="Effective date in ISO format",
                confirmed=True,
            ),
            CsvColumnIn(
                name="Enabled", data_type="boolean", required=True, description="Enabled flag", confirmed=True
            ),
        ],
    )
    registry = SchemaRegistry(tmp_path)
    schema = registry.prepare(build_csv_contract(body))
    record = {"ID": 1, "Date": "2026-02-28", "Enabled": True}
    assert not schema.validate_records([record])
    assert schema.validate_records([{**record, "Date": "2026-02-30"}])
    assert "1,2026-02-28,true" in export_bytes([record], "csv", schema).decode("utf-8-sig")
    body.content = "ID,Date,Enabled"
    assert registry.prepare(build_csv_contract(body)).identity_keys == ["/ID"]


def test_csv_fixed_text_is_an_explicit_checked_contract_not_sample_inference(tmp_path):
    body = contract(content="SKU,Product Name,Price,Note\n00123,Item,12.5,CHF")
    body.columns[-1].fixed_value = "CHF"
    doc = build_csv_contract(body)
    assert doc["properties"]["Note"]["const"] == "CHF"
    assert doc["properties"]["Note"]["default"] == "CHF"
    schema = SchemaRegistry(tmp_path).prepare(doc)
    assert schema.validate_records([{"SKU": "00123", "Product Name": "Item", "Price": 12.5, "Note": "CAD"}])
    tampered = copy.deepcopy(doc)
    tampered["properties"]["Note"]["const"] = "CAD"
    with pytest.raises(ValidationFailed):
        SchemaRegistry(tmp_path).prepare(tampered)
    body.columns[-1].fixed_value = "CAD"
    with pytest.raises(ValidationFailed, match="ตัวอย่างขัด"):
        build_csv_contract(body)
    body.columns[-1].fixed_value = " "
    with pytest.raises(ValidationFailed):
        build_csv_contract(body)
    body.columns[-1].fixed_value = None
    assert "const" not in build_csv_contract(body)["properties"]["Note"]
    body.columns[2].fixed_value = "12.5"
    with pytest.raises(ValidationFailed):
        build_csv_contract(body)  # Fixed values currently supported for text fields only.


@pytest.mark.parametrize(
    "change", ["duplicate", "missing", "unknown", "nested", "delimiter", "variant", "unconfirmed"]
)
def test_registry_rejects_invalid_csv_output_configuration(tmp_path, change):
    doc = build_csv_contract(contract())
    doc["x-forma"].pop("csv_template")  # Also validate hand-authored JSON output variants.
    variant = doc["x-forma"]["output_variants"]["csv"]
    if change == "duplicate":
        variant["columns"][1]["header"] = "SKU"
    if change == "missing":
        variant["columns"].pop()
    if change == "unknown":
        variant["columns"][0]["field"] = "does_not_exist"
    if change == "nested":
        doc["properties"]["Note"] = {"type": "array", "items": {"type": "string"}}
    if change == "delimiter":
        variant["delimiter"] = "++"
    if change == "variant":
        doc["x-forma"]["output_variants"] = []
    if change == "unconfirmed":
        doc["x-forma"]["csv_template"] = {"confirmed": False}
    with pytest.raises(ValidationFailed):
        SchemaRegistry(tmp_path).prepare(doc)


def test_csv_limits_measure_utf8_bytes_and_preserve_all_rows():
    with pytest.raises(ValidationFailed, match="1 MB"):
        analyze_csv(CsvTemplateIn(content="name\n" + "ก" * 350000, delimiter=","))
    result = analyze_csv(CsvTemplateIn(content="code\n" + "123\n" * 1999 + "00123\n", delimiter=","))
    assert result["row_count"] == 2000
    assert result["columns"][0]["suggested_type"] == "string"  # leading zero at the end is not missed


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize("mode", ["csv", "json"])
def test_confirmed_currency_upload_review_and_both_downloads(client, isolated_env, mode):
    body = contract(content="SKU,Product Name,Price,Note")
    body.columns[-1].semantic = "currency"
    body.columns[-1].required = True
    body.columns[-1].fixed_value = "CHF"
    prepared = client.post("/api/schemas/csv/prepare", json=body.model_dump())
    assert prepared.status_code == 200, prepared.text
    document = prepared.json()["document"]
    if mode == "json":
        document["x-forma"].pop("csv_template")
    registered = client.post("/api/schemas", json={"document": document})
    assert registered.status_code == 201, registered.text
    customer = client.post("/api/customers", json={"code": "CONTRACT", "name": "Contract"}).json()
    rnd = client.post(
        "/api/rounds", json={"customer_id": customer["id"], "target_schema": registered.json()["ref"]}
    ).json()
    source = isolated_env / "source.csv"
    source.write_text("SKU,Product Name,Price\n00123,Item A,12.5\n00124,Item B,0\n", encoding="utf-8")
    _upload(client, rnd["id"], source, source.name)
    _run(client, rnd["id"])
    current = client.get(f"/api/rounds/{rnd['id']}").json()
    mid = current["mappings"][0]["id"]
    questions = client.get(f"/api/mappings/{mid}/questions").json()
    assert not questions["questions"], questions
    mapping = client.get(f"/api/mappings/{mid}").json()
    preview = client.post(
        f"/api/rounds/{rnd['id']}/mapping-preview", json={"document": mapping["document"]}
    ).json()
    assert [r["Note"] for r in preview["records"]] == ["CHF", "CHF"]
    approval = client.post(f"/api/mappings/{mid}/approve", json={})
    assert approval.status_code == 200, approval.text
    job = client.post(f"/api/rounds/{rnd['id']}/transform", params={"sync": "true"}).json()["job_id"]
    assert client.get(f"/api/jobs/{job}").json()["status"] == "done"
    did = client.get(f"/api/rounds/{rnd['id']}").json()["datasets"][-1]["id"]
    response = client.post(f"/api/datasets/{did}/approve", json={"acknowledge_anomalies": True})
    assert response.status_code == 200, response.text
    for fmt in ("csv", "json"):
        exported = client.post(f"/api/datasets/{did}/export", params={"fmt": fmt})
        assert exported.status_code == 201, exported.text
        downloaded = client.get(f"/api/exports/{exported.json()['id']}/download")
        assert downloaded.status_code == 200
        rows = (
            list(csv.DictReader(io.StringIO(downloaded.content.decode("utf-8-sig"))))
            if fmt == "csv"
            else downloaded.json()
        )
        assert [r["SKU"] for r in rows] == ["00123", "00124"]
        assert [r["Note"] for r in rows] == ["CHF", "CHF"]


def test_api_confirmation_permissions_and_no_preview_writes(client):
    before = client.get("/api/schemas").json()
    body = contract().model_dump()
    for route, request in [("analyze", {"content": body["content"]}), ("prepare", body)]:
        assert (
            client.post(
                f"/api/schemas/csv/{route}", json=request, headers={"X-Forma-User": "reviewer"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/schemas/csv/{route}",
                json=request,
                headers={"X-Forma-User": "approver", "X-Forma-Customers": "C1"},
            ).status_code
            == 403
        )
    body["columns"][0]["confirmed"] = False
    assert client.post("/api/schemas/csv/prepare", json=body).status_code == 422
    body["columns"][0]["confirmed"] = True
    result = client.post("/api/schemas/csv/prepare", json=body)
    assert result.status_code == 200, result.text
    document = result.json()["document"]
    assert client.post("/api/schemas/preview", json={"document": document}).status_code == 200
    assert client.get("/api/schemas").json() == before
    assert client.post("/api/schemas", json={"document": document}).status_code == 201
    document["properties"]["Price"]["type"] = "string"
    assert client.post("/api/schemas", json={"document": document}).status_code == 422


def test_csv_confirmation_survives_browser_number_serialization(client):
    result = client.post("/api/schemas/csv/prepare", json=contract().model_dump())
    assert result.status_code == 200, result.text
    document = result.json()["document"]
    assert document["properties"]["Price"]["examples"] == [12.5, 15.0]
    # Real browser JSON.stringify drops .0, unlike Python's json.dumps used by TestClient.
    document["properties"]["Price"]["examples"][1] = 15
    preview = client.post("/api/schemas/preview", json={"document": document})
    assert preview.status_code == 200, preview.text
    registered = client.post("/api/schemas", json={"document": document})
    assert registered.status_code == 201, registered.text
    document["properties"]["Price"]["examples"][1] = 16
    assert client.post("/api/schemas/preview", json={"document": document}).status_code == 422
    document["properties"]["Price"]["examples"][1] = True
    assert client.post("/api/schemas/preview", json={"document": document}).status_code == 422


def test_csv_target_full_mock_pipeline_and_both_downloads(client, isolated_env: Path):
    from forma.services.context import get_context

    c = client
    assert get_context().llm.is_mock
    body = contract()
    body.columns = body.columns[:3]
    body.content = "SKU,Product Name,Price\n00123,น้ำดื่ม,12.50"
    prepared = c.post("/api/schemas/csv/prepare", json=body.model_dump())
    assert prepared.status_code == 200, prepared.text
    doc = prepared.json()["document"]
    registered = c.post("/api/schemas", json={"document": doc})
    assert registered.status_code == 201, registered.text
    customer = c.post("/api/customers", json={"code": "CSV1", "name": "CSV test"}).json()
    assert (
        c.patch(f"/api/customers/{customer['id']}", json={"default_target_schema": "csv_catalog"}).status_code
        == 200
    )
    rid = c.post("/api/rounds", json={"customer_id": customer["id"]}).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / "sample.xlsx"))
    job = _run(c, rid)
    mid = job["result"]["mapping_version_id"]
    mapping = c.get(f"/api/mappings/{mid}").json()
    assert mapping["validation"]["ok"], json.dumps(mapping["validation"], ensure_ascii=False)
    assert c.post(f"/api/mappings/{mid}/approve", json={}).status_code == 200
    started = c.post(f"/api/rounds/{rid}/transform", params={"sync": "true"}).json()
    transformed = c.get(f"/api/jobs/{started['job_id']}").json()
    assert transformed["status"] == "done", transformed
    did = transformed["result"]["dataset_version_id"]
    assert c.post(f"/api/datasets/{did}/approve", json={}).status_code == 200
    results = {}
    for fmt in ("csv", "json"):
        exported = c.post(f"/api/datasets/{did}/export", params={"fmt": fmt})
        assert exported.status_code in (200, 201), exported.text
        downloaded = c.get(f"/api/exports/{exported.json()['id']}/download")
        assert downloaded.status_code == 200
        results[fmt] = downloaded.content
    csv_rows = list(csv.DictReader(io.StringIO(results["csv"].decode("utf-8-sig"))))
    json_rows = json.loads(results["json"])
    assert len(csv_rows) == len(json_rows) == 12
    assert list(csv_rows[0]) == ["SKU", "Product Name", "Price"]
    assert {r["SKU"] for r in csv_rows} == {f"A{i:03}" for i in range(1, 13)}
    for row, js in zip(csv_rows, json_rows, strict=True):
        assert row["SKU"] == js["SKU"] and row["Product Name"] == js["Product Name"]
        assert float(row["Price"]) == js["Price"] == int(row["SKU"][1:]) * 107
