"""Provider-agnostic LLM client with structured (JSON) output, retries, and usage/cost tracking.

Providers: ``mock`` (deterministic, offline — used in tests and when no API key is configured),
``openai`` / ``openai_compatible`` (any /chat/completions endpoint: OpenAI, Azure, vLLM, Ollama,
an internal AI gateway), ``anthropic`` (Messages API, JSON forced through tool-use).

A client has a primary route and optional backup routes (ADR-025): when a provider call fails
(transport, HTTP error, open circuit) the next route answers; each route has its own circuit breaker.
"""

from __future__ import annotations

import asyncio
import json
import time
from base64 import b64encode
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from forma.ai.budget import bounded_task
from forma.core.settings import Settings, get_settings

# USD per 1M tokens (input, output) — indicative; override via policies if needed.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.1, 0.5),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-1": (15.0, 75.0),
}


def model_catalogue() -> dict[str, list[str]]:
    """Models FORMA knows per provider (the Settings model picker); any other name can still be typed."""
    out: dict[str, list[str]] = {"anthropic": [], "openai": []}
    for m in PRICING:
        out["anthropic" if m.startswith("claude") else "openai"].append(m)
    return out


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


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    if get_settings().llm_mock_only:
        raise LLMError("Offline reproduction mode: external AI requests are disabled")
    from forma.ai.limiter import provider_slot

    with provider_slot(url, headers, timeout):
        return _post_json_admitted(url, headers, body, timeout)


def _post_json_admitted(
    url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
) -> dict[str, Any]:
    """Cancel the network request on wall-clock expiry, including keepalive/whitespace bodies."""
    if get_settings().llm_mock_only:
        raise LLMError("Offline reproduction mode: external AI requests are disabled")
    from forma.ai.budget import progress, remaining

    left = remaining()
    timeout = min(timeout, left) if left is not None else timeout
    progress(f"กำลังรอ AI · {_agent_name()} · จำกัดครั้งนี้ {timeout:.0f} วินาที")

    async def request() -> dict[str, Any]:
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=body)
                if response.status_code >= 400:
                    if response.status_code == 429:
                        from forma.ai.limiter import cooldown

                        cooldown(url, headers, response.headers.get("Retry-After"))
                    raise LLMError(f"AI HTTP {response.status_code}: {response.text[:500]}")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise LLMError("AI returned invalid JSON instead of a provider response") from exc
                if not isinstance(data, dict):
                    raise LLMError("AI returned an empty or malformed provider response")
                return data
        except TimeoutError as exc:
            raise httpx.ReadTimeout(f"AI request wall-clock deadline exceeded ({timeout:.0f}s)") from exc

    # The public client is synchronous. Worker and FastAPI sync handlers already run in threads.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(request())
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    context = copy_context()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(context.run, lambda: asyncio.run(request())).result()


@dataclass(frozen=True)
class ImagePart:
    """One picture for a model to look at (ADR-049). What it shows is data, never instructions."""

    media_type: str
    data: bytes

    @property
    def base64(self) -> str:
        return b64encode(self.data).decode("ascii")


class Provider(Protocol):
    name: str

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        images: Sequence[ImagePart] = (),
    ) -> tuple[str, Usage]: ...


# --------------------------------------------------------------------------------------------
class MockProvider:
    """Offline provider. Returns ``{}`` unless a responder is registered (tests inject behaviour)."""

    name = "mock"

    def __init__(self, responder: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        images: Sequence[ImagePart] = (),
    ) -> tuple[str, Usage]:
        self.calls.append({"system": system, "user": user, **({"images": list(images)} if images else {})})
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
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        images: Sequence[ImagePart] = (),
    ) -> tuple[str, Usage]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": model,
            "max_tokens": get_settings().llm_max_output_tokens,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system + _json_instruction(schema)},
                {"role": "user", "content": _openai_content(user, images)},
            ],
        }
        t0 = time.perf_counter()
        data = _post_json(f"{self.base_url}/chat/completions", headers, body, self.timeout)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError("AI response has no completion choices; retry the import")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise LLMError("AI reply was cut off at max_tokens; no partial mapping was accepted")
        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise LLMError("AI response has no answer content; no partial mapping was accepted")
        u = data.get("usage") or {}
        if not isinstance(u, dict):
            raise LLMError("AI response contains malformed usage metadata")
        usage = Usage(
            _tokens(u, "prompt_tokens"),
            _tokens(u, "completion_tokens"),
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
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        model: str,
        temperature: float,
        images: Sequence[ImagePart] = (),
    ) -> tuple[str, Usage]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        # Current Claude models reject forced tool use and non-default sampling parameters, and the
        # mapping schema is recursive (out of reach of structured outputs): the schema goes in the system
        # prompt and the reply is parsed as JSON, with LLMClient.structured retrying on invalid output.
        body = {
            "model": model,
            "max_tokens": get_settings().llm_max_output_tokens,
            "system": system + _json_instruction(schema),
            "messages": [{"role": "user", "content": _anthropic_content(user, images)}],
        }
        t0 = time.perf_counter()
        data = _post_json(f"{self.base_url}/v1/messages", headers, body, self.timeout)
        stop = data.get("stop_reason")
        if stop == "refusal":
            category = (data.get("stop_details") or {}).get("category")
            raise LLMError(f"{self.name} declined the request (refusal: {category or 'unspecified'})")
        if stop == "max_tokens":
            raise LLMError(f"{self.name} reply was cut off at max_tokens")
        blocks = data.get("content")
        if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
            raise LLMError("AI response contains malformed content blocks")
        parts = [b.get("text") for b in blocks if b.get("type") == "text"]
        if not parts or any(not isinstance(p, str) for p in parts):
            raise LLMError("AI response has no answer content")
        text = "".join(parts)
        if not text.strip():
            raise LLMError("AI response has empty answer content")
        u = data.get("usage") or {}
        if not isinstance(u, dict):
            raise LLMError("AI response contains malformed usage metadata")
        usage = Usage(
            _tokens(u, "input_tokens"),
            _tokens(u, "output_tokens"),
            1,
            (time.perf_counter() - t0) * 1000,
            model,
        )
        return text, usage


