"""Eligibility evaluated from the database on every recall, never a process-local cache."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from forma.core.errors import NotFound, ValidationFailed
from forma.core.hashing import stable_hash
from forma.core.settings import get_settings
from forma.db.models import Correction, ImportRound, KnowledgeLabel, KnowledgeRelease, MappingVersion

REAL_ORIGINS = {"real_trial", "production"}
ORIGINS = {"unknown", "synthetic", *REAL_ORIGINS}
STATES = {"pending", "allowed", "revoked"}


def subject(row: MappingVersion | Correction) -> str:
    return ("mapping:" if isinstance(row, MappingVersion) else "correction:") + row.id


def lookup(session: Session, key: str) -> MappingVersion | Correction:
    kind, _, entity_id = key.partition(":")
    cls = {"mapping": MappingVersion, "correction": Correction}.get(kind)
    row = session.get(cls, entity_id) if cls else None
    if not isinstance(row, (MappingVersion, Correction)):
        raise NotFound("Knowledge item not found")
    return row


def content(session: Session, row: MappingVersion | Correction) -> dict[str, Any]:
    if isinstance(row, MappingVersion):
        r = session.get(ImportRound, row.round_id) if row.round_id else None
        return {
            "customer_id": row.customer_id,
            "target_schema": row.target_schema,
            "document": row.document,
            "policy": row.policy,
            "requirements": r.notes if r else None,
        }
    return {
        k: getattr(row, k)
        for k in ("customer_id", "kind", "target", "header", "before", "after", "mapping_version_id")
    }


def dependencies(row: MappingVersion | Correction) -> set[str]:
    if isinstance(row, Correction):
        return {"mapping:" + row.mapping_version_id} if row.mapping_version_id else set()
    p = row.policy or {}
    result = set(p.get("knowledge_dependencies") or [])
    if p.get("reused_from"):
        result.add("mapping:" + p["reused_from"])
    for decision in p.get("confirmed_decisions") or []:
        if decision.get("knowledge_mapping_id") and decision["knowledge_mapping_id"] != row.id:
            result.add("mapping:" + decision["knowledge_mapping_id"])
        if decision.get("knowledge_id"):
            result.add("correction:" + decision["knowledge_id"])
    result.update("correction:" + i for i in (p.get("learning") or {}).get("knowledge_ids", []))
    result.discard(subject(row))
    return result


def label_for(session: Session, key: str) -> KnowledgeLabel | None:
    return session.scalar(
        select(KnowledgeLabel).where(KnowledgeLabel.subject == key).execution_options(populate_existing=True)
    )


def allowed(
    session: Session,
    row: MappingVersion | Correction,
    *,
    strict: bool | None = None,
    trail: frozenset[str] = frozenset(),
) -> bool:
    strict = get_settings().knowledge_policy == "strict" if strict is None else strict
    key = subject(row)
    if key in trail or len(trail) > 64:
        return False
    label = label_for(session, key)
    if label is None:
        if strict:
            return False
    elif label.origin not in REAL_ORIGINS or label.state != "allowed":
        return False
    elif label.digest != stable_hash(content(session, row)):
        return False  # reviewed content changed; must be reviewed again
    if label and label.release_id:
        receipt = session.scalar(
            select(KnowledgeRelease)
            .where(KnowledgeRelease.id == label.release_id)
            .execution_options(populate_existing=True)
        )
        if receipt is None or receipt.state != "active":
            return False
    if isinstance(row, MappingVersion):
        if row.status not in {"approved", "superseded"} or not row.approved_at:
            return False
        if session.scalar(
            select(Correction.id)
            .where(
                Correction.mapping_version_id == row.id,
                Correction.kind.in_(("decision", "constant", "alias")),
                Correction.status == "retired",
            )
            .limit(1)
        ):
            return False
    elif row.status != "confirmed":
        return False
    for dep in dependencies(row):
        try:
            parent = lookup(session, dep)
        except NotFound:
            return False
        if not allowed(session, parent, strict=strict, trail=trail | {key}):
            return False
    return True


def classify(session: Session, key: str, origin: str, state: str, actor: str, reason: str) -> KnowledgeLabel:
    from forma.services.rounds import audit

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(74603821)"))

    if origin not in ORIGINS or state not in STATES or not reason.strip():
        raise ValidationFailed("Choose provenance, permission and a review reason")
    row = lookup(session, key)
    existing = label_for(session, key)
    if existing and existing.release_id:
        raise ValidationFailed(
            "Imported knowledge is managed through its release; revoke the release instead"
        )
    if state == "allowed":
        if origin not in REAL_ORIGINS:
            raise ValidationFailed("Synthetic or unknown data cannot be approved as production knowledge")
        mapping = (
            row
            if isinstance(row, MappingVersion)
            else (session.get(MappingVersion, row.mapping_version_id) if row.mapping_version_id else None)
        )
        if mapping is None:
            raise ValidationFailed(
                "Knowledge needs a verified source mapping; standalone global hints cannot transfer"
            )
        v = (mapping.policy or {}).get("verification") or {}
        if (
            mapping.status not in {"approved", "superseded"}
            or not mapping.approved_at
            or not (v.get("complete") is True and v.get("passed") is True)
        ):
            raise ValidationFailed("Approve and fully verify the source mapping before allowing knowledge")
        for dep in dependencies(row):
            if not allowed(session, lookup(session, dep), strict=True):
                raise ValidationFailed("Review the source knowledge first", details={"dependency": dep})
    label = existing or KnowledgeLabel(subject=key)
    label.origin, label.state, label.actor, label.reason = origin, state, actor, reason
    label.digest = stable_hash(content(session, row))
    session.add(label)
    audit(
        session,
        "knowledge.classify",
        "knowledge",
        key,
        actor=actor,
        origin=origin,
        state=state,
        reason=reason,
    )
    session.flush()
    return label
