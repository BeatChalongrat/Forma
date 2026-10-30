"""Reconcile queue records only when durable output proves the work already finished."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from forma.db.models import (
    AuditLog,
    BatchItem,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    MappingVersion,
)
from forma.db.session import session_scope


def published_result(session: Session, round_id: str) -> dict[str, Any] | None:
    r = session.get(ImportRound, round_id)
    if r is None or r.status != "published":
        return None
    dv = session.scalar(
        select(DatasetVersion).where(DatasetVersion.round_id == r.id).order_by(DatasetVersion.version.desc())
    )
    if (
        dv is None
        or dv.status != "published"
        or dv.customer_id != r.customer_id
        or dv.id != (r.summary or {}).get("dataset_version_id")
    ):
        return None
    mv = session.get(MappingVersion, dv.mapping_version_id)
    if mv is None or mv.round_id != r.id or mv.customer_id != r.customer_id:
        return None
    art = session.scalar(
        select(ExportArtifact)
        .where(ExportArtifact.dataset_version_id == dv.id, ExportArtifact.fmt == "json")
        .order_by(ExportArtifact.created_at.desc())
    )
    if art is None or not art.storage_key or art.customer_id != r.customer_id:
        return None
    return {
        "round_id": r.id,
        "mapping_version_id": mv.id,
        "mapping_status": mv.status,
        "dataset_version_id": dv.id,
        "rows": dv.row_count,
        "publish": {"decision": "publish", "export_id": art.id, "reasons": ["existing published output"]},
        "reconciled": True,
    }


def completed_job_result(session: Session, job: Job) -> dict[str, Any] | None:
    payload = job.payload or {}
    if payload.get("force"):
        return None
    if job.kind == "process_batch_item":
        item_id = payload.get("item_id")
        item = session.get(BatchItem, item_id) if item_id else None
        if item is None or not item.round_id:
            return None
        result = published_result(session, item.round_id)
        return {**result, "item_id": item.id} if result else None
    if job.kind == "run_pipeline" and payload.get("round_id"):
        return published_result(session, payload["round_id"])
    return None


def record_reconciliation(session: Session, job_id: str, result: dict[str, Any]) -> None:
    session.add(
        AuditLog(
            actor="system",
            action="job.reconciled",
            entity="job",
            entity_id=job_id,
            details={"reason": "existing published output; no processing repeated", **result},
        )
    )


def reconcile_completed_jobs() -> list[str]:
    """Compare-and-set queued jobs; never take an active worker's job or erase its stage history."""
    reconciled = []
    with session_scope() as s:
        jobs = s.scalars(
            select(Job).where(Job.status == "queued", Job.kind.in_(("process_batch_item", "run_pipeline")))
        )
        for j in jobs:
            result = completed_job_result(s, j)
            if result is None:
                continue
            changed = s.execute(
                update(Job)
                .where(Job.id == j.id, Job.status == "queued")
                .values(
                    status="done",
                    progress=100,
                    round_id=result["round_id"],
                    result={**(j.result or {}), **result},
                    error=None,
                    message="Reconciled with existing published output",
                    finished_at=dt.datetime.now(dt.UTC),
                )
            )
            if getattr(changed, "rowcount", 0) == 1:
                record_reconciliation(s, j.id, result)
                reconciled.append(j.id)
    return reconciled
