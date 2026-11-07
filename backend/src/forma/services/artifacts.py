"""What the assistant makes in a chat: a flow or a file, with every version kept (ADR-050).

A new request or bringing an old version back adds the next number, and what came before stays. One message makes at
most one version of a work: the drafts a model throws away while it checks itself never reach the reader.

Previews are made in the sandbox, because a file a model wrote is untrusted and only that container opens one.
A flow is data that FORMA draws, so it needs no sandbox and runs nothing.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.code_sandbox.client import Input, RunResult, SandboxUnavailable
from forma.core.errors import NotFound, ValidationFailed
from forma.core.logging import get_logger
from forma.db.models import AssistantArtifact as Artifact
from forma.db.models import AssistantArtifactVersion as Version
from forma.db.models import AssistantAttachment as Attachment
from forma.db.models import AssistantConversation as Chat
from forma.db.models import DatasetVersion, SourceFile, utcnow
from forma.services import attachments, code_sandbox
from forma.services.context import ServiceContext
from forma.services.rounds import audit

log = get_logger(__name__)

PREVIEW_TIMEOUT_S = 60.0
MAX_PAGES = 30
NO_DATA_TOOLS = {
    "feature_guide",
    "conversation_history",
    "save_flow",
    "run_python",
    "remember",
    "search_chats",
    "recent_chats",
}
_SUFFIXES = {
    "deck": (".pptx", ".ppt", ".odp"),
    "doc": (".pdf", ".docx", ".doc", ".odt", ".rtf"),
    "sheet": (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods", ".csv", ".tsv"),
    "web": (".html", ".htm"),
    "image": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"),
    "text": (".txt", ".md", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".log", ".sql", ".py", ".js", ".ts"),
}
KIND_OF = {suffix: kind for kind, suffixes in _SUFFIXES.items() for suffix in suffixes}

FLOW_TOOL: dict[str, Any] = {
    "name": "save_flow",
    "description": (
        "Save a flow, process map, org chart or any other boxes and arrows. FORMA draws it beside the chat, where "
        "the user can open it, pick a box and download it. To change a flow, send the whole new flow with its "
        "artifact_id. tone: plain, question, problem or done. back: true for an arrow that goes back to an earlier "
        "step. direction: down for long flows, right for short ones. note: what this version changed, in the "
        "user's language, under 80 characters."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "maxLength": 40},
            "title": {"type": "string", "minLength": 1, "maxLength": 120},
            "note": {"type": "string", "maxLength": 120},
            "direction": {"enum": ["down", "right"]},
            "nodes": {
                "type": "array",
                "minItems": 2,
                "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1, "maxLength": 40},
                        "label": {"type": "string", "minLength": 1, "maxLength": 80},
                        "detail": {"type": "string", "maxLength": 160},
                        "tone": {"enum": ["plain", "question", "problem", "done"]},
                    },
                    "required": ["id", "label"],
                    "additionalProperties": False,
                },
            },
            "edges": {
                "type": "array",
                "maxItems": 120,
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "maxLength": 40},
                        "to": {"type": "string", "maxLength": 40},
                        "label": {"type": "string", "maxLength": 40},
                        "back": {"type": "boolean"},
                    },
                    "required": ["from", "to"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "nodes", "edges"],
        "additionalProperties": False,
    },
}
NOTE = (
    "save_flow is how you draw a flow, process, org chart or other boxes and arrows: never answer such a request "
    "in prose. The input working_on, when present, is the work the user has open and target the part they picked: "
    "change that work and keep everything else as it is. Say in one sentence what you made; the work itself is "
    "shown beside the chat, so do not repeat its content. "
)

_PAGES_CODE = f"""
import glob, shutil, subprocess

source = glob.glob("inputs/preview/*")[0]
pdf = source
if not source.lower().endswith(".pdf"):
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", "tmp", source],
        check=True, capture_output=True, timeout=45,
    )
    pdf = glob.glob("tmp/*.pdf")[0]
