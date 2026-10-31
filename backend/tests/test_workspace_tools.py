from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import delivery, rpc


@pytest.fixture
def client(isolated_env: Path):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def call(c: TestClient, name: str, args: dict[str, Any], **kw: Any) -> dict[str, Any]:
    response = rpc(c, "tools/call", {"name": name, "arguments": args}, **kw)
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert not result["isError"], result
    return json.loads(result["content"][0]["text"])  # type: ignore[no-any-return]


def test_work_round_dashboard_tool_targets_and_scope(client: TestClient) -> None:
    response = client.post(
        "/api/batches?sync=true",
        data={"label": "Target round", "expected_files": "300"},
        files=[("files", ("one.csv", b"a,b\n1,2\n"))],
    )
    assert response.status_code == 201
    bid = response.json()["id"]
    result = call(client, "work_rounds", {"batch_id": bid})
    assert result["round"]["expected_files"] == 300 and result["round"]["missing_files"] == 299
    assert result["round"]["receipt_status"] == "missing_files" and "complete" not in result["round"]
    assert result["round"]["total"] == 1 and len(result["files"]) == 1
    assert result["links"][0]["url"] == "/uploads/" + bid
    assert call(client, "work_rounds", {"search": "Target"})["items"][0]["id"] == bid
    assert call(client, "work_rounds", {"batch_id": bid, "offset": 1})["files"] == []
    denied = rpc(
        client, "tools/call", {"name": "work_rounds", "arguments": {}}, headers={"X-Forma-Customers": "cus_x"}
    )
    assert denied.json()["result"]["isError"]
    client.patch("/api/batches/" + bid, json={"expected_files": None})
    unknown = call(client, "work_rounds", {"batch_id": bid})["round"]
    assert unknown["receipt_status"] == "unknown" and unknown["missing_files"] is None


def test_evidence_access_pagination_and_sources(client: TestClient, isolated_env: Path) -> None:
    r = delivery(client, isolated_env)
    mid, fid, cid = r["mappings"][0]["id"], r["files"][0]["id"], r["customer_id"]
    customers = call(client, "customers", {"search": "W001"})
    assert customers["total"] == 1
    assert call(client, "customers", {"offset": 1})["items"] == []
    assert call(client, "customers", {"search": "%"})["total"] == 0
    mapping = call(client, "inspect_mapping", {"mapping_id": mid})
    assert mapping["total_rules"] >= len(mapping["rules"]) > 0
    assert mapping["links"][0]["url"] == f"/rounds/{r['id']}/mapping/{mid}"
    assert call(client, "customer_rules", {"customer_id": cid, "kind": "mappings"})["items"][0]["id"] == mid
    assert call(client, "customer_rules", {"customer_id": cid, "kind": "overrides"})["total"] == 0
    source = call(client, "read_source", {"file_id": fid})
    assert source["rows"] and source["sheets"] and source["complete_search"]
    found = call(client, "read_source", {"file_id": fid, "search": "A001"})
    assert found["matched_rows"] == 1
    assert found["rows"][0]["row_number"] > 1
    assert call(client, "read_source", {"file_id": fid, "column_offset": 1000})["rows"][0]["cells"] == []
    assert call(client, "review_queue", {})["total"] >= 1
    assert call(client, "processing_jobs", {"round_id": r["id"]})["total"] >= 1
    assert call(client, "bulk_uploads", {})["total"] == 0
    for kind in ("versions", "changes", "exports"):
        assert "total" in call(client, "dataset_history", {"round_id": r["id"], "kind": kind})
    queries = [
        ("inspect_mapping", {"mapping_id": mid}),
        ("read_source", {"file_id": fid}),
        ("customer_rules", {"customer_id": cid, "kind": "mappings"}),
        ("dataset_history", {"round_id": r["id"], "kind": "versions"}),
        ("processing_jobs", {"round_id": r["id"]}),
        ("bulk_uploads", {}),
        ("audit_history", {}),
    ]
    for name, args in queries:
        result = rpc(
            client, "tools/call", {"name": name, "arguments": args}, headers={"X-Forma-Customers": ""}
        ).json()["result"]
        assert result["isError"], name
        assert "October" not in json.dumps(result)
    assert call(client, "review_queue", {}, headers={"X-Forma-Customers": ""})["total"] == 0
    assert call(client, "customers", {}, headers={"X-Forma-Customers": ""})["total"] == 0


