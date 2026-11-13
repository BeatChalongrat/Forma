"""Human-labelled pilot observations. Append-only audit; never teach or alter mappings."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.api.routes import _mapping_scoped, ctx_dep
from forma.auth import Principal, require_role
from forma.core.errors import Conflict, ValidationFailed
from forma.db.models import AuditLog, MappingVersion
from forma.db.session import get_session
from forma.services import review_questions
from forma.services.context import ServiceContext
from forma.services.rounds import audit

router = APIRouter(prefix="/api/pilot", tags=["pilot"])
ACTION = "pilot.question_observation"


def catalogue(ctx: ServiceContext, m: MappingVersion) -> dict[str, Any]:
    result = review_questions.questions(ctx, m)
    choices = {q["id"]: q for q in result["questions"]}
    covered = {q.get("question") for q in result["questions"]}
    choices.update(
        {
            f"note:{i}": {"question": text}
            for i, text in enumerate(result["open_questions"])
            if text not in covered
        }
    )
    return choices


class ObservationIn(BaseModel):
    question_id: str = Field(min_length=1, max_length=1000)
    category: Literal["missing_information", "evidence_in_file", "conflicting_evidence", "not_assessed"]
    note: str = Field(default="", max_length=2000)
    evidence_location: str = Field(default="", max_length=1000)


def view(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "mapping_id": row.entity_id,
        "actor": row.actor,
        "created_at": row.created_at,
        **row.details,
    }


@router.get("/mappings/{mapping_id}/observations")
def observations(
    mapping_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    _mapping_scoped(session, p, mapping_id)
    rows = list(
        session.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == ACTION,
                AuditLog.entity_id == mapping_id,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )
    )
    latest: dict[str, Any] = {}
    for row in rows:
        latest.setdefault(row.details["question_id"], view(row))
    return {"enabled": ctx.settings.pilot_mode, "latest": latest, "history": [view(r) for r in rows]}


@router.post("/mappings/{mapping_id}/observations")
def record_observation(
    mapping_id: str,
    body: ObservationIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    m = _mapping_scoped(session, p, mapping_id)
    if not ctx.settings.pilot_mode:
        raise Conflict("Pilot feedback is not enabled in this environment.")
    choices = catalogue(ctx, m)
    if body.question_id not in choices:
        raise ValidationFailed("Question does not belong to this mapping version.")
    if body.category != "not_assessed" and not body.note.strip():
        raise ValidationFailed("Explain what you checked or what information is missing.")
    if body.category in ("evidence_in_file", "conflicting_evidence") and not body.evidence_location.strip():
        raise ValidationFailed("Specify the source file, sheet and cells/column containing the evidence.")
    audit(
        session,
        ACTION,
        "mapping",
        m.id,
        actor=p.actor,
        **body.model_dump(),
        round_id=m.round_id,
        customer_id=m.customer_id,
        question=choices[body.question_id],
        target_schema=m.target_schema,
        label_source="human_review",
        synthetic=ctx.settings.llm_mock_only,
    )
    session.flush()
    return observations(mapping_id, session, ctx, p)


@router.get("/questions")
def question_inventory(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    # Count occurrences by mapping VERSION, not as independent files or model accuracy.
    query = select(MappingVersion)
    if p.customers is not None:
        query = query.where(MappingVersion.customer_id.in_(sorted(p.customers)))
    mappings = list(
        session.scalars(
            query.order_by(MappingVersion.created_at, MappingVersion.id).offset(offset).limit(limit + 1)
        )
    )
    items = []
    for m in mappings[:limit]:
        latest = observations(m.id, session, ctx, p)["latest"]
        for qid, question in catalogue(ctx, m).items():
            items.append(
                {
                    "mapping_id": m.id,
                    "round_id": m.round_id,
                    "customer_id": m.customer_id,
                    "question_id": qid,
                    "question": question,
                    "observation": latest.get(qid),
                }
            )
    return {
        "items": items,
        "next_offset": offset + limit if len(mappings) > limit else None,
        "unit": "question occurrence per mapping version; includes revisions",
    }


@router.get("/observations")
def all_observations(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    query = (
        select(AuditLog)
        .join(MappingVersion, MappingVersion.id == AuditLog.entity_id)
        .where(AuditLog.action == ACTION)
    )
    if p.customers is not None:
        query = query.where(MappingVersion.customer_id.in_(sorted(p.customers)))
    rows = list(
        session.scalars(query.order_by(AuditLog.created_at, AuditLog.id).offset(offset).limit(limit + 1))
    )
    return {
        "items": [view(r) for r in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }
