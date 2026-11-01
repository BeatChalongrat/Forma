"""REST API. One router per bounded context, all mounted under /api."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from forma.ai.agents import copilot_agent, diagnosis_agent
from forma.api import serialize as ser
from forma.api.schemas import (
    AskIn,
    BatchRunIn,
    ChangesIn,
    CloseIn,
    CompareIn,
    CopilotIn,
    CustomerIn,
    CustomerPatch,
    Decision,
    MappingEditIn,
    MappingPreviewIn,
    MoveIn,
    ProposeIn,
    RelationshipsIn,
    RoundIn,
    SchemaIn,
    SourcePreviewIn,
)
from forma.auth import (
    Principal,
    authenticated_principal,
    current_principal,
    ensure_all_customers,
    ensure_customer,
    llm_rate_limited,
    require_role,
)
from forma.core.errors import Forbidden, NotFound, PayloadTooLarge, ValidationFailed
from forma.db.models import (
    ChangeSet,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    LLMCall,
    MappingVersion,
    Override,
    ReviewTask,
)
from forma.db.session import get_session
from forma.export.exporter import MEDIA_TYPES
from forma.jobs import runner
from forma.mapping_dsl.models import MappingDocument, Op
from forma.schema_registry import PROVENANCE_KEY, TargetSchema
from forma.services import assistant
from forma.services import config as cfg
from forma.services import datasets as ds
from forma.services import lineage as lin
from forma.services import mapping as ms
from forma.services import publishing as pub
from forma.services import relationships as rel
from forma.services import round_lifecycle as lifecycle
from forma.services import rounds as rs
from forma.services.context import ServiceContext, get_context

api = APIRouter(prefix="/api")


def ctx_dep() -> ServiceContext:
    return get_context()


def _scoped(p: Principal, q: Any, col: Any) -> Any:
    """Tenant scoping (ADR-013): restrict a query to the principal's customers when a claim is present."""
    return q if p.customers is None else q.where(col.in_(sorted(p.customers)))


def _round_scoped(session: Session, p: Principal, round_id: str) -> ImportRound:
    r = rs.get_round(session, round_id)
    ensure_customer(p, r.customer_id)
    return r


def _mapping_scoped(session: Session, p: Principal, mapping_id: str) -> MappingVersion:
    m = ms.get_mapping(session, mapping_id)
    ensure_customer(p, m.customer_id)
    return m


def _dataset_scoped(session: Session, p: Principal, dataset_id: str) -> DatasetVersion:
    d = ds.get_dataset(session, dataset_id)
    ensure_customer(p, d.customer_id)
    return d


# ============================================================================================
# Identity
# ============================================================================================
@api.get("/me")
def me(p: Principal = Depends(authenticated_principal)) -> dict[str, Any]:
    return p.to_dict()


@api.get("/auth/config")
def auth_config(ctx: ServiceContext = Depends(ctx_dep)) -> dict[str, Any]:
    """Public: what the SPA needs to log in (never secrets)."""
    s = ctx.settings
    if s.auth != "oidc":
        from forma.auth.deps import DEV_USERS

        return {"mode": "dev", "dev_users": sorted(DEV_USERS)}
    return {
        "mode": "oidc",
        "issuer": s.auth_issuer,
        "client_id": s.auth_client_id,
        "audience": s.auth_audience,
        "scopes": "openid profile email",
    }


