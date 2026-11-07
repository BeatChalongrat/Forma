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
from forma.db.models import (
    AssistantChatState,
    AssistantFeedback,
    AssistantRun,
    DatasetVersion,
    ImportRound,
    Job,
    MappingVersion,
    utcnow,
)
from forma.db.models import AssistantConversation as Chat
from forma.db.models import AssistantTurn as Turn
from forma.db.session import session_scope
from forma.services import artifacts, attachments, memory
from forma.services.context import ServiceContext
from forma.services.rounds import audit

ACTIVE_STREAMS: dict[tuple[str, str], AnswerStream] = {}
INCOGNITO_DAYS = 1


def stop(session: Session, p: Principal, chat_id: str, request_id: str) -> dict[str, Any]:
    chat = get_chat(session, p, chat_id)
    # Lock in the same order as send/delete. Stop is scoped to this exact request, never a newer one.
    session.execute(update(Chat).where(Chat.id == chat.id).values(updated_at=Chat.updated_at))
    session.expire_all()
    turn = session.scalar(select(Turn).where(Turn.conversation_id == chat_id, Turn.request_id == request_id))
    if turn is None:
        raise NotFound("Message not found")
    run = session.get(AssistantRun, turn.id)
    job = session.get(Job, run.job_id) if run else None
    if turn.status == "pending" or (turn.status == "failed" and job and job.status in ("queued", "running")):
        turn.status, turn.completed_at = "stopped", utcnow()
        if turn.reply:
            turn.reply = {**turn.reply, "reason": "stopped", "actions": []}
        chat.active_run, chat.busy_until = None, None
        audit(session, "assistant.stop", "conversation", chat_id, actor=p.actor, turn_id=turn.id)
        if job and job.status in ("queued", "running"):
            job.status, job.lease_token, job.lease_expires_at = "cancelled", None, None
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
        "memory_off": chat.memory_off,
        "incognito": chat.incognito,
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
        "attachments": turn.attachments or [],
        "status": state,
        "created_at": turn.created_at,
    }


def _run_view(view: dict[str, Any], turn: Turn, run: AssistantRun | None, job: Job | None) -> None:
    if run:
        view["cursor"] = run.revision
        view["work_status"] = job.status if job else "failed"
        if turn.status in ("pending", "failed") and job and job.status in ("queued", "running"):
            view["status"] = "pending"
        elif turn.status == "pending" and job and job.status in ("failed", "cancelled"):
            view["status"] = "stopped" if job.status == "cancelled" else "failed"


def message_progress(session: Session, p: Principal, chat_id: str, request_id: str) -> dict[str, Any]:
    """Read a saved attempt; reconnecting never starts another provider request."""
    chat = get_chat(session, p, chat_id)
    turn = session.scalar(select(Turn).where(Turn.conversation_id == chat.id, Turn.request_id == request_id))
    if turn is None:
        raise NotFound("Message not found")
    view = turn_view(turn, chat)
    run = session.get(AssistantRun, turn.id)
    _run_view(view, turn, run, session.get(Job, run.job_id) if run else None)
    if view["reply"] is not None:
        refs = artifacts.refs_for_turns(session, [turn.id], chat.id)
        view["reply"] = {**view["reply"], "artifacts": refs.get(turn.id, [])}
    return view


def create(
    session: Session,
    p: Principal,
    title: str,
    round_id: str | None,
    dataset_id: str | None,
    mapping_id: str | None = None,
    memory_off: bool = False,
    incognito: bool = False,
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
        memory_off=memory_off or incognito,
        incognito=incognito,
    )
    session.add(chat)
    session.flush()
    audit(session, "assistant.create", "conversation", chat.id, actor=p.actor)
    return chat


def visible(p: Principal) -> Select[Chat]:
    """Chats that count as history: an incognito chat is in no list and no search."""
    return owned(p).where(Chat.incognito.is_(False))