subprocess.run(
    ["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "{MAX_PAGES}", pdf, "outputs/page"],
    check=True, capture_output=True, timeout=30,
)
shutil.copy(pdf, "outputs/document.pdf")
"""
_SHEET_CODE = """
import csv, datetime, glob, json, subprocess
from pathlib import Path
import openpyxl

MAX_SHEETS, MAX_ROWS, MAX_COLUMNS = 8, 200, 30


def cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime.datetime):
        return value.isoformat(" ", "minutes")
    return str(value)


source = Path(glob.glob("inputs/preview/*")[0])
sheets = []
if source.suffix.lower() in (".csv", ".tsv"):
    data = source.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp874", "replace")
    rows = list(csv.reader(text.splitlines(), delimiter="\\t" if source.suffix.lower() == ".tsv" else ","))
    sheets.append({
        "name": source.stem,
        "rows": [row[:MAX_COLUMNS] for row in rows[:MAX_ROWS]],
        "formulas": {},
        "total_rows": len(rows),
        "total_columns": max(map(len, rows), default=0),
    })
else:
    # LibreOffice opens every spreadsheet format and fills in the values of formulas nothing has calculated yet.
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", "tmp", str(source)],
        check=True, capture_output=True, timeout=45,
    )
    calculated = next(Path("tmp").glob("*.xlsx"))
    values, formulas = openpyxl.load_workbook(calculated, data_only=True), openpyxl.load_workbook(calculated)
    for sheet in values.worksheets[:MAX_SHEETS]:
        found = {}
        for row in formulas[sheet.title].iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS):
            for item in row:
                if isinstance(item.value, str) and item.value.startswith("="):
                    found[item.coordinate] = item.value
        sheets.append({
            "name": sheet.title,
            "rows": [
                [cell(v) for v in row]
                for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS, values_only=True)
            ],
            "formulas": found,
            "total_rows": sheet.max_row,
            "total_columns": sheet.max_column,
        })
Path("outputs/preview.json").write_text(json.dumps({"sheets": sheets}, ensure_ascii=False), encoding="utf-8")
"""


def data_mark(session: Session, p: Principal) -> dict[str, Any]:
    """What a work's numbers rest on: when this differs from the mark saved with it, the data has moved on."""

    def scoped(query: Any, column: Any) -> Any:
        return query if p.customers is None else query.where(column.in_(sorted(p.customers)))

    files = session.execute(
        scoped(select(func.count(SourceFile.id), func.max(SourceFile.created_at)), SourceFile.customer_id)
    ).one()
    datasets = session.execute(
        scoped(
            select(func.count(DatasetVersion.id), func.max(DatasetVersion.created_at)),
            DatasetVersion.customer_id,
        )
    ).one()
    return {
        "files": files[0],
        "last_file": str(files[1]),
        "datasets": datasets[0],
        "last_dataset": str(datasets[1]),
    }


def _source(session: Session, p: Principal, uses_data: bool, before: Version | None = None) -> dict[str, Any]:
    """A version that read no data of its own still carries the numbers of the one before it."""
    if before and before.source and before.source.get("uses_data") and not uses_data:
        return before.source
    return {
        "generated_at": utcnow().isoformat(),
        "uses_data": uses_data,
        "mark": data_mark(session, p) if uses_data else None,
    }


def _title(name: str) -> str:
    return Path(name).stem.replace("_", " ").replace("-", " ").strip()[:160] or name


def _latest(session: Session, artifact: Artifact) -> Version:
    version = session.scalar(
        select(Version).where(Version.artifact_id == artifact.id, Version.number == artifact.latest)
    )
    assert version is not None
    return version


