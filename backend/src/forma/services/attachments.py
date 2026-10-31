"""Files in an assistant chat (ADR-048, ADR-049): what the user attaches and what the code sandbox produces.

They live in object storage under the chat, never in a dataset: nothing here can become a record, a mapping or
an export. A file is only ever reached through a chat its owner can open.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from forma.ai.llm import ImagePart
from forma.auth import Principal
from forma.core.errors import ValidationFailed
from forma.core.ids import new_id
from forma.db.models import AssistantAttachment as Attachment
from forma.services.context import ServiceContext
from forma.services.rounds import audit

_TEXT = "text/plain"
DATA_TYPES = {
    # tables
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".parquet": "application/vnd.apache.parquet",
    ".sqlite": "application/vnd.sqlite3",
    ".db": "application/vnd.sqlite3",
    # documents and slides
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".epub": "application/epub+zip",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
    # text, markup and code
    ".txt": _TEXT,
    ".log": _TEXT,
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": _TEXT,
    ".ini": _TEXT,
    ".sql": _TEXT,
    ".py": _TEXT,
    ".js": _TEXT,
    ".ts": _TEXT,
    # pictures a run opens; a model reads only PNG, JPEG and WEBP itself
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".svg": "image/svg+xml",
    # archives
    ".zip": "application/zip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
    ".tar": "application/x-tar",
    ".gz": "application/gzip",
    ".tgz": "application/gzip",
}
OUTPUT_TYPES = {
    **DATA_TYPES,
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
VIEWABLE = {"image/png", "image/jpeg", "image/webp"}  # shown in the page; everything else downloads
MAX_PER_MESSAGE = 5
MAX_CONTEXT_IMAGES = 4  # most recent images a model sees
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")


def image_type(data: bytes) -> str | None:
    """From the bytes: a file's name and declared type come from the sender."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_name(name: str) -> str:
    """A display name in any language, never a path. Thai vowels and tone marks are not word characters."""
    base = _UNSAFE_NAME.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    return base[-120:] or "file"


def view(row: Attachment) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "media_type": row.media_type,
        "size": row.size,
        "kind": row.kind,
    }


def _store(
    ctx: ServiceContext,
    session: Session,
    chat_id: str,
    turn_id: str | None,
    kind: str,
    name: str,
    media_type: str,
    data: bytes,
    actor: str,
) -> Attachment:
    """The object first, the row last: a row always points at a whole file."""
    file_id = new_id("att")
    key = f"assistant/{chat_id}/{file_id}{Path(name).suffix.lower()}"
    ctx.storage.put(key, data)
    row = Attachment(
        id=file_id,
        conversation_id=chat_id,
        turn_id=turn_id,
        kind=kind,
        name=name,
        media_type=media_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
        created_by=actor,
    )
    session.add(row)
    return row


def add_upload(
    ctx: ServiceContext, session: Session, p: Principal, chat_id: str, name: str, data: bytes
) -> dict[str, Any]:
    clean = safe_name(name)
    media_type = image_type(data)
    limit = ctx.settings.assistant_max_image_mb if media_type else ctx.settings.assistant_max_file_mb
    if media_type is None:
        media_type = DATA_TYPES.get(Path(clean).suffix.lower())
        if media_type is None:
            raise ValidationFailed(
                f"FORMA cannot open {Path(clean).suffix or 'this kind of'} files in a chat"
            )
    if not data:
        raise ValidationFailed("This file is empty")
    if len(data) > limit * 1024 * 1024:
        raise ValidationFailed(f"This file is larger than {limit} MB")
    row = _store(ctx, session, chat_id, None, "upload", clean, media_type, data, p.actor)
    audit(session, "assistant.attach", "conversation", chat_id, actor=p.actor, file=row.id, sha256=row.sha256)
    session.commit()
    return view(row)


def add_output(
    ctx: ServiceContext, session: Session, p: Principal, chat_id: str, turn_id: str, name: str, data: bytes
) -> dict[str, Any]:
    clean = safe_name(name)
    media_type = OUTPUT_TYPES.get(Path(clean).suffix.lower(), "application/octet-stream")
    return view(_store(ctx, session, chat_id, turn_id, "output", clean, media_type, data, p.actor))


def add_preview(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    chat_id: str,
    turn_id: str,
    name: str,
    media_type: str,
    data: bytes,
) -> dict[str, Any]:
    """A picture of a work's page. Its own kind, so a run never receives one as an earlier file."""
    return view(_store(ctx, session, chat_id, turn_id, "preview", name, media_type, data, p.actor))


def check(session: Session, chat_id: str, ids: list[str], turn_id: str | None = None) -> list[Attachment]:
    """The files this message may carry: uploads of this chat that no other message has used."""
    if len(ids) > MAX_PER_MESSAGE:
        raise ValidationFailed(f"Attach at most {MAX_PER_MESSAGE} files to one message")
    rows = session.scalars(
        select(Attachment).where(
            Attachment.id.in_(ids), Attachment.conversation_id == chat_id, Attachment.kind == "upload"
        )
    ).all()
    if len(rows) != len(set(ids)) or any(r.turn_id not in (None, turn_id) for r in rows):
        raise ValidationFailed("One of these files is not available in this chat")
    return list(rows)


def claim(session: Session, chat_id: str, turn_id: str, ids: list[str]) -> list[dict[str, Any]]:
    rows = check(session, chat_id, ids, turn_id)
    for row in rows:
        row.turn_id = turn_id
    return [view(r) for r in rows]


def listing(session: Session, chat_id: str, kind: str) -> list[Attachment]:
    """Oldest first; files not sent with any message yet are left out."""
    return list(
        session.scalars(
            select(Attachment)
            .where(
                Attachment.conversation_id == chat_id,
                Attachment.kind == kind,
                Attachment.turn_id.is_not(None),
            )
            .order_by(Attachment.created_at, Attachment.id)
        ).all()
    )


def recent_images(ctx: ServiceContext, session: Session, chat_id: str) -> list[ImagePart]:
    rows = [r for r in listing(session, chat_id, "upload") if r.media_type in VIEWABLE]
    return [ImagePart(r.media_type, ctx.storage.get(r.storage_key)) for r in rows[-MAX_CONTEXT_IMAGES:]]


def delete_rows(session: Session, chat_id: str) -> list[str]:
    """The rows go first and the objects after the commit, so no row is ever left pointing at nothing."""
    keys = list(
        session.scalars(select(Attachment.storage_key).where(Attachment.conversation_id == chat_id)).all()
    )
    session.execute(delete(Attachment).where(Attachment.conversation_id == chat_id))
    return keys


def delete_objects(ctx: ServiceContext, keys: list[str]) -> None:
    for key in keys:
        ctx.storage.delete(key)
