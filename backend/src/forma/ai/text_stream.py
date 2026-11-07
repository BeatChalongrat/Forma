"""Real provider text deltas after evidence selection; never stream tool arguments or reasoning."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from forma.ai import guard
from forma.ai.breaker import get_breaker
from forma.ai.limiter import cooldown, provider_slot
from forma.ai.llm import AnthropicProvider, LLMClient, LLMError, OpenAICompatibleProvider, Usage
from forma.core import metrics


class StreamCancelled(Exception):
    pass


@contextlib.contextmanager
def _stream_deadline(response: httpx.Response, seconds: float):  # type: ignore[no-untyped-def]
    timer = threading.Timer(seconds, response.close)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


@dataclass
class AnswerStream:
    emit: Callable[[dict[str, Any]], None]
    checkpoint: Callable[[], None] = lambda: None
    text: str = ""
    phase: str = "research"
    metadata: dict[str, Any] = field(default_factory=dict)
    cancelled: threading.Event = field(default_factory=threading.Event)
    close_provider: Callable[[], None] | None = None

    def check(self) -> None:
        if self.cancelled.is_set():
            raise StreamCancelled()
        self.checkpoint()
        if self.cancelled.is_set():
            raise StreamCancelled()

    def cancel(self) -> None:
        self.cancelled.set()
        if self.close_provider:
            with contextlib.suppress(Exception):
                self.close_provider()

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        self.check()
        self.emit({"type": "phase", "phase": phase})

    def delta(self, text: str) -> None:
        self.check()
        candidate = self.text + text
        errors = guard.scan({"answer": candidate}, allow_credential_terms=True, max_text=12000)
        if errors:
            self.text = ""
            self.emit({"type": "reset"})
            raise guard.GuardRejected(errors)
        self.text = candidate
        self.emit({"type": "delta", "text": text})
        self.checkpoint()


def stream_text(
    llm: LLMClient,
    system: str,
    user: str,
    stream: AnswerStream,
    record_usage: Callable[[Usage, bool], None],
) -> str:
    """Try a backup only before any text is shown. Closing the response cancels its local read."""
    for route in llm.routes:
        stream.check()
        provider = route.provider
        if not isinstance(provider, (OpenAICompatibleProvider, AnthropicProvider)):
            continue
        breaker = get_breaker(route.name)
        usage = Usage(model=route.model, calls=1)
        t0, ok, started = time.perf_counter(), False, False
        try:
            breaker.before_call()
            from forma.ai.budget import reserve_request

            reserve_request()
            headers = {"Content-Type": "application/json"}
            if isinstance(provider, AnthropicProvider):
                url = provider.base_url + "/v1/messages"
                headers.update({"x-api-key": provider.api_key, "anthropic-version": "2023-06-01"})
                body: dict[str, Any] = {
                    "model": route.model,
                    "max_tokens": 4096,
                    "stream": True,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
            else:
                url = provider.base_url + "/chat/completions"
                if provider.api_key:
                    headers["Authorization"] = "Bearer " + provider.api_key
                body = {
                    "model": route.model,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }
            with (
                provider_slot(url, headers, provider.timeout, check=stream.check),
                httpx.Client(timeout=provider.timeout) as client,
                client.stream("POST", url, headers=headers, json=body) as response,
                _stream_deadline(response, provider.timeout),
            ):
                started = True
                stream.close_provider = response.close
                stream.check()
                if response.status_code >= 400:
                    if response.status_code == 429:
                        cooldown(url, headers, response.headers.get("Retry-After"))
                    raise LLMError(f"Streaming HTTP {response.status_code}")
                ended = False
                for line in response.iter_lines():
                    stream.check()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        ended = True
                        break
                    if not raw:
                        continue
                    event = json.loads(raw)
                    if event.get("error") or event.get("type") == "error":
                        raise LLMError("Provider stream failed")
                    delta = ""
                    if isinstance(provider, AnthropicProvider):
                        kind = event.get("type")
                        if kind == "message_start":
                            u = event.get("message", {}).get("usage", {})
                            usage.input_tokens = int(u.get("input_tokens", 0))
                            usage.output_tokens = int(u.get("output_tokens", 0))
                        elif (
                            kind == "content_block_delta"
                            and event.get("delta", {}).get("type") == "text_delta"
                        ):
                            delta = event["delta"].get("text", "")
                        elif kind == "message_delta":
                            usage.output_tokens = int(
                                event.get("usage", {}).get("output_tokens", usage.output_tokens)
                            )
                            if event.get("delta", {}).get("stop_reason") not in (
                                None,
                                "end_turn",
                                "stop_sequence",
                            ):
                                raise LLMError("Incomplete provider response")
                        elif kind == "message_stop":
                            ended = True
                            break
                    else:
                        if event.get("usage"):
                            usage.input_tokens = int(event["usage"].get("prompt_tokens", 0))
                            usage.output_tokens = int(event["usage"].get("completion_tokens", 0))
                        for choice in event.get("choices", []):
                            if choice.get("finish_reason") not in (None, "stop"):
                                raise LLMError("Incomplete provider response")
                            # reasoning_content and tool_calls are deliberately excluded.
                            delta += choice.get("delta", {}).get("content") or ""
                    if delta:
                        stream.delta(delta)
                if not ended or not stream.text.strip():
                    raise LLMError("Incomplete provider stream")
            stream.check()
            breaker.success()
            ok = True
            return stream.text
        except StreamCancelled:
            raise
        except guard.GuardRejected:
            raise
        except Exception:
            if stream.cancelled.is_set():
                raise StreamCancelled() from None
            breaker.failure()
            if stream.text:
                raise LLMError("Response interrupted") from None
        finally:
            stream.close_provider = None
            if started:
                usage.latency_ms = (time.perf_counter() - t0) * 1000
                llm.total_usage.add(usage)
                metrics.observe_llm("workspace_stream", usage, ok=ok)
                record_usage(usage, ok)
    raise LLMError("No streaming provider is available")
