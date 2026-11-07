"""Owner- and access-scope-checked report queue. No business mutations or paid calls."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal, require_role
from forma.db.models import Job
from forma.db.session import get_session
from forma.jobs import runner
from forma.reporting.registry import catalog
from forma.services import conversations
from forma.services import document_reports as reports

router = APIRouter(prefix="/api/assistant", tags=["documents"])


@router.get("/report-templates")
def templates(p: Principal = Depends(require_role("importer"))) -> dict[str, Any]:
    return catalog()


@router.get("/conversations/{chat_id}/reports")
def jobs(
    chat_id: str,
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    conversations.get_chat(session, p, chat_id)
    rows = list(
        session.scalars(
            select(Job)
            .where(Job.kind == reports.KIND, Job.payload["chat_id"].as_string() == chat_id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .offset(offset)
            .limit(21)
        )
    )
    return {
        "items": [reports.view(j) for j in rows[:20]],
        "next_offset": offset + 20 if len(rows) > 20 else None,
    }


@router.get("/reports/{job_id}")
def detail(
    job_id: str, session: Session = Depends(get_session), p: Principal = Depends(require_role("importer"))
) -> dict[str, Any]:
    j = reports.scoped(session, p, job_id)
    return {**reports.view(j), "snapshot": j.payload["snapshot"], "error_detail": (j.error or "")[-3000:]}


@router.post("/reports/{job_id}/{action}")
def act(
    job_id: str,
    action: Literal["cancel", "retry"],
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, str]:
    reports.scoped(session, p, job_id)
    session.commit()
    if action == "cancel":
        runner.cancel_job(job_id, actor=p.actor)
    else:
        runner.retry_job(job_id)
    return {"id": job_id}