def _openai_content(user: str, images: Sequence[ImagePart]) -> Any:
    if not images:
        return user
    pictures = [
        {"type": "image_url", "image_url": {"url": f"data:{i.media_type};base64,{i.base64}"}} for i in images
    ]
    return [{"type": "text", "text": user}, *pictures]


def _anthropic_content(user: str, images: Sequence[ImagePart]) -> Any:
    if not images:
        return user
    pictures = [
        {"type": "image", "source": {"type": "base64", "media_type": i.media_type, "data": i.base64}}
        for i in images
    ]
    return [*pictures, {"type": "text", "text": user}]


def _json_instruction(schema: dict[str, Any]) -> str:
    """The reply contract every provider gets: one JSON object, and the schema it must follow."""
    if not schema:
        return "\n\nRespond with a single JSON object only."
    return (
        "\n\nRespond with a single JSON object only (no prose, no code fence) that conforms to this "
        "JSON Schema:\n" + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def make_provider(kind: str, api_key: str | None, base_url: str | None, timeout: float) -> Provider:
    """The provider for a kind and key; ``MockProvider`` when it cannot be reached (no key, no URL)."""
    if get_settings().llm_mock_only:
        return MockProvider()
    if kind in ("openai", "openai_compatible") and api_key:
        return OpenAICompatibleProvider(api_key, base_url, timeout)
    if kind == "openai_compatible" and base_url:
        return OpenAICompatibleProvider(None, base_url, timeout)
    if kind == "anthropic" and api_key:
        return AnthropicProvider(api_key, base_url, timeout)
    return MockProvider()


@dataclass(frozen=True)
class Route:
    """One way to reach a model. ``name`` ("primary" or a backup's id) also names its circuit breaker."""

    name: str
    provider: Provider
    model: str
    vision: bool | None = None  # can it read images? None until tested (ADR-049)


# --------------------------------------------------------------------------------------------
@dataclass
class LLMClient:
    provider: Provider
    model: str
    max_retries: int = 2
    temperature: float = 0.0
    total_usage: Usage = field(default_factory=Usage)
    fallbacks: list[Route] = field(default_factory=list)  # tried in order when the primary fails
    primary_name: str = "primary"
    last_route: str | None = None  # the route that answered the last call
    primary_vision: bool | None = None  # can the primary read images? None until tested (ADR-049)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        if s.llm_mock_only:
            return cls(MockProvider(), "mock", s.llm_max_retries)
        provider = make_provider(s.llm_provider, s.llm_api_key, s.llm_base_url, s.llm_timeout_s)
        return cls(provider, s.llm_model, s.llm_max_retries)

    @property
    def is_mock(self) -> bool:
        return isinstance(self.provider, MockProvider)

    @property
    def routes(self) -> list[Route]:
        return [Route(self.primary_name, self.provider, self.model, self.primary_vision), *self.fallbacks]

    @property
    def supports_vision(self) -> bool:
        return any(r.vision for r in self.routes)

    def _call_provider(
        self, system: str, user: str, schema: dict[str, Any], images: Sequence[ImagePart] = ()
    ) -> tuple[str, Usage]:
        """One provider round-trip with transport retries + the circuit breaker (ADR-015), falling over
        to the backup routes in order (ADR-025). The mock provider is never wrapped so unit tests stay
        deterministic. A call with images only goes to routes tested to read them (ADR-049): a backup that
        cannot see them must not answer as if it had."""
        pictures: dict[str, Any] = {"images": images} if images else {}

        def invoke(provider: Provider, model: str) -> tuple[str, Usage]:
            from forma.ai.budget import record_request_usage, reserve_request
            from forma.jobs.activity import span

            reserve_request()

            with span(
                "model.request",
                inputs={
                    "provider": provider.name,
                    "model": model,
                    "images": len(images),
                    "agent": _agent_name(),
                },
            ) as receipt:
                response = provider.complete_json(
                    system=system,
                    user=user,
                    schema=schema,
                    model=model,
                    temperature=self.temperature,
                    **pictures,
                )
                usage = response[1]
                record_request_usage(usage)
                receipt["usage"] = {
                    "model": usage.model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "latency_ms": usage.latency_ms,
                }
                return response

        if self.is_mock:
            return invoke(self.provider, self.model)
        from forma.ai.breaker import CircuitOpen, get_breaker, guarded_call
        from forma.core.logging import get_logger
        from forma.core.settings import get_settings

        log = get_logger(__name__)
        s = get_settings()
        routes = [r for r in self.routes if r.vision] if images else self.routes
        if not routes:
            raise LLMError("no connected model can read images")
        failures: list[str] = []
        for route in routes:

            def call(r: Route = route) -> tuple[str, Usage]:
                from forma.ai.budget import remaining

                remaining()
                return invoke(r.provider, r.model)

            try:
                out: tuple[str, Usage] = guarded_call(
                    call,
                    attempts=s.llm_transport_retries,
                    base_s=s.llm_backoff_s,
                    breaker=get_breaker(route.name),
                    sleep=_provider_sleep,
                )
            except CircuitOpen as e:
                failures.append(f"{route.name}: {e}")
            except Exception as e:
                failures.append(f"{route.name}: {type(e).__name__}: {e}"[:300])
            else:
                if route.name != routes[0].name:
                    log.warning("llm answered by a backup", route=route.name, model=route.model)
                self.last_route = route.name
                return out
            if len(routes) > 1:
                log.warning("llm route failed", route=route.name, error=failures[-1][:200])
        if len(routes) == 1:
            raise LLMError(failures[0].split(": ", 1)[1][:500])
        raise LLMError("every AI route failed — " + " | ".join(failures)[:1000])

    @bounded_task
    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        validate: Callable[[dict[str, Any]], list[str]] | None = None,
        images: Sequence[ImagePart] = (),
    ) -> StructuredResult:
        """Call the model until the JSON parses and passes ``validate`` (returns error strings)."""
        from forma.core import metrics
        from forma.core.telemetry import set_attrs, span

        usage = Usage()  # takes the model of the route that answers first
        errors: list[str] = []
        text = ""
        with span("llm.structured", model=self.model, provider=self.provider.name) as sp:
            try:
                res = self._structured(system, user, schema, validate, usage, errors, text, images)
            except LLMError:
                usage.model = usage.model or self.model
                set_attrs(sp, attempts=usage.calls, input_tokens=usage.input_tokens, outcome="error")
                metrics.observe_llm(_agent_name(), usage, ok=False)
                raise
            set_attrs(
                sp,
                route=self.last_route or self.primary_name,
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
        images: Sequence[ImagePart] = (),
    ) -> StructuredResult:
        for attempt in range(1, self.max_retries + 2):
            prompt = (
                user
                if not errors
                else user
                + "\n\nYour previous answer had these problems, fix them:\n- "
                + "\n- ".join(errors[:12])
            )
            try:
                text, u = self._call_provider(system, prompt, schema, images)
            except LLMError as exc:
                # A truncated response is never parsed or accepted. Retry with a
                # smaller response within the existing structured attempt budget.
                if "cut off at max_tokens" not in str(exc) or attempt > self.max_retries:
                    raise
                errors = [
                    "Response exceeded the output limit. Return compact complete JSON. "
                    "Use declarative filters and expressions instead of enumerating records. "
                    "Keep evidence brief; omit optional repetition, never required rules or fields."
                ]
                continue
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


_AGENT: ContextVar[str] = ContextVar("llm_agent", default="unknown")


def _provider_sleep(seconds: float) -> None:
    from forma.ai.budget import remaining
    from forma.jobs.execution import interruptible_sleep

    left = remaining()
    interruptible_sleep(min(seconds, left) if left is not None else seconds)
    remaining()


def set_agent(name: str) -> None:
    """Name the agent for the next structured() call's span/metrics (agents set it before calling)."""
    _AGENT.set(name)


def _agent_name() -> str:
    return _AGENT.get()


def _tokens(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LLMError(f"AI response contains malformed usage metadata: {key}")
    return value


def _parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{") :]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object found")
    try:
        # the first complete object; a model that adds a second object or prose after it is not wrong
        # about the mapping, only chatty (ADR-059)
        data, _ = json.JSONDecoder().raw_decode(t, start)
    except json.JSONDecodeError:
        data = json.loads(t[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data
