"""Read-only, customer-scoped access to real job receipts and their business outputs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.auth import Principal, current_principal
from forma.core.errors import NotFound
from forma.db.models import BatchItem, ImportRound, Job, MappingVersion, SourceFile
from forma.db.session import get_session
from forma.jobs import activity, runner

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("/jobs/{job_id}/spans/{span_id}")
def span_detail(job_id: str, span_id: str, p: Principal = Depends(current_principal)) -> list[dict[str, Any]]:
    try:
        runner.job_status(job_id, customers=p.customers)
    except KeyError as exc:
        raise NotFound("job not found") from exc
    e = activity.events
    with activity.engine().connect() as conn:
        return [
            _receipt(r)
            for r in conn.execute(
                select(e)
                .where(
                    e.c.job_id == job_id,
                    e.c.span_id == span_id,
                )
                .order_by(e.c.id)
            ).mappings()
        ]


def _receipt(row: Any) -> dict[str, Any]:
    return {**row, "detail": json.loads(row["detail"])}


@router.get("/jobs")
def jobs(
    batch: str | None = None,
    round_id: str | None = None,
    status: str | None = None,
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    q = (
        select(Job)
        .where(Job.kind.not_in(("create_document", "assistant_message")))
        .outerjoin(ImportRound, ImportRound.id == Job.round_id)
    )
    if p.customers is not None:
        q = q.where(ImportRound.customer_id.in_(sorted(p.customers)))
    if batch:
        q = q.where(Job.batch_id == batch)
    if round_id:
        q = q.where(Job.round_id == round_id)
    if search:
        matching = select(SourceFile.round_id).where(
            SourceFile.original_name.icontains(search, autoescape=True)
        )
        items = select(BatchItem.id).where(BatchItem.file_name.icontains(search, autoescape=True))
        q = q.where(Job.round_id.in_(matching) | Job.payload["item_id"].as_string().in_(items))
    sub = q.subquery()
    counts: dict[str, int] = dict(
        session.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all()
    )
    if status:
        q = q.where(Job.status == status)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    selected = list(
        session.scalars(q.order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit))
    )
    rids = [j.round_id for j in selected if j.round_id]
    rounds = {r.id: r for r in session.scalars(select(ImportRound).where(ImportRound.id.in_(rids)))}
    files: dict[str, list[str]] = {}
    for f in session.scalars(select(SourceFile).where(SourceFile.round_id.in_(rids))):
        files.setdefault(f.round_id, []).append(f.original_name)
    item_ids = [j.payload.get("item_id") for j in selected if j.payload.get("item_id")]
    items_by_id = {i.id: i for i in session.scalars(select(BatchItem).where(BatchItem.id.in_(item_ids)))}
    latest: dict[str, Any] = {}
    journal_available = True
    if selected:
        try:
            e = activity.events
            with activity.engine().connect() as conn:
                ids = (
                    select(func.max(e.c.id))
                    .where(e.c.job_id.in_([j.id for j in selected]))
                    .group_by(e.c.job_id)
                )
                latest = {
                    r["job_id"]: _receipt(r)
                    for r in conn.execute(select(e).where(e.c.id.in_(ids))).mappings()
                }
        except Exception:
            journal_available = False
    result = []
    for job in selected:
        r = rounds.get(job.round_id or "")
        item = items_by_id.get(job.payload.get("item_id") or "")
        event = latest.get(job.id)
        if event:
            # Room polling carries a compact action, not hundreds of tool transcripts.
            event["detail"] = {"message": (event.get("detail") or {}).get("message")}
        result.append(
            {
                **runner._job_dict(job),
                "result": {
                    k: v
                    for k, v in (job.result or {}).items()
                    if k in {"state", "mapping_status", "mapping_version_id", "dataset_version_id"}
                },
                "error": (job.error or "").splitlines()[0] if job.error else None,
                "files": files.get(job.round_id or "", [item.file_name] if item else []),
                "round_status": r.status if r else None,
                "format_memory": (r.summary or {}).get("format_memory") if r else None,
                "latest": event if event and event["attempt"] == job.attempts else None,
            }
        )
    return {
        "items": result,
        "total": total,
        "counts": counts,
        "journal_available": journal_available,
        "next_offset": offset + limit if offset + limit < total else None,
    }


@router.get("/jobs/{job_id}")
def detail(
    job_id: str,
    attempt: int | None = Query(None, ge=0),
    before: int | None = Query(None, ge=1),
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    try:
        job = runner.job_status(job_id, customers=p.customers)
    except KeyError as exc:
        raise NotFound("job not found") from exc
    chosen = job["attempts"] if attempt is None else attempt
    e = activity.events
    with activity.engine().connect() as conn:
        attempts = list(
            conn.scalars(
                select(e.c.attempt).where(e.c.job_id == job_id).distinct().order_by(e.c.attempt.desc())
            )
        )
        q = select(e).where(e.c.job_id == job_id, e.c.attempt == chosen)
        if before:
            q = q.where(e.c.id < before)
        rows = [_receipt(r) for r in conn.execute(q.order_by(e.c.id.desc()).limit(101)).mappings()]
    more = len(rows) > 100
    rows = list(reversed(rows[:100]))
    # Historical attempts use their own receipts, never the latest attempt's mutable output.
    mid = (job.get("result") or {}).get("mapping_version_id") if chosen == job["attempts"] else None
    mv = session.get(MappingVersion, mid) if mid else None
    if mv and (p.customers is not None and mv.customer_id not in p.customers):
        mv = None
    mapping = (
        activity.safe(
            {
                "id": mv.id,
                "document": mv.document,
                "agent_log": mv.agent_log,
                "questions": mv.open_questions,
                "validation": mv.validation,
                "policy": mv.policy,
                "status": mv.status,
            }
        )
        if mv
        else None
    )
    return {
        "job": job,
        "attempt": chosen,
        "attempts": sorted(set([*attempts, job["attempts"]]), reverse=True),
        "events": rows,
        "previous_cursor": rows[0]["id"] if more else None,
        "mapping": mapping,
    }
