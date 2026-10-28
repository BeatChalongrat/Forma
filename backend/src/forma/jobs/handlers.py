"""Job handlers: each is one pipeline stage with its own transaction."""

from __future__ import annotations

from typing import Any

from forma.db.session import session_scope
from forma.jobs.runner import JobProgress, handler
from forma.services import datasets as ds
from forma.services import mapping as ms
from forma.services import rounds as rs
from forma.services.context import ServiceContext


@handler("profile_round")
def profile_round(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    prog.update(10, "profiling files")
    with session_scope() as s:
        return rs.profile_round(ctx, s, payload["round_id"])


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
    """Upload → profile → propose → (auto) transform. The one-click path for returning customers."""
    rid = payload["round_id"]
    prog.update(5, "profiling files")
    with session_scope() as s:
        rs.profile_round(ctx, s, rid)
    prog.update(35, "proposing mapping")
    with session_scope() as s:
        mv = ms.propose_for_round(
            ctx, s, rid, use_llm=payload.get("use_llm"), actor=payload.get("actor", "system")
        )
        mv_id, status = mv.id, mv.status
    out: dict[str, Any] = {"mapping_version_id": mv_id, "mapping_status": status}
    if status == "approved":
        prog.update(70, "transforming")
        with session_scope() as s:
            dv = ds.transform_round(
                ctx, s, rid, mapping_version_id=mv_id, actor=payload.get("actor", "system")
            )
            out["dataset_version_id"] = dv.id
            out["rows"] = dv.row_count
    return out


@handler("transform_round")
def transform_round(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    prog.update(10, "transforming")
    with session_scope() as s:
        dv = ds.transform_round(
            ctx,
            s,
            payload["round_id"],
            mapping_version_id=payload.get("mapping_version_id"),
            actor=payload.get("actor", "system"),
        )
        return {"dataset_version_id": dv.id, "rows": dv.row_count}


@handler("export_dataset")
def export_dataset(ctx: ServiceContext, payload: dict[str, Any], prog: JobProgress) -> dict[str, Any]:
    with session_scope() as s:
        art = ds.export_dataset(
            ctx, s, payload["dataset_id"], payload["fmt"], actor=payload.get("actor", "operator")
        )
        return {"export_id": art.id, "path": art.path, "rows": art.row_count}
