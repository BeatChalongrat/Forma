"""Failure injection is local only: no live provider, no dev data."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from forma.db.models import AssistantAttachment, AssistantRun, AssistantTurn, Job, utcnow
from forma.db.session import session_scope
from forma.jobs import runner
from forma.services import assistant_jobs, attachments, workspace_assistant
from forma.services.assistant_runtime import Journal, RecoveryPaused, step
from forma.services.context import get_context


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def stage(client, monkeypatch):
    monkeypatch.setattr(runner, "deliver_jobs", lambda ids: None)
    monkeypatch.setattr(runner, "dispatch_pending", lambda *args: [])
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    # The default development principal is read from the conversation owner via the real dependency.
    from starlette.requests import Request

    from forma.auth.deps import authenticated_principal

    p = authenticated_principal(Request({"type": "http", "headers": []}))
    body = {"question": "Create the report", "request_id": "recovery-request-01"}
    with session_scope() as s:
        result = assistant_jobs.accept(get_context(), s, p, cid, body)
        run = s.get(AssistantRun, result["id"])
        jid, tid = run.job_id, run.turn_id
    return cid, tid, jid, p, body


def test_terminal_reply_replay_and_unknown_progress_never_start_work(client, monkeypatch):
    from forma.core.errors import Conflict, NotFound
    from forma.services import conversations

    cid, tid, _jid, p, body = stage(client, monkeypatch)
    with session_scope() as s:
        s.get(AssistantTurn, tid).status = "complete"
    with session_scope() as s:
        stopped = conversations.stop(s, p, cid, body["request_id"])
        assert stopped["status"] == "complete"
        replay = conversations.send(
            get_context(), s, p, cid, body["question"], body["request_id"], "en", "UTC"
        )
        assert replay["id"] == tid and replay["status"] == "complete"
    with session_scope() as s, pytest.raises(Conflict, match="another question"):
        conversations.send(get_context(), s, p, cid, "Different question", body["request_id"], "en", "UTC")
    with session_scope() as s, pytest.raises(NotFound):
        conversations.message_progress(s, p, cid, "missing-request")
    with session_scope() as s:
        from forma.db.models import AssistantConversation

        s.get(AssistantConversation, cid).archived = True
    with session_scope() as s, pytest.raises(Conflict, match="Restore"):
        conversations.send(get_context(), s, p, cid, "Another", "new-request", "en", "UTC")


def test_legacy_pending_message_without_job_can_be_stopped(client, monkeypatch):
    from forma.services import conversations

    cid, tid, _jid, p, body = stage(client, monkeypatch)
    with session_scope() as s:
        s.delete(s.get(AssistantRun, tid))
    with session_scope() as s:
        assert conversations.stop(s, p, cid, body["request_id"])["status"] == "stopped"
    with session_scope() as s:
        assert conversations.stop(s, p, cid, body["request_id"])["status"] == "stopped"


def test_worker_crash_replays_receipt_not_output(client, monkeypatch):
    cid, tid, jid, p, body = stage(client, monkeypatch)
    calls, crashed = [], []

    class WorkerKilled(BaseException):
        pass

    def ask(ctx, s, principal, *args, **kw):
        def create_file():
            calls.append("file")
            return attachments.add_output(ctx, s, principal, cid, tid, "report.csv", b"item,value\nA,12")

        output = step("create_output", {"title": "report"}, create_file)
        if not crashed:
            crashed.append(True)
            raise WorkerKilled()
        kw["stream"].delta("Report ready")
        return {"answer": "Report ready", "ai": True, "evidence": [], "actions": [], "output": output}

    monkeypatch.setattr(workspace_assistant, "ask", ask)
    with pytest.raises(WorkerKilled):
        runner.run_job(jid)
    with session_scope() as s:
        assert s.get(Job, jid).status == "running"
        assert s.scalar(select(func.count()).select_from(AssistantAttachment)) == 1
        s.get(Job, jid).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert jid in runner.recover_stale()
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(runner.run_job, [jid, jid]))
    assert claims.count(True) == 1
    assert calls == ["file"]
    with session_scope() as s:
        assert s.get(AssistantTurn, tid).status == "complete"
        assert s.get(Job, jid).status == "done"
        assert s.scalar(select(func.count()).select_from(AssistantAttachment)) == 1
        assert assistant_jobs.accept(get_context(), s, p, cid, body)["status"] == "complete"
    assert not any(j["id"] == jid for j in client.get("/api/activity/jobs").json()["items"])


@pytest.mark.parametrize("status", ["complete", "pending", "failed"])
def test_next_question_uses_committed_turn_not_worker_cleanup_status(client, monkeypatch, status):
    from forma.core.errors import Conflict
    from forma.db.models import AssistantConversation

    cid, tid, jid, p, _ = stage(client, monkeypatch)
    with session_scope() as s:
        chat = s.get(AssistantConversation, cid)
        chat.active_run, chat.busy_until = None, None
        s.get(AssistantTurn, tid).status = status
        s.get(Job, jid).status = "running"
    body = {"question": "Next question", "request_id": "next-after-commit"}
    if status != "complete":
        with session_scope() as s, pytest.raises(Conflict):
            assistant_jobs.accept(get_context(), s, p, cid, body)
        return
    with session_scope() as s:
        next_turn = assistant_jobs.accept(get_context(), s, p, cid, body)
        assert next_turn["status"] == "pending"
        assert next_turn["id"] != tid
    # Duplicate delivery of the completed job cannot repeat tools or clear the new lease.
    with session_scope() as s:
        s.get(Job, jid).status = "queued"
    assert runner.run_job(jid)
    with session_scope() as s:
        assert s.get(AssistantTurn, tid).status == "complete"
        assert s.get(AssistantConversation, cid).active_run != jid
        assert s.get(AssistantTurn, next_turn["id"]).status == "pending"


def test_provider_retry_reuses_tool_and_partial_answer(client, monkeypatch):
    _cid, tid, jid, _, _ = stage(client, monkeypatch)
    calls = []
    monkeypatch.setattr(assistant_jobs, "interruptible_sleep", lambda seconds: None)

    def ask(ctx, s, principal, *args, **kw):
        value = step("read_data", {}, lambda: calls.append("read") or {"total": 12})

        def write():
            calls.append("write")
            kw["stream"].delta("Twelve")
            if calls.count("write") == 1:
                raise ConnectionError("mock provider disconnected")
            return "Twelve items"

        answer = step("model.write", {}, write)
        return {"answer": answer, "ai": True, "evidence": [{"total": value["total"]}], "actions": []}

    monkeypatch.setattr(workspace_assistant, "ask", ask)
    assert runner.run_job(jid)
    with session_scope() as s:
        turn = s.get(AssistantTurn, tid)
        assert turn.status == "complete"
        assert turn.answer == "Twelve items"
    assert calls == ["read", "write", "write"]


def test_durable_budget_cannot_reset_on_restart(client, monkeypatch):
    _, tid, _, _, _ = stage(client, monkeypatch)
    settings = get_context().settings.model_copy(update={"assistant_run_max_calls": 2})
    with session_scope() as s:
        Journal(s, tid, settings).reserve_request()
    with session_scope() as s:
        Journal(s, tid, settings).reserve_request()
    with session_scope() as s:
        with pytest.raises(RecoveryPaused):
            Journal(s, tid, settings).reserve_request()
        assert s.get(AssistantRun, tid).calls == 2


def test_validation_error_receipt_and_changed_workflow_fail_closed(client, monkeypatch):
    _, tid, _, _, _ = stage(client, monkeypatch)
    from forma.core.errors import ValidationFailed

    calls = []

    def invalid():
        calls.append(1)
        raise ValidationFailed("Unsupported column")

    with session_scope() as s, pytest.raises(ValidationFailed):
        Journal(s, tid, get_context().settings).invoke("tool", {"x": 1}, invalid)
    with session_scope() as s:
        with pytest.raises(ValidationFailed):
            Journal(s, tid, get_context().settings).invoke("tool", {"x": 1}, invalid)
        with pytest.raises(RecoveryPaused):
            Journal(s, tid, get_context().settings).invoke("tool", {"x": 2}, invalid)
    assert calls == [1]


def test_drafts_compare_and_swap_across_tabs_and_owner(client):
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    url = f"/api/assistant/conversations/{cid}/state"
    body = {"revision": 0, "draft": {"input": "unsent text", "clarifications": {"turn": {"step": 1}}}}
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: client.put(url, json=body).status_code, range(2)))
    assert sorted(results) == [200, 409]
    assert client.get(url).json()["draft"] == body["draft"]
    assert client.get(url, headers={"X-Forma-User": "other"}).status_code == 404
    assert client.put(url, json={**body, "revision": 1}).status_code == 200


def test_branch_excludes_future_and_feedback_persists(client, monkeypatch):
    monkeypatch.setattr(
        workspace_assistant,
        "ask",
        lambda *a, **kw: {"answer": "Answer", "ai": True, "evidence": [], "actions": []},
    )
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    path = f"/api/assistant/conversations/{cid}"
    ids = []
    for i in range(3):
        r = client.post(
            path + "/messages", json={"question": f"Question {i}", "request_id": f"request-{i:08}"}
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    body = {
        "turn_id": ids[1],
        "mode": "edit",
        "question": "Corrected question",
        "request_id": "branch-request-01",
    }
    branch = client.post(path + "/branch", json=body)
    assert branch.status_code == 200, branch.text
    bid = branch.json()["id"]
    assert client.post(path + "/branch", json=body).json()["id"] == bid
    copied = client.get(f"/api/assistant/conversations/{bid}").json()
    assert [t["question"] for t in copied["turns"]] == ["Question 0"]
    assert (
        client.get(f"/api/assistant/conversations/{bid}/state").json()["draft"]["input"]
        == "Corrected question"
    )
    assert len(client.get(path).json()["turns"]) == 3
    url = path + f"/turns/{ids[1]}/feedback"
    assert client.put(url, json={"rating": "incorrect", "comment": "Wrong number"}).status_code == 200
    from forma.db.models import AssistantFeedback

    with session_scope() as s:
        assert s.get(AssistantFeedback, ids[1]).comment == "Wrong number"
    assert client.put(url, json={"rating": "helpful"}, headers={"X-Forma-User": "other"}).status_code == 404


def test_stop_before_delivery_prevents_any_model_or_tool(client, monkeypatch):
    cid, tid, jid, _, body = stage(client, monkeypatch)
    calls = []
    monkeypatch.setattr(workspace_assistant, "ask", lambda *a, **k: calls.append(1))
    result = client.post(f"/api/assistant/conversations/{cid}/stop", json={"request_id": body["request_id"]})
    assert result.status_code == 200, result.text
    assert not runner.run_job(jid)
    with session_scope() as s:
        assert s.get(AssistantTurn, tid).status == "stopped"
        assert s.get(Job, jid).status == "cancelled"
    assert not calls


def test_journal_encoding_preserves_values_and_untrusted_marker_keys():
    from decimal import Decimal

    from forma.ai.llm import StructuredResult, Usage
    from forma.services.assistant_runtime import decode, encode

    values = {
        "price": Decimal("12.30"),
        "at": utcnow(),
        "data": {"$type": "not-a-class", "$bytes": "ordinary data"},
        "result": StructuredResult(
            data={"ok": True}, raw_text='{"ok": true}', usage=Usage(model="mock"), attempts=1
        ),
    }
    assert decode(encode(values)) == values


def test_regenerate_keeps_artifact_and_does_not_repeat_tools(client, monkeypatch):
    from forma.db.models import AssistantArtifact, AssistantArtifactVersion

    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    monkeypatch.setattr(
        workspace_assistant,
        "ask",
        lambda *a, **kw: {"answer": "Saved answer", "ai": True, "evidence": [], "actions": []},
    )
    path = f"/api/assistant/conversations/{cid}"
    turn = client.post(
        path + "/messages", json={"question": "Original", "request_id": "original-request"}
    ).json()
    with session_scope() as s:
        a = AssistantArtifact(
            conversation_id=cid, kind="flow", title="Saved", name="Saved", latest=1, created_by="test"
        )
        s.add(a)
        s.flush()
        s.add(
            AssistantArtifactVersion(
                artifact_id=a.id,
                number=1,
                turn_id=turn["id"],
                content={"nodes": [{"id": "a", "label": "Upload"}], "edges": [], "direction": "down"},
                created_by="test",
            )
        )
    bid = client.post(
        path + "/branch",
        json={
            "turn_id": turn["id"],
            "mode": "regenerate",
            "question": "Original",
            "request_id": "regenerate-request",
        },
    ).json()["id"]

    def forbidden(*a, **kw):
        pytest.fail("Regenerating must not repeat tools or business actions")

    monkeypatch.setattr(workspace_assistant, "ask", forbidden)
    child = client.post(
        f"/api/assistant/conversations/{bid}/messages",
        json={"question": "Original", "request_id": "child-request"},
    ).json()
    assert child["status"] == "complete", child
    assert child["reply"]["answer"] == "Saved answer"
    assert len(child["reply"]["artifacts"]) == 1
    assert client.delete(path).status_code in (200, 204)
    art = client.get("/api/assistant/artifacts/" + child["reply"]["artifacts"][0]["id"])
    assert art.status_code == 200
    assert art.json()["versions"][0]["content"]["nodes"][0]["label"] == "Upload"


def test_clarification_submission_links_source_and_survives_draft_loss(client, monkeypatch):
    reply = {
        "answer": "Which period?",
        "ai": True,
        "evidence": [],
        "actions": [],
        "clarifications": [
            {"question": "Which period?", "reason": "Need period", "options": ["November", "October"]}
        ],
    }
    monkeypatch.setattr(workspace_assistant, "ask", lambda *a, **kw: reply)
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    path = f"/api/assistant/conversations/{cid}"
    turn = client.post(path + "/messages", json={"question": "Report", "request_id": "first-request"}).json()
    r = client.post(
        path + "/messages",
        json={"question": "November", "request_id": "answer-request", "clarification_turn_id": turn["id"]},
    )
    assert r.status_code == 200, r.text
    assert client.get(path + "/state").json()["draft"]["clarifications"][turn["id"]]["submitted"]
    other = client.post("/api/assistant/conversations", json={}).json()["id"]
    r = client.post(
        f"/api/assistant/conversations/{other}/messages",
        json={"question": "November", "request_id": "cross-request", "clarification_turn_id": turn["id"]},
    )
    assert r.status_code == 422
    later = client.post(path + "/messages", json={"question": "Next", "request_id": "later-request"}).json()
    branch = client.post(
        path + "/branch",
        json={
            "turn_id": later["id"],
            "mode": "edit",
            "question": "Changed",
            "request_id": "branch-clarify-01",
        },
    ).json()["id"]
    copied = client.get(f"/api/assistant/conversations/{branch}").json()["turns"]
    state = client.get(f"/api/assistant/conversations/{branch}/state").json()
    assert state["draft"]["clarifications"][copied[0]["id"]]["submitted"]
    branch_path = f"/api/assistant/conversations/{branch}"
    continued = client.post(
        branch_path + "/messages", json={"question": "Changed", "request_id": "continue-branch"}
    ).json()
    grandchild = client.post(
        branch_path + "/branch",
        json={"turn_id": continued["id"], "mode": "edit", "question": "Again", "request_id": "branch-again"},
    ).json()["id"]
    copied_again = client.get(f"/api/assistant/conversations/{grandchild}").json()["turns"]
    state = client.get(f"/api/assistant/conversations/{grandchild}/state").json()
    assert state["draft"]["clarifications"][copied_again[0]["id"]]["submitted"]
    # Going back before the response must reopen the question, even in a copied branch.
    earlier = client.post(
        branch_path + "/branch",
        json={
            "turn_id": copied[1]["id"],
            "mode": "edit",
            "question": "October",
            "request_id": "branch-earlier",
        },
    ).json()["id"]
    assert not client.get(f"/api/assistant/conversations/{earlier}/state").json()["draft"]["clarifications"]


def test_expired_incognito_removes_drafts_feedback_and_replay_payload(client, monkeypatch):
    from starlette.requests import Request

    from forma.auth.deps import authenticated_principal
    from forma.db.models import AssistantChatState, AssistantConversation, AssistantFeedback
    from forma.services import conversations

    monkeypatch.setattr(
        workspace_assistant,
        "ask",
        lambda *a, **kw: {"answer": "private", "ai": True, "evidence": [], "actions": []},
    )
    cid = client.post("/api/assistant/conversations", json={"incognito": True}).json()["id"]
    path = f"/api/assistant/conversations/{cid}"
    turn = client.post(
        path + "/messages", json={"question": "Private", "request_id": "private-request"}
    ).json()
    client.put(path + "/state", json={"revision": 0, "draft": {"input": "private draft"}})
    client.put(path + f"/turns/{turn['id']}/feedback", json={"rating": "helpful"})
    with session_scope() as s:
        s.get(AssistantConversation, cid).updated_at = utcnow() - timedelta(days=2)
    with session_scope() as s:
        conversations.sweep_incognito(s, authenticated_principal(Request({"type": "http", "headers": []})))
    with session_scope() as s:
        assert s.get(AssistantConversation, cid) is None
        assert s.get(AssistantChatState, cid) is None
        assert s.get(AssistantRun, turn["id"]) is None
        assert s.get(AssistantFeedback, turn["id"]) is None
        assert (
            s.scalar(select(func.count()).select_from(Job).where(Job.payload["chat_id"].as_string() == cid))
            == 0
        )


def test_real_process_exit_and_restart_retains_single_output(client, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    _, tid, jid, _, _ = stage(client, monkeypatch)
    helper = str(Path(__file__).with_name("assistant_recovery_worker.py"))
    first = subprocess.run([sys.executable, helper, jid, "crash"], capture_output=True, timeout=40)
    assert first.returncode == 86, first.stderr.decode(errors="replace")
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(AssistantAttachment)) == 1
        s.get(Job, jid).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert jid in runner.recover_stale()
    second = subprocess.run([sys.executable, helper, jid, "resume"], capture_output=True, timeout=40)
    assert second.returncode == 0, second.stderr.decode(errors="replace")
    with session_scope() as s:
        assert s.get(AssistantTurn, tid).status == "complete"
        assert s.scalar(select(func.count()).select_from(AssistantAttachment)) == 1
        assert s.get(Job, jid).attempts == 2


def test_stop_between_provider_attempts_and_old_retry_cannot_take_new_job(client, monkeypatch):
    cid, tid, jid, p, body = stage(client, monkeypatch)
    with session_scope() as s:
        s.get(AssistantTurn, tid).status = "failed"
    result = client.post(f"/api/assistant/conversations/{cid}/stop", json={"request_id": body["request_id"]})
    assert result.json()["status"] == "stopped"
    with session_scope() as s:
        assert s.get(Job, jid).status == "cancelled"
        s.get(AssistantTurn, tid).status = "failed"
        s.get(Job, jid).status = "done"
    with session_scope() as s:
        assistant_jobs.accept(
            get_context(), s, p, cid, {"question": "Next question", "request_id": "next-request-01"}
        )
    from forma.core.errors import Conflict

    with session_scope() as s, pytest.raises(Conflict):
        assistant_jobs.accept(get_context(), s, p, cid, body)


def test_work_badge_is_scoped_and_tracks_seen_and_pending(client, monkeypatch):
    cid, tid, jid, _, _body = stage(client, monkeypatch)
    item = client.get("/api/assistant/work").json()["items"][0]
    assert item["chat_id"] == cid and item["status"] == "queued"
    assert client.get("/api/assistant/work", headers={"X-Forma-User": "other"}).json()["items"] == []
    with session_scope() as s:
        s.get(Job, jid).status = "done"
        turn = s.get(AssistantTurn, tid)
        turn.status, turn.reply = "complete", {"answer": "Done", "ai": True}
    assert client.get("/api/assistant/work").json()["items"][0]["status"] == "complete"
    assert client.post(f"/api/assistant/conversations/{cid}/seen").status_code == 200
    assert client.get("/api/assistant/work").json()["items"] == []
