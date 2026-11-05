"""Human-controlled revision of approved interpretation knowledge."""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from forma.api import serialize
from forma.api.routes import ctx_dep
from forma.auth import Principal, ensure_customer, require_role
from forma.core.errors import ValidationFailed
from forma.db.models import Correction
from forma.db.session import get_session
from forma.services import knowledge_revision, learning
from forma.services.context import ServiceContext

router = APIRouter(prefix="/api/learning")


def scoped(session: Session, p: Principal, correction_id: str) -> Correction:
    row = learning.get_correction(session, correction_id)
    if row.customer_id is None:
        raise ValidationFailed("แก้คำศัพท์กลางได้จากหน้าการจับคู่ต้นทาง")
    ensure_customer(p, row.customer_id)
    return row


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: Any
    reason: str = Field(min_length=1, max_length=500)
    digest: str = Field(default="", max_length=64)


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: dict[str, str] = Field(min_length=1, max_length=50)


@router.get("/{correction_id}/revision")
def revision_form(
    correction_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    return knowledge_revision.form(ctx, session, scoped(session, p, correction_id))


@router.post("/{correction_id}/revision/preview")
def preview_revision(
    correction_id: str,
    body: RevisionIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    return knowledge_revision.preview(
        ctx, session, scoped(session, p, correction_id), body.answer, body.reason
    )


@router.post("/{correction_id}/revision/apply", status_code=201)
def apply_revision(
    correction_id: str,
    body: RevisionIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    return serialize.mapping_version(
        knowledge_revision.revise(
            ctx, session, scoped(session, p, correction_id), body.answer, body.reason, body.digest, p.actor
        )
    )


@router.get("/{correction_id}/impacts")
def impacts(
    correction_id: str,
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    return knowledge_revision.impacts(session, scoped(session, p, correction_id), offset)


@router.get("/{correction_id}/changes")
def changes(
    correction_id: str,
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    from forma.services.inspection import events

    row = scoped(session, p, correction_id)
    return events(session, entity_id=row.id, action="learning.revise", offset=offset)


@router.post("/{correction_id}/review", status_code=201)
def request_review(
    correction_id: str,
    body: ReviewIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    return {
        "items": knowledge_revision.request_review(
            ctx, session, scoped(session, p, correction_id), body.selections, p.actor
        )
    }
