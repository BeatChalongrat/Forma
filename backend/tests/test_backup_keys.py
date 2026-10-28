"""Backup AI keys and model choice (ADR-025): when the primary provider fails the next key answers, each
key has its own circuit breaker, hard errors move on without retrying, and keys stay encrypted."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from forma.ai.llm import LLMClient, LLMError, Route, Usage


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from forma.ai import breaker
    from forma.core.settings import get_settings

    monkeypatch.setenv("FORMA_LLM_BACKOFF_S", "0")
    monkeypatch.setenv("FORMA_LLM_BREAKER_FAILURES", "2")
    get_settings.cache_clear()
    breaker.reset_breaker()
    yield
    breaker.reset_breaker()
    get_settings.cache_clear()


class _Failing:
    name = "anthropic"

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        self.calls += 1
        raise self.error


class _Answering:
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *, model: str, **kw: Any) -> tuple[str, Usage]:
        self.calls += 1
        return '{"ok": true}', Usage(10, 2, 1, 1.0, model)


def _client(primary: Any, *backups: tuple[str, Any, str]) -> LLMClient:
    c = LLMClient(primary, "claude-opus-5-5", max_retries=0)
    c.fallbacks = [Route(n, p, m) for n, p, m in backups]
    return c


def test_a_hard_error_moves_to_the_backup_without_retrying(no_backoff: None) -> None:
    dead = _Failing(LLMError("anthropic HTTP 401: invalid x-api-key"))
    backup = _Answering()
    c = _client(dead, ("bk_1", backup, "gpt-4.1-mini"))
    res = c.structured(system="s", user="u", schema={})
    assert res.data == {"ok": True} and dead.calls == 1 and backup.calls == 1
    assert c.last_route == "bk_1" and res.usage.model == "gpt-4.1-mini"  # the cost is the backup's


def test_transient_errors_are_retried_then_fail_over(no_backoff: None) -> None:
    from forma.core.settings import get_settings

    slow = _Failing(httpx.ReadTimeout("read timed out"))
    backup = _Answering()
    c = _client(slow, ("bk_1", backup, "gpt-4o-mini"))
    assert c.structured(system="s", user="u", schema={}).data == {"ok": True}
    assert slow.calls == get_settings().llm_transport_retries


def test_an_open_circuit_skips_the_primary(no_backoff: None) -> None:
    from forma.ai import breaker

    dead = _Failing(LLMError("anthropic HTTP 400: credit balance is too low"))
    backup = _Answering()
    c = _client(dead, ("bk_1", backup, "gpt-4.1-mini"))
    for _ in range(4):
        c.structured(system="s", user="u", schema={})
    assert dead.calls == 2  # FORMA_LLM_BREAKER_FAILURES=2: then the primary is skipped
    assert backup.calls == 4 and breaker.is_open("primary") and not breaker.is_open("bk_1")
    assert breaker.state()["open"] is True and breaker.state()["backups"] == {"bk_1": False}


def test_every_route_failing_names_each_one(no_backoff: None) -> None:
    c = _client(
        _Failing(LLMError("anthropic HTTP 401: bad key")),
        ("bk_1", _Failing(LLMError("openai HTTP 429: quota exceeded")), "gpt-4.1"),
    )
    with pytest.raises(LLMError, match="every AI route failed") as e:
        c.structured(system="s", user="u", schema={})
    assert "primary:" in str(e.value) and "bk_1:" in str(e.value)


def test_single_route_keeps_the_provider_message(no_backoff: None) -> None:
    c = _client(_Failing(LLMError("anthropic HTTP 401: bad key")))
    with pytest.raises(LLMError, match=r"^LLMError: anthropic HTTP 401"):
        c.structured(system="s", user="u", schema={})


def test_model_catalogue_lists_known_models_per_provider() -> None:
    from forma.ai.llm import model_catalogue

    cat = model_catalogue()
    assert "claude-opus-5-5" in cat["anthropic"] and "gpt-4.1-mini" in cat["openai"]
    assert not [m for m in cat["openai"] if m.startswith("claude")]


# --------------------------------------------------------------------------------------------
def _fake_probe(monkeypatch: pytest.MonkeyPatch, fail: set[str] | None = None) -> list[tuple[str, str]]:
    from forma.services import config as cfg

    seen: list[tuple[str, str]] = []

    def probe(provider: Any, model: str, temperature: float = 0.0) -> dict[str, Any]:
        seen.append((provider.name, model))
        if fail and model in fail:
            return {"ok": False, "offline": False, "message": "HTTP 401: invalid key", "model": model}
        return {
            "ok": True,
            "offline": False,
            "message": "connected",
            "model": model,
            "provider": provider.name,
            "latency_ms": 3.0,
            "usage": {"input_tokens": 5, "output_tokens": 2, "cost_usd": 0.0},
        }

    monkeypatch.setattr(cfg, "_probe", probe)
    return seen


def test_backup_keys_are_tried_saved_encrypted_ordered_and_removed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import AppSetting, AuditLog, LLMCall
    from forma.db.session import session_scope
    from forma.services.context import get_context

    c = client
    seen = _fake_probe(monkeypatch, fail={"claude-haiku-5-5"})
    primary = "sk-ant-primary-0000000000aaaa"
    assert c.post("/api/settings/ai/connect", json={"api_key": primary}).status_code == 200

    k1, k2 = "sk-openai-backup-111111bbbb", "sk-ant-backup-2222222cccc"
    bad = c.post("/api/settings/ai/backups", json={"api_key": k2, "model": "claude-haiku-5-5"})
    assert bad.status_code == 422 and "did not work" in bad.json()["message"]  # not saved
    r1 = c.post("/api/settings/ai/backups", json={"api_key": k1}).json()
    assert r1["test"]["ok"] and seen[-1] == ("openai", "gpt-4.1-mini")  # provider and default model
    view = c.post("/api/settings/ai/backups", json={"api_key": k2, "model": "claude-sonnet-5-5"}).json()["ai"]
    b1, b2 = view["backups"]
    assert (b1["provider"], b1["model"], b1["api_key_hint"]) == ("openai", "gpt-4.1-mini", "…bbbb")
    assert b2["model"] == "claude-sonnet-5-5" and b1["usable"] and b2["usable"]
    assert view["models"]["anthropic"] and k1 not in json.dumps(view) and k2 not in json.dumps(view)
    assert [r.name for r in get_context().llm.fallbacks] == [b1["id"], b2["id"]]

    # order, model, removal — each applied to the running client at once
    assert c.put("/api/settings/ai/backups/order", json={"ids": [b1["id"]]}).status_code == 422
    c.put("/api/settings/ai/backups/order", json={"ids": [b2["id"], b1["id"]]})
    assert [r.name for r in get_context().llm.fallbacks] == [b2["id"], b1["id"]]
    c.patch(f"/api/settings/ai/backups/{b1['id']}", json={"model": "gpt-4.1"})
    assert get_context().llm.fallbacks[1].model == "gpt-4.1"
    assert c.patch(f"/api/settings/ai/backups/{b1['id']}", json={"model": ""}).status_code == 422
    assert c.delete("/api/settings/ai/backups/nope").status_code == 404
    left = c.delete(f"/api/settings/ai/backups/{b2['id']}").json()["backups"]
    assert [b["id"] for b in left] == [b1["id"]]

    with session_scope() as s:
        stored = json.dumps(s.get(AppSetting, "ai").value)  # type: ignore[union-attr]
        audits = [a.details for a in s.query(AuditLog).filter(AuditLog.action.like("settings.ai.backup%"))]
        ledger = s.query(LLMCall).filter(LLMCall.agent == "settings.backup").count()
    assert k1 not in stored and k2 not in stored and stored.count("api_key_enc") == 2
    assert len(audits) == 5 and all(k1 not in json.dumps(d) and k2 not in json.dumps(d) for d in audits)
    assert ledger == 2

    # the connection test covers every route
    test = c.post("/api/settings/ai/test").json()
    assert test["ok"] and [b["id"] for b in test["backups"]] == [b1["id"]]


def test_backups_answer_when_there_is_no_primary_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.services.context import get_context

    _fake_probe(monkeypatch)
    c = client
    assert c.get("/api/settings/ai").json()["offline"] is True
    view = c.post("/api/settings/ai/backups", json={"api_key": "sk-ant-only-backup-9999dddd"}).json()["ai"]
    assert view["offline"] is False and view["active_route"] == view["backups"][0]["id"]
    assert "first backup" in view["warning"] and view["active_model"] == "claude-opus-5-5"
    assert get_context().llm.primary_name == view["backups"][0]["id"] and not get_context().llm.fallbacks


def test_backup_validation(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_probe(monkeypatch)
    c = client
    unknown = c.post("/api/settings/ai/backups", json={"api_key": "gw-123"})
    assert unknown.status_code == 422 and unknown.json()["details"]["needs_provider"] is True
    gw = c.post("/api/settings/ai/backups", json={"api_key": "gw-123", "provider": "openai_compatible"})
    assert gw.status_code == 422  # a gateway needs its URL and model
    assert (
        c.post(
            "/api/settings/ai/backups",
            json={"api_key": "gw-123", "provider": "openai_compatible", "base_url": "ftp://x", "model": "m"},
        ).status_code
        == 422
    )
    for i in range(4):
        assert c.post("/api/settings/ai/backups", json={"api_key": f"sk-key-{i}-000000"}).status_code == 200
    assert c.post("/api/settings/ai/backups", json={"api_key": "sk-key-5-000000"}).status_code == 422
    assert c.post("/api/settings/ai/backups", json={"api_key": "   "}).status_code == 422
