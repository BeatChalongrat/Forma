from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def replies(monkeypatch):
    from forma.services import workspace_assistant

    calls = []

    def answer(*args, **kwargs):
        calls.append((args, kwargs))
        return {"answer": "Saved answer: " + args[3], "ai": True, "evidence": [], "actions": []}

    monkeypatch.setattr(workspace_assistant, "ask", answer)
    return calls


def new_chat(c: TestClient) -> str:
    response = c.post("/api/assistant/conversations", json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send(c: TestClient, cid: str, question: str = "Remember project Mango", rid: str = "request-0001"):
    return c.post(
        f"/api/assistant/conversations/{cid}/messages", json={"question": question, "request_id": rid}
    )


def test_persist_reopen_followup_and_idempotency(client, replies):
    cid = new_chat(client)
    result = send(client, cid)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "complete"
    assert send(client, cid).json()["id"] == result.json()["id"]
    assert len(replies) == 1
    assert send(client, cid, "Different content").status_code == 409
    assert send(client, cid, "What project?", "request-0002").status_code == 200
    assert "Mango" in replies[-1][0][6][0]["content"]
    assert replies[-1][1]["conversation_id"] == cid
    from forma.api.app import create_app

    with TestClient(create_app()) as reopened:
        history = reopened.get(f"/api/assistant/conversations/{cid}").json()
        assert len(history["turns"]) == 2
        assert history["conversation"]["title"] == "Remember project Mango"
        assert (
            reopened.get("/api/assistant/conversations", params={"search": "Saved answer"}).json()["total"]
            == 1
        )
    assert client.get("/api/assistant/conversations", params={"search": "%"}).json()["total"] == 0
    assert send(client, cid, "   ", "request-0003").status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Forma-User": "someone-else"},
        {"X-Forma-Customers": ""},
        {"X-Forma-Roles": "importer"},
    ],
)
def test_owner_and_scope_isolation(client, replies, headers):
    cid = new_chat(client)
    send(client, cid)
    assert client.get("/api/assistant/conversations", headers=headers).json()["total"] == 0
    assert client.get(f"/api/assistant/conversations/{cid}", headers=headers).status_code == 404
    assert (
        client.patch(f"/api/assistant/conversations/{cid}", json={"title": "x"}, headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/assistant/conversations/{cid}/messages",
            json={"question": "x", "request_id": "request-1234"},
            headers=headers,
        ).status_code
        == 404
    )


def test_archive_rename_restore(client, replies):
    cid = new_chat(client)
    assert (
        client.patch(
            f"/api/assistant/conversations/{cid}", json={"title": "Mango history", "archived": True}
        ).status_code
        == 200
    )
    assert client.get("/api/assistant/conversations").json()["total"] == 0
    assert (
        client.get("/api/assistant/conversations?archived=true").json()["items"][0]["title"]
        == "Mango history"
    )
    assert send(client, cid).status_code == 409
    client.patch(f"/api/assistant/conversations/{cid}", json={"archived": False})
    assert send(client, cid).status_code == 200
    assert client.patch(f"/api/assistant/conversations/{cid}", json={"title": "   "}).status_code == 422


def test_long_history_pagination_and_tool_scope(client, replies):
    from forma.auth import Principal
    from forma.core.errors import NotFound
    from forma.db.models import AssistantTurn
    from forma.db.session import session_scope
    from forma.services.context import get_context
    from forma.services.workspace_assistant import run_tool

    cid = new_chat(client)
    with session_scope() as s:
        for i in range(35):
            s.add(
                AssistantTurn(
                    conversation_id=cid,
                    request_id=f"request-{i:04}",
                    sequence=i + 1,
                    question=f"Topic {i}",
                    answer=f"Remember {i}",
                    status="complete",
                    reply={"answer": f"Remember {i}", "ai": True, "evidence": []},
                )
            )
    page = client.get(f"/api/assistant/conversations/{cid}").json()
    assert len(page["turns"]) == 30 and page["older_before"] == 6
    assert len(client.get(f"/api/assistant/conversations/{cid}?before=6").json()["turns"]) == 5
    assert send(client, cid, "Remember topic zero", "request-0035").status_code == 200
    assert len(replies[-1][0][6]) == 17
    assert "Topic 0" in replies[-1][0][6][0]["content"]
    me = client.get("/api/me").json()
    owner = Principal(subject=me["subject"], roles=frozenset(me["roles"]), mode=me["mode"])
    with session_scope() as s:
        history = run_tool(
            get_context(),
            s,
            owner,
            "conversation_history",
            {"conversation_id": cid, "search": "Topic 0"},
        )
        assert history["messages"][0]["answer"] == "Remember 0"
        assert history["messages"][0]["historical_not_live"]
        with pytest.raises(NotFound):
            run_tool(
                get_context(),
                s,
                Principal(subject="another", roles=owner.roles),
                "conversation_history",
                {"conversation_id": cid},
            )


