"""Formal reports: freeze trusted evidence, queue rendering, publish an atomic reviewed pack."""

from __future__ import annotations

import io
import json
import zipfile
from importlib.resources import files
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.code_sandbox.client import Input, SocketRunner
from forma.core.errors import NotFound, ValidationFailed
from forma.db.models import (
    AssistantArtifact,
    AssistantArtifactVersion,
    AssistantTurn,
    Job,
    utcnow,
)
from forma.db.session import session_scope
from forma.jobs import runner
from forma.jobs.execution import check
from forma.reporting.registry import catalog, digest, skill
from forma.services import artifacts, attachments, code_sandbox, conversations
from forma.services.context import ServiceContext
from forma.services.rounds import audit

KIND = "create_document"
CATALOG_TOOL = {
    "name": "report_templates",
    "description": "List approved formal report templates and skills. No model calls or data mutations.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}
SKILL_TOOL = {
    "name": "read_report_skill",
    "description": "Load the approved skill before creating a formal report, presentation or SOP.",
    "inputSchema": {
        "type": "object",
        "properties": {"skill": {"enum": [s["id"] for s in catalog()["skills"]]}},
        "required": ["skill"],
        "additionalProperties": False,
    },
}
CREATE_TOOL = {
    "name": KIND,
    "description": "Queue formal Word/PowerPoint/PDF files from build_report evidence in THIS turn, or an exact saved flow version. Load the matching report skill first. Never provide numbers: the server freezes the computed evidence. A receipt means queued, not finished. Users can view progress, stop/retry and open outputs in the chat.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "template": {"enum": [t["id"] for t in catalog()["templates"]]},
            "formats": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "items": {"enum": ["docx", "pptx", "pdf"]},
            },
            "evidence_ids": {
                "type": "array",
                "maxItems": 6,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 1},
            },
            "flow_artifact_id": {"type": "string", "maxLength": 40},
            "flow_version": {"type": "integer", "minimum": 1},
            "revise_artifact_id": {"type": "string", "maxLength": 40},
        },
        "required": ["template", "formats"],
        "additionalProperties": False,
    },
}
TOOLS = [CATALOG_TOOL, SKILL_TOOL, CREATE_TOOL]
NOTE = (
    "For formal executive/monthly/incident reports, presentations and SOP documents, call report_templates, "
    "read_report_skill, then build_report for grounded metrics (or save_flow for a process), and create_document. "
    "Ask about missing period, customer scope, units, or factual causes; do not guess. create_document queues "
    "a separate worker without an LLM. Do not duplicate it with run_python. The job card shows progress and downloads. "
    "Call it at most once per user turn. It accepts only evidence from this turn, not copied numbers. "
    "For custom content outside these templates use run_python, clearly stating that it lacks the formal numerical gate. "
    "To revise a formal report, pass revise_artifact_id from working_on, retaining the original chat. "
)


def validate(tool: dict[str, Any], args: dict[str, Any]) -> None:
    errors = list(Draft202012Validator(tool["inputSchema"]).iter_errors(args))
    if errors:
        raise ValidationFailed(errors[0].message[:250])


def freeze(
    args: dict[str, Any],
    reports: dict[int, dict[str, Any]],
    evidence: list[dict[str, Any]],
    language: str,
    timezone: str,
    loaded: set[str],
) -> dict[str, Any]:
    validate(CREATE_TOOL, args)
    required = {"process": "process-flow", "presentation": "presentation"}.get(
        args["template"], "formal-report"
    )
    if required not in loaded:
        raise ValidationFailed(f"Load read_report_skill({required}) first")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationFailed("Specify a valid time zone") from exc
    sources = []
    for eid in args.get("evidence_ids", []):
        report = reports.get(eid)
        source = next((e for e in evidence if e["id"] == eid and e["tool"] == "build_report"), None)
        if report is None or source is None:
            raise ValidationFailed("Only current build_report evidence can supply formal report numbers")
        if len(report["rows"]) > 500 or len(report["columns"]) > 25:
            raise ValidationFailed("Choose a narrower report; no rows will be silently removed")
        sources.append({"report": report, "query": source["arguments"], "sha256": digest(report)})
    if not sources and args["template"] != "process":
        raise ValidationFailed("Build a scoped data report first; ask the user if scope is unclear")
    if args["template"] == "monthly" and not all(
        s["query"].get("start") and s["query"].get("end") for s in sources
    ):
        raise ValidationFailed(
            "Monthly reports need explicit start/end dates; use upload_trend or clarify the intended report"
        )
    c = catalog()
    template = next(t for t in c["templates"] if t["id"] == args["template"])
    result = {
        "contract_version": 1,
        "template": template["id"],
        "template_version": c["version"],
        "brand": c["brand"],
        "title": template["name"]
        if language == "th"
        else template["id"].replace("-", " ").title() + " report",
        "language": language,
        "timezone": timezone,
        "created_at": utcnow().isoformat(),
        "formats": args["formats"],
        "sources": sources,
        "skills": [skill(required), skill("quality-check")],
        "flow": None,
        "renderer_sha256": digest(files("forma.reporting").joinpath("render.py").read_text(encoding="utf-8")),
    }
    # Detach from ORM/model-owned references before persisting.
    return cast(dict[str, Any], json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False)))


