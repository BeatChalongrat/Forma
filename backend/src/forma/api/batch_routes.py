"""Bulk upload (ADR-027): many files at once → each matched to its customer by format → one JSON per file."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from forma.api.routes import ctx_dep, read_upload
from forma.api.schemas import BatchAssignIn
from forma.auth import Principal, current_principal, llm_rate_limited, require_role
from forma.core.errors import Conflict, Forbidden, ValidationFailed
from forma.db.session import get_session
from forma.jobs import runner
from forma.services import batches as bs
from forma.services import rounds as rs
from forma.services.context import ServiceContext

router = APIRouter(prefix="/api")


def _all_customers(p: Principal) -> None:
    # a bulk upload matches files against every customer's formats: a customer-scoped user cannot run one
    if p.customers is not None:
        raise Forbidden("bulk upload matches files to any customer; it needs access to all customers")


def _process(items: list[str], p: Principal, *, sync: bool, use_llm: bool | None) -> None:
    if items:
        runner.enqueue_batch(
            "process_batch_item",
            [{"item_id": i, "actor": p.actor, "use_llm": use_llm} for i in items],
            round_ids=[None] * len(items),
            sync=sync,
            request_id=p.request_id,
        )


@router.post("/batches", status_code=201)
async def create_batch(
    files: list[UploadFile] = File(...),
    label: str | None = Form(None),
    auto_publish: bool = Form(True),
    use_llm: bool | None = Form(None),
    sync: bool = Query(False),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """Store every file, then one job per file: match its format, make it a round, run the pipeline and
    (when ``auto_publish`` and the policy allow) publish its JSON."""
    _all_customers(p)
    if use_llm:
        llm_rate_limited(p)
    data = [(up.filename or "upload.xlsx", await read_upload(up, ctx.settings.max_upload_mb)) for up in files]
    b = bs.create_batch(ctx, session, data, p.actor, label=label, auto_publish=auto_publish)
    todo = [i.id for i in b.items if i.status == "received"]
    session.commit()
    # off the event loop: the arq backend submits with asyncio.run, which cannot nest in this async handler
    await run_in_threadpool(_process, todo, p, sync=sync, use_llm=use_llm)
    session.expire_all()
    return bs.batch_view(session, bs.get_batch(session, b.id))


@router.get("/batches")
def list_batches(
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    return [] if p.customers is not None else bs.list_batches(session, limit)


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: str, session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    _all_customers(p)
    return bs.batch_view(session, bs.get_batch(session, batch_id))


@router.post("/batches/{batch_id}/assign")
def assign(
    batch_id: str,
    body: BatchAssignIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """Files whose format matched no customer: give them a customer (an existing one or a new one)."""
    _all_customers(p)
    b = bs.get_batch(session, batch_id)
    if (body.customer_id is None) == (body.new_customer is None):
        raise ValidationFailed("give either customer_id or new_customer")
    items = {i.id: i for i in b.items}
    picked = [items[i] for i in body.item_ids if i in items]
    if len(picked) != len(body.item_ids):
        raise ValidationFailed("some files are not part of this upload")
    if any(i.status != "needs_customer" for i in picked):
        raise Conflict("only files waiting for a customer can be assigned")
    customer_id = body.customer_id
    if body.new_customer is not None:
        nc = body.new_customer
        customer_id = rs.create_customer(session, nc.code, nc.name, nc.erp_type, nc.notes).id
    assert customer_id is not None
    for it in picked:
        bs.attach(ctx, session, it, customer_id, p.actor)
    session.commit()
    _process([i.id for i in picked], p, sync=body.sync, use_llm=body.use_llm)
    session.expire_all()
    return bs.batch_view(session, bs.get_batch(session, batch_id))


@router.post("/batches/{batch_id}/resume")
def resume(
    batch_id: str,
    sync: bool = Query(False),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """Queue again the files no job is working on (the queue was full or down at upload time)."""
    _all_customers(p)
    b = bs.get_batch(session, batch_id)
    todo = bs.stuck_items(session, b)
    session.commit()
    _process(todo, p, sync=sync, use_llm=None)
    session.expire_all()
    return bs.batch_view(session, bs.get_batch(session, batch_id))


@router.get("/batches/{batch_id}/download")
def download(
    batch_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> Response:
    """A zip with one JSON per file whose JSON is ready, named after the file."""
    _all_customers(p)
    b = bs.get_batch(session, batch_id)
    blob, n = bs.zip_outputs(ctx, session, b)
    if n == 0:
        raise Conflict("no JSON is ready yet in this upload")
    rs.audit(session, "batch.download", "batch", b.id, actor=p.actor, files=n)
    name = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (b.label or b.id)) or b.id
    return Response(
        blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )
