"""Retention (ADR-015): `policies/retention.yaml` → a plan of deletions; dry-run by default, audited when
applied. Never touches open rounds, approved/published dataset versions or rollback parents."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.logging import get_logger
from forma.db.models import DatasetVersion, ExportArtifact, ImportRound, Job, LLMCall, SourceFile
from forma.services.context import ServiceContext
from forma.services.rounds import audit

log = get_logger(__name__)
CLOSED_ROUND_STATUSES = ("published", "rejected", "blocked", "cancelled", "moved", "superseded")


def load_policy(policies_dir: Path) -> dict[str, Any]:
    p = policies_dir / "retention.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def plan(ctx: ServiceContext, session: Session, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """Compute what retention would delete. Pure read; returns a serialisable plan."""
    pol = load_policy(ctx.settings.resolve(ctx.settings.policies_dir))
    now = now or dt.datetime.now(dt.UTC)
    out: dict[str, Any] = {
        "at": now.isoformat(),
        "policy": pol,
        "source_files": [],
        "dataset_versions": [],
        "exports": [],
        "jobs": [],
        "llm_calls": [],
        "cache": [],
    }

    # ---- source files: rounds closed longer than N days --------------------------------------
    days = int((pol.get("source_files") or {}).get("keep_days_after_publish", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        closed = {
            r.id
            for r in session.scalars(select(ImportRound).where(ImportRound.status.in_(CLOSED_ROUND_STATUSES)))
            if (_aware(r.updated_at) or now) < cutoff
        }
        open_rounds = {r.id for r in session.scalars(select(ImportRound)) if r.id not in closed}
        files = list(session.scalars(select(SourceFile).where(SourceFile.storage_key != "")))
        by_key: dict[str, list[SourceFile]] = {}
        for f in files:
            by_key.setdefault(f.storage_key, []).append(f)
        for key, refs in by_key.items():
            if all(f.round_id in closed for f in refs) and not any(f.round_id in open_rounds for f in refs):
                out["source_files"].append(
                    {"key": key, "file_ids": [f.id for f in refs], "size": refs[0].size}
                )

    # ---- dataset versions: keep last K per round + approved/published + rollback parents --------
    keep_k = int((pol.get("dataset_versions") or {}).get("keep_last_versions", 0) or 0)
    if keep_k > 0:
        per_round: dict[str, list[DatasetVersion]] = {}
        for dv in session.scalars(select(DatasetVersion).order_by(DatasetVersion.version.desc())):
            per_round.setdefault(dv.round_id, []).append(dv)
        for rid, versions in per_round.items():
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
        for e in session.scalars(select(ExportArtifact)):
            if (_aware(e.created_at) or now) < cutoff and e.storage_key:
                out["exports"].append({"id": e.id, "key": e.storage_key})

    # ---- jobs / llm calls -----------------------------------------------------------------------
    days = int((pol.get("jobs") or {}).get("keep_days", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        out["jobs"] = [
            j.id
            for j in session.scalars(select(Job).where(Job.status.in_(["done", "failed"])))
            if (_aware(j.finished_at) or _aware(j.created_at) or now) < cutoff
        ]
    days = int((pol.get("llm_calls") or {}).get("keep_days", 0) or 0)
    if days > 0:
        cutoff = now - dt.timedelta(days=days)
        out["llm_calls"] = [
            c.id for c in session.scalars(select(LLMCall)) if (_aware(c.created_at) or now) < cutoff
        ]

    # ---- S3 cache ---------------------------------------------------------------------------------
    days = int((pol.get("cache") or {}).get("keep_days", 0) or 0)
    cache_dir = ctx.settings.data_path / "cache"
    if days > 0 and ctx.storage.name == "s3" and cache_dir.exists():
        cutoff_ts = (now - dt.timedelta(days=days)).timestamp()
        out["cache"] = [str(p) for p in cache_dir.rglob("*") if p.is_file() and p.stat().st_atime < cutoff_ts]

    out["summary"] = {k: len(v) for k, v in out.items() if isinstance(v, list)}
    return out


def apply(
    ctx: ServiceContext, session: Session, plan_: dict[str, Any], *, actor: str = "retention"
) -> dict[str, Any]:
    """Execute a plan: delete storage objects first, then rows/markers; every deletion is audited."""
    done: dict[str, Any] = {
        "source_files": 0,
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
    if done["jobs"] or done["llm_calls"]:
        audit(
            session,
            "retention.purge_rows",
            "retention",
            "jobs+llm_calls",
            actor=actor,
            jobs=done["jobs"],
            llm_calls=done["llm_calls"],
        )
    log.info(
        "retention applied", **{k: v for k, v in done.items() if k != "errors"}, errors=len(done["errors"])
    )
    return done
