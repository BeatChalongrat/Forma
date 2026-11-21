"""A file that got FORMA's own draft while the AI was down goes back to the AI once it answers (ADR-077)."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _upload, make_workbook

from forma.ai.llm import LLMClient, Usage
from forma.core.settings import Settings


class _Provider:
    """Down until told otherwise; then it answers like a healthy model (and the connectivity check)."""

    name = "openai"

    def __init__(self) -> None:
        self.down = True
        self.calls = 0

    def complete_json(
        self, *, system: str, user: str, schema: Any, model: str, temperature: float, images=()
    ):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.down:
            raise httpx.ConnectError("connection refused")
        if "connectivity check" in system:
            data: dict[str, Any] = {"ok": True}
        elif "Evidence Analyst" in system:
            data = {
                "analysis": {"facts": [], "hypotheses": [], "mapping_guidance": [], "missing_information": []}
            }
        elif "Semantic Reviewer" in system:
            data = {
                "review": {"verdict": "pass", "checked": ["sources"], "concerns": [], "clarifications": []}
            }
        else:
            data = {"mapping": json.loads(user)["draft_mapping"], "clarifications": [], "open_questions": []}
        text = json.dumps(data, ensure_ascii=False)
        return text, Usage(len(system + user) // 4, len(text) // 4, 1, 1.0, model)


@pytest.fixture
def env(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _Provider, list[str]]]:
    from tests.conftest import clear_forma_caches

    from forma.ai import breaker
    from forma.api.app import create_app
    from forma.jobs import runner
    from forma.services.context import get_context

    monkeypatch.setenv("FORMA_MAPPING_WORKFLOW", "graph")
    monkeypatch.setenv("FORMA_LLM_BACKOFF_S", "0")
    monkeypatch.setenv("FORMA_JOB_RECOVERY_INTERVAL_S", "3600")  # the test drives every sweep itself
    clear_forma_caches()
    breaker.reset_breaker()
    delivered: list[str] = []
    deliver = runner.deliver_jobs

    def hold(ids: list[str], *, sync: bool = False) -> None:  # the test runs staged jobs itself
        deliver(ids, sync=True) if sync else delivered.extend(ids)

    monkeypatch.setattr(runner, "deliver_jobs", hold)
    provider = _Provider()
    with TestClient(create_app()) as c:
        get_context().llm = LLMClient(provider, model="test", max_retries=0)  # type: ignore[arg-type]
        yield c, provider, delivered
    breaker.reset_breaker()


def _fallback_round(c: TestClient, tmp: Path, code: str) -> tuple[str, dict[str, Any]]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(tmp / f"{code}.xlsx"))
    c.post(f"/api/rounds/{rid}/profile", params={"sync": True})
    job = c.post(f"/api/rounds/{rid}/propose", json={"sync": True, "use_llm": True}).json()
    result = c.get(f"/api/jobs/{job['job_id']}").json()
    assert result["status"] == "done", result.get("error")
    mapping = c.get(f"/api/mappings/{result['result']['mapping_version_id']}").json()
    assert mapping["policy"]["checks"]["llm_fallback"] is True and mapping["policy"]["source"] == "heuristic"
    return rid, mapping


def _created(mapping: dict[str, Any]) -> dt.datetime:
    return dt.datetime.fromisoformat(mapping["created_at"].replace("Z", "+00:00"))


def _tasks(c: TestClient, rid: str) -> list[dict[str, Any]]:
    return c.get(f"/api/rounds/{rid}").json()["review_tasks"]


def _run(job_id: str) -> dict[str, Any]:
    from forma.jobs import runner

    runner.run_job(job_id)
    return runner.job_status(job_id)


def test_a_fallback_goes_back_to_the_ai_once_it_answers_and_replaces_the_draft(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.core.errors import Conflict
    from forma.db.session import session_scope
    from forma.services import ai_retry
    from forma.services.context import get_context

    c, provider, delivered = env
    rid, old = _fallback_round(c, isolated_env, "OUT1")
    ctx = get_context()
    soon = _created(old) + dt.timedelta(seconds=ctx.settings.ai_retry_after_s + 1)

    # the provider is still down: the probe says so, and nothing is sent
    assert ai_retry.sweep(ctx, now=soon)["state"] == "provider_unavailable" and not delivered
    # too early for the first retry, whatever the provider says
    assert ai_retry.sweep(ctx, now=_created(old), probe=lambda _: True)["state"] == "nothing_due"

    provider.down = False
    out = ai_retry.sweep(ctx, now=soon)  # the real probe: one tiny call
    assert out["state"] == "staged" and delivered == out["jobs"]
    claim = c.get(f"/api/rounds/{rid}").json()["summary"]["ai_retry"]
    assert claim["state"] == "running" and claim["attempts"] == 1 and claim["replaces"] == old["id"]

    # while the AI reads the file again, the old card refuses people's changes with a reason
    refused = c.post(f"/api/mappings/{old['id']}/reject", json={"comment": "wrong"})
    assert refused.status_code == 409 and "reading this file again" in refused.json()["message"]
    assert c.post(f"/api/mappings/{old['id']}/edit", json={"document": old["document"]}).status_code == 409
    # approving checks the mapping first, then the claim
    with session_scope() as s, pytest.raises(Conflict, match="reading this file again"):
        ai_retry.refuse_while_running(s, rid)

    job = _run(delivered[0])
    assert job["status"] == "done", job.get("error")
    assert job["result"]["state"] == "done"
    new = c.get(f"/api/mappings/{job['result']['mapping_version_id']}").json()
    assert new["policy"]["source"] == "ai" and not new["policy"]["checks"]["llm_fallback"]
    assert new["policy"]["ai_retry"] == {"replaces": old["id"], "attempt": 1, "outcome": "done"}
    assert c.get(f"/api/mappings/{old['id']}").json()["status"] == "superseded"
    tasks = {t["mapping_version_id"]: t["status"] for t in _tasks(c, rid)}
    assert tasks.get(new["id"]) == "open" and "open" not in [s for k, s in tasks.items() if k != new["id"]]
    rnd = c.get(f"/api/rounds/{rid}").json()
    assert rnd["summary"]["mapping_version_id"] == new["id"] and rnd["summary"]["ai_retry"]["state"] == "done"
    # nothing left to send, and the old card cannot be answered any more
    assert ai_retry.sweep(ctx, now=soon + dt.timedelta(days=1))["state"] == "nothing_due"
    assert c.post(f"/api/mappings/{old['id']}/approve", json={}).status_code == 409


def test_retries_are_bounded_wait_longer_each_time_and_say_so_on_the_card(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.services import ai_retry
    from forma.services.context import get_context
    from forma.services.review_explanations import explain

    c, _provider, _ = env
    rid, current = _fallback_round(c, isolated_env, "OUT2")
    ctx = get_context()
    after = ctx.settings.ai_retry_after_s
    up = lambda _: True  # noqa: E731 - the probe answers, the real calls still fail (a flaky provider)

    for attempt in (1, 2, 3):
        wait = after * ai_retry.GROWTH ** (attempt - 1)
        early = _created(current) + dt.timedelta(seconds=wait - 5)
        assert ai_retry.sweep(ctx, now=early, probe=up)["state"] == "nothing_due"
        out = ai_retry.sweep(ctx, now=_created(current) + dt.timedelta(seconds=wait + 1), probe=up)
        assert out["state"] == "staged", out
        job = _run(out["jobs"][0])
        assert job["status"] == "done", job.get("error")
        nxt = c.get(f"/api/mappings/{job['result']['mapping_version_id']}").json()
        assert nxt["policy"]["checks"]["llm_fallback"] is True and nxt["id"] != current["id"]
        expected = "FORMA will try again later" if attempt < 3 else "FORMA stopped retrying"
        assert any(f"AI retry {attempt} of 3" in r and expected in r for r in nxt["policy"]["reasons"])
        assert c.get(f"/api/mappings/{current['id']}").json()["status"] == "superseded"
        current = nxt

    claim = c.get(f"/api/rounds/{rid}").json()["summary"]["ai_retry"]
    assert claim["state"] == "gave_up" and claim["attempts"] == 3 and claim["produced"] == current["id"]
    far = _created(current) + dt.timedelta(hours=1)
    assert ai_retry.sweep(ctx, now=far, probe=up)["state"] == "nothing_due"
    lines = explain(ctx, current["policy"]["reasons"], current["target_schema"])
    th = " ".join(line["th"] for line in lines)
    assert "หยุดส่งซ้ำแล้ว" in th and "ระบบจะส่งไฟล์นี้กลับไปให้ AI" not in th
    assert [t["status"] for t in _tasks(c, rid)].count("open") == 1  # one card, the current one


def test_a_person_who_acts_first_keeps_the_round(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.services import ai_retry
    from forma.services.context import get_context

    c, provider, _ = env
    rid, old = _fallback_round(c, isolated_env, "OUT3")
    edited = c.post(f"/api/mappings/{old['id']}/edit", json={"document": old["document"]})
    assert edited.status_code == 201
    provider.down = False
    later = _created(old) + dt.timedelta(hours=1)
    assert ai_retry.sweep(get_context(), now=later)["state"] == "nothing_due"
    assert "ai_retry" not in c.get(f"/api/rounds/{rid}").json()["summary"]


def test_fresh_uploads_go_first(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.db.models import Job
    from forma.db.session import session_scope
    from forma.services import ai_retry
    from forma.services.context import get_context

    c, provider, delivered = env
    _, old = _fallback_round(c, isolated_env, "OUT4")
    provider.down = False
    with session_scope() as s:
        s.add(
            Job(kind="run_pipeline", payload={"round_id": "rnd_other"}, dispatched_at=dt.datetime.now(dt.UTC))
        )
    later = _created(old) + dt.timedelta(hours=1)
    out = ai_retry.sweep(get_context(), now=later)
    assert out["state"] == "queue_busy" and out["due"] == 1 and not delivered


def test_a_cancelled_round_frees_its_claim_and_is_not_retried(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.services import ai_retry
    from forma.services.context import get_context

    c, provider, delivered = env
    rid, old = _fallback_round(c, isolated_env, "OUT5")
    ctx = get_context()
    provider.down = False
    later = _created(old) + dt.timedelta(hours=1)
    assert ai_retry.sweep(ctx, now=later)["state"] == "staged"
    assert c.post(f"/api/rounds/{rid}/cancel", json={"reason": "sent twice"}).status_code == 200
    assert _run(delivered[0])["status"] == "cancelled"
    out = ai_retry.sweep(ctx, now=later)
    assert out["released"] == [rid] and out["state"] == "nothing_due"
    assert c.get(f"/api/rounds/{rid}").json()["summary"]["ai_retry"]["state"] == "waiting"


def test_a_retry_that_finds_the_round_changed_throws_its_proposal_away(env, isolated_env) -> None:  # type: ignore[no-untyped-def]
    from forma.db.models import ImportRound
    from forma.db.session import session_scope
    from forma.services import ai_retry
    from forma.services.context import get_context

    c, provider, delivered = env
    rid, old = _fallback_round(c, isolated_env, "OUT6")
    provider.down = False
    assert ai_retry.sweep(get_context(), now=_created(old) + dt.timedelta(hours=1))["state"] == "staged"
    with session_scope() as s:  # what a knowledge revision does to the round meanwhile
        r = s.get(ImportRound, rid)
        r.summary = {**r.summary, "mapping_version_id": "map_revised"}
    job = _run(delivered[0])
    assert job["status"] == "done" and job["result"]["state"] == "overtaken"
    assert c.get(f"/api/mappings/{old['id']}").json()["status"] == "proposed"
    assert c.get(f"/api/rounds/{rid}").json()["summary"]["ai_retry"]["state"] == "overtaken"


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("every AI route failed — primary: AI HTTP 503: upstream", "unavailable"),
        ("LLM circuit open after 5 consecutive failures", "unavailable"),
        ("AI task deadline exceeded; retry this import without uploading again", "unavailable"),
        ("AI capacity is busy; retry this job later", "unavailable"),
        ("structured output failed validation: /fields: required", "rejected"),
        ("openai declined the request (refusal: unspecified)", "rejected"),
        ("Mapping workflow attempt/time budget exhausted; review the saved evidence", "rejected"),
        ("something nobody planned for", "unavailable"),
    ],
)
def test_an_outage_gets_every_retry_and_a_rejected_answer_gets_one(reason: str, kind: str) -> None:
    from forma.services import ai_retry

    settings = Settings(ai_retry_max_attempts=3)
    assert ai_retry.kind_of(reason) == kind
    assert ai_retry.allowed(settings, reason) == (3 if kind == "unavailable" else 1)