def save_flow(
    session: Session, p: Principal, chat_id: str, turn_id: str, args: dict[str, Any], uses_data: bool
) -> dict[str, Any]:
    """The model's flow, checked as data: it runs nothing, so a flow works with the sandbox switched off."""
    errors = list(Draft202012Validator(FLOW_TOOL["inputSchema"]).iter_errors(args))
    if errors:
        raise ValidationFailed("Invalid flow: " + errors[0].message[:200])
    ids = [node["id"] for node in args["nodes"]]
    if len(set(ids)) != len(ids):
        raise ValidationFailed("Every node in a flow needs its own id")
    unknown = sorted({e[side] for e in args["edges"] for side in ("from", "to")} - set(ids))
    if unknown:
        raise ValidationFailed(f"These edges point at nodes that do not exist: {', '.join(unknown[:5])}")
    content = {key: args[key] for key in ("nodes", "edges")} | {"direction": args.get("direction", "down")}
    if args.get("artifact_id"):
        artifact = session.get(Artifact, args["artifact_id"])
        if artifact is None or artifact.conversation_id != chat_id or artifact.kind != "flow":
            raise ValidationFailed(
                "That artifact_id is not a flow in this chat. Leave it out to start a new one."
            )
        artifact.title = args["title"]
    else:
        artifact = Artifact(
            conversation_id=chat_id, kind="flow", title=args["title"], name="", latest=0, created_by=p.actor
        )
        session.add(artifact)
        session.flush()
    latest = _latest(session, artifact) if artifact.latest else None
    if latest and latest.turn_id == turn_id:  # the same message: a draft nobody has seen
        latest.content, latest.note = content, args.get("note", "")
    else:
        artifact.latest += 1
        session.add(
            Version(
                artifact_id=artifact.id,
                number=artifact.latest,
                parent_id=latest.id if latest else None,
                turn_id=turn_id,
                note=args.get("note", ""),
                content=content,
                code=json.dumps(content, ensure_ascii=False, indent=2),
                preview={"status": "none"},
                source=_source(session, p, uses_data, latest),
                created_by=p.actor,
            )
        )
    artifact.updated_at = utcnow()
    audit(session, "assistant.artifact", "artifact", artifact.id, actor=p.actor, version=artifact.latest)
    if not session.info.get("assistant_atomic_tool"):
        session.commit()
    return {"artifact_id": artifact.id, "version": artifact.latest, "title": artifact.title, "saved": True}


def _render(
    ctx: ServiceContext, session: Session, p: Principal, row: Attachment, kind: str, turn_id: str
) -> dict[str, Any]:
    """A picture of each page, or the cells of each sheet. A work without a preview can still be downloaded."""
    code = {"deck": _PAGES_CODE, "doc": _PAGES_CODE, "sheet": _SHEET_CODE}.get(kind)
    if code is None:
        return {"status": "none"}
    runner = code_sandbox.get_runner(ctx.settings)
    if runner is None:
        return {"status": "failed"}
    source = Input(
        f"preview/file{Path(row.name).suffix.lower()}", row.size, lambda: ctx.storage.open(row.storage_key)
    )
    try:
        result: RunResult = runner.run(code, [source], PREVIEW_TIMEOUT_S)
    except SandboxUnavailable:
        return {"status": "failed"}
    files = dict(result.files)
    if result.status != "ok":
        log.warning("artifact preview failed", kind=kind, status=result.status)
        return {"status": "failed"}
    if kind == "sheet":
        try:
            return {"status": "ready", **json.loads(files["preview.json"])}
        except (KeyError, ValueError):
            return {"status": "failed"}

    def keep(name: str, media_type: str) -> str:
        stored = attachments.add_preview(
            ctx, session, p, row.conversation_id, turn_id, name, media_type, files[name]
        )
        return str(stored["id"])

    pages = sorted(name for name in files if name.endswith(".png"))
    if not pages:
        return {"status": "failed"}
    return {
        "status": "ready",
        "pages": [keep(name, "image/png") for name in pages],
        "pdf": keep("document.pdf", "application/pdf") if "document.pdf" in files else None,
    }


