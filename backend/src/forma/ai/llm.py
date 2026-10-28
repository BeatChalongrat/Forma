"""Provider-agnostic LLM client with structured (JSON) output, retries, and usage/cost tracking.

Providers: ``mock`` (deterministic, offline — used in tests and when no API key is configured),
``openai`` / ``openai_compatible`` (any /chat/completions endpoint: OpenAI, Azure, vLLM, Ollama,
an internal AI gateway), ``anthropic`` (Messages API, JSON forced through tool-use).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from forma.core.settings import Settings, get_settings

# USD per 1M tokens (input, output) — indicative; override via policies if needed.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-1": (15.0, 75.0),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    latency_ms: float = 0.0
    model: str = ""

    @property
    def cost_usd(self) -> float:
        inp, out = PRICING.get(self.model, (1.0, 4.0))
        return round(self.input_tokens / 1e6 * inp + self.output_tokens / 1e6 * out, 6)

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.calls += other.calls
        self.latency_ms += other.latency_ms
        self.model = self.model or other.model

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": self.cost_usd,
        }


@dataclass
class StructuredResult:
    data: dict[str, Any]
    usage: Usage
    raw_text: str
    attempts: int


class LLMError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Usage]: ...


# --------------------------------------------------------------------------------------------
class MockProvider:
    """Offline provider. Returns ``{}`` unless a responder is registered (tests inject behaviour)."""

    name = "mock"

    def __init__(self, responder: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Usage]:
        self.calls.append({"system": system, "user": user})
        data = self.responder(system, user, schema) if self.responder else {}
        text = json.dumps(data, ensure_ascii=False)
        return text, Usage(len(system + user) // 4, len(text) // 4, 1, 1.0, model)


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, api_key: str | None, base_url: str | None = None, timeout: float = 90.0) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Usage]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": model,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + "\n\nRespond with a single JSON object only."},
                {"role": "user", "content": user},
            ],
        }
        t0 = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        text = data["choices"][0]["message"]["content"] or ""
        u = data.get("usage", {})
        usage = Usage(
            int(u.get("prompt_tokens", 0)),
            int(u.get("completion_tokens", 0)),
            1,
            (time.perf_counter() - t0) * 1000,
            model,
        )
        return text, usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, base_url: str | None = None, timeout: float = 90.0) -> None:
        self.api_key = api_key or ""
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout = timeout

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Usage]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        tool = {
            "name": "emit",
            "description": "Return the structured result.",
            "input_schema": _loosen(schema),
        }
        body = {
            "model": model,
            "max_tokens": 8192,
            "temperature": temperature,
            "system": system,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "emit"},
            "messages": [{"role": "user", "content": user}],
        }
        t0 = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(f"{self.base_url}/v1/messages", headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:500]}")
        data = r.json()
        payload: Any = {}
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                payload = block.get("input", {})
                break
        u = data.get("usage", {})
        usage = Usage(
            int(u.get("input_tokens", 0)),
            int(u.get("output_tokens", 0)),
            1,
            (time.perf_counter() - t0) * 1000,
            model,
        )
        return json.dumps(payload, ensure_ascii=False), usage


def _loosen(schema: dict[str, Any]) -> dict[str, Any]:
    """Anthropic input_schema must be a plain object schema; keep $defs so refs resolve."""
    out = dict(schema)
    out.setdefault("type", "object")
    return out


# --------------------------------------------------------------------------------------------
@dataclass
class LLMClient:
    provider: Provider
    model: str
    max_retries: int = 2
    temperature: float = 0.0
    total_usage: Usage = field(default_factory=Usage)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        provider: Provider
        if s.llm_provider in ("openai", "openai_compatible") and s.llm_api_key:
            provider = OpenAICompatibleProvider(s.llm_api_key, s.llm_base_url, s.llm_timeout_s)
        elif s.llm_provider == "openai_compatible" and s.llm_base_url:
            provider = OpenAICompatibleProvider(None, s.llm_base_url, s.llm_timeout_s)
        elif s.llm_provider == "anthropic" and s.llm_api_key:
            provider = AnthropicProvider(s.llm_api_key, s.llm_base_url, s.llm_timeout_s)
        else:
            provider = MockProvider()
        return cls(provider, s.llm_model, s.llm_max_retries)

    @property
    def is_mock(self) -> bool:
        return isinstance(self.provider, MockProvider)

    def _call_provider(self, system: str, user: str, schema: dict[str, Any]) -> tuple[str, Usage]:
        """One provider round-trip with transport retries + the circuit breaker (ADR-015).
        The mock provider is never wrapped so unit tests stay deterministic."""
        if self.is_mock:
            return self.provider.complete_json(
                system=system, user=user, schema=schema, model=self.model, temperature=self.temperature
            )
        from forma.ai.breaker import CircuitOpen, guarded_call
        from forma.core.settings import get_settings

        s = get_settings()
        try:
            out: tuple[str, Usage] = guarded_call(
                lambda: self.provider.complete_json(
                    system=system, user=user, schema=schema, model=self.model, temperature=self.temperature
                ),
                attempts=s.llm_transport_retries,
                base_s=s.llm_backoff_s,
            )
        except CircuitOpen as e:
            raise LLMError(str(e)) from e
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}"[:500]) from e
        return out

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        validate: Callable[[dict[str, Any]], list[str]] | None = None,
    ) -> StructuredResult:
        """Call the model until the JSON parses and passes ``validate`` (returns error strings)."""
        from forma.core import metrics
        from forma.core.telemetry import set_attrs, span

        usage = Usage(model=self.model)
        errors: list[str] = []
        text = ""
        with span("llm.structured", model=self.model, provider=self.provider.name) as sp:
            try:
                res = self._structured(system, user, schema, validate, usage, errors, text)
            except LLMError:
                set_attrs(sp, attempts=usage.calls, input_tokens=usage.input_tokens, outcome="error")
                metrics.observe_llm(_agent_name(), usage, ok=False)
                raise
            set_attrs(
                sp,
                attempts=res.attempts,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                outcome="ok",
            )
            metrics.observe_llm(_agent_name(), usage, ok=True)
            return res

    def _structured(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        validate: Callable[[dict[str, Any]], list[str]] | None,
        usage: Usage,
        errors: list[str],
        text: str,
    ) -> StructuredResult:
        for attempt in range(1, self.max_retries + 2):
            prompt = (
                user
                if not errors
                else user
                + "\n\nYour previous answer had these problems, fix them:\n- "
                + "\n- ".join(errors[:12])
            )
            text, u = self._call_provider(system, prompt, schema)
            usage.add(u)
            try:
                data = _parse_json(text)
            except ValueError as e:
                errors = [f"invalid JSON: {e}"]
                continue
            errors = validate(data) if validate else []
            if not errors:
                self.total_usage.add(usage)
                return StructuredResult(data, usage, text, attempt)
        self.total_usage.add(usage)
        raise LLMError("structured output failed validation: " + "; ".join(errors[:5]))


_AGENT: list[str] = ["unknown"]


def set_agent(name: str) -> None:
    """Name the agent for the next structured() call's span/metrics (agents set it before calling)."""
    _AGENT[0] = name


def _agent_name() -> str:
    return _AGENT[0]


def _parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{") :]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    data = json.loads(t[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data
