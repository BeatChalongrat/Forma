"""Job handlers: each is one pipeline stage with its own transaction.

Handlers are idempotent and resumable (ADR-010): they consult ``JobProgress.stages`` (what this job
already finished before a crash) and the database (what exists regardless of who produced it) and
skip work that is already done. ``payload["force"]`` disables the skips.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.telemetry import span
from forma.db.models import DatasetVersion, ImportRound, MappingVersion
from forma.db.session import session_scope
from forma.jobs.runner import JobProgress, handler
from forma.services import datasets as ds
from forma.services import mapping as ms
from forma.services import rounds as rs
from forma.services.context import ServiceContext


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _round_profiled(r: ImportRound) -> bool:
    return (
        bool(r.files)
        and all(f.status in ("profiled", "error") for f in r.files)
        and bool((r.summary or {}).get("profile"))
    )


def _current_mapping(session: Session, r: ImportRound) -> MappingVersion | None:
    """The mapping version this round's last proposal produced, if it is still live."""
    mv_id = (r.summary or {}).get("mapping_version_id")
    mv = session.get(MappingVersion, mv_id) if mv_id else None
    if mv is None or mv.round_id != r.id or mv.status not in ("proposed", "approved"):
        return None
    return mv


def _existing_dataset(session: Session, round_id: str, mapping_version_id: str) -> DatasetVersion | None:
    return session.scalar(
        select(DatasetVersion)
        .where(
            DatasetVersion.round_id == round_id,
            DatasetVersion.mapping_version_id == mapping_version_id,
            DatasetVersion.status != "superseded",
        )
        .order_by(DatasetVersion.version.desc())
    )


@handler("profile_round")
def profile_round(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    force = bool(payload.get("force"))
    prog.update(10, "profiling files")
    with session_scope() as s:
        return rs.profile_round(ctx, s, payload["round_id"], force=force)


@handler("propose_mapping")
def propose_mapping(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    prog.update(10, "analysing structure")
    with session_scope() as s:
        mv = ms.propose_for_round(
            ctx, s, payload["round_id"], use_llm=payload.get("use_llm"), actor=payload.get("actor", "system")
        )
        return {
            "mapping_version_id": mv.id,
            "status": mv.status,
            "decision": (mv.policy or {}).get("decision"),
        }


@handler("run_pipeline")
def run_pipeline(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    """Upload → profile → propose → (auto) transform. The one-click path for returning customers.

    Resumable: a crashed run re-queued by ``recover_stale`` continues from the first unfinished stage.
    """
    rid = payload["round_id"]
    force = bool(payload.get("force"))
    actor = payload.get("actor", "system")
    out: dict[str, Any] = {}

    # ---- profile ------------------------------------------------------------------------
    skip_profile = False
    if not force and not prog.done("profile"):
        with session_scope() as s:
            skip_profile = _round_profiled(rs.get_round(s, rid))
    if force or not (prog.done("profile") or skip_profile):
        prog.update(5, "profiling files")
        t0 = time.perf_counter()
        with span("stage.profile", round_id=rid), session_scope() as s:
            rs.profile_round(ctx, s, rid, force=force)
        prog.mark("profile", duration_ms=_ms(t0))
    elif skip_profile and not prog.done("profile"):
        prog.mark("profile", skipped=True)

    # ---- propose ------------------------------------------------------------------------
    mv_id: str | None = None
    status: str | None = None
    if not force and prog.done("propose"):
        mv_id = prog.stages["propose"].get("mapping_version_id")
        status = prog.stages["propose"].get("status")
    if mv_id is None and not force:
        with session_scope() as s:
            r = rs.get_round(s, rid)
            if r.status not in ("uploaded", "profiled", "failed"):
                mv = _current_mapping(s, r)
                if mv is not None:
                    mv_id, status = mv.id, mv.status
                    prog.mark("propose", mapping_version_id=mv_id, status=status, skipped=True)
    if mv_id is None or status is None:
        prog.update(35, "proposing mapping")
        t0 = time.perf_counter()
        with span("stage.propose", round_id=rid), session_scope() as s:
            mv = ms.propose_for_round(ctx, s, rid, use_llm=payload.get("use_llm"), actor=actor)
            mv_id, status = mv.id, mv.status
        prog.mark("propose", mapping_version_id=mv_id, status=status, duration_ms=_ms(t0))
    out.update({"mapping_version_id": mv_id, "mapping_status": status})

    # ---- transform ----------------------------------------------------------------------
    if status == "approved":
        if not force and prog.done("transform"):
            out["dataset_version_id"] = prog.stages["transform"].get("dataset_version_id")
            out["rows"] = prog.stages["transform"].get("rows")
            return out
        with session_scope() as s:
            existing = None if force else _existing_dataset(s, rid, mv_id)
            if existing is not None:
                out["dataset_version_id"], out["rows"] = existing.id, existing.row_count
                prog.mark("transform", dataset_version_id=existing.id, rows=existing.row_count, skipped=True)
                return out
        prog.update(70, "transforming")
        t0 = time.perf_counter()
        with span("stage.transform", round_id=rid), session_scope() as s:
            dv = ds.transform_round(ctx, s, rid, mapping_version_id=mv_id, actor=actor)
            out["dataset_version_id"] = dv.id
            out["rows"] = dv.row_count
        prog.mark(
            "transform", dataset_version_id=out["dataset_version_id"], rows=out["rows"], duration_ms=_ms(t0)
        )
    return out


@handler("transform_round")
def transform_round(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    if not payload.get("force") and prog.done("transform"):
        return dict(prog.stages["transform"])
    prog.update(10, "transforming")
    t0 = time.perf_counter()
    with span("stage.transform", round_id=payload["round_id"]), session_scope() as s:
        dv = ds.transform_round(
            ctx,
            s,
            payload["round_id"],
            mapping_version_id=payload.get("mapping_version_id"),
            actor=payload.get("actor", "system"),
        )
        res = {"dataset_version_id": dv.id, "rows": dv.row_count}
    prog.mark("transform", **res, duration_ms=_ms(t0))
    return res


@handler("export_dataset")
def export_dataset(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    if not payload.get("force") and prog.done("export"):
        return dict(prog.stages["export"])
    with session_scope() as s:
        art = ds.export_dataset(
            ctx, s, payload["dataset_id"], payload["fmt"], actor=payload.get("actor", "operator")
        )
        res = {"export_id": art.id, "key": art.storage_key, "rows": art.row_count}
    prog.mark("export", **res)
    return res