def test_failed_retry_and_expired_lease(client, replies, monkeypatch):
    from forma.db.models import AssistantConversation, AssistantTurn, utcnow
    from forma.db.session import session_scope
    from forma.services import workspace_assistant

    cid = new_chat(client)
    good = workspace_assistant.ask

    def fail(*args, **kwargs):
        raise RuntimeError("secret-provider-error")

    monkeypatch.setattr(workspace_assistant, "ask", fail)
    response = send(client, cid)
    assert response.json()["status"] == "failed"
    assert "secret-provider-error" not in response.text
    monkeypatch.setattr(workspace_assistant, "ask", good)
    assert send(client, cid).json()["status"] == "complete"
    assert client.get(f"/api/assistant/conversations/{cid}").json()["total"] == 1
    with session_scope() as s:
        chat = s.get(AssistantConversation, cid)
        chat.busy_until = utcnow() + timedelta(minutes=1)
    assert send(client, cid, "next", "request-0002").status_code == 409
    with session_scope() as s:
        chat = s.get(AssistantConversation, cid)
        chat.busy_until = utcnow() - timedelta(minutes=1)
        s.add(
            AssistantTurn(
                conversation_id=cid, request_id="request-0002", sequence=2, question="next", status="pending"
            )
        )
    assert client.get(f"/api/assistant/conversations/{cid}").json()["turns"][-1]["status"] == "failed"
    assert send(client, cid, "next", "request-0002").json()["status"] == "complete"
    assert client.get(f"/api/assistant/conversations/{cid}").json()["total"] == 2


def test_legacy_import_idempotent_and_safe(client):
    body = {"messages": [{"question": "Old question", "answer": "Old answer"}]}
    a = client.post("/api/assistant/conversations/import", json=body)
    b = client.post("/api/assistant/conversations/import", json=body)
    assert a.status_code == 201, a.text
    assert a.json()["id"] == b.json()["id"]
    turn = client.get("/api/assistant/conversations/" + a.json()["id"]).json()["turns"][0]
    assert turn["reply"]["reason"] == "imported_history"
    assert turn["reply"]["evidence"] == [] and "actions" not in turn["reply"]
    assert (
        client.post("/api/assistant/conversations/import", json={**body, "round_id": "missing"}).status_code
        == 404
    )


