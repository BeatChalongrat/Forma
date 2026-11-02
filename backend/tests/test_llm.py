"""LLM client: structured output retries, JSON parsing, usage accounting, provider selection."""

from __future__ import annotations

import json
from typing import Any

import pytest

from forma.ai.llm import (
    AnthropicProvider,
    LLMClient,
    LLMError,
    MockProvider,
    OpenAICompatibleProvider,
    Usage,
    _parse_json,
)
from forma.core.settings import Settings


class _Stateful:
    """Responder that answers differently on each call."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, system: str, user: str, schema: dict[str, Any]) -> Any:
        self.prompts.append(user)
        return self.answers.pop(0)


def test_structured_retries_on_invalid_json() -> None:
    # a JSON array is not an object → "invalid JSON" → retry with the error appended to the prompt
    responder = _Stateful([[1, 2, 3], {"ok": True}])
    client = LLMClient(MockProvider(responder), model="gpt-4.1-mini", max_retries=2)
    res = client.structured(system="sys", user="hello", schema={"type": "object"})
    assert res.data == {"ok": True}
    assert res.attempts == 2
    assert res.usage.calls == 2 and client.total_usage.calls == 2
    assert "previous answer had these problems" in responder.prompts[1]
    assert "invalid JSON" in responder.prompts[1]
    assert responder.prompts[0] == "hello"
    assert client.is_mock


def test_structured_validate_callback_retry_and_failure() -> None:
    responder = _Stateful([{"n": 1}, {"n": 2}, {"n": 3}])
    client = LLMClient(MockProvider(responder), model="m", max_retries=2)

    def validate(d: dict[str, Any]) -> list[str]:
        return [] if d.get("n", 0) >= 2 else [f"n must be >= 2, got {d.get('n')}"]

    res = client.structured(system="s", user="u", schema={}, validate=validate)
    assert res.data == {"n": 2} and res.attempts == 2
    assert "n must be >= 2, got 1" in responder.prompts[1]

    never = LLMClient(MockProvider(lambda s, u, sc: {"n": 0}), model="m", max_retries=1)
    with pytest.raises(LLMError, match="failed validation"):
        never.structured(system="s", user="u", schema={}, validate=validate)
    assert never.total_usage.calls == 2  # retries exhausted but usage still tracked


def test_parse_json_variants() -> None:
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('Sure! Here it is: {"a": {"b": [1, 2]}} thanks') == {"a": {"b": [1, 2]}}
    assert _parse_json('  {"th": "สินค้า"}  ') == {"th": "สินค้า"}
    with pytest.raises(ValueError, match="no JSON object"):
        _parse_json("nothing here")
    with pytest.raises(ValueError, match="no JSON object"):
        _parse_json("[1, 2]")
    assert _parse_json('[{"a": 1}]') == {"a": 1}  # the inner object is salvaged
    # a second object or prose after the first complete object is chatter, not a wrong answer (ADR-059)
    assert _parse_json('{"a": 1}\n{"note": "and here is why"}') == {"a": 1}
    assert _parse_json('{"a": {"b": 1}} Hope this helps} {') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        _parse_json('{"a": }')


def test_usage_cost_and_add() -> None:
    u = Usage(input_tokens=1_000_000, output_tokens=500_000, calls=1, latency_ms=10.0, model="gpt-4.1-mini")
    assert u.cost_usd == pytest.approx(0.4 + 0.8)
    unknown = Usage(input_tokens=1_000_000, output_tokens=0, model="some-model")
    assert unknown.cost_usd == 1.0  # fallback pricing
    total = Usage()
    total.add(u)
    total.add(Usage(input_tokens=10, output_tokens=5, calls=2, latency_ms=1.5, model="other"))
    assert total.model == "gpt-4.1-mini"  # first model wins
    assert total.calls == 3 and total.input_tokens == 1_000_010 and total.latency_ms == 11.5
    d = total.to_dict()
    assert d["calls"] == 3 and d["cost_usd"] == total.cost_usd and d["latency_ms"] == 11.5


def test_mock_provider_records_calls() -> None:
    p = MockProvider()
    text, usage = p.complete_json(system="a" * 40, user="b" * 40, schema={}, model="m", temperature=0.0)
    assert text == "{}" and usage.calls == 1 and usage.input_tokens == 20 and usage.model == "m"
    assert p.calls == [{"system": "a" * 40, "user": "b" * 40}]


def test_from_settings_provider_selection() -> None:
    assert isinstance(LLMClient.from_settings(Settings(llm_provider="mock")).provider, MockProvider)
    c = LLMClient.from_settings(Settings(llm_provider="openai", llm_api_key="k", llm_model="gpt-4o"))
    assert isinstance(c.provider, OpenAICompatibleProvider) and c.model == "gpt-4o"
    assert c.provider.base_url == "https://api.openai.com/v1"
    gw = LLMClient.from_settings(
        Settings(llm_provider="openai_compatible", llm_base_url="http://gw:8080/v1/")
    )
    assert isinstance(gw.provider, OpenAICompatibleProvider) and gw.provider.base_url == "http://gw:8080/v1"
    a = LLMClient.from_settings(Settings(llm_provider="anthropic", llm_api_key="k"))
    assert isinstance(a.provider, AnthropicProvider) and a.provider.base_url == "https://api.anthropic.com"
    # no key → offline mock
    assert LLMClient.from_settings(Settings(llm_provider="anthropic")).is_mock


def test_http_providers_with_mocked_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def openai_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer key"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"x": 1}'}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    def anthropic_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "key"
        body = json.loads(request.content)
        # current Claude models reject forced tool use and non-default sampling: neither is sent,
        # the schema travels in the system prompt and the JSON comes back as text
        assert "tool_choice" not in body and "tools" not in body and "temperature" not in body
        assert '"properties":{}' in body["system"] and body["max_tokens"] == 16000
        return httpx.Response(
            200,
            json={
                "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"y": 2}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        )

    def refusing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}}
        )

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    handlers = {"openai": openai_handler}
    real_client = httpx.Client

    def fake_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handlers["current"])
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)

    handlers["current"] = openai_handler
    text, usage = OpenAICompatibleProvider("key").complete_json(
        system="s", user="u", schema={}, model="gpt-4o", temperature=0.1
    )
    assert text == '{"x": 1}' and (usage.input_tokens, usage.output_tokens, usage.calls) == (7, 3, 1)

    handlers["current"] = anthropic_handler
    text, usage = AnthropicProvider("key").complete_json(
        system="s", user="u", schema={"properties": {}}, model="claude-opus-5-5", temperature=0.0
    )
    assert text == '{"y": 2}' and usage.input_tokens == 5 and usage.cost_usd > 0

    handlers["current"] = refusing
    with pytest.raises(LLMError, match="refusal: cyber"):
        AnthropicProvider("key").complete_json(system="s", user="u", schema={}, model="m", temperature=0.0)

    handlers["current"] = failing
    with pytest.raises(LLMError, match="HTTP 500"):
        OpenAICompatibleProvider("key").complete_json(
            system="s", user="u", schema={}, model="m", temperature=0.0
        )
    with pytest.raises(LLMError, match="HTTP 500"):
        AnthropicProvider("key").complete_json(system="s", user="u", schema={}, model="m", temperature=0.0)
