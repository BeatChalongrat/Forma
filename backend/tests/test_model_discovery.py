from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    from forma.services import model_discovery

    original = httpx.Client
    monkeypatch.setattr(
        model_discovery.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )


def test_models_gateway_headers_and_no_persistence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://gateway.example/v1/models"
        assert request.headers["authorization"] == "Bearer test-token"
        return httpx.Response(200, json={"data": [{"id": "custom-large"}, {"id": "custom-small"}]})

    transport(monkeypatch, handle)
    before = client.get("/api/settings/ai").json()
    response = client.post(
        "/api/settings/ai/models",
        json={
            "provider": "openai_compatible",
            "base_url": "https://gateway.example/v1",
            "api_key": "test-token",
        },
    )
    assert response.status_code == 200
    assert response.json()["models"] == ["custom-large", "custom-small"]
    assert client.get("/api/settings/ai").json() == before
    assert "test-token" not in response.text


def test_anthropic_models_pagination(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "test-token"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if len(calls) == 1:
            return httpx.Response(200, json={"data": [{"id": "first"}], "has_more": True, "last_id": "first"})
        assert request.url.params["after_id"] == "first"
        return httpx.Response(200, json={"data": [{"id": "second"}], "has_more": False})

    transport(monkeypatch, handle)
    response = client.post(
        "/api/settings/ai/models",
        json={"provider": "anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "test-token"},
    )
    assert response.json()["models"] == ["first", "second"]
    assert len(calls) == 2


def test_discovery_failure_redacted_and_role_guard(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport(monkeypatch, lambda request: httpx.Response(401, text="secret upstream response test-token"))
    body = {"provider": "openai", "api_key": "test-token"}
    response = client.post("/api/settings/ai/models", json=body)
    assert response.json()["source"] == "unavailable"
    assert "test-token" not in response.text and "secret" not in response.text
    assert (
        client.post("/api/settings/ai/models", json=body, headers={"X-Forma-User": "reviewer"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/settings/ai/models", json={**body, "base_url": "https://u:p@example.com"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/settings/ai/models",
            json={"provider": "openai_compatible", "base_url": "https://different.example/v1"},
        ).status_code
        == 422
    )


def test_connect_uses_explicit_endpoint_model_and_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.services import config

    def probe(llm: Any) -> dict[str, Any]:
        assert llm.provider.base_url == "https://gateway.example/v1"
        assert llm.provider.api_key == "sk-not-an-openai-key"
        assert llm.model == "gateway-specific-model"
        return {
            "ok": True,
            "model": llm.model,
            "provider": "openai",
            "latency_ms": 1,
            "usage": {"input_tokens": 1, "output_tokens": 1, "cost_usd": 0},
        }

    monkeypatch.setattr(config, "probe", probe)
    response = client.post(
        "/api/settings/ai/connect",
        json={
            "api_key": "sk-not-an-openai-key",
            "provider": "openai_compatible",
            "model": "gateway-specific-model",
            "base_url": "https://gateway.example/v1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ai"]["provider"] == "openai_compatible"
    assert response.json()["ai"]["base_url"] == "https://gateway.example/v1"
    assert "sk-not-an-openai-key" not in response.text
    # A stored key must never be silently forwarded to a different endpoint.
    assert (
        client.post(
            "/api/settings/ai/connect",
            json={
                "api_key": "",
                "provider": "openai_compatible",
                "model": "gateway-specific-model",
                "base_url": "https://other.example/v1",
            },
        ).status_code
        == 422
    )
