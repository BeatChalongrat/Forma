"""User-controlled chat drafts, alternatives, acknowledgements and feedback."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, NotFound, ValidationFailed
from forma.core.ids import new_id
from forma.db.models import AssistantArtifact as Artifact
from forma.db.models import AssistantArtifactVersion as Version
from forma.db.models import AssistantAttachment as Attachment
from forma.db.models import AssistantChatState as State
from forma.db.models import AssistantFeedback as Feedback
from forma.db.models import AssistantTurn as Turn
from forma.db.models import Job, utcnow
from forma.services import conversations
from forma.services.context import ServiceContext


def get_state(s: Session, p: Principal, chat_id: str, lock: bool = False) -> State:
    chat = conversations.get_chat(s, p, chat_id)
    if lock:
        s.execute(update(type(chat)).where(type(chat).id == chat_id).values(updated_at=type(chat).updated_at))
    state = s.get(State, chat_id, populate_existing=True)
    if state is None:
        state = State(conversation_id=chat_id, revision=0, draft={}, branch={}, seen_sequence=0)
        if lock:
            s.add(state)
            s.flush()
    return state


def read_state(s: Session, p: Principal, chat_id: str) -> dict[str, Any]:
    result = view(get_state(s, p, chat_id))
    # Submission is derived from accepted work, not a browser's optimistic flag.
    draft = copy.deepcopy(result["draft"])
    cards = draft.setdefault("clarifications", {})
    for body in s.scalars(
        select(Job.payload).where(
            Job.kind == "assistant_message", Job.payload["chat_id"].as_string() == chat_id
        )
    ):
        source = body.get("body", {}).get("clarification_turn_id")
        if source:
            cards[source] = {
                "step": 0,
                "answers": {},
                "custom": {},
                **cards.get(source, {}),
                "submitted": True,
            }
    result["draft"] = draft
    return result


def view(state: State) -> dict[str, Any]:
    return {
        "revision": state.revision,
        "draft": state.draft,
        "branch": {k: v for k, v in state.branch.items() if k != "answer_only"},
        "seen_sequence": state.seen_sequence,
    }


def save_draft(
    s: Session, p: Principal, chat_id: str, revision: int, draft: dict[str, Any]
) -> dict[str, Any]:
    state = get_state(s, p, chat_id, True)
    if state.revision != revision:
        raise Conflict("This draft changed in another tab. Keep your text and reload the saved draft.")
    state.draft, state.revision = draft, state.revision + 1
    return view(state)


def feedback(
    s: Session, p: Principal, chat_id: str, turn_id: str, rating: str, comment: str
) -> dict[str, Any]:
    get_state(s, p, chat_id, True)
    turn = s.get(Turn, turn_id)
    if turn is None or turn.conversation_id != chat_id:
        raise NotFound("Message not found")
    row = s.get(Feedback, turn_id)
    if row is None:
        row = Feedback(turn_id=turn_id)
        s.add(row)
    row.rating, row.comment, row.updated_at = rating, comment, utcnow()
    return {"rating": rating, "comment": comment}


def branch(
    ctx: ServiceContext,
    s: Session,
    p: Principal,
    chat_id: str,
    turn_id: str,
    question: str,
    request_id: str,
    mode: str,
) -> dict[str, Any]:
    original = conversations.get_chat(s, p, chat_id)
    original_state = get_state(s, p, chat_id, True)
    source = s.get(Turn, turn_id)
    if source is None or source.conversation_id != chat_id:
        raise NotFound("Message not found")
    key = conversations._hash(["branch", chat_id, turn_id, request_id])
    old = s.scalar(conversations.owned(p).where(type(original).legacy_key == key))
    if old:
        return {"id": old.id}
    if source.status == "pending" or original.archived:
        raise Conflict("Wait for this reply to finish or restore the chat first")
    if mode == "regenerate" and not source.reply:
        raise ValidationFailed("There is no saved answer to rewrite")
    child = conversations.create(
        s,
        p,
        original.title[:140] + " · alternative",
        original.round_id,
        original.dataset_id,
        original.mapping_id,
        original.memory_off,
        original.incognito,
    )
    child.legacy_key = key
    rows = s.scalars(
        select(Turn)
        .where(Turn.conversation_id == chat_id, Turn.sequence < source.sequence)
        .order_by(Turn.sequence)
    ).all()
    ids: dict[str, str] = {}
    for row in rows:
        ids[row.id] = new_id("turn")
    # Copy only files belonging to the selected path, never files from its discarded future.
    file_ids: dict[str, str] = {}
    files = s.scalars(
        select(Attachment).where(
            Attachment.conversation_id == chat_id, Attachment.turn_id.in_([*ids, source.id])
        )
    ).all()
    for file in files:
        if mode == "edit" and file.turn_id == source.id and file.kind != "upload":
            continue
        aid = new_id("att")
        storage_key = f"assistant/{child.id}/{aid}/{file.name}"
        ctx.storage.put(storage_key, ctx.storage.get(file.storage_key))
        file_ids[file.id] = aid
        s.add(
            Attachment(
                id=aid,
                conversation_id=child.id,
                turn_id=ids.get(file.turn_id or ""),
                kind=file.kind,
                name=file.name,
                media_type=file.media_type,
                size=file.size,
                sha256=file.sha256,
                storage_key=storage_key,
                created_by=p.actor,
            )
        )

    artifact_ids: dict[str, str] = {}

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            for old_id, new in {**file_ids, **artifact_ids, **ids}.items():
                value = value.replace(old_id, new)
            return value
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value

    versions = s.scalars(
        select(Version)
        .where(Version.turn_id.in_([*ids, source.id] if mode == "regenerate" else list(ids)))
        .order_by(Version.number)
    ).all()
    for version in versions:
        artifact = s.get(Artifact, version.artifact_id)
        if artifact is None or artifact.conversation_id != chat_id:
            continue
        if artifact.id not in artifact_ids:
            aid = new_id("art")
            artifact_ids[artifact.id] = aid
            s.add(
                Artifact(
                    id=aid,
                    conversation_id=child.id,
                    kind=artifact.kind,
                    title=artifact.title,
                    name=artifact.name,
                    latest=version.number,
                    created_by=p.actor,
                )
            )
            s.flush()
        copied = s.get(Artifact, artifact_ids[artifact.id])
        assert copied
        copied.latest = version.number
        s.add(
            Version(
                artifact_id=copied.id,
                number=version.number,
                turn_id=ids.get(version.turn_id or ""),
                note=version.note,
                attachment_id=file_ids.get(version.attachment_id or ""),
                content=copy.deepcopy(version.content),
                code=version.code,
                preview=rewrite(version.preview),
                checked=version.checked,
                source=rewrite(version.source),
                created_by=p.actor,
            )
        )

    for row in rows:
        reply = rewrite(row.reply)
        if reply:
            # Branching never repeats or undoes a business approval.
            reply["actions"] = [a for a in reply.get("actions", []) if a.get("kind") != "act"]
        s.add(
            Turn(
                id=ids[row.id],
                conversation_id=child.id,
                request_id="copied-" + row.id,
                sequence=row.sequence,
                question=row.question,
                answer=row.answer,
                reply=reply,
                attachments=rewrite(row.attachments),
                status=row.status,
                created_at=row.created_at,
                completed_at=row.completed_at,
            )
        )
    selected = [file_ids[f["id"]] for f in source.attachments or [] if f["id"] in file_ids]
    # Only responses present in the copied prefix count as submitted; do not import future decisions.
    submitted = {
        ids[card_id]: {**copy.deepcopy(card), "submitted_by": ids[card["submitted_by"]]}
        for card_id, card in original_state.draft.get("clarifications", {}).items()
        if card_id in ids and card.get("submitted") and card.get("submitted_by") in ids
    }
    for payload in s.scalars(
        select(Job.payload).where(
            Job.kind == "assistant_message",
            Job.payload["chat_id"].as_string() == chat_id,
            Job.payload["turn_id"].as_string().in_(list(ids)),
        )
    ):
        source_card = payload.get("body", {}).get("clarification_turn_id")
        if source_card in ids:
            submitted[ids[source_card]] = {
                "step": 0,
                "answers": {},
                "custom": {},
                "submitted": True,
                "submitted_by": ids[payload["turn_id"]],
            }
    draft = {
        "input": question if mode == "edit" else source.question,
        "attachment_ids": selected,
        "clarifications": submitted,
    }
    branch_info = {"chat_id": chat_id, "turn_id": turn_id, "mode": mode, "title": original.title}
    if mode == "regenerate":
        branch_info["answer_only"] = rewrite({**(source.reply or {}), "actions": [], "memory_changes": []})
    s.add(State(conversation_id=child.id, draft=draft, branch=branch_info))
    s.flush()
    return {"id": child.id}