# ============================================================================================
# Customers
# ============================================================================================
@api.get("/customers")
def list_customers(
    session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> list[dict[str, Any]]:
    rows = session.scalars(_scoped(p, select(Customer), Customer.id).order_by(Customer.code)).all()
    counts = dict(
        session.execute(select(ImportRound.customer_id, func.count()).group_by(ImportRound.customer_id)).all()
    )
    last = dict(
        session.execute(
            select(ImportRound.customer_id, func.max(ImportRound.created_at)).group_by(
                ImportRound.customer_id
            )
        ).all()
    )
    approved = {
        m.customer_id
        for m in session.scalars(select(MappingVersion).where(MappingVersion.status == "approved"))
    }
    from forma.services import foresight

    by_customer: dict[str, list[ImportRound]] = {}
    for r in session.scalars(select(ImportRound).order_by(ImportRound.created_at)):
        by_customer.setdefault(r.customer_id, []).append(r)
    return [
        ser.customer(
            c,
            rounds=counts.get(c.id, 0),
            last_round_at=ser._dt(last.get(c.id)),
            has_approved_mapping=c.id in approved,
            next_expected=foresight.cadence(by_customer.get(c.id, [])),  # ADR-039
        )
        for c in rows
    ]


@api.post("/customers", status_code=201)
def create_customer(
    body: CustomerIn, session: Session = Depends(get_session), p: Principal = Depends(require_role("admin"))
) -> dict[str, Any]:
    return ser.customer(rs.create_customer(session, body.code, body.name, body.erp_type, body.notes))


@api.patch("/customers/{customer_id}")
def update_customer(
    customer_id: str,
    body: CustomerPatch,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    ensure_customer(p, customer_id)
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    if patch.get("default_target_schema"):
        cfg.resolve_target_schema(session, ctx.registry, patch["default_target_schema"], None)
    return ser.customer(rs.update_customer(session, customer_id, patch, p.actor))


@api.get("/customers/{customer_id}")
def get_customer(
    customer_id: str, session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    c = rs.get_customer(session, customer_id)
    ensure_customer(p, c.id)
    rounds = session.scalars(
        select(ImportRound).where(ImportRound.customer_id == c.id).order_by(ImportRound.created_at.desc())
    ).all()
    mappings = session.scalars(
        select(MappingVersion)
        .where(MappingVersion.customer_id == c.id)
        .order_by(MappingVersion.created_at.desc())
    ).all()
    overrides = session.scalars(
        select(Override).where(Override.customer_id == c.id, Override.active.is_(True))
    ).all()
    return ser.customer(
        c,
        rounds=[ser.import_round(r, files=False) for r in rounds],
        mappings=[ser.mapping_version(m, document=False) for m in mappings],
        overrides=[
            {
                "id": o.id,
                "target": o.target,
                "where": o.where,
                "value": o.value,
                "note": o.note,
                "author": o.author,
                "created_at": o.created_at.isoformat(),
            }
            for o in overrides
        ],
    )


@api.delete("/customers/{customer_id}/overrides/{override_id}")
def delete_override(
    customer_id: str,
    override_id: str,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    ensure_customer(p, customer_id)
    o = session.get(Override, override_id)
    if o is None or o.customer_id != customer_id:
        raise NotFound("override not found")
    o.active = False
    return {"ok": True}


# ============================================================================================
# Import rounds & files
# ============================================================================================
@api.get("/rounds")
def list_rounds(
    status: str | None = None,
    customer_id: str | None = None,
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    q = (
        _scoped(p, select(ImportRound), ImportRound.customer_id)
        .order_by(ImportRound.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        q = q.where(ImportRound.status == status)
    if customer_id:
        q = q.where(ImportRound.customer_id == customer_id)
    rounds = session.scalars(q).all()
    customers = {c.id: c for c in session.scalars(select(Customer)).all()}
    return [ser.import_round(r, customer_obj=customers.get(r.customer_id)) for r in rounds]


@api.post("/rounds", status_code=201)
def create_round(
    body: RoundIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    ensure_customer(p, body.customer_id)
    c = rs.get_customer(session, body.customer_id)
    ref = cfg.resolve_target_schema(session, ctx.registry, body.target_schema, c.default_target_schema)
    return ser.import_round(rs.create_round(session, body.customer_id, body.label, ref))


@api.get("/rounds/{round_id}")
def get_round(
    round_id: str, session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    r = _round_scoped(session, p, round_id)
    c = session.get(Customer, r.customer_id)
    mappings = session.scalars(
        select(MappingVersion)
        .where(MappingVersion.round_id == r.id)
        .order_by(MappingVersion.created_at.desc())
    ).all()
    datasets = session.scalars(
        select(DatasetVersion).where(DatasetVersion.round_id == r.id).order_by(DatasetVersion.version.desc())
    ).all()
    tasks = session.scalars(
        select(ReviewTask).where(ReviewTask.round_id == r.id).order_by(ReviewTask.created_at.desc())
    ).all()
    jobs = session.scalars(
        select(Job).where(Job.round_id == r.id).order_by(Job.created_at.desc()).limit(10)
    ).all()
    d = ser.import_round(r, customer_obj=c)
    d.update(lifecycle.related(session, r))
    d["mappings"] = [ser.mapping_version(m, document=False) for m in mappings]
    d["datasets"] = [ser.dataset_version(x) for x in datasets]
    d["review_tasks"] = [ser.review_task(t) for t in tasks]
    d["jobs"] = [
        {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "progress": j.progress,
            "message": j.message,
            "error": j.error,
        }
        for j in jobs
    ]
    return d


async def read_upload(up: UploadFile, limit_mb: int) -> bytes:
    """An uploaded file's bytes, refused past ``FORMA_MAX_UPLOAD_MB`` (checked while reading) or when empty."""
    limit = limit_mb * 1024 * 1024
    if up.size is not None and up.size > limit:
        raise PayloadTooLarge(
            f"{up.filename} is {up.size / 1024 / 1024:.1f} MB; the limit is {limit_mb} MB",
            details={"file": up.filename, "size": up.size, "limit_bytes": limit},
        )
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await up.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise PayloadTooLarge(
                f"{up.filename} exceeds the upload limit of {limit_mb} MB",
                details={"file": up.filename, "limit_bytes": limit},
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValidationFailed(f"{up.filename} is empty")
    return data


@api.post("/rounds/{round_id}/files", status_code=201)
async def upload_files(
    round_id: str,
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> list[dict[str, Any]]:
    r = _round_scoped(session, p, round_id)
    out = []
    for up in files:
        data = await read_upload(up, ctx.settings.max_upload_mb)
        out.append(ser.source_file(rs.add_file(ctx, session, r, up.filename or "upload.xlsx", data)))
    return out


@api.get("/rounds/{round_id}/files/{file_id}")
def get_file(
    round_id: str,
    file_id: str,
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    r = _round_scoped(session, p, round_id)
    f = next((x for x in r.files if x.id == file_id), None)
    if f is None:
        raise NotFound("file not found")
    return ser.source_file(f, full=True)


@api.get("/rounds/{round_id}/files/{file_id}/grid")
def file_grid(
    round_id: str,
    file_id: str,
    sheet: int = 0,
    rows: int = Query(60, le=500),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Raw cells of a sheet (for the side-by-side Excel view)."""
    from forma.ingestion.readers import read_raw

    r = _round_scoped(session, p, round_id)
    f = next((x for x in r.files if x.id == file_id), None)
    if f is None:
        raise NotFound("file not found")
    df = read_raw(ctx.storage.local_path(f.storage_key), sheet if f.kind == "excel" else None, n_rows=rows)
    return {"sheet": sheet, "n_cols": df.width, "rows": [list(r_) for r_ in df.iter_rows()]}


@api.post("/rounds/{round_id}/profile")
def profile_round(
    round_id: str,
    sync: bool = False,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    _round_scoped(session, p, round_id)
    session.commit()
    return {
        "job_id": runner.enqueue(
            "profile_round", {"round_id": round_id}, round_id=round_id, sync=sync, request_id=p.request_id
        )
    }


@api.post("/rounds/{round_id}/propose")
def propose(
    round_id: str,
    body: ProposeIn | None = None,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    body = body or ProposeIn()
    _round_scoped(session, p, round_id)
    if body.use_llm:
        llm_rate_limited(p)
    session.commit()
    return {
        "job_id": runner.enqueue(
            "propose_mapping",
            {"round_id": round_id, "use_llm": body.use_llm, "actor": p.actor},
            round_id=round_id,
            sync=body.sync,
            request_id=p.request_id,
        )
    }


@api.post("/rounds/{round_id}/cancel")
def cancel_round(
    round_id: str,
    body: CloseIn,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """Close a round that should not count: it leaves every queue, its files and history stay (ADR-052)."""
    r = _round_scoped(session, p, round_id)
    lifecycle.cancel(session, p, r, body.reason)
    session.commit()
    return ser.import_round(r, customer_obj=session.get(Customer, r.customer_id))


@api.post("/rounds/{round_id}/reopen")
def reopen_round(
    round_id: str, session: Session = Depends(get_session), p: Principal = Depends(require_role("importer"))
) -> dict[str, Any]:
    r = _round_scoped(session, p, round_id)
    lifecycle.reopen(session, p, r)
    session.commit()
    return ser.import_round(r, customer_obj=session.get(Customer, r.customer_id))


@api.post("/rounds/{round_id}/move", status_code=201)
def move_round(
    round_id: str,
    body: MoveIn,
    sync: bool = Query(False),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """The file was another customer's: the same files become that customer's round and run at once."""
    r = _round_scoped(session, p, round_id)
    ensure_customer(p, body.customer_id)
    new = lifecycle.move(session, p, r, body.customer_id, body.reason)
    session.commit()
    job_id = runner.enqueue(
        "run_pipeline",
        {"round_id": new.id, "actor": p.actor, "auto_publish": bool((new.summary or {}).get("auto_publish"))},
        round_id=new.id,
        sync=sync,
        request_id=p.request_id,
    )
    return {**ser.import_round(new, customer_obj=session.get(Customer, new.customer_id)), "job_id": job_id}


@api.post("/rounds/{round_id}/replace", status_code=201)
async def replace_round(
    round_id: str,
    files: list[UploadFile] = File(...),
    reason: str = Form(..., min_length=1, max_length=500),
    label: str | None = Form(None, max_length=255),
    sync: bool = Query(False),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """A corrected delivery takes the old round's place; the old JSON leaves the work round's zip."""
    r = _round_scoped(session, p, round_id)
    loaded = [(f.filename or "upload.xlsx", await read_upload(f, ctx.settings.max_upload_mb)) for f in files]
    new = lifecycle.replace(ctx, session, p, r, loaded, reason, label)
    session.commit()
    payload = {
        "round_id": new.id,
        "actor": p.actor,
        "auto_publish": bool((new.summary or {}).get("auto_publish")),
    }
    # off the event loop: the arq backend submits with asyncio.run, which cannot nest in this async handler
    job_id = await run_in_threadpool(
        lambda: runner.enqueue("run_pipeline", payload, round_id=new.id, sync=sync, request_id=p.request_id)
    )
    session.expire_all()
    return {**ser.import_round(new, customer_obj=session.get(Customer, new.customer_id)), "job_id": job_id}


@api.post("/rounds/{round_id}/run")
def run_pipeline(
    round_id: str,
    body: ProposeIn | None = None,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """One click: profile → propose → (auto-approve) → transform."""
    body = body or ProposeIn()
    _round_scoped(session, p, round_id)
    if body.use_llm:
        llm_rate_limited(p)
    session.commit()
    return {
        "job_id": runner.enqueue(
            "run_pipeline",
            {"round_id": round_id, "use_llm": body.use_llm, "actor": p.actor},
            round_id=round_id,
            sync=body.sync,
            request_id=p.request_id,
        )
    }


@api.post("/rounds/{round_id}/transform")
def transform(
    round_id: str,
    mapping_version_id: str | None = None,
    actor: str = "operator",
    sync: bool = False,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    _round_scoped(session, p, round_id)
    session.commit()
    return {
        "job_id": runner.enqueue(
            "transform_round",
            {"round_id": round_id, "mapping_version_id": mapping_version_id, "actor": p.actor, "force": True},
            round_id=round_id,
            sync=sync,
            request_id=p.request_id,
        )
    }


@api.post("/rounds/batch-run")
def batch_run(
    body: BatchRunIn,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    """Fan out one `run_pipeline` job per round under a single batch id (300-file rounds, bulk re-runs)."""
    ids = list(dict.fromkeys(body.round_ids))
    found = set(
        session.scalars(
            _scoped(p, select(ImportRound.id).where(ImportRound.id.in_(ids)), ImportRound.customer_id)
        ).all()
    )
    if body.use_llm:
        llm_rate_limited(p)
    missing = [i for i in ids if i not in found]
    if missing:
        raise NotFound(f"unknown rounds: {missing[:5]}")
    session.commit()
    batch_id, job_ids = runner.enqueue_batch(
        "run_pipeline",
        [{"round_id": rid, "use_llm": body.use_llm, "actor": p.actor, "force": body.force} for rid in ids],
        sync=body.sync,
        request_id=p.request_id,
    )
    return {"batch_id": batch_id, "job_ids": job_ids, "count": len(job_ids)}


# ============================================================================================
# Mappings
# ============================================================================================
@api.get("/mappings/{mapping_id}")
def get_mapping(
    mapping_id: str, session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    return ser.mapping_version(_mapping_scoped(session, p, mapping_id))


@api.post("/mappings/{mapping_id}/approve")
def approve_mapping(
    mapping_id: str,
    body: Decision,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _mapping_scoped(session, p, mapping_id)
    if body.override_block and not p.has_role("approver"):
        raise Forbidden("overriding a policy block needs the approver role", details={"required": "approver"})
    mv = ms.approve_mapping(session, mapping_id, p.actor, body.comment, override_block=body.override_block)
    r = session.get(ImportRound, mv.round_id) if mv.round_id else None
    out = ser.mapping_version(mv, document=False)
    if r is not None and (r.summary or {}).get("auto_publish"):
        # a bulk-upload round (ADR-027): once a person approves the mapping, finish it automatically
        session.commit()
        runner.enqueue(
            "run_pipeline",
            {"round_id": r.id, "actor": p.actor, "auto_publish": True},
            round_id=r.id,
            request_id=p.request_id,
        )
    return out


@api.post("/mappings/{mapping_id}/reject")
def reject_mapping(
    mapping_id: str,
    body: Decision,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _mapping_scoped(session, p, mapping_id)
    return ser.mapping_version(ms.reject_mapping(session, mapping_id, p.actor, body.comment), document=False)


@api.post("/mappings/{mapping_id}/edit", status_code=201)
def edit_mapping(
    mapping_id: str,
    body: MappingEditIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _mapping_scoped(session, p, mapping_id)
    return ser.mapping_version(ms.save_mapping_edit(ctx, session, mapping_id, body.document, p.actor))


@api.post("/rounds/{round_id}/mapping-preview")
def mapping_preview(
    round_id: str,
    body: MappingPreviewIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _round_scoped(session, p, round_id)
    return ms.preview_mapping(ctx, session, round_id, body.document, limit=body.limit)


@api.post("/rounds/{round_id}/relationships")
def round_relationships(
    round_id: str,
    body: RelationshipsIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Visual mapping (ADR-019): tables, the keys that connect them and how well, on real rows."""
    _round_scoped(session, p, round_id)
    probe = body.probe
    return rel.discover(
        ctx,
        session,
        round_id,
        body.document,
        limit=body.limit,
        probe=(probe.from_.node, probe.from_.column, probe.to.node, probe.to.column) if probe else None,
    )


@api.get("/mappings/{mapping_id}/field-review")
def mapping_field_review(
    mapping_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Self-healing (ADR-021): each field that changed vs the approved mapping, checked on the data."""
    _mapping_scoped(session, p, mapping_id)
    return ms.field_review(ctx, session, mapping_id)


@api.post("/mappings/{mapping_id}/compare")
def compare_mapping(
    mapping_id: str,
    body: CompareIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Sandbox (ADR-018): this mapping, or unsaved edits of it, next to the approved one on real files."""
    _mapping_scoped(session, p, mapping_id)
    return ms.sandbox_compare(ctx, session, mapping_id, body.document, limit=body.limit)


@api.get("/mappings/{mapping_id}/sandbox")
def mapping_sandbox(
    mapping_id: str,
    list: str = "price_changes",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """The comparison stored with this version, and one page of a drill-down list."""
    _mapping_scoped(session, p, mapping_id)
    return ms.sandbox_details(ctx, session, mapping_id, list, offset, limit)


@api.post("/rounds/{round_id}/source-preview")
def source_preview(
    round_id: str,
    body: SourcePreviewIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _round_scoped(session, p, round_id)
    return ms.source_preview(ctx, session, round_id, body.source, limit=body.limit)


@api.get("/dsl")
def dsl_reference(p: Principal = Depends(current_principal)) -> dict[str, Any]:
    return {"ops": [o.value for o in Op], "json_schema": MappingDocument.json_schema_document()}


# ============================================================================================
# Review
# ============================================================================================
@api.get("/review")
def review_queue(
    status: str = "open",
    round_id: str | None = None,
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    from forma.services.review_explanations import explain

    q = _scoped(p, select(ReviewTask), ReviewTask.customer_id)
    if round_id:
        q = q.where(ReviewTask.round_id == round_id)
    tasks = session.scalars(
        q.where(ReviewTask.status == status)
        .order_by(ReviewTask.priority.desc(), ReviewTask.created_at.asc())
        .limit(limit)
        .offset(offset)
    ).all()
    customers = {c.id: c for c in session.scalars(select(Customer)).all()}
    rounds = (
        {
            r.id: r
            for r in session.scalars(
                select(ImportRound).where(ImportRound.id.in_([t.round_id for t in tasks]))
            ).all()
        }
        if tasks
        else {}
    )
    mappings = {
        m.id: m
        for m in session.scalars(
            select(MappingVersion).where(
                MappingVersion.id.in_([t.mapping_version_id for t in tasks if t.mapping_version_id])
            )
        ).all()
    }
    out = []
    for t in tasks:
        c = customers.get(t.customer_id)
        r = rounds.get(t.round_id)
        out.append(
            ser.review_task(
                t,
                explanations=explain(
                    get_context(),
                    t.reasons,
                    mappings[t.mapping_version_id].target_schema
                    if t.mapping_version_id in mappings
                    else None,
                ),
                customer={"id": c.id, "code": c.code, "name": c.name} if c else None,
                round={"id": r.id, "label": r.label, "status": r.status} if r else None,
            )
        )
    return out


# ============================================================================================
# Datasets
# ============================================================================================
@api.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str, session: Session = Depends(get_session), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    d = _dataset_scoped(session, p, dataset_id)
    out = ser.dataset_version(d)
    out["change_sets"] = [
        ser.change_set(c)
        for c in session.scalars(select(ChangeSet).where(ChangeSet.dataset_version_id == d.id))
    ]
    out["exports"] = [
        ser.export_artifact(e)
        for e in session.scalars(select(ExportArtifact).where(ExportArtifact.dataset_version_id == d.id))
    ]
    out["history"] = [
        ser.dataset_version(v)
        for v in session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.round_id == d.round_id)
            .order_by(DatasetVersion.version.asc())
        )
    ]
    return out


@api.get("/datasets/{dataset_id}/rows")
def dataset_rows(
    dataset_id: str,
    offset: int = 0,
    limit: int = Query(100, le=1000),
    q: str | None = None,
    sort: str | None = None,
    desc: bool = False,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    d = _dataset_scoped(session, p, dataset_id)
    schema = ctx.registry.get_ref(ms.get_mapping(session, d.mapping_version_id).target_schema)
    records = ds.records_of(ctx, d)
    if q:
        ql = q.lower()
        records = [
            r for r in records if ql in str({k: v for k, v in r.items() if k != PROVENANCE_KEY}).lower()
        ]
    if sort:
        from forma.engine.transform import _get_pointer

        records.sort(key=lambda r: (_get_pointer(r, sort) is None, str(_get_pointer(r, sort))), reverse=desc)
    page = records[offset : offset + limit]
    return {
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "identity_keys": schema.identity_keys,
        "rows": page,
        "columns": [f.to_dict() for f in schema.fields()],
        "findings": d.findings[:200],
    }


@api.post("/datasets/{dataset_id}/preview-changes")
def preview_changes(
    dataset_id: str,
    body: ChangesIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    return ds.preview_changes(ctx, session, dataset_id, body.operations)


@api.post("/datasets/{dataset_id}/changes", status_code=201)
def apply_changes(
    dataset_id: str,
    body: ChangesIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    if body.kind == "persistent" and not p.has_role("approver"):
        raise Forbidden("persistent overrides require the approver role")
    nv, cs = ds.apply_changes(
        ctx, session, dataset_id, body.operations, kind=body.kind, actor=p.actor, note=body.note
    )
    return {"dataset": ser.dataset_version(nv), "change_set": ser.change_set(cs)}


@api.post("/datasets/{dataset_id}/rollback", status_code=201)
def rollback(
    dataset_id: str,
    body: Decision,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    return ser.dataset_version(ds.rollback(ctx, session, dataset_id, p.actor))


@api.post("/datasets/{dataset_id}/approve")
def approve_dataset(
    dataset_id: str,
    body: Decision,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    dv = ds.approve_dataset(
        session,
        dataset_id,
        p.actor,
        ctx=ctx,
        acknowledge_anomalies=body.acknowledge_anomalies,
        comment=body.comment,
    )
    r = session.get(ImportRound, dv.round_id)
    if r is not None:
        pub.after_manual_approval(ctx, session, r)  # a bulk-upload round: its JSON is written at once
    return ser.dataset_version(dv)


@api.get("/datasets/{dataset_id}/changes-report")
def changes_report(
    dataset_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    return ds.change_report(ctx, session, dataset_id)


@api.get("/datasets/{dataset_id}/lineage/{identity}")
def lineage(
    dataset_id: str,
    identity: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    return ds.lineage(ctx, session, dataset_id, identity)


@api.get("/datasets/{dataset_id}/trace/{identity}")
def trace(
    dataset_id: str,
    identity: str,
    pointer: str | None = None,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Explainable lineage (ADR-020): every value back to its Excel cell; with ``pointer``, why it changed."""
    _dataset_scoped(session, p, dataset_id)
    return lin.trace(ctx, session, dataset_id, identity, pointer)


@api.post("/datasets/{dataset_id}/export", status_code=201)
def export_dataset(
    dataset_id: str,
    fmt: str = "json",
    actor: str = "operator",
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    _dataset_scoped(session, p, dataset_id)
    return ser.export_artifact(ds.export_dataset(ctx, session, dataset_id, fmt, p.actor))


@api.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> Response:
    """Pre-signed redirect when the storage supports it (S3/MinIO), streamed proxy otherwise."""
    e = session.get(ExportArtifact, export_id)
    if e is None:
        raise NotFound("export not found")
    ensure_customer(p, e.customer_id)
    name = e.storage_key.rsplit("/", 1)[-1]
    url = ctx.storage.url(e.storage_key, expires_s=ctx.settings.s3_presign_s, filename=name)
    if url:
        return RedirectResponse(url, status_code=307)
    fh = ctx.storage.open(e.storage_key)
    return StreamingResponse(
        iter(lambda: fh.read(1024 * 1024), b""),
        media_type=MEDIA_TYPES[e.fmt],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ============================================================================================
# AI: diagnosis & copilot
# ============================================================================================
@api.post("/mappings/{mapping_id}/diagnose")
def diagnose(
    mapping_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
    _rl: Principal = Depends(llm_rate_limited),
) -> dict[str, Any]:
    m = _mapping_scoped(session, p, mapping_id)
    schema = ctx.registry.get_ref(m.target_schema)
    doc = MappingDocument.model_validate(m.document)
    findings = ((m.policy or {}).get("simulation") or {}).get("findings", {})
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    sim, problems = ms.simulate(ctx, doc, schema, r.files if r else [], limit=500)
    flist = (
        [f.to_dict() for f in sim.findings]
        if sim
        else [{"kind": "binding", "severity": "error", "message": p} for p in problems]
    )
    if ctx.llm.is_mock:
        return {
            "ai": False,
            "findings": flist,
            "summary_th": "ยังไม่ได้ตั้งค่า LLM (FORMA_LLM_PROVIDER) — แสดงผลตรวจสอบจาก engine เท่านั้น",
            "root_causes": _rule_based_causes(flist),
        }
    res, patched = diagnosis_agent(ctx.llm, doc, schema, flist, sim.records[:5] if sim else [])
    ms._record_llm(session, m.round_id, res)
    out = dict(res.data)
    out["ai"] = True
    out["findings"] = flist
    out["patched_mapping"] = patched.model_dump(mode="json", exclude_none=True) if patched else None
    _ = findings
    return out


def _rule_based_causes(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in findings:
        msg = f.get("message", "")
        if f["kind"] == "join":
            out.append(
                {
                    "finding": msg,
                    "cause": "Link keys differ between tables (format, leading zeros, or missing rows).",
                    "fix": "Check normalize_keys (strip_leading_zeros) or the link column.",
                    "fix_kind": "mapping",
                    "severity": "high",
                }
            )
        elif f["kind"] == "dedupe":
            out.append(
                {
                    "finding": msg,
                    "cause": "The identity key is not unique in the source (several rows per SKU).",
                    "fix": "Add warehouse/price list to identity, or dedupe with keep=first.",
                    "fix_kind": "mapping",
                    "severity": "high",
                }
            )
        elif f["kind"] == "validation":
            out.append(
                {
                    "finding": msg,
                    "cause": "A value does not satisfy the target schema type/enum.",
                    "fix": "Add map_values / parse_* op or fix the source cell.",
                    "fix_kind": "mapping",
                    "severity": "medium",
                }
            )
        elif f["kind"] == "compile":
            out.append(
                {
                    "finding": msg,
                    "cause": "A referenced source column does not exist (renamed or moved header).",
                    "fix": "Re-point the column in Mapping Studio.",
                    "fix_kind": "mapping",
                    "severity": "high",
                }
            )
    return out


@api.post("/datasets/{dataset_id}/copilot")
def copilot(
    dataset_id: str,
    body: CopilotIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("reviewer")),
    _rl: Principal = Depends(llm_rate_limited),
) -> dict[str, Any]:
    """Natural-language edit → planned safe operations + impact preview (nothing is applied)."""
    d = _dataset_scoped(session, p, dataset_id)
    schema = ctx.registry.get_ref(ms.get_mapping(session, d.mapping_version_id).target_schema)
    records = ds.records_of(ctx, d)
    if ctx.llm.is_mock:
        plan = assistant.rule_based_plan(body.instruction, schema)
        if plan is None:
            return {
                "ai": False,
                "operations": [],
                "needs_clarification": "ยังไม่ได้ตั้งค่า LLM — รองรับคำสั่ง เช่น 'ลดราคาหมวด X 5% เฉพาะที่ stock มากกว่า 100 "
                "และไม่รวมสินค้าที่อยู่ในโปรโมชั่น' หรือ 'ตั้งสถานะ inactive ให้หมวด Y' (ต้องระบุว่าใช้กับสินค้ากลุ่มไหน)",
            }
        ops = plan
    else:
        derived = [d["name"] for d in ds.derived_columns(schema)]
        res = copilot_agent(ctx.llm, body.instruction, schema, records[:5], derived=derived)
        ms._record_llm(session, d.round_id, res)
        if res.data.get("needs_clarification"):
            return {"ai": True, "operations": [], "needs_clarification": res.data["needs_clarification"]}
        ops = _copilot_to_ops(res.data.get("operations", []), schema)
    _, impact = ds.apply_operations(records, ops, schema)
    return {"ai": not ctx.llm.is_mock, "operations": ops, "impact": impact, "needs_clarification": None}


@api.post("/datasets/{dataset_id}/ask")
def ask(
    dataset_id: str,
    body: AskIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    """Questions about the data ("why did 120 SKUs not join?") answered from evidence (ADR-023)."""
    _dataset_scoped(session, p, dataset_id)
    return assistant.answer(ctx, session, dataset_id, body.question)


def _copilot_to_ops(ops: list[dict[str, Any]], schema: TargetSchema) -> list[dict[str, Any]]:
    status = schema.semantic_field("status")
    out = []
    for o in ops:
        k = o.get("kind")
        if k == "scale_field":
            out.append(
                {
                    "kind": "scale_field",
                    "target": o["target"],
                    "factor": o.get("factor", 1.0),
                    "where": o.get("where"),
                    "explanation": o.get("explanation_th"),
                }
            )
        elif k == "set_field" or (k == "set_status" and status is not None):
            out.append(
                {
                    "kind": "set_field",
                    "target": o["target"] if k == "set_field" or status is None else status.pointer,
                    "value": o.get("value"),
                    "where": o.get("where"),
                    "explanation": o.get("explanation_th"),
                }
            )
        elif k == "delete_rows":
            out.append(
                {"kind": "delete_rows", "where": o.get("where"), "explanation": o.get("explanation_th")}
            )
    return out


# ============================================================================================
# Schemas, policy, dashboard, jobs
# ============================================================================================
@api.get("/schemas")
def list_schemas(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    stored, default = cfg.stored_refs(session), cfg.default_target_schema(session)
    latest = {s.schema_id: s.version for s in ctx.registry.list()}  # list() is sorted by (id, version)
    return [
        {
            **s.to_summary(),
            "source": "ui" if s.ref in stored else "file",
            "is_latest": latest[s.schema_id] == s.version,
            # "product" means the latest version, "product@1" pins one
            "is_default": default == s.ref or (default == s.schema_id and latest[s.schema_id] == s.version),
        }
        for s in ctx.registry.list()
    ]


@api.get("/schemas/{ref}")
def get_schema(
    ref: str, ctx: ServiceContext = Depends(ctx_dep), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    s = ctx.registry.get_ref(ref)
    return {
        **s.to_summary(),
        "fields": [f.to_dict() for f in s.fields(include_system=True)],
        "document": s.document,
    }


@api.post("/schemas", status_code=201)
def register_schema(
    body: SchemaIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    """A new target format (or a new version of one) is stored in the database, never written into the
    image: every API and worker process loads it on its next config check (ADR-017)."""
    s = ctx.registry.prepare(body.document)
    cfg.store_schema(session, s, p.actor)
    session.commit()
    ctx.refresh()
    return s.to_summary()


@api.get("/schemas/{a}/diff/{b}")
def diff_schema(
    a: str, b: str, ctx: ServiceContext = Depends(ctx_dep), p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    return ctx.registry.diff(ctx.registry.get_ref(a), ctx.registry.get_ref(b))


@api.get("/policy")
def get_policy(
    ctx: ServiceContext = Depends(ctx_dep), p: Principal = Depends(require_role("admin"))
) -> dict[str, Any]:
    return ctx.policy.policy


def _learning_brief(session: Session, p: Principal) -> dict[str, Any]:
    """Home's 'learned from you' figures (ADR-038)."""
    from forma.services import learning

    s = learning.stats(session, months=2, customers=p.customers)
    latest = s["series"][-1] if s["series"] else None
    return {
        "aliases": s["aliases"] + s["promoted"],
        "constants": s["constants"],
        "uses": s["uses"],
        "corrections_per_approval": latest["corrections_per_approval"] if latest else None,
    }


@api.get("/dashboard")
def dashboard(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(current_principal),
) -> dict[str, Any]:
    by_status = dict(
        session.execute(
            _scoped(p, select(ImportRound.status, func.count()), ImportRound.customer_id).group_by(
                ImportRound.status
            )
        ).all()
    )
    open_reviews = (
        session.scalar(
            _scoped(p, select(func.count()).select_from(ReviewTask), ReviewTask.customer_id).where(
                ReviewTask.status == "open"
            )
        )
        or 0
    )
    customers = session.scalar(_scoped(p, select(func.count()).select_from(Customer), Customer.id)) or 0
    versions = _scoped(p, select(func.count()).select_from(MappingVersion), MappingVersion.customer_id)
    approved_mappings = session.scalar(versions.where(MappingVersion.status == "approved")) or 0
    auto = session.scalar(versions.where(MappingVersion.approved_by == "policy")) or 0
    total_mv = session.scalar(versions) or 0
    reused = (
        session.scalar(
            versions.where(MappingVersion.created_by == "system", MappingVersion.confidence == 1.0)
        )
        or 0
    )
    rows = (
        session.scalar(
            _scoped(p, select(func.sum(DatasetVersion.row_count)), DatasetVersion.customer_id).where(
                DatasetVersion.status.in_(["approved", "published"])
            )
        )
        or 0
    )
    llm_query = select(
        func.count(), func.sum(LLMCall.cost_usd), func.sum(LLMCall.input_tokens + LLMCall.output_tokens)
    ).select_from(LLMCall)
    if p.customers is not None:  # calls hang off a round; those without one belong to no customer
        llm_query = _scoped(
            p, llm_query.join(ImportRound, ImportRound.id == LLMCall.round_id), ImportRound.customer_id
        )
    llm = session.execute(llm_query).one()
    recent = session.scalars(
        _scoped(p, select(ImportRound), ImportRound.customer_id)
        .order_by(ImportRound.updated_at.desc())
        .limit(8)
    ).all()
    cmap = {c.id: c for c in session.scalars(select(Customer)).all()}
    failed_query = select(func.count()).select_from(Job).where(Job.status == "failed")
    if p.customers is not None:
        failed_query = _scoped(
            p, failed_query.join(ImportRound, ImportRound.id == Job.round_id), ImportRound.customer_id
        )
    failed_jobs = session.scalar(failed_query) or 0
    return {
        "customers": customers,
        "rounds_by_status": by_status,
        "open_reviews": open_reviews,
        "approved_mappings": approved_mappings,
        "auto_approved_mappings": auto,
        "mapping_versions": total_mv,
        "reused_mappings": reused,
        "published_rows": rows,
        "failed_jobs": failed_jobs,
        "llm": {
            "calls": llm[0] or 0,
            "cost_usd": round(llm[1] or 0.0, 4),
            "tokens": int(llm[2] or 0),
            "provider": ctx.llm.provider.name,
            "model": ctx.llm.model,
            "backups": len(ctx.llm.fallbacks),
        },
        "recent_rounds": [
            ser.import_round(r, files=False, customer_obj=cmap.get(r.customer_id)) for r in recent
        ],
        "automation_rate": round(auto / total_mv, 3) if total_mv else None,
        "learning": _learning_brief(session, p),
    }


@api.get("/jobs")
def list_jobs(
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    p: Principal = Depends(current_principal),
) -> list[dict[str, Any]]:
    return runner.list_jobs(status=status, kind=kind, limit=limit, offset=offset, customers=p.customers)


@api.get("/jobs/stats")
def job_stats(p: Principal = Depends(current_principal)) -> dict[str, Any]:
    ensure_all_customers(p)
    return runner.queue_stats()


@api.get("/jobs/batch/{batch_id}")
def get_batch(batch_id: str, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    try:
        return runner.batch_status(batch_id, customers=p.customers)
    except KeyError as e:
        raise NotFound("batch not found") from e


@api.get("/jobs/{job_id}")
def get_job(job_id: str, p: Principal = Depends(current_principal)) -> dict[str, Any]:
    try:
        return runner.job_status(job_id, customers=p.customers)
    except KeyError as e:
        raise NotFound("job not found") from e


@api.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, p: Principal = Depends(require_role("admin"))) -> dict[str, Any]:
    try:
        runner.job_status(job_id, customers=p.customers)
    except KeyError as e:
        raise NotFound("job not found") from e
    return {"job_id": runner.retry_job(job_id)}


@api.get("/ops")
def ops_summary(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Everything the Ops page shows: queue, workers, failures, spend, slowest rounds, health."""
    ensure_all_customers(p)
    from forma.api.health import readiness
    from forma.api.ops import ops_summary as _ops

    return _ops(session, ctx, readiness())


@api.get("/audit")
def audit_log(
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
    entity_id: str | None = None,
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("admin")),
) -> list[dict[str, Any]]:
    ensure_all_customers(p)
    from forma.db.models import AuditLog

    q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    return [
        {
            "id": a.id,
            "actor": a.actor,
            "action": a.action,
            "entity": a.entity,
            "entity_id": a.entity_id,
            "details": a.details,
            "at": a.created_at.isoformat(),
        }
        for a in session.scalars(q)
    ]
