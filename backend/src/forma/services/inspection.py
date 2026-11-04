"""Read models for review history, recorded AI runs and approved knowledge. No model calls."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from forma.core.hashing import stable_hash
from forma.db.models import (
    AuditLog,
    ChangeSet,
    Correction,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    MappingVersion,
    SourceFile,
)
from forma.services import decision_memory, format_memory
from forma.services.rounds import audit


def safe_detail(value: Any) -> Any:
    """Defense in depth for diagnostic payloads; source data is still customer-scoped by the caller."""
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(r"api.?key|authorization|password|secret|access.?token", k, re.I)
            else safe_detail(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [safe_detail(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}", "[redacted]", value)
    return value


def events(
    session: Session,
    *,
    offset: int = 0,
    limit: int = 30,
    round_id: str | None = None,
    entity_id: str = "",
    actor: str = "",
    action: str = "",
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> dict[str, Any]:
    q = select(AuditLog)
    if round_id:
        datasets = select(DatasetVersion.id).where(DatasetVersion.round_id == round_id)
        q = q.where(
            or_(
                AuditLog.entity_id == round_id,
                AuditLog.entity_id.in_(select(SourceFile.id).where(SourceFile.round_id == round_id)),
                AuditLog.entity_id.in_(select(MappingVersion.id).where(MappingVersion.round_id == round_id)),
                AuditLog.entity_id.in_(datasets),
                AuditLog.entity_id.in_(
                    select(ExportArtifact.id).where(ExportArtifact.dataset_version_id.in_(datasets))
                ),
                AuditLog.entity_id.in_(
                    select(ChangeSet.id).where(ChangeSet.dataset_version_id.in_(datasets))
                ),
                AuditLog.details["round_id"].as_string() == round_id,
            )
        )
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    if actor:
        q = q.where(AuditLog.actor.icontains(actor, autoescape=True))
    if action:
        q = q.where(AuditLog.action.icontains(action, autoescape=True))
    if start:
        q = q.where(AuditLog.created_at >= dt.datetime.combine(start, dt.time.min, dt.UTC))
    if end:
        q = q.where(
            AuditLog.created_at < dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, dt.UTC)
        )
    total = int(session.scalar(select(func.count()).select_from(q.subquery())) or 0)
    rows = session.scalars(
        q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit)
    )
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
        "items": [
            dict(
                id=a.id,
                actor=a.actor,
                action=a.action,
                entity=a.entity,
                entity_id=a.entity_id,
                details=safe_detail(a.details),
                at=a.created_at.isoformat(),
            )
            for a in rows
        ],
    }


def mapping_trace(session: Session, m: MappingVersion, *, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    policy = m.policy or {}
    parent = session.get(MappingVersion, m.parent_id) if m.parent_id else None
    previous = (
        {stable_hash(d) for d in (parent.policy or {}).get("confirmed_decisions", [])} if parent else set()
    )
    decisions = [d for d in policy.get("confirmed_decisions", []) if stable_hash(d) not in previous]
    logs = m.agent_log or []
    references = session.scalars(
        select(AuditLog).where(
            AuditLog.action == "learning.use", AuditLog.details["mapping_id"].as_string() == m.id
        )
    ).all()
    return cast(
        dict[str, Any],
        safe_detail(
            {
                "mapping_id": m.id,
                "round_id": m.round_id,
                "version": m.version,
                "parent_id": m.parent_id,
                "status": m.status,
                "created_at": m.created_at.isoformat(),
                "target_schema": m.target_schema,
                "approved_by": m.approved_by,
                "approved_at": m.approved_at.isoformat() if m.approved_at else None,
                "comment": m.comment,
                "decisions": decisions,
                "acknowledged": policy.get("acknowledged_questions", []),
                "effect_check": policy.get("answer_effect_check"),
                "checks": {
                    k: policy.get(k)
                    for k in (
                        "source",
                        "decision",
                        "self_check",
                        "verification",
                        "agreement",
                        "reused_from",
                        "workflow",
                    )
                },
                "knowledge": [{"id": a.entity_id, **a.details} for a in references],
                "logs": {
                    "items": logs[offset : offset + limit],
                    "total": len(logs),
                    "offset": offset,
                    "next_offset": offset + limit if offset + limit < len(logs) else None,
                },
            }
        ),
    )


def record_knowledge_use(session: Session, m: MappingVersion) -> None:
    policy = m.policy or {}
    ids = {d["knowledge_id"] for d in policy.get("confirmed_decisions", []) if d.get("knowledge_id")}
    ids.update((policy.get("learning") or {}).get("knowledge_ids", []))
    reused = policy.get("reused_from")
    if reused:
        for row in session.scalars(
            select(Correction).where(
                Correction.mapping_version_id == reused,
                Correction.status == "confirmed",
                Correction.kind.in_(("decision", "constant", "alias")),
            )
        ):
            ids.add(row.id)
    for key in sorted(ids):
        used_row = session.get(Correction, key)
        if used_row is None or used_row.customer_id not in (None, m.customer_id):
            continue
        if reused:
            used_row.uses = (used_row.uses or 0) + 1
        audit(
            session,
            "learning.use",
            "correction",
            key,
            actor="system",
            round_id=m.round_id,
            mapping_id=m.id,
            target=used_row.target,
            method="format_reuse"
            if reused
            else "decision_recall"
            if used_row.kind == "decision"
            else "learned_hint",
        )


def knowledge_detail(session: Session, row: Correction, *, offset: int = 0) -> dict[str, Any]:
    origin = session.get(MappingVersion, row.mapping_version_id) if row.mapping_version_id else None
    r = session.get(ImportRound, row.round_id) if row.round_id else None
    fp = format_memory.mapping_fingerprint(origin) if origin else None
    conflicts = []
    # Conflict evidence uses the same exact layout and contract boundary as decision recall.
    if row.kind == "decision" and origin and fp:
        from forma.fingerprint.fingerprint import compare

        for other in session.scalars(
            select(Correction).where(
                Correction.customer_id == row.customer_id,
                Correction.target == row.target,
                Correction.kind == "decision",
                Correction.status == "confirmed",
                Correction.id != row.id,
            )
        ):
            data = other.after or {}
            old = session.get(MappingVersion, other.mapping_version_id) if other.mapping_version_id else None
            old_fp = format_memory.mapping_fingerprint(old) if old else None
            own = row.after or {}
            if (
                old
                and old_fp
                and old.target_schema == origin.target_schema
                and data.get("requirements_hash") == own.get("requirements_hash")
                and decision_memory.reusable(data)
                and decision_memory.reusable(own)
                and compare(fp, old_fp).classification in ("identical", "header_moved")
                and data.get("source_value") == own.get("source_value")
                and data.get("after") != own.get("after")
            ):
                conflicts.append({"id": other.id, "answer": data.get("answer"), "round_id": other.round_id})
    return cast(
        dict[str, Any],
        safe_detail(
            {
                "id": row.id,
                "kind": row.kind,
                "status": row.status,
                "target": row.target,
                "actor": row.actor,
                "created_at": row.created_at.isoformat(),
                "before": row.before,
                "after": row.after,
                "reusable": decision_memory.reusable(row.after or {}) if row.kind == "decision" else True,
                "mapping_id": row.mapping_version_id,
                "round_id": row.round_id,
                "target_schema": origin.target_schema if origin else None,
                "requirements": r.notes if r else None,
                "layout_hash": stable_hash(fp.to_dict()) if fp else None,
                "conflicts": conflicts,
                "usage": events(session, entity_id=row.id, action="learning.use", offset=offset),
                "legacy_use_count": row.uses or 0,
            }
        ),
    )