def sweep_incognito(session: Session, p: Principal) -> list[str]:
    """Incognito chats last a day. Returns the storage keys of their files, for after the commit."""
    stale = session.scalars(
        owned(p).where(
            Chat.incognito.is_(True),
            Chat.updated_at < utcnow() - timedelta(days=INCOGNITO_DAYS),
            or_(Chat.busy_until.is_(None), Chat.busy_until <= utcnow()),
        )
    ).all()
    keys: list[str] = []
    for chat in stale:
        # Match publish/delete lock order and recheck expiry after waiting for a writer.
        locked = session.scalar(
            update(Chat)
            .where(
                Chat.id == chat.id,
                Chat.updated_at < utcnow() - timedelta(days=INCOGNITO_DAYS),
                or_(Chat.busy_until.is_(None), Chat.busy_until <= utcnow()),
            )
            .values(updated_at=Chat.updated_at)
            .returning(Chat.id)
        )
        if not locked:
            continue
        session.execute(
            delete(Job).where(
                Job.kind.in_(("create_document", "assistant_message")),
                Job.payload["chat_id"].as_string() == chat.id,
            )
        )
        session.execute(
            delete(AssistantFeedback).where(
                AssistantFeedback.turn_id.in_(select(Turn.id).where(Turn.conversation_id == chat.id))
            )
        )
        session.execute(delete(AssistantRun).where(AssistantRun.conversation_id == chat.id))
        session.execute(delete(AssistantChatState).where(AssistantChatState.conversation_id == chat.id))
        artifacts.delete_rows(session, chat.id)
        keys += attachments.delete_rows(session, chat.id)
        session.execute(delete(Turn).where(Turn.conversation_id == chat.id))
        session.execute(delete(Chat).where(Chat.id == chat.id))
        audit(session, "assistant.delete", "conversation", chat.id, actor=p.actor, reason="incognito")
    return keys


def list_chats(
    session: Session, p: Principal, search: str = "", offset: int = 0, archived: bool = False
) -> dict[str, Any]:
    q = visible(p).where(Chat.archived == archived)
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


def remove(session: Session, p: Principal, chat_id: str) -> list[str]:
    """Returns the storage keys of the chat's files: the caller deletes them once this has committed."""
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
    tids = select(Turn.id).where(Turn.conversation_id == chat_id)
    session.execute(delete(AssistantFeedback).where(AssistantFeedback.turn_id.in_(tids)))
    session.execute(delete(AssistantRun).where(AssistantRun.conversation_id == chat_id))
    session.execute(delete(AssistantChatState).where(AssistantChatState.conversation_id == chat_id))
    session.execute(
        delete(Job).where(Job.kind == "assistant_message", Job.payload["chat_id"].as_string() == chat_id)
    )
    artifacts.delete_rows(session, chat_id)
    session.execute(
        delete(Job).where(Job.kind == "create_document", Job.payload["chat_id"].as_string() == chat_id)
    )
    keys = attachments.delete_rows(session, chat_id)
    session.execute(delete(Turn).where(Turn.conversation_id == chat_id))
    session.execute(delete(Chat).where(Chat.id == chat_id))
    audit(session, "assistant.delete", "conversation", chat_id, actor=p.actor)
    return keys