def create(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    chat_id: str,
    turn_id: str,
    args: dict[str, Any],
    reports: dict[int, dict[str, Any]],
    evidence: list[dict[str, Any]],
    loaded: set[str],
    language: str,
    timezone: str,
) -> dict[str, Any]:
    chat = conversations.get_chat(session, p, chat_id)
    if not session.scalar(
        update(type(chat))
        .where(type(chat).id == chat_id)
        .values(updated_at=type(chat).updated_at)
        .returning(type(chat).id)
    ):
        raise NotFound("Conversation not found")
    turn = session.get(AssistantTurn, turn_id)
    if not turn or turn.conversation_id != chat_id:
        raise NotFound("Message not found")
    # Serialize creation per turn: retries / duplicate tool calls cannot enqueue another pack.
    session.execute(
        update(AssistantTurn).where(AssistantTurn.id == turn_id).values(status=AssistantTurn.status)
    )
    existing = session.scalar(
        select(Job).where(Job.kind == KIND, Job.payload["turn_id"].as_string() == turn_id)
    )
    if existing:
        return view(existing)
    if not (ctx.settings.report_sandbox_socket or code_sandbox.get_runner(ctx.settings)):
        raise ValidationFailed("The report sandbox is not configured")
    spec = freeze(args, reports, evidence, language, timezone, loaded)
    if args["template"] == "process":
        item = artifacts.find(session, args.get("flow_artifact_id", ""))
        conversations.get_chat(session, p, item.conversation_id)
        version = session.scalar(
            select(AssistantArtifactVersion).where(
                AssistantArtifactVersion.artifact_id == item.id,
                AssistantArtifactVersion.number == args.get("flow_version"),
            )
        )
        if item.kind != "flow" or version is None or not version.content:
            raise ValidationFailed("Select a saved flow and its exact version")
        spec["flow"] = {"id": item.id, "version": version.number, "content": version.content}
    spec["snapshot_id"] = digest(spec)
    if len(json.dumps(spec, ensure_ascii=False).encode()) > 1_000_000:
        raise ValidationFailed("This report is too large; narrow its scope")
    payload = {
        "chat_id": chat_id,
        "turn_id": turn_id,
        "principal": p.to_dict(),
        "snapshot": spec,
        "source_mark": artifacts.data_mark(session, p),
    }
    if args.get("revise_artifact_id"):
        previous = artifacts.find(session, args["revise_artifact_id"])
        if previous.conversation_id != chat_id:
            raise NotFound("Open the report's original chat to revise it")
        version = artifacts._latest(session, previous)
        old_job_id = (version.source or {}).get("report", {}).get("job_id")
        old_job = scoped(session, p, old_job_id) if old_job_id else None
        if old_job is None or old_job.status != "done":
            raise ValidationFailed("Only a completed formal report can be revised with this tool")
        payload["family_id"] = old_job.payload.get("family_id", old_job.id)
    job_id = runner.stage_job(session, KIND, payload, request_id=p.request_id)
    pending = (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == KIND, Job.status.in_(("queued", "running")))
        )
        or 0
    )
    if pending > ctx.settings.report_queue_max:
        session.rollback()
        raise ValidationFailed("Report queue is full; wait for an existing report or stop it first")
    audit(
        session,
        "assistant.report.queued",
        "conversation",
        chat_id,
        actor=p.actor,
        job_id=job_id,
        snapshot_id=spec["snapshot_id"],
        template=spec["template"],
        version=spec["template_version"],
    )
    if not session.info.get("assistant_atomic_tool"):
        session.commit()
        runner.deliver_jobs([job_id])
    session.expire_all()
    job = session.get(Job, job_id)
    assert job is not None
    return view(job)


