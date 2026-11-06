"""A version-bound review: durable drafts, a complete preview, then one explicit confirmation."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import ImportRound, MappingVersion, ReviewDraft, ReviewReceipt, utcnow
from forma.jobs import runner
from forma.services import mapping, review_evidence, review_questions
from forma.services.context import ServiceContext
from forma.services.round_lifecycle import lock_round


def owner(p: Principal) -> str:
    return stable_hash([p.mode, p.subject], 64)


def draft(session: Session, m: MappingVersion, p: Principal) -> dict[str, Any]:
    row = session.get(ReviewDraft, (m.id, owner(p)))
    return {
        "mapping_id": m.id,
        "revision": row.revision if row else 0,
        "payload": row.payload if row else {},
        "updated_at": row.updated_at.isoformat() if row else None,
    }


def current(session: Session, m: MappingVersion) -> ImportRound:
    if not m.round_id:
        raise ValidationFailed("This mapping has no source delivery")
    r = lock_round(session, m.round_id)
    session.refresh(m)
    latest = (r.summary or {}).get("mapping_version_id")
    if m.status != "proposed" or (latest and latest != m.id):
        raise Conflict(
            "มีเวอร์ชันใหม่หรือมีผู้ยืนยันไฟล์นี้แล้ว เปิดเวอร์ชันล่าสุดก่อนทำต่อ",
            details={"latest_mapping_id": latest, "mapping_id": m.id},
        )
    return r


def save_draft(
    session: Session, m: MappingVersion, p: Principal, revision: int, payload: dict[str, Any]
) -> dict[str, Any]:
    current(session, m)
    key = owner(p)
    if revision == 0:
        try:
            with session.begin_nested():
                session.add(ReviewDraft(mapping_id=m.id, owner=key, revision=1, payload=payload))
                session.flush()
        except IntegrityError as exc:
            raise Conflict("ร่างนี้ถูกแก้จากอีกแท็บแล้ว โหลดร่างล่าสุดก่อนทำต่อ") from exc
    else:
        count = cast(
            CursorResult[Any],
            session.execute(
                update(ReviewDraft)
                .where(
                    ReviewDraft.mapping_id == m.id, ReviewDraft.owner == key, ReviewDraft.revision == revision
                )
                .values(revision=revision + 1, payload=payload, updated_at=utcnow())
            ),
        ).rowcount
        if count != 1:
            raise Conflict("ร่างนี้ถูกแก้จากอีกแท็บแล้ว โหลดร่างล่าสุดก่อนทำต่อ")
    session.expire_all()
    return draft(session, m, p)


def preview(
    ctx: ServiceContext,
    session: Session,
    m: MappingVersion,
    p: Principal,
    answers: dict[str, Any],
    remember: list[str],
    acknowledged: list[str],
) -> dict[str, Any]:
    r = current(session, m)
    if answers or acknowledged:
        document, remaining, answer_digest = review_questions.answer_document(
            ctx, m, answers, p.actor, remember=remember, acknowledged=acknowledged
        )
    else:
        if remember:
            raise ValidationFailed("Only answered questions can be remembered")
        document, remaining, answer_digest = m.document, m.open_questions or [], ""
    impact = review_evidence.answer_impact(ctx, session, m, document, answers)
    # Bind the displayed result to the immutable input objects, target contract, policy and decisions.
    # An answer digest alone is insufficient when another tab changes the delivery's inputs.
    digest = stable_hash(
        [
            m.id,
            document,
            answers,
            sorted(set(remember)),
            sorted(set(acknowledged)),
            sorted((f.id, f.sha256, f.status) for f in r.files),
            ctx.registry.get_ref(m.target_schema).document,
            m.policy,
            ctx.policy.policy,
            r.customer.policy_overrides if r.customer else None,
            impact["comparison"],
            {k: v for k, v in impact["preview"].items() if k != "stats"},
        ],
        64,
    )
    return {**impact, "digest": digest, "answer_digest": answer_digest, "remaining_questions": remaining}


def confirm(
    ctx: ServiceContext,
    session: Session,
    m: MappingVersion,
    p: Principal,
    answers: dict[str, Any],
    remember: list[str],
    acknowledged: list[str],
    digest: str,
    comment: str,
) -> dict[str, Any]:
    if not m.round_id:
        raise ValidationFailed("This mapping has no source delivery")
    # Serialize different users/tabs too; a personal draft lock alone cannot protect the mapping.
    lock_round(session, m.round_id)
    receipt_id = stable_hash(
        [owner(p), m.id, digest, comment, answers, sorted(remember), sorted(acknowledged)], 64
    )
    receipt = session.get(ReviewReceipt, receipt_id)
    if receipt:
        return receipt.result
    proof = preview(ctx, session, m, p, answers, remember, acknowledged)
    if digest != proof["digest"]:
        raise Conflict("ข้อมูลหรือคำตอบเปลี่ยนหลังดูผล กรุณาตรวจผลล่าสุดก่อนยืนยัน")
    if not (
        proof["preview"]["ok"]
        and proof["comparison"]["reliable"]
        and proof["comparison"]["validation"].get("passed")
    ):
        raise ValidationFailed("ผลตรวจยังไม่ผ่าน ต้องแก้จุดที่ระบุก่อนยืนยัน")
    new = (
        review_questions.apply_answers(
            ctx,
            session,
            m,
            answers,
            proof["answer_digest"],
            p.actor,
            remember=remember,
            acknowledged=acknowledged,
        )
        if answers or acknowledged
        else m
    )
    pending = review_questions.questions(ctx, new)
    job_id = None
    remaining = pending["progress"]["remaining_questions"] + pending["progress"]["remaining_notes"]
    if not remaining and not pending["blocked"] and pending["progress"]["state"] != "blocked":
        mapping.approve_mapping(session, new.id, p.actor, comment or None)
        job_id = runner.stage_job(
            session,
            "finish_review",
            {"round_id": m.round_id, "mapping_version_id": new.id, "actor": p.actor},
            round_id=m.round_id,
            request_id=p.request_id,
        )
    result = {"mapping_id": new.id, "job_id": job_id, "state": "processing" if job_id else "needs_review"}
    session.add(ReviewReceipt(id=receipt_id, mapping_id=m.id, result=result))
    return result
