from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import delivery

from forma.ai.llm import LLMClient, Usage
from forma.auth import Principal
from forma.core.errors import Forbidden
from forma.db.session import session_scope
from forma.services.assistant_navigation import navigation_choices
from forma.services.context import get_context


@pytest.fixture
def client(isolated_env: Path):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_scoped_navigation_and_review_destination(client: TestClient, isolated_env: Path) -> None:
    r = delivery(client, isolated_env)
    admin = Principal("test", frozenset({"admin"}))
    with session_scope() as s:
        choices = navigation_choices(
            s,
            admin,
            "inspect_import",
            {},
            {"id": r["id"], "mapping_id": r["mappings"][0]["id"], "status": "review"},
            "th",
        )
        assert choices[0]["url"] == f"/review?round={r['id']}&status=open"
        assert choices[0]["label"] == "เปิดตรวจปัญหาไฟล์นี้"
        assert any(a["url"].endswith("/mapping/" + r["mappings"][0]["id"]) for a in choices)
        # every link names the customer or the round; the accept action carries the verdict's words (ADR-040)
        assert all(
            a["detail"].startswith("W001") or "October" in a["detail"] or a.get("kind") == "act"
            for a in choices
        )
        denied = Principal("other", frozenset({"importer"}), frozenset())
        with pytest.raises(Forbidden):
            navigation_choices(s, denied, "inspect_import", {}, {"id": r["id"]}, "th")
        importer = Principal("importer", frozenset({"importer"}))
        assert not navigation_choices(s, importer, "runtime_configuration", {}, {}, "th")
        assert all(
            not a["url"].startswith("/settings")
            for a in navigation_choices(s, importer, "feature_guide", {}, {}, "th")
        )


def test_history_action_encodes_filters_and_ignores_untrusted_links(client: TestClient) -> None:
    args = {"search": "สินค้า & test/#", "start": "2026-10-01", "end": "2026-10-29", "timezone": "Asia/Bangkok"}
    with session_scope() as s:
        choices = navigation_choices(
            s,
            Principal("test", frozenset({"admin"})),
            "search_imports",
            args,
            {"items": [], "links": [{"url": "https://evil.example"}]},
            "th",
        )
        assert len(choices) == 1
        assert urlparse(choices[0]["url"]).path == "/imports"
        assert parse_qs(urlparse(choices[0]["url"]).query) == {k: [v] for k, v in args.items()}


def test_model_selects_existing_navigation(client: TestClient) -> None:
    class Provider:
        name = "test"

        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            evidence = json.loads(kw["user"])["evidence"]
            action = next(a for a in evidence[1]["actions"] if a["url"] == "/imports/new")
            return json.dumps(
                {
                    "answer": "เปิดหน้าอัปโหลดไฟล์ได้จากปุ่มด้านล่าง",
                    "citations": [2],
                    "calls": [],
                    "actions": [action["id"]],
                }
            ), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test")
    reply = client.post("/api/assistant/ask", json={"question": "เริ่มอัปโหลดตรงไหน"}).json()
    assert reply["ai"]
    assert len(reply["actions"]) == 1
    assert reply["actions"][0]["url"] == "/imports/new"
    assert reply["actions"][0]["label"] == "ไปอัปโหลดไฟล์"


@pytest.mark.parametrize("action_id", ["invented", "//evil.example"])
def test_model_cannot_invent_destination(client: TestClient, action_id: str) -> None:
    class Provider:
        name = "test"

        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            return json.dumps(
                {"answer": "Click here", "citations": [1], "calls": [], "actions": [action_id]}
            ), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test", max_retries=0)
    reply = client.post("/api/assistant/ask", json={"question": "help"}).json()
    assert not reply["ai"]
    assert not reply.get("actions")


def test_a_verdict_that_needs_no_decision_becomes_a_confirmable_action(
    client: TestClient, isolated_env: Path
) -> None:
    """ADR-040: the assistant proposes a server-built act (round + digest); a person confirms it in the UI."""
    from forma.db.models import ImportRound

    r = delivery(client, isolated_env)
    reviewer = Principal("rev", frozenset({"reviewer"}))
    with session_scope() as s:
        row = s.get(ImportRound, r["id"])
        assert row
        v = dict((row.summary or {}).get("verdict") or {})
        # the demo delivery leaves nothing to decide: confirm-only questions are gone (ADR-058)
        assert not v.get("must_decide") and v.get("outcome") == "accept"
        row.summary = {
            **row.summary,
            "verdict": {**v, "outcome": "answer", "must_decide": [{"id": "/sku", "kind": "confirm"}]},
        }
    with session_scope() as s:
        # an open decision: no act
        assert not any(
            a.get("kind") == "act"
            for a in navigation_choices(s, reviewer, "inspect_import", {}, {"id": r["id"]}, "en")
        )
        row = s.get(ImportRound, r["id"])
        assert row
        row.summary = {
            **row.summary,
            "verdict": {**v, "outcome": "accept", "must_decide": [], "digest": "abc12345"},
        }
    with session_scope() as s:
        acts = [
            a
            for a in navigation_choices(s, reviewer, "inspect_import", {}, {"id": r["id"]}, "th")
            if a.get("kind") == "act"
        ]
        assert len(acts) == 1
        act = acts[0]
        assert act["url"] == "" and act["label"] == "ยอมรับคำตัดสินของ FORMA"
        assert act["act"] == {
            "type": "verdict.accept",
            "round_id": r["id"],
            "digest": "abc12345",
            "preview": act["act"]["preview"],
        }
        assert len(act["act"]["preview"]) >= 3
        importer = Principal("imp", frozenset({"importer"}))
        assert not any(
            a.get("kind") == "act"
            for a in navigation_choices(s, importer, "inspect_import", {}, {"id": r["id"]}, "th")
        )