def _add_file(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    row: Attachment,
    run: dict[str, Any],
    turn_id: str,
    checked: int,
    uses_data: bool,
) -> None:
    """The same name in the same chat is the next version of that work: that is how a model revises a file."""
    artifact = session.scalar(
        select(Artifact).where(Artifact.conversation_id == row.conversation_id, Artifact.name == row.name)
    )
    latest = _latest(session, artifact) if artifact else None
    if latest and latest.attachment_id:
        previous = session.get(Attachment, latest.attachment_id)
        if previous and previous.sha256 == row.sha256:
            return
    if artifact is None:
        kind = KIND_OF.get(Path(row.name).suffix.lower(), "file")
        artifact = Artifact(
            conversation_id=row.conversation_id,
            kind=kind,
            title=_title(row.name),
            name=row.name,
            latest=0,
            created_by=p.actor,
        )
        session.add(artifact)
        session.flush()
    artifact.latest += 1
    artifact.updated_at = utcnow()
    session.add(
        Version(
            artifact_id=artifact.id,
            number=artifact.latest,
            parent_id=latest.id if latest else None,
            turn_id=turn_id,
            note=str(run.get("note", "")),
            attachment_id=row.id,
            code=run["code"],
            preview=_render(ctx, session, p, row, artifact.kind, turn_id),
            checked=checked,
            source=_source(session, p, uses_data, latest),
            created_by=p.actor,
        )
    )
    audit(session, "assistant.artifact", "artifact", artifact.id, actor=p.actor, version=artifact.latest)


def settle_turn(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    chat_id: str,
    turn_id: str,
    reply: dict[str, Any],
) -> list[dict[str, Any]]:
    """Files become works once a message is finished, so a run the model redid leaves no version behind."""
    runs: Sequence[dict[str, Any]] = [r for r in reply.get("code_runs", []) if r.get("status") == "ok"]
    last_run = {file["id"]: run for run in runs for file in run["files"]}
    newest: dict[str, str] = {}  # name -> attachment id, the last save of each name
    for run in runs:
        newest.update({file["name"]: file["id"] for file in run["files"]})
    tools = set(reply.get("tools_called", [])) - NO_DATA_TOOLS
    uses_data = bool(tools) or any(
        path.startswith(("datasets/", "sources/")) for run in runs for path in run.get("inputs", [])
    )
    for file_id in newest.values():
        row = session.get(Attachment, file_id)
        if row is None or row.conversation_id != chat_id:
            continue
        try:
            run = last_run[file_id]
            _add_file(ctx, session, p, row, run, turn_id, int(run.get("looked_at", 0)), uses_data)
            session.commit()
        except Exception as error:  # the answer still arrives; the file stays downloadable from its run
            session.rollback()
            log.warning("artifact not saved", error=type(error).__name__)
    return refs_for_turn(session, turn_id)


def refs_for_turn(session: Session, turn_id: str) -> list[dict[str, Any]]:
    return refs_for_turns(session, [turn_id]).get(turn_id, [])