def scoped(session: Session, p: Principal, job_id: str) -> Job:
    j = session.get(Job, job_id)
    if not j or j.kind != KIND:
        raise NotFound("Report not found")
    conversations.get_chat(session, p, j.payload["chat_id"])
    return j


def view(j: Job) -> dict[str, Any]:
    spec = j.payload["snapshot"]

    def label(value: Any) -> str:
        return (
            str(value.get(spec["language"], value.get("en", ""))) if isinstance(value, dict) else str(value)
        )

    return {
        "id": j.id,
        "title": spec["title"],
        "status": j.status,
        "progress": j.progress,
        "message": j.message,
        "error": "สร้างรายงานไม่สำเร็จ ตรวจรายละเอียดแล้วลองอีกครั้ง" if j.error else None,
        "snapshot_id": spec["snapshot_id"],
        "formats": spec["formats"],
        "template": spec["template"],
        "template_version": spec["template_version"],
        "created_at": j.created_at,
        "sources": [
            {
                "title": label(source["report"]["title"]),
                "scope": label(source["report"]["scope"]),
                "generated_at": source["report"]["generated_at"],
                "complete": source["report"]["complete"],
                "rows": len(source["report"]["rows"]),
            }
            for source in spec["sources"]
        ],
        "artifacts": (j.result or {}).get("artifacts", []),
        "quality": (j.result or {}).get("quality"),
        "files": (j.result or {}).get("files", []),
        "attempts": j.attempts,
    }


