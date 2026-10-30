from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_review_context_is_exact_scoped_and_persistent(client, isolated_env, monkeypatch):
    from forma.services import workspace_assistant

    r, mid, pointer = uncertain_mapping(client, isolated_env)
    created = client.post("/api/assistant/conversations", json={"mapping_id": mid})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["round_id"] == r["id"]
    assert client.get(f"/api/assistant/conversations/{cid}").json()["conversation"]["mapping_id"] == mid
    assert (
        client.post(
            "/api/assistant/conversations", json={"mapping_id": mid}, headers={"X-Forma-Customers": ""}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/assistant/conversations", json={"mapping_id": mid, "round_id": "other"}).status_code
        == 422
    )
    result = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "inspect_review", "arguments": {"mapping_id": mid, "field": pointer}},
        },
    ).json()["result"]
    assert not result.get("isError"), result
    data = json.loads(result["content"][0]["text"])
    assert data["mapping_id"] == mid
    assert data["context"]["mapping_version"] >= 2
    assert data["source_evidence"][0]["constant"]
    assert data["source_evidence"][0]["context_only"]
    denied = client.post(
        "/api/mcp",
        headers={"X-Forma-Customers": ""},
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "inspect_review", "arguments": {"mapping_id": mid}},
        },
    ).json()["result"]
    assert denied["isError"]
    seen = []

    def reply(*args, **kwargs):
        seen.append(kwargs)
        return {"answer": "Explanation", "ai": True, "evidence": []}

    monkeypatch.setattr(workspace_assistant, "ask", reply)
    client.post(
        f"/api/assistant/conversations/{cid}/messages",
        json={"question": "Explain this file", "request_id": "explain-file-001"},
    )
    assert seen[0]["mapping_id"] == mid


def test_structured_question_stream_persists_and_followup_has_choices(client):
    from forma.ai.llm import LLMClient, Usage
    from forma.services.context import get_context

    class Provider:
        name = "test"
        calls = 0

        def complete_json(self, **kw):
            self.calls += 1
            payload = json.loads(kw["user"])
            assert "do not guess" in kw["system"]
            if self.calls == 1:
                reply = {
                    "answer": "ยังไม่ทราบว่าหมายถึงไฟล์ใด",
                    "citations": [1],
                    "calls": [],
                    "actions": [],
                    "clarifications": [
                        {
                            "question": "หมายถึงไฟล์ใด?",
                            "reason": "ต้องระบุไฟล์ก่อนตรวจราคา",
                            "options": ["ไฟล์ที่อัปโหลดวันนี้", "ไฟล์รอบก่อน"],
                        }
                    ],
                }
            else:
                assert "หมายถึงไฟล์ใด" in str(payload["history"])
                assert "ไฟล์รอบก่อน" in str(payload["history"])
                reply = {
                    "answer": "ยังไม่ยืนยันราคา หากไม่ทราบชื่อไฟล์ให้เปิดประวัติไฟล์เพื่อตรวจต่อ",
                    "citations": [1],
                    "calls": [],
                    "actions": [],
                }
            return json.dumps(reply, ensure_ascii=False), Usage(model="test")

    provider = Provider()
    get_context().llm = LLMClient(provider, "test", max_retries=0)
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    wire = client.post(
        f"/api/assistant/conversations/{cid}/messages/stream",
        json={"question": "ราคานี้ถูกมั้ย", "request_id": "clarify-001"},
    )
    assert wire.status_code == 200, wire.text
    detail = client.get(f"/api/assistant/conversations/{cid}").json()
    turn = detail["turns"][0]
    assert turn["status"] == "complete", turn
    assert len(turn["reply"]["clarifications"]) == 1
    assert "delta" in wire.text and "clarifications" in wire.text
    second = client.post(
        f"/api/assistant/conversations/{cid}/messages",
        json={"question": "ยังไม่ทราบ ขอเว้นเรื่องนี้ไว้ก่อน", "request_id": "clarify-002"},
    ).json()
    assert second["status"] == "complete", second
    assert not second["reply"]["clarifications"]
    assert provider.calls == 2


def test_reason_explanations_preserve_numbers_and_do_not_assert_error(isolated_env):
    from forma.services.context import get_context
    from forma.services.review_explanations import explain

    rows = explain(
        get_context(),
        [
            "data check: /custom: only 0% of 69 SKUs keep their value — the column may mean something else",
            "structure is identical to customer C901's approved format — confirm this file belongs here",
            "unknown new diagnostic",
        ],
        None,
    )
    assert "69" in rows[0]["th"] and "0%" in rows[0]["th"]
    assert "C901" in rows[1]["th"] and "ยังบอกไม่ได้" in rows[1]["th"]
    assert "unknown new diagnostic" not in rows[2]["th"]


def test_dated_tool_evidence_can_be_saved(client, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    from forma.ai.llm import LLMClient, Usage
    from forma.services import workspace_assistant
    from forma.services.context import get_context

    original = workspace_assistant.run_tool

    def dated(ctx, session, p, name, arguments):
        result = original(ctx, session, p, name, arguments)
        result["date"] = datetime(2026, 10, 30, tzinfo=UTC)
        result["amount"] = Decimal("12.50")
        return result

    monkeypatch.setattr(workspace_assistant, "run_tool", dated)

    class Provider:
        name = "test"

        def complete_json(self, **kw):
            return json.dumps(
                {
                    "answer": "Need a detail",
                    "citations": [1],
                    "calls": [],
                    "actions": [],
                    "clarifications": [
                        {"question": "Which file?", "reason": "Need the file to check", "options": []}
                    ],
                }
            ), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test")
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/assistant/conversations/{cid}/messages/stream",
        json={"question": "Check it", "request_id": "dated-001"},
    )
    assert '"type": "done"' in response.text
    turn = client.get(f"/api/assistant/conversations/{cid}").json()["turns"][0]
    assert turn["status"] == "complete"
    assert turn["reply"]["evidence"][0]["data"]["date"].startswith("2026-10-30")
