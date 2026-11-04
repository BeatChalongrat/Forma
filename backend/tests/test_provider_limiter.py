import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from forma.ai import limiter
from forma.ai.budget import task_budget
from forma.ai.llm import LLMError
from forma.core.settings import get_settings


def test_parallel_calls_share_capacity_and_do_not_refund_rpm(isolated_env, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_shared_limiter", "local")
    monkeypatch.setattr(settings, "llm_shared_concurrency", 2)
    monkeypatch.setattr(settings, "llm_shared_rpm", 5)
    headers = {"Authorization": uuid.uuid4().hex}
    active, peak, calls = 0, 0, 0
    lock = threading.Lock()

    def call(_):
        nonlocal active, peak, calls
        try:
            with task_budget(0.7), limiter.provider_slot("http://fake/v1/chat/completions", headers, 1):
                with lock:
                    calls += 1
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.025)
                with lock:
                    active -= 1
                return True
        except LLMError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))
    assert sum(results) == calls == 5
    assert peak == 2


def test_cooldown_applies_to_other_callers(isolated_env, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_shared_limiter", "local")
    headers = {"Authorization": uuid.uuid4().hex}
    limiter.cooldown("http://fake/v1/chat/completions", headers, "1")
    with (
        pytest.raises(LLMError, match="deadline"),
        task_budget(0.05),
        limiter.provider_slot("http://fake/v1/chat/completions", headers, 1),
    ):
        pytest.fail("request must not be admitted during provider cooldown")


def test_coordination_failure_never_sends_unlimited_calls(isolated_env, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_shared_limiter", "redis")

    class RedisDown:
        def eval(self, *args):
            raise ConnectionError("offline")

        def close(self):
            pass

    monkeypatch.setattr(limiter, "_redis", RedisDown)
    with pytest.raises(LLMError, match="request was not sent"), limiter.provider_slot("http://fake", {}, 1):
        pytest.fail("must fail closed")


def test_keys_hide_credentials_and_share_stream_and_structured_quota():
    headers = {"Authorization": "secret-do-not-log"}
    a = limiter.route_key("https://api.example/v1/chat/completions", headers)
    b = limiter.route_key("https://api.example/v1/messages", headers)
    assert a == b and "secret" not in a
    assert limiter.route_key("https://different.example", headers) != a
