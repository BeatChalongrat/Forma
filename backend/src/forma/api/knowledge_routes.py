"""Administrator-only knowledge promotion; source and target are never connected to one another."""

from collections.abc import Iterator
from threading import BoundedSemaphore
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.api.routes import ctx_dep
from forma.auth import Principal, ensure_all_customers, require_role
from forma.core.errors import ValidationFailed
from forma.db.models import Correction, KnowledgeRelease
from forma.db.session import get_session
from forma.services import config
from forma.services import knowledge_gate as gate
from forma.services import knowledge_transfer as transfer
from forma.services.context import ServiceContext

router = APIRouter(prefix="/api/knowledge-transfer", tags=["knowledge-transfer"])
_package_slot = BoundedSemaphore(1)


def admin(p: Principal = Depends(require_role("admin"))) -> Principal:
    ensure_all_customers(p)
    return p


def package_slot(p: Principal = Depends(admin)) -> Iterator[None]:
    # One potentially large expanded archive per API process; never queue them in RAM.
    if not _package_slot.acquire(blocking=False):
        raise HTTPException(
            429, "Another knowledge package is being processed; retry shortly", headers={"Retry-After": "5"}
        )
    try:
        yield
    finally:
        _package_slot.release()


class Classification(BaseModel):
    subject: str
    origin: Literal["unknown", "synthetic", "real_trial", "production"]
    state: Literal["pending", "allowed", "revoked"]
    reason: str = Field(min_length=1, max_length=2000)
    include_answers: bool = False


class Selection(BaseModel):
    subjects: list[str] = Field(min_length=1, max_length=100)


class Withdrawal(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


@router.get("")
def inventory(
    offset: int = 0, session: Session = Depends(get_session), p: Principal = Depends(admin)
) -> dict[str, Any]:
    return transfer.inventory(session, offset=max(0, offset))


@router.post("/classify")
def classify(
    body: Classification, session: Session = Depends(get_session), p: Principal = Depends(admin)
) -> dict[str, Any]:
    label = gate.classify(session, body.subject, body.origin, body.state, p.actor, body.reason)
    if body.include_answers and body.subject.startswith("mapping:"):
        for c in session.scalars(
            select(Correction).where(
                Correction.mapping_version_id == body.subject.split(":", 1)[1],
                Correction.status == "confirmed",
            )
        ):
            gate.classify(session, gate.subject(c), body.origin, body.state, p.actor, body.reason)
    transfer.instance(session, create=True)
    return {"subject": label.subject, "state": label.state}


@router.post("/export", dependencies=[Depends(package_slot)])
def export(
    body: Selection,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(admin),
) -> Response:
    raw = transfer.export_bundle(session, ctx, body.subjects, p.actor)
    return Response(
        raw,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="forma-knowledge.zip"'},
    )


def package(file: UploadFile) -> transfer.Package:
    raw = file.file.read(transfer.MAX_BYTES + 1)
    if len(raw) > transfer.MAX_BYTES:
        raise ValidationFailed("Package exceeds 256 MiB")
    return transfer.read_bundle(raw)


@router.post("/preview", dependencies=[Depends(package_slot)])
def preview(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(admin),
) -> dict[str, Any]:
    return transfer.preview(session, ctx, package(file))


@router.post("/import", dependencies=[Depends(package_slot)])
def import_release(
    file: UploadFile = File(...),
    plan_digest: str = Form(...),
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(admin),
) -> dict[str, Any]:
    result = transfer.apply(session, ctx, package(file), plan_digest, p.actor)
    session.commit()
    ctx.refresh()
    return result


@router.get("/releases")
def releases(session: Session = Depends(get_session), p: Principal = Depends(admin)) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": r.id,
                "state": r.state,
                "actor": r.actor,
                "created_at": r.created_at,
                "source_instance": r.source_instance,
                "items": r.manifest.get("items", []),
            }
            for r in session.scalars(
                select(KnowledgeRelease).order_by(KnowledgeRelease.created_at.desc()).limit(100)
            )
        ]
    }


@router.post("/releases/{release_id}/revoke")
def revoke(
    release_id: str,
    body: Withdrawal,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(admin),
) -> dict[str, Any]:
    result = transfer.revoke(session, release_id, p.actor, body.reason)
    session.commit()
    ctx.refresh()
    return result


@router.post("/refresh")
def refresh(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(admin),
) -> dict[str, Any]:
    config._put(session, "knowledge.revision", {"manual_refresh": True}, p.actor)
    session.commit()
    ctx.refresh()
    # Knowledge lookup is a live relational query through gate.allowed; there is no separate
    # embedding index to rebuild or stale in-memory approval list to resurrect.
    return {"refreshed": True, "retrieval": "live eligibility-filtered database queries"}