def refs_for_turns(
    session: Session, turn_ids: list[str], chat_id: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Canonical output references, including legacy turns, without rewriting their saved receipts."""
    query = (
        select(Artifact, Version, Attachment)
        .join(Version, Version.artifact_id == Artifact.id)
        .outerjoin(
            Attachment,
            (Attachment.id == Version.attachment_id)
            & (Attachment.conversation_id == Artifact.conversation_id),
        )
        .where(Version.turn_id.in_(turn_ids))
        .order_by(Version.created_at)
    )
    if chat_id is not None:
        query = query.where(Artifact.conversation_id == chat_id)
    result: dict[str, list[dict[str, Any]]] = {}
    for a, v, file in session.execute(query):
        if v.turn_id is None:
            continue
        result.setdefault(v.turn_id, []).append(
            {
                "id": a.id,
                "kind": a.kind,
                "title": a.title,
                "version": v.number,
                "note": v.note,
                "file": attachments.view(file) if file else None,
            }
        )
    return result


def find(session: Session, artifact_id: str) -> Artifact:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise NotFound("Work not found")
    return artifact


def summary(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "conversation_id": artifact.conversation_id,
        "kind": artifact.kind,
        "title": artifact.title,
        "name": artifact.name,
        "latest": artifact.latest,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }


def _version_view(session: Session, version: Version) -> dict[str, Any]:
    row = session.get(Attachment, version.attachment_id) if version.attachment_id else None
    source = version.source or {}
    return {
        "number": version.number,
        "note": version.note,
        "restored_from": version.restored_from,
        "file": attachments.view(row) if row else None,
        "content": version.content,
        "preview": version.preview,
        "code": version.code,
        "checked": version.checked,
        "generated_at": source.get("generated_at"),
        "uses_data": bool(source.get("uses_data")),
        "report": source.get("report"),
        "created_at": version.created_at,
    }


def detail(session: Session, p: Principal, artifact: Artifact) -> dict[str, Any]:
    versions = session.scalars(
        select(Version).where(Version.artifact_id == artifact.id).order_by(Version.number)
    ).all()
    source = (versions[-1].source or {}) if versions else {}
    stale = bool(source.get("uses_data")) and source.get("mark") != data_mark(session, p)
    return {**summary(artifact), "stale": stale, "versions": [_version_view(session, v) for v in versions]}


def library(session: Session, chats: Select[Any], limit: int = 200) -> list[dict[str, Any]]:
    """Every work in the chats this account can open, newest change first."""
    rows = session.execute(
        select(Artifact, Chat.title)
        .join(Chat, Chat.id == Artifact.conversation_id)
        .where(Chat.id.in_(chats.with_only_columns(Chat.id)))
        .order_by(Artifact.updated_at.desc())
        .limit(limit)
    ).all()
    return [{**summary(artifact), "chat_title": title} for artifact, title in rows]


def restore(
    ctx: ServiceContext, session: Session, p: Principal, artifact: Artifact, number: int
) -> dict[str, Any]:
    """An old version comes back as the next number: what happened in between stays in the history."""
    old = session.scalar(select(Version).where(Version.artifact_id == artifact.id, Version.number == number))
    if old is None:
        raise NotFound("Version not found")
    latest = _latest(session, artifact)
    if old.number == latest.number:
        raise ValidationFailed("This is already the latest version")
    attachment_id = old.attachment_id
    row = session.get(Attachment, attachment_id) if attachment_id else None
    if row is not None:
        # A new output, so the next run finds this version under inputs/previous/.
        stored = attachments.add_output(
            ctx,
            session,
            p,
            artifact.conversation_id,
            row.turn_id or "",
            row.name,
            ctx.storage.get(row.storage_key),
        )
        attachment_id = stored["id"]
    artifact.latest += 1
    artifact.updated_at = utcnow()
    session.add(
        Version(
            artifact_id=artifact.id,
            number=artifact.latest,
            parent_id=latest.id,
            restored_from=old.number,
            note="",
            attachment_id=attachment_id,
            content=old.content,
            code=old.code,
            preview=old.preview,
            checked=old.checked,
            source=old.source,
            created_by=p.actor,
        )
    )
    audit(session, "assistant.artifact_restore", "artifact", artifact.id, actor=p.actor, version=number)
    session.commit()
    return detail(session, p, artifact)


def working_on(session: Session, chat_id: str, artifact_id: str, target: str) -> dict[str, Any]:
    """The open work, for the model. It belongs to this chat or the message is refused."""
    artifact = find(session, artifact_id)
    if artifact.conversation_id != chat_id:
        raise NotFound("Work not found")
    latest = _latest(session, artifact)
    context: dict[str, Any] = {
        "artifact_id": artifact.id,
        "title": artifact.title,
        "kind": artifact.kind,
        "version": artifact.latest,
        "target": target,
    }
    if artifact.kind == "flow":
        return {**context, "flow": latest.content}
    return {**context, "file": artifact.name, "code_of_this_version": (latest.code or "")[:6000]}


def delete_rows(session: Session, chat_id: str) -> None:
    ids = select(Artifact.id).where(Artifact.conversation_id == chat_id)
    session.execute(delete(Version).where(Version.artifact_id.in_(ids)))
    session.execute(delete(Artifact).where(Artifact.conversation_id == chat_id))