@runner.handler(KIND)
def render_report(ctx: ServiceContext, payload: dict[str, Any], prog: runner.JobProgress) -> dict[str, Any]:
    spec = payload["snapshot"]
    if digest({k: v for k, v in spec.items() if k != "snapshot_id"}) != spec["snapshot_id"]:
        raise ValidationFailed("Report snapshot checksum mismatch")
    raw = payload["principal"]
    p = Principal(
        subject=raw["subject"],
        roles=frozenset(raw["roles"]),
        customers=frozenset(raw["customers"]) if raw["customers"] is not None else None,
        name=raw["name"],
        mode=raw["mode"],
    )
    with session_scope() as s:
        conversations.get_chat(s, p, payload["chat_id"])
        j = s.get(Job, prog.job_id)
        published = (j.result or {}).get("stages", {}).get("report_published") if j else None
        if published:
            return cast(dict[str, Any], published["output"])
    prog.update(15, "เตรียมข้อมูลและแม่แบบแล้ว กำลังสร้างเอกสาร")
    sandbox = (
        SocketRunner(ctx.settings.report_sandbox_socket)
        if ctx.settings.report_sandbox_socket
        else code_sandbox.get_runner(ctx.settings)
    )
    if sandbox is None:
        raise ValidationFailed("Report sandbox unavailable")
    data = json.dumps(spec, ensure_ascii=False).encode()
    script = files("forma.reporting").joinpath("render.py").read_text(encoding="utf-8")
    if digest(script) != spec["renderer_sha256"]:
        raise ValidationFailed(
            "The report renderer changed after this request. Create a new report to use the current version."
        )
    result = sandbox.run(
        script, [Input("report.json", len(data), lambda: io.BytesIO(data))], ctx.settings.report_timeout_s
    )
    check()
    prog.update(75, "ตรวจตัวเลขและตัวอย่างทุกหน้า")
    output = dict(result.files)
    if result.status != "ok" or result.omitted:
        raise ValidationFailed("Report rendering failed: " + result.stderr[-1600:])
    quality = json.loads(output["quality.json"])
    if (
        quality.get("status") != "automatic_checks_passed"
        or quality.get("snapshot_id") != spec["snapshot_id"]
    ):
        raise ValidationFailed("Report quality gate failed")
    import hashlib

    for fmt in spec["formats"]:
        name = "report." + fmt
        if hashlib.sha256(output[name]).hexdigest() != quality["files"][name]["sha256"]:
            raise ValidationFailed("Report output checksum mismatch")
    previews = zipfile.ZipFile(io.BytesIO(output["previews.zip"]))
    if sum(i.file_size for i in previews.infolist()) > 200_000_000:
        raise ValidationFailed("Report previews exceed the allowed size")
    with session_scope() as s:
        # Lock the chat against deletion while publishing. Fenced commit rejects stopped/expired jobs.
        chat = conversations.get_chat(s, p, payload["chat_id"])
        if not s.scalar(
            update(type(chat))
            .where(type(chat).id == chat.id)
            .values(updated_at=type(chat).updated_at)
            .returning(type(chat).id)
        ):
            raise NotFound("Conversation not found")
        saved, refs = [], []
        for fmt in spec["formats"]:
            name = f"report-{payload.get('family_id', prog.job_id)}.{fmt}"
            f = attachments.add_output(ctx, s, p, chat.id, payload["turn_id"], name, output["report." + fmt])
            saved.append(f)
            pages = []
            names = sorted(
                (n for n in previews.namelist() if n.startswith(fmt + "/") and n.endswith(".png")),
                key=lambda n: int(n.rsplit("-", 1)[1].split(".")[0]),
            )
            if len(names) != quality["files"]["report." + fmt]["pages"]:
                raise ValidationFailed("Report previews are incomplete")
            for i, n in enumerate(names):
                pages.append(
                    attachments.add_preview(
                        ctx,
                        s,
                        p,
                        chat.id,
                        payload["turn_id"],
                        f"{prog.job_id}-{fmt}-{i}.png",
                        "image/png",
                        previews.read(n),
                    )["id"]
                )
            pdf = attachments.add_preview(
                ctx,
                s,
                p,
                chat.id,
                payload["turn_id"],
                f"{prog.job_id}-{fmt}.pdf",
                "application/pdf",
                previews.read(f"{fmt}/document.pdf"),
            )
            a = s.scalar(
                select(AssistantArtifact).where(
                    AssistantArtifact.conversation_id == chat.id, AssistantArtifact.name == name
                )
            )
            previous_version = artifacts._latest(s, a) if a else None
            if a is None:
                a = AssistantArtifact(
                    conversation_id=chat.id,
                    kind="deck" if fmt == "pptx" else "doc",
                    title=spec["title"],
                    name=name,
                    latest=0,
                    created_by=p.actor,
                )
                s.add(a)
            a.latest += 1
            a.updated_at = utcnow()
            s.flush()
            s.add(
                AssistantArtifactVersion(
                    artifact_id=a.id,
                    number=a.latest,
                    parent_id=previous_version.id if previous_version else None,
                    turn_id=payload["turn_id"],
                    attachment_id=f["id"],
                    note="ตรวจอัตโนมัติแล้ว — โปรดตรวจทานก่อนเผยแพร่",
                    preview={"status": "ready", "pages": pages, "pdf": pdf["id"]},
                    code=None,
                    checked=0,
                    source={
                        **artifacts._source(s, p, bool(spec["sources"])),
                        "generated_at": spec["created_at"],
                        "mark": payload.get("source_mark"),
                        "report": {
                            "job_id": prog.job_id,
                            "snapshot_id": spec["snapshot_id"],
                            "template": spec["template"],
                            "version": spec["template_version"],
                            "quality": quality,
                        },
                    },
                    created_by=p.actor,
                )
            )
            refs.append({"id": a.id, "kind": a.kind, "title": a.title, "version": a.latest, "note": ""})
        for name in ("snapshot.json", "quality.json"):
            saved.append(
                attachments.add_output(
                    ctx, s, p, chat.id, payload["turn_id"], f"{prog.job_id}-{name}", output[name]
                )
            )
        outcome = {"artifacts": refs, "files": saved, "quality": quality, "snapshot_id": spec["snapshot_id"]}
        prog.mark_in(s, "report_published", output=outcome)
        audit(
            s,
            "assistant.report.completed",
            "conversation",
            chat.id,
            actor=p.actor,
            job_id=prog.job_id,
            snapshot_id=spec["snapshot_id"],
            artifacts=[r["id"] for r in refs],
        )
    prog.update(100, "สร้างรายงานแล้ว โปรดตรวจทานก่อนเผยแพร่")
    return outcome
