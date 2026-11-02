"""Approved interpretation rules, scoped to one customer, contract, layout and operator requirements."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.hashing import stable_hash
from forma.db.models import Correction, ImportRound, MappingVersion
from forma.mapping_dsl.models import Expr, MappingDocument
from forma.schema_registry import TargetSchema
from forma.services import format_memory
from forma.services.decision_actions import field_mapping

REUSABLE = {"date_format", "value_map", "choice", "confirm"}


def reusable(decision: dict[str, Any]) -> bool:
    return decision.get("action") in REUSABLE or (
        decision.get("action") == "constant" and decision.get("remember") is True
    )


def record(session: Session, m: MappingVersion, actor: str) -> list[Correction]:
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    out = []
    for decision in (m.policy or {}).get("confirmed_decisions", []):
        # Always retain the audited answer; only approved interpretation rules may be recalled.
        row = Correction(
            customer_id=m.customer_id,
            kind="decision",
            target=decision["target"],
            before=decision.get("before"),
            after={**decision, "requirements_hash": stable_hash(r.notes or "" if r else "")},
            actor=actor,
            mapping_version_id=m.id,
            round_id=m.round_id,
        )
        session.add(row)
        out.append(row)
    return out


def eligible(session: Session, m: MappingVersion) -> bool:
    """An approved one-delivery exception is not a reusable format."""
    decisions = (m.policy or {}).get("confirmed_decisions", [])
    if any(not reusable(d) for d in decisions):
        return False
    origins = {m.id, *(d.get("knowledge_mapping_id") for d in decisions)} - {None}
    retired = session.scalar(
        select(Correction.id)
        .where(
            Correction.mapping_version_id.in_(origins),
            Correction.kind.in_(("decision", "constant", "alias")),
            Correction.status != "confirmed",
        )
        .limit(1)
    )
    if retired is not None:
        return False
    # A contradictory approved answer in the same scope prevents verbatim reuse as well as recall.
    from forma.fingerprint.fingerprint import compare

    fp = format_memory.mapping_fingerprint(m)
    own_round = session.get(ImportRound, m.round_id) if m.round_id else None
    requirements = stable_hash(own_round.notes or "" if own_round else "")
    for row in session.scalars(
        select(Correction).where(
            Correction.kind == "decision",
            Correction.status == "confirmed",
            Correction.customer_id == m.customer_id,
        )
    ):
        data = row.after or {}
        if data.get("requirements_hash") != requirements or not reusable(data):
            continue
        origin = session.get(MappingVersion, row.mapping_version_id) if row.mapping_version_id else None
        old = format_memory.mapping_fingerprint(origin) if origin else None
        if (
            not origin
            or origin.target_schema != m.target_schema
            or not fp
            or not old
            or compare(old, fp).classification not in ("identical", "header_moved")
        ):
            continue
        for d in decisions:
            if (
                d["target"] == row.target
                and d.get("source_value") == data.get("source_value")
                and d.get("after") != data.get("after")
            ):
                return False
    return True


def recall(
    session: Session, r: ImportRound, schema: TargetSchema, doc: MappingDocument
) -> list[dict[str, Any]]:
    """Apply only an exact, still-valid expression rule. Conflicting answers cause abstention."""
    from forma.fingerprint.fingerprint import compare

    current = format_memory.files_fingerprint(r.files)
    if current is None:
        return []
    grouped: dict[str, list[Correction]] = {}
    rows = session.scalars(
        select(Correction)
        .where(
            Correction.kind == "decision",
            Correction.status == "confirmed",
            Correction.customer_id == r.customer_id,
        )
        .order_by(Correction.created_at.desc())
    )
    for row in rows:
        data = row.after or {}
        if not reusable(data) or data.get("requirements_hash") != stable_hash(r.notes or ""):
            continue
        origin = session.get(MappingVersion, row.mapping_version_id)
        if origin is None or origin.approved_at is None or not format_memory.compatible(origin, schema):
            continue
        fingerprint = format_memory.mapping_fingerprint(origin)
        if fingerprint is None or compare(fingerprint, current).classification not in (
            "identical",
            "header_moved",
        ):
            continue
        grouped.setdefault(str(row.target), []).append(row)
    used = []
    for target, candidates in grouped.items():
        # No last-writer-wins for contradictory business facts.
        if len({stable_hash((c.after or {}).get("after")) for c in candidates}) != 1:
            continue
        row = candidates[0]
        data = row.after or {}
        field = field_mapping(doc, target)
        if field is None or field.expr.model_dump(mode="json") != data.get("before"):
            continue  # Different source or rule: preserve the new evidence and ask again.
        field.expr = TypeAdapter(Expr).validate_python(data["after"])
        field.confidence = 1.0
        field.evidence = [
            *field.evidence,
            f"learned: approved decision {row.id} by {row.actor}; same customer, layout, contract and requirements",
        ]
        row.uses = (row.uses or 0) + 1
        used.append({**data, "knowledge_id": row.id, "knowledge_mapping_id": row.mapping_version_id})
    return used