def test_pending_is_durable_and_blocks_concurrent_send(client, replies, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from forma.services import workspace_assistant

    cid = new_chat(client)
    entered, release = Event(), Event()
    good = workspace_assistant.ask

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return good(*args, **kwargs)

    monkeypatch.setattr(workspace_assistant, "ask", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(send, client, cid)
        try:
            assert entered.wait(10)
            pending = client.get(f"/api/assistant/conversations/{cid}").json()
            assert pending["conversation"]["busy"]
            assert pending["turns"][0]["status"] == "pending"
            assert send(client, cid, "Another question", "parallel-request").status_code == 409
        finally:
            release.set()
        assert response.result(timeout=15).status_code == 200
    assert send(client, cid).status_code == 200
    assert len(replies) == 1


def test_model_retrieves_older_context_via_tool(client):
    import json

    from forma.ai.llm import LLMClient, Usage
    from forma.db.models import AssistantTurn
    from forma.db.session import session_scope
    from forma.services.context import get_context

    cid = new_chat(client)
    with session_scope() as session:
        for i in range(12):
            question = "Project codename is Orchard" if i == 0 else f"Later discussion {i}"
            session.add(
                AssistantTurn(
                    conversation_id=cid,
                    request_id=f"earlier-{i}",
                    sequence=i + 1,
                    question=question,
                    answer="Acknowledged",
                    status="complete",
                    reply={"ai": True, "answer": "Acknowledged", "evidence": []},
                )
            )

    class Provider:
        name = "test"

        def complete_json(self, **kwargs):
            payload = json.loads(kwargs["user"])
            assert payload["current_conversation_id"] == cid
            assert "Orchard" in json.dumps(payload["history"])
            evidence = next((e for e in payload["evidence"] if e["tool"] == "conversation_history"), None)
            if evidence:
                assert "Orchard" in evidence["data"]["messages"][0]["question"]
                result = {
                    "answer": "Your project codename was Orchard.",
                    "citations": [evidence["id"]],
                    "calls": [],
                    "actions": [],
                }
            else:
                result = {
                    "answer": "",
                    "citations": [],
                    "calls": [
                        {
                            "name": "conversation_history",
                            "arguments": {"conversation_id": cid, "search": "codename"},
                        }
                    ],
                }
            return json.dumps(result), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test", max_retries=0)
    result = send(client, cid, "What was my original project codename?", "followup-001").json()
    assert result["status"] == "complete", result
    assert "Orchard" in result["reply"]["answer"]
    assert "conversation_history" in result["reply"]["tools_used"]


def test_pins_persist_sort_and_remain_personal(client):
    first, second = new_chat(client), new_chat(client)
    assert not client.get(f"/api/assistant/conversations/{first}").json()["conversation"]["pinned"]
    response = client.patch(f"/api/assistant/conversations/{first}", json={"pinned": True})
    assert response.status_code == 200 and response.json()["pinned"]
    rows = client.get("/api/assistant/conversations").json()["items"]
    assert [r["id"] for r in rows] == [first, second]
    assert (
        client.patch(
            f"/api/assistant/conversations/{first}",
            json={"pinned": False},
            headers={"X-Forma-User": "another"},
        ).status_code
        == 404
    )
    assert client.get(f"/api/assistant/conversations/{first}").json()["conversation"]["pinned"]
    client.patch(f"/api/assistant/conversations/{first}", json={"archived": True})
    assert client.get("/api/assistant/conversations").json()["items"][0]["id"] == second
    assert client.get("/api/assistant/conversations?archived=true").json()["items"][0]["pinned"]
    client.patch(f"/api/assistant/conversations/{first}", json={"archived": False, "pinned": False})
    assert client.get("/api/assistant/conversations").json()["items"][0]["id"] == second


def test_delete_removes_only_owned_chat_and_messages(client, replies):
    from sqlalchemy import select

    from forma.db.models import AssistantTurn, AuditLog
    from forma.db.session import session_scope

    deleted, retained = new_chat(client), new_chat(client)
    send(client, deleted)
    send(client, retained)
    assert (
        client.delete(
            f"/api/assistant/conversations/{deleted}", headers={"X-Forma-User": "another"}
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/assistant/conversations/{deleted}", headers={"X-Forma-Customers": ""}
        ).status_code
        == 404
    )
    assert client.get(f"/api/assistant/conversations/{deleted}").json()["total"] == 1
    assert client.delete(f"/api/assistant/conversations/{deleted}").status_code == 204
    assert client.get(f"/api/assistant/conversations/{deleted}").status_code == 404
    assert client.delete(f"/api/assistant/conversations/{deleted}").status_code == 404
    assert client.get(f"/api/assistant/conversations/{retained}").json()["total"] == 1
    assert client.get("/api/assistant/conversations").json()["total"] == 1
    with session_scope() as s:
        assert not s.scalars(select(AssistantTurn).where(AssistantTurn.conversation_id == deleted)).all()
        entry = s.scalar(
            select(AuditLog).where(AuditLog.action == "assistant.delete", AuditLog.entity_id == deleted)
        )
        assert entry is not None and entry.actor == client.get("/api/me").json()["name"]


def test_delete_is_blocked_while_answering(client, replies, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from forma.services import workspace_assistant

    cid = new_chat(client)
    entered, release = Event(), Event()
    good = workspace_assistant.ask

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return good(*args, **kwargs)

    monkeypatch.setattr(workspace_assistant, "ask", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(send, client, cid)
        try:
            assert entered.wait(10)
            assert client.delete(f"/api/assistant/conversations/{cid}").status_code == 409
        finally:
            release.set()
        assert response.result(timeout=15).status_code == 200
    assert client.delete(f"/api/assistant/conversations/{cid}").status_code == 204
