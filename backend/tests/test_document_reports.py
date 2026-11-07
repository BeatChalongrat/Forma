"""Formal report evidence, queue isolation, permissions, cancellation and publication gates."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_artifacts import Maker, model, new_chat, say
from tests.test_assistant_reports import args, seed_records

from forma.code_sandbox.client import RunResult
from forma.core.errors import ValidationFailed
from forma.db.models import AssistantArtifact, Job
from forma.db.session import session_scope
from forma.jobs import runner
from forma.reporting.registry import catalog, digest, skill
from forma.services import code_sandbox
from forma.services import document_reports as dr


class DocumentSandbox:
    """Simulates the trusted renderer protocol; Docker probe covers actual Office/PDF rendering."""

    broken = False
    calls = 0

    def run(self, code: str, inputs: Any, timeout_s: float) -> RunResult:
        self.calls += 1
        with next(iter(inputs)).open() as f:
            spec = json.load(f)
        if self.broken:
            return RunResult("error", 1, 1, "", "render failed")
        data = {f"report.{fmt}": (fmt + spec["snapshot_id"]).encode() for fmt in spec["formats"]}
        quality = {
            "snapshot_id": spec["snapshot_id"],
            "status": "automatic_checks_passed",
            "visual_review": "human_required",
            "files": {
                name: {"pages": 1, "sha256": hashlib.sha256(value).hexdigest()}
                for name, value in data.items()
            },
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            for fmt in spec["formats"]:
                z.writestr(f"{fmt}/page-1.png", b"png")
                z.writestr(f"{fmt}/document.pdf", b"pdf")
        data.update(
            {
                "quality.json": json.dumps(quality).encode(),
                "snapshot.json": json.dumps(spec).encode(),
                "previews.zip": archive.getvalue(),
            }
        )
        return RunResult("ok", 0, 5, "", "", files=list(data.items()))


@pytest.fixture
def app(isolated_env: Path, monkeypatch: pytest.MonkeyPatch):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        if os.environ.get("FORMA_TEST_REPORT_DOCKER") == "1":
            from tests.document_docker import DockerDocumentSandbox

            sandbox = DockerDocumentSandbox()
        else:
            sandbox = DocumentSandbox()
        monkeypatch.setattr(code_sandbox, "get_runner", lambda settings: sandbox)

        # Run the assistant normally, but retain report deliveries for explicit worker tests.
        # The durable chat now owns a separate job; blocking every kind would also block the question.
        def deliver(ids, **kw):
            with session_scope() as session:
                assistant_ids = list(
                    session.scalars(select(Job.id).where(Job.id.in_(ids), Job.kind == "assistant_message"))
                )
            for jid in assistant_ids:
                runner.run_job(jid)

        monkeypatch.setattr(runner, "deliver_jobs", deliver)
        monkeypatch.setattr(runner, "dispatch_pending", lambda *a, **kw: [])
        yield c, sandbox


def make_job(c: TestClient, **extra: Any) -> tuple[str, str, Maker]:
    seed_records(
        [
            {"id": "a", "department": "TH", "amount": "0.10", "currency": "THB"},
            {"id": "b", "department": "TH", "amount": "0.20", "currency": "THB"},
        ]
    )
    chat = new_chat(c)
    maker = model(
        ("report_templates", {}),
        ("read_report_skill", {"skill": "formal-report"}),
        ("build_report", args()),
        (
            "create_document",
            {"template": "executive", "formats": ["docx", "pptx", "pdf"], "evidence_ids": [5], **extra},
        ),
    )
    # Evidence IDs include initial summary + guide; derive from the actual current tool receipt.
    original = maker.complete_json

    def complete(**kw: Any):
        payload = json.loads(kw["user"])
        if maker.steps and maker.steps[0][0] == "create_document":
            eid = next(e["id"] for e in payload["evidence"] if e["tool"] == "build_report")
            maker.steps[0][1]["evidence_ids"] = [eid]
        return original(**kw)

    maker.complete_json = complete
    say(c, chat, language="th")
    jobs = c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]
    assert len(jobs) == 1, maker.payloads[-1]
    return chat, jobs[0]["id"], maker


def test_end_to_end_snapshot_publish_and_access(app):
    c, sandbox = app
    chat, jid, maker = make_job(c)
    detail = c.get(f"/api/assistant/reports/{jid}").json()
    spec = detail["snapshot"]
    assert spec["sources"][0]["report"]["rows"][0]["value"] == "0.30"
    before = len(maker.payloads)
    assert runner.run_job(jid)
    assert not runner.run_job(jid)
    detail = c.get(f"/api/assistant/reports/{jid}").json()
    assert detail["status"] == "done", detail
    assert len(detail["artifacts"]) == 3
    assert sandbox.calls == 1
    assert len(maker.payloads) == before  # worker never calls an LLM
    for artifact in detail["artifacts"]:
        work = c.get(f"/api/assistant/artifacts/{artifact['id']}").json()
        version = work["versions"][0]
        assert version["checked"] == 0
        assert version["report"]["snapshot_id"] == spec["snapshot_id"]
        assert version["preview"]["status"] == "ready"
        assert c.get(f"/api/assistant/attachments/{version['file']['id']}").status_code == 200
    other = {"X-Forma-User": "other", "X-Forma-Roles": "admin"}
    for url in (f"/api/assistant/reports/{jid}", f"/api/assistant/conversations/{chat}/reports"):
        assert c.get(url, headers=other).status_code == 404
    assert c.post(f"/api/assistant/reports/{jid}/retry", headers=other).status_code == 404
    assert c.get(f"/api/jobs/{jid}").status_code == 404
    assert c.get(f"/api/activity/jobs/{jid}").status_code == 404
    assert all(j["id"] != jid for j in c.get("/api/jobs").json())


def test_failure_retries_preserve_snapshot_and_publish_once(app, monkeypatch):
    c, sandbox = app
    _, jid, _ = make_job(c)
    frozen = c.get(f"/api/assistant/reports/{jid}").json()["snapshot_id"]
    sandbox.broken = True
    runner.run_job(jid)
    assert c.get(f"/api/assistant/reports/{jid}").json()["status"] == "failed"
    with session_scope() as s:
        assert not list(s.scalars(select(AssistantArtifact)))
    monkeypatch.setattr(runner, "dispatch_pending", lambda *a: [])
    assert c.post(f"/api/assistant/reports/{jid}/retry").status_code == 200
    sandbox.broken = False
    runner.run_job(jid)
    assert c.get(f"/api/assistant/reports/{jid}").json()["snapshot_id"] == frozen
    with session_scope() as s:
        job = s.get(Job, jid)
        # Simulate a worker crash after fenced publication but before marking the job done.
        job.status, job.lease_token = "queued", None
    runner.run_job(jid)
    assert sandbox.calls == 2
    with session_scope() as s:
        assert len(list(s.scalars(select(AssistantArtifact)))) == 3


def test_cancelled_job_never_publishes(app):
    c, sandbox = app
    _, jid, _ = make_job(c)
    assert c.post(f"/api/assistant/reports/{jid}/cancel").status_code == 200
    assert not runner.run_job(jid)
    assert sandbox.calls == 0


def test_private_scope_and_template_inputs(app):
    c, _ = app
    assert len(c.get("/api/assistant/report-templates").json()["templates"]) == 5
    for name in ("../../secrets", "unknown"):
        with pytest.raises(ValidationFailed):
            skill(name)
    with pytest.raises(ValidationFailed):
        dr.freeze(
            {"template": "executive", "formats": ["pdf"], "evidence_ids": [9]},
            {},
            [],
            "th",
            "Asia/Bangkok",
            {"formal-report"},
        )
    with pytest.raises(ValidationFailed):
        dr.freeze(
            {"template": "executive", "formats": ["pdf"], "rows": [{"value": 999}]},
            {},
            [],
            "th",
            "Asia/Bangkok",
            {"formal-report"},
        )
    with pytest.raises(ValidationFailed):
        dr.freeze({"template": "executive", "formats": ["pdf"]}, {}, [], "th", "Asia/Bangkok", set())


def test_queue_is_separate(isolated_env):
    normal = runner.get_backend()
    reports = runner.get_backend("create_document")
    assert normal is not reports
    assert reports.workers == 1
    assert runner.ArqBackend("redis://localhost", "arq:reports").queue == "arq:reports"


def test_cancel_during_render_discards_outputs(app):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    c, sandbox = app
    _, jid, _ = make_job(c)
    entered, released = Event(), Event()
    original = sandbox.run

    def blocked(*a, **kw):
        entered.set()
        assert released.wait(10)
        return original(*a, **kw)

    sandbox.run = blocked
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.run_job, jid)
        assert entered.wait(10)
        assert c.post(f"/api/assistant/reports/{jid}/cancel").status_code == 200
        released.set()
        future.result(timeout=15)
    with session_scope() as s:
        assert s.get(Job, jid).status == "cancelled"
        assert not list(s.scalars(select(AssistantArtifact)))


def test_delete_chat_removes_report_snapshot(app):
    c, _ = app
    chat, jid, _ = make_job(c)
    assert c.delete(f"/api/assistant/conversations/{chat}").status_code == 204
    with session_scope() as s:
        assert s.get(Job, jid) is None
    assert not runner.run_job(jid)


def test_revision_keeps_original_version(app):
    c, _ = app
    chat, jid, _ = make_job(c)
    runner.run_job(jid)
    old = c.get(f"/api/assistant/reports/{jid}").json()
    artifact_id = old["artifacts"][0]["id"]
    maker = model(
        ("read_report_skill", {"skill": "formal-report"}),
        ("build_report", args()),
        (
            "create_document",
            {
                "template": "executive",
                "formats": ["docx"],
                "evidence_ids": [],
                "revise_artifact_id": artifact_id,
            },
        ),
    )
    original = maker.complete_json

    def complete(**kw):
        payload = json.loads(kw["user"])
        if maker.steps and maker.steps[0][0] == "create_document":
            maker.steps[0][1]["evidence_ids"] = [
                next(e["id"] for e in payload["evidence"] if e["tool"] == "build_report")
            ]
        return original(**kw)

    maker.complete_json = complete
    say(c, chat, request="revision-0001", language="th")
    jobs = c.get(f"/api/assistant/conversations/{chat}/reports").json()["items"]
    assert len(jobs) == 2
    runner.run_job(jobs[0]["id"])
    versions = c.get(f"/api/assistant/artifacts/{artifact_id}").json()["versions"]
    assert [v["number"] for v in versions] == [1, 2]
    assert versions[0]["report"]["snapshot_id"] == old["snapshot_id"]
    assert versions[1]["report"]["snapshot_id"] != old["snapshot_id"]


def test_packaged_skill_versions():
    assert catalog()["version"] == "1.1.0"
    for item in catalog()["skills"]:
        assert len(skill(item["id"])["sha256"]) == 64
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})


def test_modified_snapshot_and_output_are_refused(app, monkeypatch):
    c, sandbox = app
    _, jid, _ = make_job(c)
    with session_scope() as s:
        job = s.get(Job, jid)
        payload = json.loads(json.dumps(job.payload))
        payload["snapshot"]["sources"][0]["report"]["rows"][0]["value"] = "999"
        job.payload = payload
    runner.run_job(jid)
    assert c.get(f"/api/assistant/reports/{jid}").json()["status"] == "failed"
    assert sandbox.calls == 0
    with session_scope() as s:
        job = s.get(Job, jid)
        payload = json.loads(json.dumps(job.payload))
        payload["snapshot"]["sources"][0]["report"]["rows"][0]["value"] = "0.30"
        job.payload = payload
    original = sandbox.run

    def corrupt(*a, **kw):
        result = original(*a, **kw)
        result.files = [(name, b"modified" if name == "report.pdf" else data) for name, data in result.files]
        return result

    monkeypatch.setattr(sandbox, "run", corrupt)
    monkeypatch.setattr(runner, "dispatch_pending", lambda *a: [])
    c.post(f"/api/assistant/reports/{jid}/retry")
    runner.run_job(jid)
    assert c.get(f"/api/assistant/reports/{jid}").json()["status"] == "failed"
    with session_scope() as s:
        assert not list(s.scalars(select(AssistantArtifact)))


def test_monthly_requires_explicit_dates_and_freeze_is_detached(app):
    c, _ = app
    _, jid, _ = make_job(c)
    source = c.get(f"/api/assistant/reports/{jid}").json()["snapshot"]["sources"][0]
    arguments = {"template": "monthly", "formats": ["pdf"], "evidence_ids": [1]}
    evidence = [{"id": 1, "tool": "build_report", "arguments": {}}]
    with pytest.raises(ValidationFailed, match="explicit start/end"):
        dr.freeze(arguments, {1: source["report"]}, evidence, "th", "Asia/Bangkok", {"formal-report"})
    evidence[0]["arguments"] = {"start": "2026-10-01", "end": "2026-10-31"}
    frozen = dr.freeze(arguments, {1: source["report"]}, evidence, "th", "Asia/Bangkok", {"formal-report"})
    source["report"]["rows"][0]["value"] = "999"
    assert frozen["sources"][0]["report"]["rows"][0]["value"] == "0.30"


@pytest.mark.skipif(
    os.environ.get("FORMA_TEST_REPORT_DOCKER") != "1", reason="Opt-in, real Office renderer; no API calls"
)
@pytest.mark.parametrize(
    "case",
    [
        "executive_th",
        "monthly_en",
        "presentation_long",
        "incident_empty",
        "process_th",
        "partial_unknown",
        "negative_values",
        "large_decimal",
    ],
)
def test_real_formal_templates(case, tmp_path):
    from importlib.resources import files

    from tests.document_docker import DockerDocumentSandbox

    from forma.code_sandbox.client import Input
    from forma.services.assistant_reports import column, report, text

    template = case.split("_")[0]
    template = template if template in {t["id"] for t in catalog()["templates"]} else "executive"
    language = "en" if case.endswith("en") else "th"
    rows = [{"label": "ประเทศไทย", "value": "12345.67"}, {"label": "Malaysia", "value": "0.30"}]
    if case == "presentation_long":
        rows = [{"label": f"สินค้าทดสอบชื่อยาวที่อ่านได้ชัดเจน รายการ {i}", "value": f"{i}.25"} for i in range(1, 18)]
    elif case == "incident_empty":
        rows = []
    elif case == "partial_unknown":
        rows[0]["value"] = None
    elif case == "negative_values":
        rows[0]["value"] = "-125.50"
    elif case == "large_decimal":
        rows[0]["value"] = "12345678901234567890.123456789"
    r = report(
        text("Measured results", "ผลการดำเนินงานที่ตรวจวัดได้"),
        [
            column("label", "Market / product", "ตลาด / สินค้า", "text"),
            column("value", "Net amount (THB)", "ยอดสุทธิ (THB)"),
        ],
        rows,
        ["value"],
        text("Synthetic fixture · THB · October 2026", "ข้อมูลจำลองสำหรับทดสอบ · THB · ตุลาคม 2026"),
        [
            text(
                "Values are synthetic; do not use for business decisions.",
                "ตัวเลขนี้เป็นข้อมูลจำลอง ไม่ใช้ตัดสินใจทางธุรกิจ",
            )
        ],
        [],
        complete=case != "partial_unknown",
    )["report"]
    args_ = {"template": template, "formats": ["docx", "pptx", "pdf"], "evidence_ids": [1]}
    evidence = [
        {
            "id": 1,
            "tool": "build_report",
            "arguments": {
                "report": "upload_trend",
                "start": "2026-10-01",
                "end": "2026-10-31",
                "timezone": "Asia/Bangkok",
            },
        }
    ]
    if case == "partial_unknown":
        with pytest.raises(ValidationFailed, match="preview"):
            dr.freeze(args_, {1: r}, evidence, language, "Asia/Bangkok", {"formal-report"})
        return  # Partial evidence must be rejected before entering the renderer.
    spec = dr.freeze(
        args_, {1: r}, evidence, language, "Asia/Bangkok", {"formal-report", "presentation", "process-flow"}
    )
    if template == "process":
        spec["flow"] = {
            "id": "flow_fixture",
            "version": 1,
            "content": {
                "title": "ขั้นตอนตรวจข้อมูลก่อนส่งออก",
                "nodes": [
                    {"id": "a", "label": "อัปโหลดไฟล์"},
                    {"id": "b", "label": "ตรวจและจับคู่ข้อมูล"},
                    {"id": "c", "label": "ผู้ใช้ยืนยันคำตอบ"},
                    {"id": "d", "label": "ดาวน์โหลดผลลัพธ์"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "c", "label": "ต้องยืนยัน"},
                    {"from": "b", "to": "d", "label": "ตรวจผ่าน"},
                    {"from": "c", "to": "b", "label": "ตรวจอีกครั้ง", "back": True},
                ],
            },
        }
    spec["snapshot_id"] = digest(spec)
    raw = json.dumps(spec, ensure_ascii=False).encode()
    docker = DockerDocumentSandbox()
    # Keep each case's previews separate for human visual inspection.
    docker.calls = [
        "executive_th",
        "monthly_en",
        "presentation_long",
        "incident_empty",
        "process_th",
        "partial_unknown",
        "negative_values",
        "large_decimal",
    ].index(case) + 10
    result = docker.run(
        files("forma.reporting").joinpath("render.py").read_text(encoding="utf-8"),
        [Input("report.json", len(raw), lambda: io.BytesIO(raw))],
        240,
    )
    assert result.status == "ok", result.stderr
    assert not result.omitted
    receipt = json.loads(dict(result.files)["quality.json"])
    assert receipt["visual_review"] == "human_required"
    for file in receipt["files"].values():
        assert file["table_cells"] == "exact_snapshot_match"
        assert file["pages"] == file["rendered_pages"]
