"""Durable chat history. Ownership and current access scope are checked on every read/write."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forma.ai.text_stream import AnswerStream, StreamCancelled
from forma.auth import Principal, ensure_customer
from forma.core.errors import Conflict, NotFound, ValidationFailed
from forma.db.models import AssistantConversation as Chat
from forma.db.models import AssistantTurn as Turn
from forma.db.models import DatasetVersion, ImportRound, MappingVersion, utcnow
from forma.db.session import session_scope
from forma.services.context import ServiceContext
from forma.services.rounds import audit

ACTIVE_STREAMS: dict[tuple[str, str], AnswerStream] = {}


def stop(session: Session, p: Principal, chat_id: str, request_id: str) -> dict[str, Any]:
    chat = get_chat(session, p, chat_id)
    # Lock in the same order as send/delete. Stop is scoped to this exact request, never a newer one.
    session.execute(update(Chat).where(Chat.id == chat.id).values(updated_at=Chat.updated_at))
    session.expire_all()
    turn = session.scalar(select(Turn).where(Turn.conversation_id == chat_id, Turn.request_id == request_id))
    if turn is None:
        raise NotFound("Message not found")
    if turn.status == "pending":
        turn.status, turn.completed_at = "stopped", utcnow()
        if turn.reply:
            turn.reply = {**turn.reply, "reason": "stopped", "actions": []}
        chat.active_run, chat.busy_until = None, None
        audit(session, "assistant.stop", "conversation", chat_id, actor=p.actor, turn_id=turn.id)
        session.commit()
        control = ACTIVE_STREAMS.get((chat_id, request_id))
        if control:
            control.cancel()
    return turn_view(turn, chat)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def identity(p: Principal) -> tuple[str, str]:
    return _hash([p.mode, p.subject]), _hash(
        [sorted(p.roles), sorted(p.customers) if p.customers is not None else None]
    )


def owned(p: Principal) -> Select[Chat]:
    owner, access = identity(p)
    return select(Chat).where(Chat.owner_key == owner, Chat.access_key == access)


def get_chat(session: Session, p: Principal, chat_id: str) -> Chat:
    chat = session.scalar(owned(p).where(Chat.id == chat_id))
    if chat is None:
        raise NotFound("Conversation not found")
    return chat


def summary(chat: Chat) -> dict[str, Any]:
    return {
        "id": chat.id,
        "title": chat.title,
        "round_id": chat.round_id,
        "dataset_id": chat.dataset_id,
        "mapping_id": chat.mapping_id,
        "archived": chat.archived,
        "pinned": chat.pinned,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "busy": bool(chat.busy_until and chat.busy_until > utcnow()),
    }


def turn_view(turn: Turn, chat: Chat) -> dict[str, Any]:
    state = turn.status
    if state == "pending" and (not chat.busy_until or chat.busy_until <= utcnow()):
        state = "failed"
    return {
        "id": turn.id,
        "request_id": turn.request_id,
        "sequence": turn.sequence,
        "question": turn.question,
        "reply": turn.reply,
        "status": state,
        "created_at": turn.created_at,
    }


def create(
    session: Session,
    p: Principal,
    title: str,
    round_id: str | None,
    dataset_id: str | None,
    mapping_id: str | None = None,
) -> Chat:
    if mapping_id:
        mapping = session.get(MappingVersion, mapping_id)
        if mapping is None:
            raise NotFound("Review context not found")
        ensure_customer(p, mapping.customer_id)
        if not mapping.round_id or (round_id and round_id != mapping.round_id):
            raise ValidationFailed("Mapping does not belong to this import")
        round_id = mapping.round_id
    for model, entity_id in ((ImportRound, round_id), (DatasetVersion, dataset_id)):
        if entity_id:
            row = (
                session.get(ImportRound, entity_id)
                if model is ImportRound
                else session.get(DatasetVersion, entity_id)
            )
            if row is None:
                raise NotFound("Conversation context not found")
            ensure_customer(p, row.customer_id)
            if isinstance(row, DatasetVersion) and round_id and row.round_id != round_id:
                raise ValidationFailed("Dataset does not belong to this import")
    owner, access = identity(p)
    chat = Chat(
        owner_key=owner,
        access_key=access,
        title=title.strip() or "New chat",
        round_id=round_id,
        dataset_id=dataset_id,
        mapping_id=mapping_id,
    )
    session.add(chat)
    session.flush()
    audit(session, "assistant.create", "conversation", chat.id, actor=p.actor)
    return chat


def list_chats(
    session: Session, p: Principal, search: str = "", offset: int = 0, archived: bool = False
) -> dict[str, Any]:
    q = owned(p).where(Chat.archived == archived)
    if search.strip():
        q = q.where(
            or_(
                Chat.title.icontains(search.strip(), autoescape=True),
                exists().where(
                    Turn.conversation_id == Chat.id,
                    or_(
                        Turn.question.icontains(search.strip(), autoescape=True),
                        Turn.answer.icontains(search.strip(), autoescape=True),
                    ),
                ),
            )
        )
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    chats = session.scalars(
        q.order_by(Chat.pinned.desc(), Chat.updated_at.desc(), Chat.id).offset(offset).limit(20)
    ).all()
    return {
        "items": [summary(c) for c in chats],
        "total": total,
        "next_offset": offset + 20 if offset + 20 < total else None,
    }


def remove(session: Session, p: Principal, chat_id: str) -> None:
    # Serialize with send's lease acquisition. SQLite serializes the write transaction;
    # PostgreSQL rechecks this predicate after a concurrent lease update finishes.
    owner, access = identity(p)
    removed = session.execute(
        update(Chat)
        .where(
            Chat.id == chat_id,
            Chat.owner_key == owner,
            Chat.access_key == access,
            or_(Chat.busy_until.is_(None), Chat.busy_until <= utcnow()),
        )
        .values(active_run=None)
    )
    if not removed.rowcount:  # type: ignore[attr-defined]
        get_chat(session, p, chat_id)  # 404 for another account; never reveal its busy state.
        raise Conflict("Wait for the current reply before deleting this conversation")
    session.execute(delete(Turn).where(Turn.conversation_id == chat_id))
    session.execute(delete(Chat).where(Chat.id == chat_id))
    audit(session, "assistant.delete", "conversation", chat_id, actor=p.actor)


def detail(session: Session, p: Principal, chat_id: str, before: int | None = None) -> dict[str, Any]:
    chat = get_chat(session, p, chat_id)
    q = select(Turn).where(Turn.conversation_id == chat.id)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    if before:
        q = q.where(Turn.sequence < before)
    turns = list(reversed(session.scalars(q.order_by(Turn.sequence.desc()).limit(30)).all()))
    return {
        "conversation": summary(chat),
        "turns": [turn_view(t, chat) for t in turns],
        "total": total,
        "older_before": turns[0].sequence if turns and turns[0].sequence > 1 else None,
    }


def read_history(
    session: Session, p: Principal, chat_id: str, search: str = "", offset: int = 0
) -> dict[str, Any]:
    chat = get_chat(session, p, chat_id)
    q = select(Turn).where(Turn.conversation_id == chat.id, Turn.status == "complete")
    if search:
        q = q.where(
            or_(
                Turn.question.icontains(search, autoescape=True),
                Turn.answer.icontains(search, autoescape=True),
            )
        )
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    turns = session.scalars(q.order_by(Turn.sequence).offset(offset).limit(3)).all()
    return {
        "conversation_id": chat.id,
        "total": total,
        "offset": offset,
        "next_offset": offset + 3 if offset + 3 < total else None,
        "messages": [
            {
                "sequence": t.sequence,
                "question": t.question,
                "answer": t.answer,
                "clarifications": (t.reply or {}).get("clarifications", []),
                "reports": [
                    {k: report.get(k) for k in ("title", "scope", "query", "generated_at")}
                    for report in (t.reply or {}).get("visualizations", [])
                ],
                "historical_not_live": True,
            }
            for t in turns
        ],
        "links": [],
    }


def import_legacy(
    session: Session,
    p: Principal,
    messages: list[dict[str, str]],
    round_id: str | None,
    dataset_id: str | None,
) -> Chat:
    key = _hash([messages, round_id, dataset_id])
    existing = session.scalar(owned(p).where(Chat.legacy_key == key))
    if existing:
        return existing
    try:
        with session.begin_nested():
            chat = create(session, p, messages[0]["question"][:80], round_id, dataset_id)
            chat.legacy_key = key
            for i, message in enumerate(messages):
                answer = message["answer"]
                session.add(
                    Turn(
                        conversation_id=chat.id,
                        request_id=f"legacy-{i}",
                        sequence=i + 1,
                        question=message["question"],
                        answer=answer,
                        status="complete",
                        reply={"answer": answer, "ai": False, "reason": "imported_history", "evidence": []},
                        completed_at=utcnow(),
                    )
                )
            session.flush()
    except IntegrityError:
        existing = session.scalar(owned(p).where(Chat.legacy_key == key))
        if existing:
            return existing
        raise
    return chat


def send(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    chat_id: str,
    question: str,
    request_id: str,
    language: str,
    timezone: str,
    stream: AnswerStream | None = None,
) -> dict[str, Any]:
    from forma.services import workspace_assistant

    chat = get_chat(session, p, chat_id)
    if chat.archived:
        raise Conflict("Restore this conversation before sending a message")
    existing = session.scalar(
        select(Turn).where(Turn.conversation_id == chat.id, Turn.request_id == request_id)
    )
    if existing and existing.question != question:
        raise Conflict("This request ID belongs to another question")
    if existing and existing.status in ("complete", "stopped"):
        return turn_view(existing, chat)
    run_id, now = uuid4().hex, utcnow()
    locked = session.execute(
        update(Chat)
        .where(Chat.id == chat.id, or_(Chat.busy_until.is_(None), Chat.busy_until <= now))
        .values(active_run=run_id, busy_until=now + timedelta(minutes=15), updated_at=now)
    )
    if not locked.rowcount:  # type: ignore[attr-defined]
        raise Conflict("A reply is still being prepared. Reopen the conversation to see its progress.")
    # Another request may have finished between the first lookup and acquiring the lease.
    existing = session.scalar(
        select(Turn)
        .where(Turn.conversation_id == chat.id, Turn.request_id == request_id)
        .execution_options(populate_existing=True)
    )
    if existing and existing.question != question:
        session.rollback()
        raise Conflict("This request ID belongs to another question")
    if existing and existing.status in ("complete", "stopped"):
        chat.active_run = None
        chat.busy_until = None
        session.commit()
        return turn_view(existing, chat)
    # Expired attempts remain visible and can be retried without duplicating their question.
    session.execute(
        update(Turn).where(Turn.conversation_id == chat.id, Turn.status == "pending").values(status="failed")
    )
    turn = existing
    if turn is None:
        sequence = (
            session.scalar(select(func.max(Turn.sequence)).where(Turn.conversation_id == chat.id)) or 0
        ) + 1
        turn = Turn(conversation_id=chat.id, request_id=request_id, sequence=sequence, question=question)
        session.add(turn)
        if sequence == 1 and chat.title == "New chat":
            chat.title = question.replace("\n", " ")[:80]
    turn.status = "pending"
    turn.answer, turn.reply = "", None
    session.flush()
    turn_id = turn.id
    audit(session, "assistant.message", "conversation", chat.id, actor=p.actor, turn_id=turn_id)
    session.commit()  # The question survives disconnects/restarts before the provider is called.
    if stream:
        last_checkpoint = 0.0

        def checkpoint() -> None:
            nonlocal last_checkpoint
            now_tick = time.monotonic()
            if now_tick - last_checkpoint < 0.4:
                return
            last_checkpoint = now_tick
            with session_scope() as progress:
                active = progress.scalar(select(Chat.active_run).where(Chat.id == chat_id))
                if active != run_id:
                    stream.cancelled.set()
                    raise StreamCancelled()
                if stream.text:
                    partial = {
                        "answer": stream.text,
                        "ai": True,
                        "reason": "streaming",
                        "evidence": [],
                        "actions": [],
                    }
                    progress.execute(
                        update(Turn)
                        .where(Turn.id == turn_id, Turn.status == "pending")
                        .values(answer=stream.text, reply=partial)
                    )

        stream.checkpoint = checkpoint
        ACTIVE_STREAMS[(chat_id, request_id)] = stream
        stream.emit({"type": "started", "request_id": request_id})
    past = session.scalars(
        select(Turn)
        .where(
            Turn.conversation_id == chat.id,
            Turn.status.in_(["complete", "stopped"]),
            Turn.sequence < turn.sequence,
        )
        .order_by(Turn.sequence.desc())
        .limit(8)
    ).all()
    history = []
    for previous in reversed(past):
        refs = " ".join(
            link["url"] for e in (previous.reply or {}).get("evidence", []) for link in e.get("links", [])
        )
        history += [
            {"role": "user", "content": previous.question},
            {
                "role": "assistant",
                "content": (
                    ("[Stopped by user; incomplete answer]\n" if previous.status == "stopped" else "")
                    + previous.answer
                    + "\nClarification questions: "
                    + json.dumps((previous.reply or {}).get("clarifications", []), ensure_ascii=False)
                    + "\nHistorical report queries (recheck live data): "
                    + json.dumps(
                        [
                            {k: report.get(k) for k in ("title", "query", "generated_at")}
                            for report in (previous.reply or {}).get("visualizations", [])
                        ],
                        ensure_ascii=False,
                    )
                    + "\nReferences: "
                    + refs
                )[:6000],
            },
        ]
    try:
        extra: dict[str, Any] = {}
        if chat.mapping_id:
            extra["mapping_id"] = chat.mapping_id
        if stream:
            extra["stream"] = stream
        reply = workspace_assistant.ask(
            ctx,
            session,
            p,
            question,
            language,
            chat.round_id,
            history,
            chat.dataset_id,
            timezone,
            conversation_id=chat.id,
            **extra,
        )
        state = "complete" if reply.get("ai") else "failed"
    except StreamCancelled:
        session.rollback()
        reply = {
            "answer": stream.text if stream else "",
            "ai": True,
            "reason": "stopped",
            "evidence": [],
            "actions": [],
        }
        state = "stopped"
    except Exception:
        # Never persist raw provider errors, credentials, or stack traces as chat content.
        session.rollback()
        reply = {
            "answer": stream.text
            if stream and stream.text
            else ("ยังตอบไม่ได้ ลองส่งคำถามนี้อีกครั้ง" if language == "th" else "Could not answer. Please retry."),
            "ai": False,
            "reason": "request_failed",
            "evidence": [],
        }
        state = "failed"
    finally:
        if stream and ACTIVE_STREAMS.get((chat_id, request_id)) is stream:
            ACTIVE_STREAMS.pop((chat_id, request_id), None)
    finished = session.execute(
        update(Chat)
        .where(Chat.id == chat.id, Chat.active_run == run_id)
        .values(active_run=None, busy_until=None, updated_at=utcnow())
    )
    if not finished.rowcount:  # type: ignore[attr-defined]
        session.rollback()
        saved = session.get(Turn, turn_id, populate_existing=True)
        if saved and saved.status == "stopped":
            if stream:
                saved.answer = stream.text
                saved.reply = {
                    "answer": stream.text,
                    "ai": True,
                    "reason": "stopped",
                    "evidence": [],
                    "actions": [],
                }
                session.commit()
            return turn_view(saved, get_chat(session, p, chat_id))
        raise Conflict("A newer attempt has replaced this request")
    session.execute(
        update(Turn)
        .where(Turn.id == turn_id)
        .values(answer=reply["answer"], reply=reply, status=state, completed_at=utcnow())
    )
    audit(session, "assistant.reply", "conversation", chat.id, actor=p.actor, turn_id=turn_id, status=state)
    session.commit()
    session.expire_all()
    saved = session.get(Turn, turn_id)
    assert saved is not None
    return turn_view(saved, get_chat(session, p, chat.id))
