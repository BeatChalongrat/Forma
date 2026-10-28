"""Circuit breaker for the LLM provider (ADR-015).

Transport errors (timeouts, connection errors, 5xx/429) are retried with backoff; after
``FORMA_LLM_BREAKER_FAILURES`` consecutive failures the circuit opens for
``FORMA_LLM_BREAKER_RESET_S`` and every call fails fast with ``LLMError`` — the agents then fall back
to the deterministic draft and policy routes the round to review (never auto-approve, see
``PolicyEngine.evaluate(llm_fallback=True)``). One probe call is allowed when the window expires.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from forma.core.logging import get_logger
from forma.core.retry import retry

log = get_logger(__name__)


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self, failures: int = 5, reset_s: float = 120.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.threshold = max(1, failures)
        self.reset_s = reset_s
        self.clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._opened_wall: dt.datetime | None = None
        self._half_open = False
        self._lock = threading.Lock()
        self.total_opened = 0

    # ---- state -----------------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open_locked()

    def _open_locked(self) -> bool:
        if self._opened_at is None:
            return False
        if self.clock() - self._opened_at >= self.reset_s:
            self._half_open = True  # let exactly one probe through
            return False
        return True

    def state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "open": self._open_locked(),
                "half_open": self._half_open,
                "failures": self._failures,
                "threshold": self.threshold,
                "reset_s": self.reset_s,
                "opened_at": self._opened_wall.isoformat() if self._opened_wall else None,
                "times_opened": self.total_opened,
            }

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._opened_wall = None
            self._half_open = False

    # ---- bookkeeping -----------------------------------------------------------------------
    def before_call(self) -> None:
        with self._lock:
            if self._open_locked():
                raise CircuitOpen(f"LLM circuit open after {self._failures} consecutive failures")

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._opened_wall = None
            self._half_open = False
            _gauge(0)

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._half_open or self._failures >= self.threshold:
                if self._opened_at is None or self._half_open:
                    self.total_opened += 1
                self._opened_at = self.clock()
                self._opened_wall = dt.datetime.now(dt.UTC)
                self._half_open = False
                log.warning("llm circuit opened", failures=self._failures, reset_s=self.reset_s)
                _gauge(1)


def _gauge(v: int) -> None:
    try:
        from forma.core import metrics

        metrics.BREAKER_STATE.set(v)
    except Exception:  # pragma: no cover
        pass


TRANSIENT = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    ConnectionError,
)


def is_transient(e: BaseException) -> bool:
    if isinstance(e, TRANSIENT):
        return True
    msg = str(e)
    return any(f"HTTP {code}" in msg for code in (429, 500, 502, 503, 504))


_BREAKER: CircuitBreaker | None = None
_LOCK = threading.Lock()


def get_breaker() -> CircuitBreaker:
    global _BREAKER
    with _LOCK:
        if _BREAKER is None:
            from forma.core.settings import get_settings

            s = get_settings()
            _BREAKER = CircuitBreaker(s.llm_breaker_failures, s.llm_breaker_reset_s)
        return _BREAKER


def reset_breaker() -> None:
    global _BREAKER
    with _LOCK:
        _BREAKER = None
    _gauge(0)


def state() -> dict[str, Any]:
    return get_breaker().state()


def guarded_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    breaker: CircuitBreaker | None = None,
) -> Any:
    """Run a provider call through retries (transient errors only) and the breaker."""
    b = breaker or get_breaker()
    b.before_call()

    def _once() -> Any:
        try:
            return fn()
        except Exception as e:
            if is_transient(e):
                raise
            b.failure()  # a hard provider error counts, but is not retried
            raise

    try:
        out = retry(
            _once,
            attempts=attempts,
            base_s=base_s,
            retry_on=(*TRANSIENT, RuntimeError),
            sleep=sleep,
            on_retry=lambda i, e: (
                log.warning("llm transient error, retrying", attempt=i, error=str(e)[:200])
                if is_transient(e)
                else None
            ),
        )
    except Exception as e:
        if is_transient(e):
            b.failure()
        raise
    b.success()
    return out
