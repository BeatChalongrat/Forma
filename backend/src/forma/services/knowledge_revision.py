"""Versioned corrections and explicit review of affected deliveries. Never rewrites published data."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from forma.core.errors import Conflict, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import Correction, ImportRound, Job, MappingVersion
from forma.mapping_dsl.models import Expr, MappingDocument
from forma.services import decision_actions, knowledge_gate, mapping, review_evidence, review_questions
from forma.services.context import ServiceContext
from forma.services.round_lifecycle import CLOSED, lock_round
from forma.services.rounds import audit


def _parent(session: Session, row: Correction) -> MappingVersion:
    if row.kind != "decision" or not row.mapping_version_id or not row.round_id:
        raise ValidationFailed("คำตอบนี้แก้ได้จากหน้าการจับคู่ต้นทางเท่านั้น")
    r = session.get(ImportRound, row.round_id)
    if r is None or r.status in CLOSED or any(f.status == "purged" for f in r.files):
        raise Conflict("ไฟล์ต้นทางปิดใช้งานหรือถูกลบตามอายุข้อมูลแล้ว กรุณาอัปโหลดใหม่")
    head = (r.summary or {}).get("mapping_version_id") or row.mapping_version_id
    return mapping.get_mapping(session, head)


def question(ctx: ServiceContext, row: Correction, parent: MappingVersion) -> dict[str, Any]:
    data = row.after or {}
    schema = ctx.registry.get_ref(parent.target_schema)
    if not row.target or not schema.has_field(row.target):
        raise ValidationFailed("รูปแบบปลายทางไม่มีฟิลด์นี้แล้ว")
    spec = schema.field(row.target)
    action = data.get("action")
    if action not in {"constant", "value_map", "date_format", "choice", "confirm"}:
        raise ValidationFailed("คำตอบประเภทนี้ต้องแก้จากหน้าการจับคู่")
    kind = (
        "boolean"
        if "boolean" in spec.json_type
        else "number"
        if {"number", "integer"} & set(spec.json_type)
        else "enum"
        if spec.enum
        else "text"
    )
    q: dict[str, Any] = {
        "id": data.get("question_id") or row.target,
        "target": row.target,
        "field": spec.to_dict(),
        "question": data.get("question") or f"ยืนยันการอ่านค่า {spec.description or spec.name}",
        "kind": kind,
        "action": action,
        "scope": data.get("scope", "constant_for_this_file"),
        "options": spec.enum or [],
        "source_value": data.get("source_value"),
        "current": data.get("before") or {},
        "evidence": [],
        "contested": True,
    }
    if action == "choice":
        doc = MappingDocument.model_validate(parent.document)
        cols = review_questions._table_columns(parent, doc)
        old_col = decision_actions.column(TypeAdapter(Expr).validate_python(data["before"]))
        source = (old_col.source if old_col else None) or next(
            (c.source for c in doc.collections if any(f.target == row.target for f in c.fields)),
            doc.record.root_source,
        )
        q.update(kind="choice", options=[c["name"] for c in cols.get(source, [])])
        q.pop("action")  # The existing answer validator handles bounded column choices.
    else:
        q = decision_actions.normalize(q, schema)
    from forma.services.question_presentation import presentation

    q.update(presentation(q))
    return q


def form(ctx: ServiceContext, session: Session, row: Correction) -> dict[str, Any]:
    parent = _parent(session, row)
    return {
        "question": question(ctx, row, parent),
        "answer": (row.after or {}).get("answer"),
        "mapping_id": parent.id,
        "active": row.status == "confirmed",
    }


def _document(
    ctx: ServiceContext, row: Correction, parent: MappingVersion, answer: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    if row.status != "confirmed":
        raise Conflict("คำตอบนี้เลิกใช้หรือถูกแก้ไปแล้ว กรุณาโหลดใหม่")
    data = row.after or {}
    doc = MappingDocument.model_validate(parent.document)
    field = decision_actions.field_mapping(doc, str(row.target))
    if field is None or field.expr.model_dump(mode="json") != data.get("after"):
        raise Conflict("การอ่านฟิลด์นี้เปลี่ยนไปแล้ว กรุณาเปิดการจับคู่ล่าสุดเพื่อตรวจแก้")
    q = question(ctx, row, parent)
    if q["kind"] == "boolean" and not isinstance(answer, bool):
        raise ValidationFailed("เลือกใช่หรือไม่ใช่")
    if q["kind"] == "number" and (
        isinstance(answer, bool) or not isinstance(answer, (int, float)) or not math.isfinite(answer)
    ):
        raise ValidationFailed("กรอกตัวเลขที่ถูกต้อง")
    if q["kind"] in {"enum", "choice"} and answer not in q["options"]:
        raise ValidationFailed("เลือกคำตอบจากรายการ")
    if q["kind"] == "text" and (not isinstance(answer, str) or not answer.strip() or len(answer) > 5000):
        raise ValidationFailed("กรอกคำตอบไม่เกิน 5000 ตัวอักษร")
    if q["kind"] == "confirm" and answer != "confirm":
        raise ValidationFailed("หากอ่านผิด ให้เปลี่ยนคอลัมน์หรือกฎในหน้าการจับคู่")
    # Restore the exact original expression before replacing its answer (no nested old wrong rule).
    field.expr = TypeAdapter(Expr).validate_python(data["before"])
    if q["kind"] == "choice":
        col = decision_actions.column(field.expr)
        if col is None:
            raise ValidationFailed("ต้องแก้คอลัมน์จากหน้าการจับคู่")
        field.expr = review_questions.repoint(field.expr, col.name, answer)
    else:
        decision_actions.apply(doc, q, answer)
    field.confidence = 1.0
    return doc.model_dump(mode="json", exclude_none=True), q


def _digest(session: Session, row: Correction, parent: MappingVersion, answer: Any, reason: str) -> str:
    r = session.get(ImportRound, parent.round_id)
    return stable_hash(
        [
            row.id,
            row.status,
            row.after,
            parent.id,
            parent.status,
            parent.document,
            parent.policy,
            (r.notes if r else None),
            [(f.id, f.sha256, f.status) for f in r.files] if r else [],
            answer,
            reason,
        ],
        64,
    )


def preview(
    ctx: ServiceContext, session: Session, row: Correction, answer: Any, reason: str
) -> dict[str, Any]:
    if not reason.strip() or len(reason) > 500:
        raise ValidationFailed("ระบุเหตุผลในการแก้คำตอบ ไม่เกิน 500 ตัวอักษร")
    parent = _parent(session, row)
    document, q = _document(ctx, row, parent, answer)
    return {
        **review_evidence.answer_impact(ctx, session, parent, document, {q["id"]: answer}, decisions=[q]),
        "digest": _digest(session, row, parent, answer, reason),
        "remaining_questions": parent.open_questions or [],
        "old_answer": (row.after or {}).get("answer"),
        "new_answer": answer,
        "scope": q["scope"],
    }


def _idle(session: Session, r: ImportRound) -> None:
    if session.scalar(
        select(Job.id).where(Job.round_id == r.id, Job.status.in_(("queued", "running"))).limit(1)
    ):
        raise Conflict("ไฟล์นี้กำลังประมวลผล กรุณารอให้เสร็จก่อนแก้คำตอบ")


def revise(
    ctx: ServiceContext, session: Session, row: Correction, answer: Any, reason: str, digest: str, actor: str
) -> MappingVersion:
    if not reason.strip() or len(reason) > 500:
        raise ValidationFailed("ระบุเหตุผลในการแก้คำตอบ ไม่เกิน 500 ตัวอักษร")
    r = lock_round(session, row.round_id or "")
    session.refresh(row)
    _idle(session, r)
    parent = _parent(session, row)
    if digest != _digest(session, row, parent, answer, reason):
        raise Conflict("ข้อมูลหรือคำตอบเปลี่ยนแล้ว กรุณาดูผลอีกครั้งก่อนบันทึก")
    document, q = _document(ctx, row, parent, answer)
    checked = review_evidence.validate_answer_effects(ctx, session, parent, document, [q])
    new = mapping.save_mapping_edit(ctx, session, parent.id, document, actor)
    data = row.after or {}
    corrected = {
        **data,
        "answer": answer,
        "after": decision_actions.field_mapping(
            MappingDocument.model_validate(document), str(row.target)
        ).expr.model_dump(mode="json"),
        "actor": actor,
        "mapping_id": new.id,
        "answered_at": dt.datetime.now(dt.UTC).isoformat(),
        "replaces_knowledge_id": row.id,
    }
    for key in ("knowledge_id", "knowledge_mapping_id"):
        corrected.pop(key, None)
    retained = [
        d
        for d in (parent.policy or {}).get("confirmed_decisions", [])
        if not (d.get("target") == row.target and d.get("source_value") == data.get("source_value"))
    ]
    new.policy = {
        **(new.policy or {}),
        "confirmed_decisions": [*retained, corrected],
        "answer_effect_check": checked,
        "knowledge_revision": {
            "id": row.id,
            "reason": reason,
            "old_answer": data.get("answer"),
            "new_answer": answer,
            "actor": actor,
        },
    }
    # Stop a known wrong answer immediately. The replacement becomes reusable only after approval.
    row.status = "retired"
    r.status = "review"
    audit(
        session,
        "learning.revise",
        "correction",
        row.id,
        actor=actor,
        reason=reason,
        mapping_id=new.id,
        old_answer=data.get("answer"),
        new_answer=answer,
        field=q["field"],
        effect_check=checked,
    )
    return new


def _affected(session: Session, row: Correction) -> list[MappingVersion]:
    """Transitive lineage, including legacy parent links and copied decisions, scoped to one customer."""
    maps = list(
        session.scalars(
            select(MappingVersion)
            .where(MappingVersion.customer_id == row.customer_id)
            .options(
                load_only(
                    MappingVersion.id,
                    MappingVersion.customer_id,
                    MappingVersion.round_id,
                    MappingVersion.parent_id,
                    MappingVersion.version,
                    MappingVersion.policy,
                )
            )
        )
    )
    reached = {"correction:" + row.id, "mapping:" + str(row.mapping_version_id)}
    affected: dict[str, MappingVersion] = {}
    changed = True
    while changed:
        changed = False
        for m in maps:
            if (
                m.id in affected
                or (m.policy or {}).get("knowledge_revision", {}).get("id") == row.id
                or (m.policy or {}).get("knowledge_recheck") == row.id
            ):
                continue
            deps = knowledge_gate.dependencies(m)
            if m.parent_id:
                deps.add("mapping:" + m.parent_id)
            if "mapping:" + m.id in reached or deps & reached:
                affected[m.id] = m
                reached.add("mapping:" + m.id)
                changed = True
    return list(affected.values())


def require_current_knowledge(session: Session, candidate: MappingVersion) -> None:
    retired = list(
        session.scalars(
            select(Correction).where(
                Correction.customer_id == candidate.customer_id,
                Correction.kind == "decision",
                Correction.status == "retired",
            )
        )
    )
    if not retired:
        return
    by_id = {r.id: r for r in retired}
    pending = [(candidate, frozenset[str](), 0)]
    visited = set()
    while pending:
        current, resolved, depth = pending.pop()
        policy = current.policy or {}
        resolved = resolved | {
            str(policy.get("knowledge_recheck")),
            str((policy.get("knowledge_revision") or {}).get("id")),
        }
        key = (current.id, resolved)
        if key in visited:
            continue
        visited.add(key)
        deps = knowledge_gate.dependencies(current)
        blocked = next(
            (
                r
                for r in retired
                if r.id not in resolved
                and (r.mapping_version_id == current.id or "correction:" + r.id in deps)
            ),
            None,
        )
        if blocked:
            raise Conflict(
                "คำตอบที่ไฟล์นี้เคยใช้ถูกเลิกใช้แล้ว กรุณาส่งกลับมาตรวจจากหน้าความรู้ก่อนอนุมัติ",
                details={"knowledge_id": blocked.id},
            )
        if depth >= 64:
            raise Conflict("ประวัติการใช้ความรู้ยาวเกินขอบเขตที่ตรวจได้ กรุณาสร้างการจับคู่ใหม่")
        if current.parent_id:
            deps.add("mapping:" + current.parent_id)
        for dep in deps:
            kind, _, entity_id = dep.partition(":")
            if kind == "correction" and entity_id in by_id:
                continue  # Checked above, including an explicitly replaced answer.
            source = session.get(MappingVersion, entity_id) if kind == "mapping" else None
            if source is not None:
                pending.append((source, resolved, depth + 1))


def impacts(session: Session, row: Correction, offset: int = 0) -> dict[str, Any]:
    grouped: dict[str, list[MappingVersion]] = {}
    for m in _affected(session, row):
        if m.round_id:
            grouped.setdefault(m.round_id, []).append(m)
    items = []
    for rid, versions in grouped.items():
        r = session.get(ImportRound, rid)
        if r is None:
            continue
        head = (r.summary or {}).get("mapping_version_id")
        affected_head = next((m for m in versions if m.id == head), None)
        items.append(
            {
                "round_id": rid,
                "label": r.label,
                "files": [{"id": f.id, "name": f.original_name} for f in r.files],
                "status": r.status,
                "mapping_id": head,
                "versions": sorted(m.version for m in versions),
                "can_review": bool(
                    affected_head and r.status not in CLOSED and all(f.status != "purged" for f in r.files)
                ),
                "historical_only": affected_head is None,
                "created_at": r.created_at.isoformat(),
            }
        )
    items.sort(key=lambda item: (item["created_at"], item["round_id"]), reverse=True)
    return {
        "items": items[offset : offset + 50],
        "total": len(items),
        "offset": offset,
        "next_offset": offset + 50 if offset + 50 < len(items) else None,
    }


def request_review(
    ctx: ServiceContext, session: Session, row: Correction, selections: dict[str, str], actor: str
) -> list[dict[str, str]]:
    if row.status != "retired":
        raise Conflict("แก้คำตอบหรือเลิกใช้คำตอบเดิมก่อนส่งงานกลับมาตรวจ")
    affected = {m.id for m in _affected(session, row)}
    result = []
    for rid, expected in sorted(selections.items()):
        r = lock_round(session, rid)
        _idle(session, r)
        head = (r.summary or {}).get("mapping_version_id")
        parent = mapping.get_mapping(session, head or "")
        if (parent.policy or {}).get("knowledge_recheck") == row.id:
            result.append({"round_id": rid, "mapping_id": parent.id})
            continue  # Retried request, no duplicate draft.
        if r.customer_id != row.customer_id or head != expected or head not in affected:
            raise Conflict("รายการที่เลือกเปลี่ยนไปแล้ว กรุณาโหลดรายการผลกระทบใหม่")
        if any(f.status == "purged" for f in r.files):
            raise Conflict("ไฟล์ต้นทางไม่อยู่แล้ว กรุณาอัปโหลดใหม่")
        doc = MappingDocument.model_validate(parent.document)
        field = decision_actions.field_mapping(doc, str(row.target))
        if field is None:
            raise Conflict("ฟิลด์นี้ไม่มีอยู่แล้ว กรุณาตรวจรูปแบบปลายทางก่อนส่งกลับมาตรวจ")
        unchanged = field.expr.model_dump(mode="json") == (row.after or {}).get("after")
        if unchanged:
            field.expr = TypeAdapter(Expr).validate_python((row.after or {})["before"])
        field.confidence = 0.0
        q = question(ctx, row, parent)
        if not unchanged:
            q.update(
                kind="confirm",
                action="confirm",
                scope="mapping",
                options=[],
                current=field.expr.model_dump(mode="json"),
                question="คำตอบเดิมถูกเลิกใช้ และกฎของไฟล์นี้เปลี่ยนไปแล้ว ตรวจต้นทางและผลลัพธ์ปัจจุบันว่าถูกต้องหรือไม่?",
                question_th="คำตอบเดิมถูกเลิกใช้ และกฎของไฟล์นี้เปลี่ยนไปแล้ว ตรวจต้นทางและผลลัพธ์ปัจจุบันว่าถูกต้องหรือไม่?",
            )
        prompt = q["question"]
        new = mapping.save_mapping_edit(
            ctx,
            session,
            parent.id,
            doc.model_dump(mode="json", exclude_none=True),
            actor,
            remaining_questions=list(dict.fromkeys([*(parent.open_questions or []), prompt])),
        )
        new.policy = {
            **(new.policy or {}),
            "knowledge_recheck": row.id,
            "knowledge_questions": [q],
            "confirmed_decisions": [
                d
                for d in (parent.policy or {}).get("confirmed_decisions", [])
                if not (
                    d.get("target") == row.target
                    and d.get("source_value") == (row.after or {}).get("source_value")
                )
            ],
        }
        r.status = "review"
        audit(
            session,
            "learning.review_requested",
            "correction",
            row.id,
            actor=actor,
            round_id=rid,
            mapping_id=new.id,
            parent_id=parent.id,
        )
        result.append({"round_id": rid, "mapping_id": new.id})
    return result
