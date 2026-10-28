"""Import rounds: customers, file intake, profiling, fingerprinting, drift & wrong-file detection."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.errors import NotFound, ValidationFailed
from forma.core.telemetry import span
from forma.db.models import AuditLog, Customer, ImportRound, MappingVersion, SourceFile
from forma.fingerprint.fingerprint import Fingerprint, compare, fingerprint_profile, merge_fingerprints
from forma.ingestion.sniff import sniff_kind
from forma.profiling.profile import WorkbookProfile, merge_profiles, profile_workbook
from forma.services.context import ServiceContext
from forma.storage import safe_name


def audit(
    session: Session, action: str, entity: str, entity_id: str, actor: str = "system", **details: Any
) -> None:
    session.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id, details=details))


# --------------------------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------------------------
def create_customer(
    session: Session, code: str, name: str, erp_type: str | None = None, notes: str | None = None
) -> Customer:
    code = code.strip()
    if not code:
        raise ValidationFailed("customer code is required")
    if session.scalar(select(Customer).where(Customer.code == code)):
        raise ValidationFailed(f"customer code {code!r} already exists")
    c = Customer(code=code, name=name.strip() or code, erp_type=erp_type, notes=notes)
    session.add(c)
    session.flush()
    audit(session, "customer.create", "customer", c.id, code=code)
    return c


def get_customer(session: Session, customer_id: str) -> Customer:
    c = session.get(Customer, customer_id)
    if c is None:
        raise NotFound(f"customer {customer_id} not found")
    return c


# --------------------------------------------------------------------------------------------
# Rounds & files
# --------------------------------------------------------------------------------------------
def create_round(
    session: Session, customer_id: str, label: str | None = None, target_schema: str = "product"
) -> ImportRound:
    get_customer(session, customer_id)
    r = ImportRound(
        customer_id=customer_id, label=label or dt.date.today().isoformat(), target_schema=target_schema
    )
    session.add(r)
    session.flush()
    audit(session, "round.create", "round", r.id, customer_id=customer_id)
    return r


def get_round(session: Session, round_id: str) -> ImportRound:
    r = session.get(ImportRound, round_id)
    if r is None:
        raise NotFound(f"import round {round_id} not found")
    return r


def add_file(
    ctx: ServiceContext, session: Session, round_: ImportRound, name: str, data: bytes
) -> SourceFile:
    kind = sniff_kind(data, name)  # bytes decide, not the extension (ADR-013)
    name = safe_name(name)
    stored = ctx.files.put_bytes(data, name)
    dup = session.scalar(
        select(SourceFile)
        .where(SourceFile.sha256 == stored.sha256, SourceFile.round_id != round_.id)
        .order_by(SourceFile.created_at.desc())
    )
    f = SourceFile(
        round_id=round_.id,
        customer_id=round_.customer_id,
        sha256=stored.sha256,
        original_name=name,
        size=stored.size,
        kind=kind,
        storage_key=stored.key,
        duplicate_of=dup.id if dup else None,
    )
    session.add(f)
    session.flush()
    audit(session, "file.add", "file", f.id, round_id=round_.id, name=name, duplicate_of=f.duplicate_of)
    return f


# --------------------------------------------------------------------------------------------
# Profiling + fingerprint + drift
# --------------------------------------------------------------------------------------------
def latest_approved_mapping(session: Session, customer_id: str) -> MappingVersion | None:
    return session.scalar(
        select(MappingVersion)
        .where(MappingVersion.customer_id == customer_id, MappingVersion.status == "approved")
        .order_by(MappingVersion.approved_at.desc(), MappingVersion.created_at.desc())
    )


def _profile_file(path: Path) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Pure, process/thread-safe: profile + fingerprint one file (no DB)."""
    prof = profile_workbook(path)
    fp = fingerprint_profile(prof)
    return prof.to_dict(), fp.to_dict(), len(prof.tables)


