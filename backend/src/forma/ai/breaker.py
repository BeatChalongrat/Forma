"""Circuit breaker for the LLM provider (ADR-015).

Transport errors (timeouts, connection errors, 5xx/429) are retried with backoff; after
``FORMA_LLM_BREAKER_FAILURES`` consecutive failures the circuit opens for
``FORMA_LLM_BREAKER_RESET_S`` and every call fails fast with ``LLMError`` — the agents then fall back
to the deterministic draft and policy routes the round to review (never auto-approve, see
``PolicyEngine.evaluate(llm_fallback=True)``). One probe call is allowed when the window expires.

Every AI route (the primary and each backup key, ADR-025) has its own breaker, so a dead key opens only
its own circuit and the next route answers. Hard errors (401, 400, refusals) are not retried: they
count against the breaker and the call moves on at once.
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
        self,
        failures: int = 5,
        reset_s: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "primary",
    ) -> None:
        self.name = name
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
            if self.name == "primary":
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
                log.warning(
                    "llm circuit opened", route=self.name, failures=self._failures, reset_s=self.reset_s
                )
                if self.name == "primary":
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


_BREAKERS: dict[str, CircuitBreaker] = {}
_LOCK = threading.Lock()


def get_breaker(name: str = "primary") -> CircuitBreaker:
    """The breaker of one AI route ("primary" or a backup key's id)."""
    with _LOCK:
        if name not in _BREAKERS:
            from forma.core.settings import get_settings

            s = get_settings()
            _BREAKERS[name] = CircuitBreaker(s.llm_breaker_failures, s.llm_breaker_reset_s, name=name)
        return _BREAKERS[name]


def reset_breaker() -> None:
    with _LOCK:
        _BREAKERS.clear()
    _gauge(0)


def state() -> dict[str, Any]:
    """The primary route's breaker, plus the open/closed state of every backup that has been used."""
    with _LOCK:
        backups = {k: b for k, b in _BREAKERS.items() if k != "primary"}
    return {**get_breaker().state(), "backups": {k: b.state()["open"] for k, b in backups.items()}}


def is_open(name: str) -> bool:
    with _LOCK:
        b = _BREAKERS.get(name)
    return b.is_open if b else False


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
            raise _Hard(e) from e

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
    except _Hard as h:
        raise h.error from None
    except Exception as e:
        if is_transient(e):
            b.failure()
        raise
    b.success()
    return out


class _Hard(Exception):  # internal carrier, never escapes guarded_call
    """Wraps a non-transient error so ``retry`` (which retries RuntimeError for HTTP 429/5xx) skips it."""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error