def detail(session: Session, p: Principal, chat_id: str, before: int | None = None) -> dict[str, Any]:
    chat = get_chat(session, p, chat_id)
    q = select(Turn).where(Turn.conversation_id == chat.id)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    if before:
        q = q.where(Turn.sequence < before)
    turns = list(reversed(session.scalars(q.order_by(Turn.sequence.desc()).limit(30)).all()))
    refs = artifacts.refs_for_turns(session, [t.id for t in turns], chat.id)
    views = [turn_view(t, chat) for t in turns]
    runs = {
        r.turn_id: r
        for r in session.scalars(select(AssistantRun).where(AssistantRun.turn_id.in_([t.id for t in turns])))
    }
    jobs = {
        j.id: j for j in session.scalars(select(Job).where(Job.id.in_([r.job_id for r in runs.values()])))
    }
    for t, view in zip(turns, views, strict=True):
        run = runs.get(t.id)
        _run_view(view, t, run, jobs.get(run.job_id) if run else None)
    feedback = {
        f.turn_id: f
        for f in session.scalars(
            select(AssistantFeedback).where(AssistantFeedback.turn_id.in_([t.id for t in turns]))
        )
    }
    for view in views:
        item = feedback.get(view["id"])
        view["feedback"] = {"rating": item.rating, "comment": item.comment} if item else None
        if view["reply"] is not None:
            view["reply"] = {**view["reply"], "artifacts": refs.get(view["id"], [])}
    return {
        "conversation": {**summary(chat), "busy": any(v["status"] == "pending" for v in views)},
        "turns": views,
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
    attachment_ids: list[str] | None = None,
    artifact_id: str | None = None,
    target: str = "",
    prepared_job: str | None = None,
) -> dict[str, Any]:
    from forma.services import config, workspace_assistant

    chat = get_chat(session, p, chat_id)
    if chat.archived:
        raise Conflict("Restore this conversation before sending a message")
    file_ids = attachment_ids or []
    existing = session.scalar(
        select(Turn).where(Turn.conversation_id == chat.id, Turn.request_id == request_id)
    )
    if existing and existing.question != question:
        raise Conflict("This request ID belongs to another question")
    if existing and existing.status in ("complete", "stopped"):
        return turn_view(existing, chat)
    attachments.check(session, chat.id, file_ids, existing.id if existing else None)
    open_work = artifacts.working_on(session, chat.id, artifact_id, target) if artifact_id else None
    run_id, now = prepared_job or uuid4().hex, utcnow()
    locked = session.execute(
        update(Chat)
        .where(
            Chat.id == chat.id,
            or_(
                Chat.busy_until.is_(None),
                Chat.busy_until <= now,
                Chat.active_run == prepared_job if prepared_job else Chat.id.is_(None),
            ),
        )
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
    if file_ids:
        turn.attachments = attachments.claim(session, chat.id, turn_id, file_ids)
    audit(session, "assistant.message", "conversation", chat.id, actor=p.actor, turn_id=turn_id)
    session.commit()  # The question survives disconnects/restarts before the provider is called.
    if stream:
        last_checkpoint = 0.0
        saved_text = ""

        def checkpoint() -> None:
            nonlocal last_checkpoint, saved_text
            now_tick = time.monotonic()
            if now_tick - last_checkpoint < 0.4 and not (stream.text and not saved_text):
                return
            last_checkpoint = now_tick
            saved_text = stream.text
            with session_scope() as progress:
                active = progress.scalar(select(Chat.active_run).where(Chat.id == chat_id))
                if active != run_id:
                    stream.cancelled.set()
                    raise StreamCancelled()
                if stream.text or stream.phase:
                    partial = {
                        "answer": stream.text,
                        "ai": True,
                        "reason": "streaming",
                        "phase": stream.phase,
                        "evidence": [],
                        "actions": [],
                    }
                    progress.execute(
                        update(AssistantRun)
                        .where(AssistantRun.turn_id == turn_id)
                        .values(revision=AssistantRun.revision + 1)
                    )
                    progress.execute(
                        update(Chat)
                        .where(Chat.id == chat_id, Chat.active_run == run_id)
                        .values(busy_until=utcnow() + timedelta(minutes=15))
                    )
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
    from forma.services.assistant_runtime import context_history, step

    history = context_history(session, chat.id, turn.sequence, history)
    try:
        history = step("input.history", {}, lambda: history)
        extra: dict[str, Any] = {}
        if chat.mapping_id:
            extra["mapping_id"] = chat.mapping_id
        if stream:
            extra["stream"] = stream
        uploads = attachments.listing(session, chat.id, "upload")
        outputs = attachments.listing(session, chat.id, "output")
        if uploads or outputs:
            extra["attachments"] = [attachments.view(row) for row in [*uploads, *outputs]]
        if uploads and ctx.llm.supports_vision:
            extra["images"] = attachments.recent_images(ctx, session, chat.id)
        extra["turn_id"] = turn_id
        if open_work:
            extra["working_on"] = open_work
        prefs = config.personal_preferences(session, p.subject, p.mode)
        extra["recall"] = memory.recall_for(
            session, identity(p)[0], prefs, chat, visible(p), str(prefs.get("display_name", ""))
        )
        # Freeze prompt data across recovery; live authorization is still checked on every run.
        extra["recall"].items = step("input.memory", {}, lambda: extra["recall"].items)
        extra["attachments"] = step("input.files", {}, lambda: extra.get("attachments", []))
        extra["images"] = step("input.images", {}, lambda: extra.get("images", []))
        if not extra["images"]:
            extra.pop("images")
        if open_work:
            extra["working_on"] = step("input.artifact", {}, lambda: open_work)
        branch_state = session.get(AssistantChatState, chat.id)
        rewrite = (branch_state.branch or {}).get("answer_only") if branch_state else None
        if rewrite and branch_state is not None and not branch_state.branch.get("rewrite_sent"):
            from forma.services.assistant_runtime import step

            if ctx.llm.is_mock:
                rewritten = str(rewrite.get("answer", ""))
            else:
                result = step(
                    "model.rewrite",
                    {},
                    lambda: ctx.llm.structured(
                        system="Rewrite the saved answer using ONLY the supplied frozen evidence. Do not run tools, create files, change data or infer new facts. Clearly retain uncertainty. Reply in "
                        + language,
                        user=json.dumps(
                            {"question": question, "saved": rewrite}, ensure_ascii=False, default=str
                        ),
                        schema={
                            "type": "object",
                            "properties": {"answer": {"type": "string"}},
                            "required": ["answer"],
                            "additionalProperties": False,
                        },
                    ),
                )
                rewritten = result.data["answer"]
            from forma.ai.guard import GuardRejected, scan

            problems = scan({"answer": rewritten}, max_text=24000, allow_credential_terms=True)
            if problems:
                raise GuardRejected(problems)
            reply = {**rewrite, "answer": rewritten, "ai": True, "actions": [], "memory_changes": []}
            if stream:
                stream.metadata = reply
                stream.delta(rewritten)
            branch_state.branch = {**branch_state.branch, "rewrite_sent": True}
            from forma.db.models import AssistantArtifact, AssistantArtifactVersion

            session.execute(
                update(AssistantArtifactVersion)
                .where(
                    AssistantArtifactVersion.turn_id.is_(None),
                    AssistantArtifactVersion.artifact_id.in_(
                        select(AssistantArtifact.id).where(AssistantArtifact.conversation_id == chat.id)
                    ),
                )
                .values(turn_id=turn_id)
            )
        else:
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
    except Exception as error:
        from forma.services.assistant_runtime import RecoveryPaused

        # Never persist raw provider errors, credentials, or stack traces as chat content.
        session.rollback()
        saved_run = session.get(AssistantRun, turn_id)
        if saved_run:
            saved_run.state = {
                **saved_run.state,
                "last_failure": {
                    "type": type(error).__name__,
                    "at": utcnow().isoformat(),
                    "reason": str(error)
                    if isinstance(error, RecoveryPaused)
                    else "An unfinished step failed; inspect its receipt and attempt count",
                },
            }
        reply = {
            "answer": stream.text
            if stream and stream.text
            else ("ยังตอบไม่ได้ ลองส่งคำถามนี้อีกครั้ง" if language == "th" else "Could not answer. Please retry."),
            "ai": False,
            "reason": "recovery_paused" if isinstance(error, RecoveryPaused) else "request_failed",
            "evidence": [],
        }
        if stream and stream.metadata:
            reply = {
                **stream.metadata,
                **reply,
                "code_runs": stream.metadata.get("code_runs", []),
                "evidence": stream.metadata.get("evidence", []),
            }
        state = "failed"
    finally:
        if stream and ACTIVE_STREAMS.get((chat_id, request_id)) is stream:
            ACTIVE_STREAMS.pop((chat_id, request_id), None)
    if state != "stopped":
        reply["artifacts"] = artifacts.settle_turn(ctx, session, p, chat.id, turn_id, reply)
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
