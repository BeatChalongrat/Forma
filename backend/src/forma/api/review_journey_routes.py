"""Review workspace endpoints. Draft persistence never approves or learns anything."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr
from sqlalchemy.orm import Session

from forma.api.routes import _mapping_scoped, ctx_dep
from forma.auth import Principal, require_role
from forma.db.session import get_session
from forma.jobs import runner
from forma.services import review_journey as journey
from forma.services.context import ServiceContext

router = APIRouter(prefix="/api/mappings/{mapping_id}/review")
BoundedText = Annotated[StrictStr, Field(max_length=5000)]
Answer = StrictBool | StrictInt | StrictFloat | BoundedText


class DraftPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    answers: dict[BoundedText, Answer] = Field(default_factory=dict, max_length=100)
    remember: list[BoundedText] = Field(default_factory=list, max_length=100)
    acknowledged: list[BoundedText] = Field(default_factory=list, max_length=100)
    unsure: dict[BoundedText, StrictBool] = Field(default_factory=dict, max_length=100)
    section: str = Field(default="", max_length=1000)
    comment: str = Field(default="", max_length=2000)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    payload: DraftPayload


class DecisionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    answers: dict[BoundedText, Answer] = Field(default_factory=dict, max_length=100)
    remember: list[BoundedText] = Field(default_factory=list, max_length=100)
    acknowledged: list[BoundedText] = Field(default_factory=list, max_length=100)
    digest: str = Field(default="", max_length=64)
    comment: str = Field(default="", max_length=2000)


@router.get("/draft")
def get_draft(
    mapping_id: str, session: Session = Depends(get_session), p: Principal = Depends(require_role("importer"))
) -> dict[str, Any]:
    return journey.draft(session, _mapping_scoped(session, p, mapping_id), p)


@router.put("/draft")
def put_draft(
    mapping_id: str,
    body: DraftIn,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    return journey.save_draft(
        session, _mapping_scoped(session, p, mapping_id), p, body.revision, body.payload.model_dump()
    )


@router.post("/preview")
def preview(
    mapping_id: str,
    body: DecisionsIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    return journey.preview(
        ctx,
        session,
        _mapping_scoped(session, p, mapping_id),
        p,
        body.answers,
        body.remember,
        body.acknowledged,
    )


@router.post("/confirm")
def confirm(
    mapping_id: str,
    body: DecisionsIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    result = journey.confirm(
        ctx,
        session,
        _mapping_scoped(session, p, mapping_id),
        p,
        body.answers,
        body.remember,
        body.acknowledged,
        body.digest,
        body.comment,
    )
    session.commit()
    if result["job_id"]:
        runner.deliver_jobs([result["job_id"]])
    return result
