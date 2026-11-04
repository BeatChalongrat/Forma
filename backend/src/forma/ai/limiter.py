"""Provider admission shared across workers (Redis); fail closed when coordination is unavailable."""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import urlsplit

from forma.core.settings import get_settings

ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 60)
local cooldown = tonumber(redis.call('GET', KEYS[3]) or '0')
if cooldown > now then return 0 end
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then return 0 end
if redis.call('ZCARD', KEYS[2]) >= tonumber(ARGV[3]) then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[4]), ARGV[1])
redis.call('ZADD', KEYS[2], now, ARGV[1])
redis.call('EXPIRE', KEYS[1], math.ceil(tonumber(ARGV[4])) + 60)
redis.call('EXPIRE', KEYS[2], 120)
return 1
"""
COOLDOWN = """
local t = redis.call('TIME')
local until_ = tonumber(t[1]) + tonumber(t[2]) / 1000000 + tonumber(ARGV[1])
local old = tonumber(redis.call('GET', KEYS[1]) or '0')
if until_ > old then redis.call('SET', KEYS[1], until_, 'EX', math.ceil(tonumber(ARGV[1])) + 1) end
"""
_LOCK = threading.Lock()
_SLOTS: dict[str, dict[str, float]] = defaultdict(dict)
_CALLS: dict[str, list[float]] = defaultdict(list)
_COOLDOWNS: dict[str, float] = {}


def route_key(url: str, headers: dict[str, str]) -> str:
    # Never include credentials in Redis keys or diagnostics. Paths for chat/structured share quota.
    parsed = urlsplit(url)
    credential = headers.get("Authorization") or headers.get("x-api-key") or "anonymous"
    return hashlib.sha256(f"{parsed.scheme}://{parsed.netloc}|{credential}".encode()).hexdigest()


def _redis() -> Any:
    from redis import Redis

    return Redis.from_url(get_settings().redis_url, socket_timeout=3, socket_connect_timeout=3)


def _shared() -> bool:
    s = get_settings()
    return s.llm_shared_limiter == "redis" or (s.llm_shared_limiter == "auto" and s.job_backend == "arq")


def cooldown(url: str, headers: dict[str, str], retry_after: str | None) -> None:
    from email.utils import parsedate_to_datetime

    try:
        seconds = float(retry_after or "5")
    except ValueError:
        try:
            seconds = parsedate_to_datetime(retry_after or "").timestamp() - time.time()
        except (ValueError, TypeError):
            seconds = 5
    seconds = max(1, min(seconds, 3600))
    key = route_key(url, headers)
    if _shared():
        with _redis() as redis:
            redis.eval(COOLDOWN, 1, f"forma:llm:{{{key}}}:cooldown", seconds)
    else:
        with _LOCK:
            _COOLDOWNS[key] = max(_COOLDOWNS.get(key, 0), time.monotonic() + seconds)


@contextmanager
def provider_slot(
    url: str, headers: dict[str, str], timeout: float, check: Callable[[], None] | None = None
) -> Iterator[None]:
    from forma.ai.budget import remaining
    from forma.ai.llm import LLMError
    from forma.jobs.execution import interruptible_sleep

    settings = get_settings()
    key, token = route_key(url, headers), uuid.uuid4().hex
    prefix = f"forma:llm:{{{key}}}"
    keys = [prefix + ":slots", prefix + ":calls", prefix + ":cooldown"]
    redis = _redis() if _shared() else None
    acquired = False
    wait_until = time.monotonic() + settings.llm_task_budget_s
    try:
        while not acquired:
            remaining()
            if check:
                check()
            if time.monotonic() >= wait_until:
                raise LLMError("AI capacity is busy; retry this job later")
            try:
                if redis:
                    acquired = bool(
                        redis.eval(
                            ACQUIRE,
                            3,
                            *keys,
                            token,
                            settings.llm_shared_concurrency,
                            settings.llm_shared_rpm,
                            timeout + 10,
                        )
                    )
                else:
                    with _LOCK:
                        now = time.monotonic()
                        _SLOTS[key] = {k: v for k, v in _SLOTS[key].items() if v > now}
                        _CALLS[key] = [t for t in _CALLS[key] if t > now - 60]
                        acquired = (
                            len(_SLOTS[key]) < settings.llm_shared_concurrency
                            and len(_CALLS[key]) < settings.llm_shared_rpm
                            and _COOLDOWNS.get(key, 0) <= now
                        )
                        if acquired:
                            _SLOTS[key][token] = now + timeout + 10
                            _CALLS[key].append(now)
            except Exception as exc:
                raise LLMError("AI capacity coordinator unavailable; request was not sent") from exc
            if not acquired:
                interruptible_sleep(0.2)
        yield
    finally:
        if acquired:
            if redis:
                with suppress(Exception):
                    redis.zrem(keys[0], token)
            else:
                with _LOCK:
                    _SLOTS[key].pop(token, None)
        if redis:
            redis.close()