def test_exact_aggregate_beyond_old_scan_limit(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import DatasetVersion, MappingVersion
    from forma.db.session import session_scope
    from forma.services.context import get_context

    r = delivery(client, isolated_env)
    ctx = get_context()
    with session_scope() as s:
        mapping = s.get(MappingVersion, r["mappings"][0]["id"])
        assert mapping
        schema = ctx.registry.get_ref(mapping.target_schema)
        pointer = sorted(schema.semantic_pointers("price.amount"))[0]
        parts = pointer.lstrip("/").split("/")

        def record(value: Any) -> dict[str, Any]:
            node: Any = value
            for part in reversed(parts):
                node = [node] if part == "*" else {part: node}
            return node  # type: ignore[no-any-return]

        lines = [json.dumps(record("0.10"))] * 100001
        lines += [json.dumps(record(None)), json.dumps(record(True)), json.dumps(record("NaN"))]
        key = ctx.dataset_key(r["customer_id"], "dsv_exact")
        ctx.storage.put(key, ("\n".join(lines) + "\n").encode())
        s.add(
            DatasetVersion(
                id="dsv_exact",
                round_id=r["id"],
                customer_id=r["customer_id"],
                mapping_version_id=mapping.id,
                records_key=key,
                row_count=len(lines),
            )
        )
    args = {"dataset_id": "dsv_exact", "field": pointer, "operation": "sum"}
    result = call(client, "query_records", args)
    assert result["value"] == "10000.10"
    assert result["numeric_values"] == 100001
    assert result["missing_or_non_numeric_values"] == 3
    assert result["scanned_records"] == 100004 and result["complete_scan"]
    assert len(result["records"]) == 20 and result["next_offset"] == 20
    assert call(client, "query_records", {"dataset_id": "dsv_exact", "offset": 100000})["value"] == 100004
    assert call(client, "query_records", {**args, "search": "not-present"})["value"] is None
    for operation in ("average", "min", "max"):
        assert call(client, "query_records", {**args, "operation": operation})["value"] == "0.10"
    invalid = rpc(
        client, "tools/call", {"name": "query_records", "arguments": {**args, "field": "/invented"}}
    )
    assert invalid.json()["result"]["isError"]
    denied = rpc(
        client, "tools/call", {"name": "query_records", "arguments": args}, headers={"X-Forma-Customers": ""}
    )
    assert denied.json()["result"]["isError"]
    assert (
        client.post(
            "/api/assistant/ask",
            json={"question": "help", "dataset_id": "dsv_exact"},
            headers={"X-Forma-Customers": ""},
        ).status_code
        == 403
    )


def test_status_and_configuration_exclude_secrets(client: TestClient) -> None:
    status = client.get("/api/assistant/status").json()
    assert not status["enabled"]
    assert "query_records" in status["tools"]
    assert set(status) == {"enabled", "provider", "model", "tools", "vision", "code_sandbox", "file_types"}
    assert status["vision"] is False and status["code_sandbox"] is False
    config = call(client, "runtime_configuration", {})
    assert "approval_policy" in config
    importer = call(client, "runtime_configuration", {}, headers={"X-Forma-User": "importer"})
    assert "approval_policy" not in importer
    assert "api_key" not in json.dumps(importer)
    audit = call(client, "audit_history", {})
    assert all("details" not in row for row in audit["items"])
    assert (
        client.post("/api/assistant/ask", json={"question": "hi", "timezone": "Invalid/Zone"}).status_code
        == 422
    )


def test_avatar_is_personal_and_rejects_active_content(client: TestClient) -> None:
    avatar = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0test\xff\xd9").decode()
    assert client.put("/api/me/preferences", json={"avatar": avatar}).status_code == 200
    assert client.get("/api/me/preferences").json()["avatar"] == avatar
    assert client.get("/api/me/preferences", headers={"X-Forma-User": "importer"}).json()["avatar"] == ""
    for invalid in (
        "https://tracking.example/avatar",
        "data:image/svg+xml,<svg/>",
        "data:image/jpeg;base64,bad",
    ):
        assert client.put("/api/me/preferences", json={"avatar": invalid}).status_code == 422
    assert client.put("/api/me/preferences", json={"avatar": ""}).status_code == 200
