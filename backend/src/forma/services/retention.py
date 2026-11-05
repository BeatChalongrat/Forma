"""Retention (ADR-015): `policies/retention.yaml` → a plan of deletions; dry-run by default, audited when
applied. Never touches open rounds, approved/published dataset versions or rollback parents."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.logging import get_logger
from forma.db.models import (
    AuditLog,
    BatchItem,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    KnowledgeLabel,
    LLMCall,
    ReviewTask,
    SourceFile,
)
from forma.services.context import ServiceContext
from forma.services.rounds import audit

log = get_logger(__name__)
CLOSED_ROUND_STATUSES = ("published", "rejected", "blocked", "cancelled", "moved", "superseded")


def run(ctx: ServiceContext, session: Session, *, apply_: bool, actor: str) -> dict[str, Any]:
    """One retention pass, recorded so the System health page can say when it last ran and what it did."""
    plan_ = plan(ctx, session)
    result = apply(ctx, session, plan_, actor=actor) if apply_ else plan_["summary"]
    counts = {k: v for k, v in result.items() if k != "errors"}
    audit(
        session,
        "retention.run",
        "retention",
        "run",
        actor=actor,
        applied=apply_,
        counts=counts,
        errors=len(result.get("errors", [])),
    )
    return {"applied": apply_, "counts": counts, "errors": result.get("errors", [])}


def ran_within(session: Session, seconds: float) -> bool:
    """True when a pass ran recently: the API and the worker each carry the schedule, and only one is needed."""
    last = _last(session, "retention.run")
    return last is not None and (dt.datetime.now(dt.UTC) - last[0]).total_seconds() < seconds


def _last(session: Session, action: str) -> tuple[dt.datetime, dict[str, Any]] | None:
    row = session.scalar(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at.desc()).limit(1)
    )
    if row is None:
        return None
    at = _aware(row.created_at)
    return (at, row.details or {}) if at else None


_USAGE: tuple[float, dict[str, Any]] | None = None
USAGE_TTL_S = 300.0


def usage(ctx: ServiceContext) -> dict[str, Any]:
    """Bytes and objects in storage. Walking a bucket is slow, and the page refreshes every few seconds."""
    global _USAGE
    clock = time.monotonic()
    if _USAGE is not None and clock - _USAGE[0] < USAGE_TTL_S:
        return _USAGE[1]
    objects, total = 0, 0
    try:
        for obj in ctx.storage.list(""):
            objects += 1
            total += obj.size
        value: dict[str, Any] = {"objects": objects, "bytes": total, "known": True}
    except Exception as e:  # a health page must still load when the bucket does not answer
        log.warning("storage usage unavailable", error=str(e)[:200])
        value = {"objects": None, "bytes": None, "known": False}
    _USAGE = (clock, value)
    return value


def status(ctx: ServiceContext, session: Session) -> dict[str, Any]:
    """Housekeeping for the System health page: storage, the last retention pass, the last backup."""
    now = dt.datetime.now(dt.UTC)
    run_ = _last(session, "retention.run")
    backup = _last(session, "backup.create")
    return {
        "storage": usage(ctx),
        "retention": {
            "schedule_h": ctx.settings.retention_interval_h,
            "deletes": ctx.settings.retention_apply,
            "last_at": run_[0].isoformat() if run_ else None,
            "last_applied": bool(run_[1].get("applied")) if run_ else None,
            "last_counts": run_[1].get("counts") if run_ else None,
            "last_errors": run_[1].get("errors") if run_ else None,
        },
        "backup": {
            "last_at": backup[0].isoformat() if backup else None,
            "age_hours": round((now - backup[0]).total_seconds() / 3600, 1) if backup else None,
            "path": backup[1].get("path") if backup else None,
            "files_included": backup[1].get("objects_included") if backup else None,
            "bytes": backup[1].get("bytes") if backup else None,
        },
    }


def load_policy(policies_dir: Path) -> dict[str, Any]:
    p = policies_dir / "retention.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def knowledge_rounds(session: Session) -> set[str]:
    """Keep evidence until an explicit withdrawal, including transitive mapping/answer dependencies."""
    from forma.core.errors import NotFound
    from forma.db.models import Correction
    from forma.services import knowledge_gate as gate

    pending = list(session.scalars(select(KnowledgeLabel.subject).where(KnowledgeLabel.state == "allowed")))
    visited: set[str] = set()
    rounds: set[str] = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        try:
            row = gate.lookup(session, key)
        except NotFound:
            continue
        if row.round_id:
            rounds.add(row.round_id)
        pending.extend(gate.dependencies(row))
        if isinstance(row, Correction) and row.mapping_version_id:
            pending.append("mapping:" + row.mapping_version_id)
    return rounds


def plan(ctx: ServiceContext, session: Session, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Compute what retention would delete. Pure read; returns a serialisable plan."""
    pol = load_policy(ctx.settings.resolve(ctx.settings.policies_dir))
    now = now or dt.datetime.now(dt.UTC)
    protected = knowledge_rounds(session)
    out: dict[str, Any] = {
        "at": now.isoformat(),
        "policy": pol,
        "source_files": [],
        "batch_items": [],
        "dataset_versions": [],
        "exports": [],
        "jobs": [],
        "llm_calls": [],
        "review_tasks": [],
        "cache": [],
        "protected_knowledge_rounds": sorted(protected),
    }
    # A file still waiting for a customer keeps its object, even when a closed round has the same bytes.
    unassigned = list(session.scalars(select(BatchItem).where(BatchItem.round_id.is_(None))))
    waiting_keys = {i.storage_key for i in unassigned if i.storage_key and i.status != "purged"}

    # ---- source files: rounds closed longer than N days --------------------------------------
    days = int((pol.get("source_files") or {}).get("keep_days_after_publish", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        closed = {
            r.id
            for r in session.scalars(select(ImportRound).where(ImportRound.status.in_(CLOSED_ROUND_STATUSES)))
            if r.id not in protected and (_aware(r.updated_at) or now) < cutoff
        }
        open_rounds = {r.id for r in session.scalars(select(ImportRound)) if r.id not in closed}
        files = list(session.scalars(select(SourceFile).where(SourceFile.storage_key != "")))
        by_key: dict[str, list[SourceFile]] = {}
        for f in files:
            by_key.setdefault(f.storage_key, []).append(f)
        for key, refs in by_key.items():
            if key in waiting_keys:
                continue
            if all(f.round_id in closed for f in refs) and not any(f.round_id in open_rounds for f in refs):
                out["source_files"].append(
                    {"key": key, "file_ids": [f.id for f in refs], "size": refs[0].size}
                )

    # ---- work-round files that never became a round -------------------------------------------
    days = int((pol.get("batch_items") or {}).get("keep_days_unassigned", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        stored = set(session.scalars(select(SourceFile.storage_key).where(SourceFile.storage_key != "")))
        items = [i for i in unassigned if i.storage_key and i.status in ("needs_customer", "failed")]
        old = {i.id for i in items if (_aware(i.created_at) or now) < cutoff}
        young_keys = {i.storage_key for i in unassigned if i.id not in old}
        for i in items:
            if i.id in old:
                # The object goes only when no round and no newer file uses the same bytes.
                out["batch_items"].append(
                    {
                        "id": i.id,
                        "key": i.storage_key,
                        "shared": i.storage_key in stored or i.storage_key in young_keys,
                    }
                )

    # ---- dataset versions: keep last K per round + approved/published + rollback parents --------
    keep_k = int((pol.get("dataset_versions") or {}).get("keep_last_versions", 0) or 0)
    if keep_k > 0:
        per_round: dict[str, list[DatasetVersion]] = {}
        for dv in session.scalars(select(DatasetVersion).order_by(DatasetVersion.version.desc())):
            per_round.setdefault(dv.round_id, []).append(dv)
        for rid, versions in per_round.items():
            if rid in protected:
                continue
            keep = {v.id for v in versions[:keep_k]}
            keep |= {v.id for v in versions if v.status in ("approved", "published")}
            keep |= {v.parent_id for v in versions if v.id in keep and v.parent_id}
            for v in versions:
                if v.id not in keep and v.records_key and v.status != "purged":
                    out["dataset_versions"].append(
                        {"id": v.id, "round_id": rid, "version": v.version, "key": v.records_key}
                    )

    # ---- exports --------------------------------------------------------------------------------
    days = int((pol.get("exports") or {}).get("keep_days", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        protected_datasets = set(
            session.scalars(select(DatasetVersion.id).where(DatasetVersion.round_id.in_(protected)))
        )
        for e in session.scalars(select(ExportArtifact)):
            if (
                e.dataset_version_id not in protected_datasets
                and (_aware(e.created_at) or now) < cutoff
                and e.storage_key
            ):
                out["exports"].append({"id": e.id, "key": e.storage_key})

    # ---- jobs / llm calls -----------------------------------------------------------------------
    days = int((pol.get("jobs") or {}).get("keep_days", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        out["jobs"] = [
            j.id
            for j in session.scalars(select(Job).where(Job.status.in_(["done", "failed"])))
            if j.round_id not in protected and (_aware(j.finished_at) or _aware(j.created_at) or now) < cutoff
        ]
    days = int((pol.get("llm_calls") or {}).get("keep_days", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        out["llm_calls"] = [
            c.id
            for c in session.scalars(select(LLMCall))
            if c.round_id not in protected and (_aware(c.created_at) or now) < cutoff
        ]

    # ---- resolved review questions ------------------------------------------------------------
    days = int((pol.get("review_tasks") or {}).get("keep_days_resolved", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        cancelled = set(session.scalars(select(ImportRound.id).where(ImportRound.status == "cancelled")))
        out["review_tasks"] = [
            t.id
            for t in session.scalars(
                select(ReviewTask).where(ReviewTask.status.in_(("approved", "rejected", "dismissed")))
            )
            if t.round_id not in cancelled | protected
            and (_aware(t.resolved_at or t.created_at) or now) < cutoff
        ]

    # ---- S3 cache ---------------------------------------------------------------------------------
    days = int((pol.get("cache") or {}).get("keep_days", 0) or 0)
    cache_dir = ctx.settings.data_path / "cache"
    if days > 0 and ctx.storage.name == "s3" and cache_dir.exists():
        cutoff_ts = (now - dt.timedelta(days=days)).timestamp()
        out["cache"] = [str(p) for p in cache_dir.rglob("*") if p.is_file() and p.stat().st_atime < cutoff_ts]

    out["summary"] = {
        k: len(v) for k, v in out.items() if isinstance(v, list) and k != "protected_knowledge_rounds"
    }
    return out


def apply(
    ctx: ServiceContext, session: Session, plan_: dict[str, Any], *, actor: str = "retention"
) -> dict[str, Any]:
    """Execute a plan: delete storage objects first, then rows/markers; every deletion is audited."""
    from sqlalchemy import text, update

    # Serialize against knowledge approval/import/revoke and re-check old previews before deleting.
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(74603821)"))
    else:
        session.execute(update(KnowledgeLabel).values(state=KnowledgeLabel.state))
    fresh = plan(ctx, session)

    def identity(item: Any) -> str:
        return str(item.get("key", item.get("id"))) if isinstance(item, dict) else str(item)

    plan_ = {
        key: [item for item in fresh.get(key, []) if identity(item) in {identity(old) for old in values}]
        for key, values in plan_.items()
        if isinstance(values, list)
    }
    done: dict[str, Any] = {
        "source_files": 0,
        "batch_items": 0,
        "review_tasks": 0,
        "dataset_versions": 0,
        "exports": 0,
        "jobs": 0,
        "llm_calls": 0,
        "cache": 0,
        "errors": [],
    }
    for item in plan_["source_files"]:
        try:
            ctx.storage.delete(item["key"])
            for fid in item["file_ids"]:
                f = session.get(SourceFile, fid)
                if f:
                    f.storage_key = ""
                    f.status = "purged"
                audit(session, "retention.purge_file", "file", fid, actor=actor, key=item["key"])
            done["source_files"] += 1
        except Exception as e:
            done["errors"].append(f"file {item['key']}: {e}")
    gone: set[str] = set()
    for item in plan_.get("batch_items", []):
        try:
            if not item["shared"] and item["key"] not in gone:
                ctx.storage.delete(item["key"])
                gone.add(item["key"])
            row = session.get(BatchItem, item["id"])
            if row:
                row.storage_key = ""
                row.status = "purged"
            audit(session, "retention.purge_file", "batch_item", item["id"], actor=actor, key=item["key"])
            done["batch_items"] += 1
        except Exception as e:
            done["errors"].append(f"batch item {item['id']}: {e}")
    for item in plan_["dataset_versions"]:
        try:
            ctx.storage.delete(item["key"])
            dv = session.get(DatasetVersion, item["id"])
            if dv:
                dv.status = "purged"
                dv.records_key = ""
            audit(session, "retention.purge_dataset", "dataset", item["id"], actor=actor, key=item["key"])
            done["dataset_versions"] += 1
        except Exception as e:
            done["errors"].append(f"dataset {item['id']}: {e}")
    for item in plan_["exports"]:
        try:
            ctx.storage.delete(item["key"])
            e_ = session.get(ExportArtifact, item["id"])
            if e_:
                session.delete(e_)
            audit(session, "retention.purge_export", "export", item["id"], actor=actor, key=item["key"])
            done["exports"] += 1
        except Exception as e:
            done["errors"].append(f"export {item['id']}: {e}")
    for jid in plan_["jobs"]:
        j = session.get(Job, jid)
        if j:
            session.delete(j)
            done["jobs"] += 1
    for tid in plan_.get("review_tasks", []):
        task = session.get(ReviewTask, tid)
        if task:
            session.delete(task)
            done["review_tasks"] += 1
    for cid in plan_["llm_calls"]:
        c = session.get(LLMCall, cid)
        if c:
            session.delete(c)
            done["llm_calls"] += 1
    for p in plan_["cache"]:
        try:
            Path(p).unlink(missing_ok=True)
            done["cache"] += 1
        except OSError as e:
            done["errors"].append(f"cache {p}: {e}")
    if done["jobs"] or done["llm_calls"] or done["review_tasks"]:
        audit(
            session,
            "retention.purge_rows",
            "retention",
            "jobs+llm_calls",
            actor=actor,
            jobs=done["jobs"],
            llm_calls=done["llm_calls"],
            review_tasks=done["review_tasks"],
        )
    log.info(
        "retention applied", **{k: v for k, v in done.items() if k != "errors"}, errors=len(done["errors"])
    )
    return done
