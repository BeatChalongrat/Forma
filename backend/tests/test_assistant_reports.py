from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import rpc
from tests.test_workspace_tools import call


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def seed_records(records):
    from forma.db.models import DatasetVersion, MappingVersion
    from forma.db.session import session_scope
    from forma.services.context import get_context

    ctx = get_context()
    schema = ctx.registry.register(
        {
            "type": "object",
            "x-forma": {"schema_id": "report_test", "version": 1, "identity_keys": ["/id"]},
            "properties": {
                "id": {"type": "string"},
                "department": {"type": ["string", "null"]},
                "amount": {"type": ["number", "null"], "x-forma": {"semantic": "price.amount"}},
                "currency": {"type": "string", "x-forma": {"semantic": "currency"}},
                "items": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"qty": {"type": "number"}}},
                },
            },
        },
        persist=False,
    )
    key = ctx.dataset_key("customer-report", "dsv_report")
    ctx.storage.put(key, ("\n".join(json.dumps(r) for r in records) + "\n").encode())
    with session_scope() as s:
        s.add(
            MappingVersion(
                id="map_report", customer_id="customer-report", target_schema=schema.ref, document={}
            )
        )
        s.add(
            DatasetVersion(
                id="dsv_report",
                round_id="rnd_report",
                customer_id="customer-report",
                mapping_version_id="map_report",
                records_key=key,
                row_count=len(records),
            )
        )


def args(**kw):
    return {
        "report": "dataset_grouped",
        "dataset_id": "dsv_report",
        "group_by": "/department",
        "field": "/amount",
        "operation": "sum",
        **kw,
    }


def failed(c, arguments, **kw):
    result = rpc(c, "tools/call", {"name": "build_report", "arguments": arguments}, **kw).json()["result"]
    assert result["isError"], result
    return str(result)


def test_decimal_null_negative_and_full_scan(client):
    records = [{"department": "A", "amount": n, "currency": "THB"} for n in (0.1, 0.2, None, True, "NaN")]
    records += [
        {"department": "B", "amount": -2, "currency": "THB"},
        {"department": "C", "amount": None, "currency": "THB"},
    ]
    records += [{"department": "D", "amount": "0.01", "currency": "THB"}] * 1001
    seed_records(records)
    result = call(client, "build_report", args())["report"]
    rows = {r["label"]: r for r in result["rows"]}
    assert rows["A"]["value"] == "0.3" and rows["A"]["numeric"] == 2
    assert rows["A"]["records"] == 5
    assert rows["B"]["value"] == "-2"
    assert rows["C"]["value"] is None
    assert rows["D"]["value"] == "10.01"
    assert result["query"] == args() and result["complete"]
    assert result["views"] == ["table", "bar"]
    assert "1008" in result["notes"][0]["en"] and "4 missing" in result["notes"][0]["en"]
    avg = call(client, "build_report", args(operation="average"))["report"]
    assert avg["rows"][0]["value"] == "0.15"
    failed(client, args(), headers={"X-Forma-Customers": ""})
    failed(client, args(field="/items/*/qty"))
    failed(client, args(group_by="/invented"))
    failed(client, args(start="2026-10-01"))


def test_currency_and_cardinality_are_not_silently_combined(client):
    from forma.auth import SYSTEM
    from forma.core.errors import ValidationFailed
    from forma.db.session import session_scope
    from forma.services.assistant_reports import build_report
    from forma.services.context import get_context

    seed_records(
        [{"department": str(i), "amount": 1, "currency": "USD" if i == 0 else "THB"} for i in range(31)]
    )
    with session_scope() as session:
        with pytest.raises(ValidationFailed, match="30 groups"):
            build_report(get_context(), session, SYSTEM, args())
        with pytest.raises(ValidationFailed, match="currencies"):
            build_report(get_context(), session, SYSTEM, args(group_by="/currency"))
    result = call(client, "build_report", args(filters=[{"field": "/currency", "value": "USD"}]))["report"]
    assert len(result["rows"]) == 1 and result["rows"][0]["value"] == "1"
    assert "USD" in result["scope"]["en"]


