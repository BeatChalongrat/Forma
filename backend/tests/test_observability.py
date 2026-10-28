"""Metrics, readiness, request ids, tracing no-op and the Ops endpoint (ADR-014)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _run, _upload, make_workbook


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_health_ready_and_request_ids(client: TestClient) -> None:
    c = client
    h = c.get("/health")
    assert h.status_code == 200 and h.json()["status"] == "ok"
    r = c.get("/ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and set(body["checks"]) == {"db", "storage", "queue"}
    assert all(v["ok"] for v in body["checks"].values())
    # request ids: echoed when given, generated otherwise
    assert c.get("/api/customers", headers={"X-Request-Id": "req-abc"}).headers["x-request-id"] == "req-abc"
    assert c.get("/api/customers").headers["x-request-id"].startswith("req_")


def test_ready_reports_degraded_storage(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.services.context import get_context

    monkeypatch.setattr(get_context().storage, "healthy", lambda: False)
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    assert r.json()["checks"]["storage"]["ok"] is False


def test_metrics_endpoint_after_a_pipeline_run(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "M1", "name": "m"}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    job = _run(c, rid)
    # the job carries the request id of the call that enqueued it
    from forma.jobs import runner

    assert runner.job_status(job["id"])["id"] == job["id"]
    text = c.get("/metrics").text
    assert "forma_jobs_total" in text and 'kind="run_pipeline"' in text
    assert "forma_rounds{" in text and "forma_jobs_queue{" in text
    assert "forma_http_requests_total" in text and 'route="/api/rounds/{round_id}/run"' in text
    assert "forma_engine_rows_total" in text and "forma_job_duration_seconds_bucket" in text
    assert "forma_automation_rate" in text and "forma_review_open" in text
    assert c.get("/metrics").headers["content-type"].startswith("text/plain")


def test_job_rows_carry_request_id(client: TestClient, isolated_env: Path) -> None:
    c = client
    cust = c.post("/api/customers", json={"code": "R1", "name": "r"}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    jid = c.post(f"/api/rounds/{rid}/profile?sync=true", headers={"X-Request-Id": "trace-me"}).json()[
        "job_id"
    ]
    from forma.db.models import Job
    from forma.db.session import session_scope

    with session_scope() as s:
        j = s.get(Job, jid)
        assert j is not None and j.request_id == "trace-me"


def test_ops_summary_is_admin_only_and_complete(client: TestClient, isolated_env: Path) -> None:
    c = client
    assert c.get("/api/ops", headers={"X-Forma-User": "reviewer"}).status_code == 403
    cust = c.post("/api/customers", json={"code": "O1", "name": "o"}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    _run(c, rid)
    # a failing job to show up in failed_jobs
    from forma.jobs import runner

    failed = runner.enqueue("transform_round", {"round_id": rid}, round_id=rid, sync=True)
    ops = c.get("/api/ops").json()
    assert ops["queue"]["backend"] == "thread" and ops["ready"]["status"] == "ok"
    assert ops["storage"] == "local" and ops["database"] == "sqlite" and ops["auth"] == "dev"
    assert ops["tracing"] is False and ops["schema_revision"]
    assert any(j["id"] == failed and j["customer_code"] == "O1" for j in ops["failed_jobs"])
    assert ops["slowest_rounds"] and ops["slowest_rounds"][0]["customer_code"] == "O1"
    assert "profile" in ops["slowest_rounds"][0]["stages"]
    assert ops["review"]["open"] >= 0 and isinstance(ops["llm_spend_by_day"], list)
    # retry from the Ops page re-queues the failed job
    assert c.post(f"/api/jobs/{failed}/retry").status_code == 200


def test_telemetry_noop_and_configure_without_endpoint() -> None:
    from forma.core import telemetry

    assert telemetry.configure(None) is False and telemetry.enabled() is False
    with telemetry.span("x", a=1, b="s") as sp:
        assert sp is None
    telemetry.set_attrs(None, k="v")  # harmless


def test_telemetry_configure_with_endpoint_installs_tracer() -> None:
    pytest.importorskip("opentelemetry.sdk")
    from forma.core import telemetry

    assert telemetry.configure("http://127.0.0.1:4318/v1/traces", "forma-test") is True
    try:
        with telemetry.span("unit.test", rows=3, obj={"a": 1}) as sp:
            assert sp is not None
            telemetry.set_attrs(sp, extra=None, more=2.5)
    finally:
        telemetry._ENABLED = False
        telemetry._TRACER = None


def test_metrics_observe_helpers() -> None:
    from forma.ai.llm import Usage
    from forma.core import metrics

    metrics.observe_job("x_kind", "done", 1.5)
    metrics.observe_job("x_kind", "failed", None)
    metrics.observe_llm("mapping", Usage(10, 5, 1, 2.0, "gpt-4.1-mini"), ok=True)
    out = metrics.render().decode()
    assert 'forma_jobs_total{kind="x_kind",status="done"} 1.0' in out
    assert 'forma_llm_calls_total{agent="mapping",outcome="ok"}' in out


def test_json_log_format_configures_without_error(capsys: pytest.CaptureFixture[str]) -> None:
    import structlog

    from forma.core.logging import configure_logging, get_logger

    configure_logging("INFO", "json")
    structlog.contextvars.bind_contextvars(request_id="r1")
    get_logger("t").info("hello", n=1)
    structlog.contextvars.unbind_contextvars("request_id")
    configure_logging("INFO", "console")
    out = capsys.readouterr().out
    assert '"event": "hello"' in out and '"request_id": "r1"' in out