def profile_round(
    ctx: ServiceContext, session: Session, round_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Profile every file of the round (concurrently), fingerprint, classify drift and wrong-file matches.

    Idempotent: files already ``profiled`` keep their profile unless ``force``; drift and the round
    summary are always recomputed because they depend on the customer's *current* approved format.
    """
    r = get_round(session, round_id)
    previous = latest_approved_mapping(session, r.customer_id)
    prev_fps: dict[str, Fingerprint] = {}
    if previous and previous.policy and previous.policy.get("file_fingerprints"):
        prev_fps = {k: Fingerprint.from_dict(v) for k, v in previous.policy["file_fingerprints"].items()}
    prev_round_fp = merge_fingerprints(list(prev_fps.values())) if prev_fps else None
    cur_fps: list[Fingerprint] = []
    other_matches: list[dict[str, Any]] = []

    todo = [f for f in r.files if force or not (f.status == "profiled" and f.profile and f.fingerprint)]
    computed: dict[str, tuple[dict[str, Any], dict[str, Any], int] | Exception] = {}
    if todo:
        workers = max(1, min(ctx.settings.file_workers, len(todo)))
        with span("round.profile", round_id=r.id, files=len(todo), workers=workers):
            if workers == 1:
                for f in todo:
                    try:
                        computed[f.id] = _profile_file(ctx.storage.local_path(f.storage_key))
                    except Exception as e:
                        computed[f.id] = e
            else:
                with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="forma-profile") as pool:
                    futs = {
                        pool.submit(_profile_file, ctx.storage.local_path(f.storage_key)): f.id for f in todo
                    }
                    for fut, fid in futs.items():
                        try:
                            computed[fid] = fut.result()
                        except Exception as e:
                            computed[fid] = e

    results: list[dict[str, Any]] = []
    skipped = 0
    worst = "identical"
    for f in r.files:
        res = computed.get(f.id)
        if isinstance(res, Exception):
            f.status = "error"
            f.error = str(res)[:1000]
            f.profile = None
            f.fingerprint = None
            results.append({"file_id": f.id, "name": f.original_name, "error": f.error})
            continue
        if res is not None:
            prof_d, fp_d, n_tables = res
            f.profile = prof_d
            f.fingerprint = fp_d
            f.status = "profiled"
            f.error = None
        else:
            if f.status != "profiled" or not f.fingerprint:
                continue
            skipped += 1
            n_tables = len((f.profile or {}).get("tables", []))
        fp = Fingerprint.from_dict(f.fingerprint or {})
        cur_fps.append(fp)
        drift: dict[str, Any] | None = None
        if prev_fps:
            # per-file view: best match among previous files of this customer (informational)
            reports = [compare(pfp, fp) for pfp in prev_fps.values()]
            best = max(reports, key=lambda d: d.similarity)
            drift = best.to_dict()
        # wrong-file check against other customers' approved formats
        other = _match_other_customer(session, r.customer_id, fp)
        if other:
            # Identical structure to another customer's approved format. For a returning customer this
            # usually means the wrong file was sent; for a first-time customer it may simply be the same
            # ERP template — policy routes it to review with the match named, never a hard block.
            drift = {
                **(drift or {"classification": "unknown", "similarity": 0.0}),
                "matches_other_customer": other,
            }
            other_matches.append(other)
        f.drift = drift
        results.append(
            {
                "file_id": f.id,
                "name": f.original_name,
                "tables": n_tables,
                "fingerprint": fp.strict,
                "drift": drift,
            }
        )
    round_drift: dict[str, Any] | None = None
    if prev_round_fp is not None and cur_fps:
        rep = compare(prev_round_fp, merge_fingerprints(cur_fps))
        round_drift = rep.to_dict()
        worst = rep.classification
    r.status = "profiled"
    r.summary = {
        **(r.summary or {}),
        "profile": results,
        "drift": worst if prev_round_fp is not None else None,
        "drift_report": round_drift,
        "matches_other_customer": other_matches[0] if other_matches else None,
        "has_previous_approval": previous is not None,
        "profiled_files": len(todo),
        "skipped_files": skipped,
    }
    audit(session, "round.profile", "round", r.id, files=len(results), drift=r.summary["drift"])
    return r.summary


def _match_other_customer(session: Session, customer_id: str, fp: Fingerprint) -> dict[str, Any] | None:
    rows = session.scalars(
        select(MappingVersion).where(
            MappingVersion.status == "approved",
            MappingVersion.customer_id != customer_id,
            MappingVersion.fingerprint_strict == fp.strict,
        )
    ).all()
    if rows:
        m = rows[0]
        c = session.get(Customer, m.customer_id)
        return {
            "customer_id": m.customer_id,
            "customer_code": c.code if c else None,
            "mapping_version_id": m.id,
        }
    return None


def round_profile(session: Session, round_id: str) -> WorkbookProfile:
    r = get_round(session, round_id)
    parts = [
        (f.id, f.original_name, WorkbookProfile.from_dict(f.profile))
        for f in r.files
        if f.profile and f.status == "profiled"
    ]
    if not parts:
        raise ValidationFailed("round has no profiled files")
    return merge_profiles(parts)