def test_upload_days_respect_timezone_boundaries_scope_and_zero_days(client):
    from forma.db.models import Customer, ImportRound, SourceFile
    from forma.db.session import session_scope

    with session_scope() as s:
        s.add(Customer(id="cus_report", code="REPORT", name="Report"))
        s.flush()
        s.add(ImportRound(id="rnd_report", customer_id="cus_report", status="published"))
        s.flush()
        for i, when in enumerate(
            ["2026-10-29T16:59:59", "2026-10-29T17:00:00", "2026-10-30T16:59:59", "2026-10-30T17:00:00"]
        ):
            s.add(
                SourceFile(
                    round_id="rnd_report",
                    customer_id="cus_report",
                    sha256=str(i),
                    original_name=f"{i}.csv",
                    size=1,
                    kind="csv",
                    storage_key="unused",
                    created_at=dt.datetime.fromisoformat(when).replace(tzinfo=dt.UTC),
                )
            )
        s.add(
            SourceFile(
                round_id="rnd_report",
                customer_id="cus_report",
                sha256="dup",
                original_name="dup.csv",
                size=1,
                kind="csv",
                storage_key="unused",
                duplicate_of="other",
                created_at=dt.datetime(2026, 10, 30, tzinfo=dt.UTC),
            )
        )
    query = {
        "report": "uploads_by_day",
        "start": "2026-10-30",
        "end": "2026-11-01",
        "timezone": "Asia/Bangkok",
    }
    result = call(client, "build_report", query)["report"]
    assert [r["received"] for r in result["rows"]] == [2, 1, 0]
    assert [r["ready"] for r in result["rows"]] == [2, 1, 0]
    assert "line" in result["views"] and "not completion date" in result["notes"][0]["en"]
    scoped = call(client, "build_report", query, headers={"X-Forma-Customers": ""})["report"]
    assert all(r["received"] == 0 for r in scoped["rows"])
    failed(client, {**query, "timezone": "unknown"})
    failed(client, {**query, "start": "2020-01-01"})


def test_work_round_receipts_unknown_expected_and_partial_file_list(client):
    from forma.db.models import Batch, BatchItem
    from forma.db.session import session_scope

    with session_scope() as s:
        s.add(Batch(id="bch_report", label="October", created_by="test", expected_files=None))
        s.flush()
        for i in range(51):
            s.add(
                BatchItem(
                    batch_id="bch_report",
                    file_name=f"{i}.csv",
                    sha256=str(i),
                    size=1,
                    kind="csv",
                    storage_key="unused",
                    status="needs_customer",
                )
            )
    query = {"report": "work_rounds", "batch_ids": ["bch_report"]}
    result = call(client, "build_report", query)["report"]
    assert result["rows"][0]["received"] == 51
    assert result["rows"][0]["review"] == 51 and result["rows"][0]["ready"] == 0
    assert result["rows"][0]["expected"] is None
    files = call(client, "build_report", {**query, "report": "work_round_files"})["report"]
    assert len(files["rows"]) == 50 and not files["complete"] and "50 of 51" in files["scope"]["en"]
    failed(client, {"report": "work_rounds"})
    failed(client, query, headers={"X-Forma-Customers": ""})


def test_server_report_persists_and_model_cannot_supply_values(client):
    from forma.ai.llm import LLMClient, Usage
    from forma.services.context import get_context

    query = {
        "report": "uploads_by_day",
        "start": "2026-10-29",
        "end": "2026-10-30",
        "timezone": "Asia/Bangkok",
    }

    class Provider:
        name = "test"
        calls = 0

        def complete_json(self, **kw):
            self.calls += 1
            payload = json.loads(kw["user"])
            evidence = payload.get("evidence", [])
            report = next((e for e in evidence if e["tool"] == "build_report"), None)
            if not report:
                reply = {
                    "answer": "",
                    "citations": [],
                    "calls": [{"name": "build_report", "arguments": query}],
                }
            else:
                reply = {
                    "answer": "No files in this period.",
                    "citations": [report["id"]],
                    "calls": [],
                    "visualizations": [
                        {"evidence_id": report["id"], "view": "line"},
                        {"evidence_id": report["id"], "view": "table"},
                    ],
                }
            return json.dumps(reply), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test", max_retries=0)
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/assistant/conversations/{cid}/messages",
        json={"question": "Chart uploads October 29-30 Bangkok", "request_id": "report-001"},
    )
    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert reply["ai"] and len(reply["visualizations"]) == 1
    chart = reply["visualizations"][0]
    assert chart["view"] == "line" and [r["received"] for r in chart["rows"]] == [0, 0]
    saved = client.get(f"/api/assistant/conversations/{cid}").json()["turns"][0]["reply"]
    assert saved["visualizations"] == reply["visualizations"]
    history = call(client, "conversation_history", {"conversation_id": cid})
    assert history["messages"][0]["reports"][0]["query"] == query


@pytest.mark.parametrize(
    "visual",
    [
        {"evidence_id": 1, "view": "bar"},
        {"evidence_id": 999, "view": "table"},
        {"evidence_id": 1, "view": "bar", "rows": [{"value": 999}]},
    ],
)
def test_fabricated_visualization_is_rejected(client, visual):
    from forma.ai.llm import LLMClient, Usage
    from forma.services.context import get_context

    class Provider:
        name = "test"

        def complete_json(self, **kw):
            return json.dumps(
                {"answer": "Fake chart", "citations": [1], "calls": [], "visualizations": [visual]}
            ), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test", max_retries=0)
    response = client.post("/api/assistant/ask", json={"question": "chart"})
    assert response.status_code == 200
    assert not response.json().get("visualizations")
    assert not response.json()["ai"]
