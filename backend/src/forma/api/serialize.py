from __future__ import annotations

from typing import Any

from forma.db.models import (
    ChangeSet,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    MappingVersion,
    ReviewTask,
    SourceFile,
)


def _dt(v: Any) -> str | None:
    return v.isoformat() if v else None


def customer(c: Customer, **extra: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "erp_type": c.erp_type,
        "notes": c.notes,
        "active": c.active,
        "default_target_schema": c.default_target_schema,
        "created_at": _dt(c.created_at),
        **extra,
    }


def source_file(f: SourceFile, *, full: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": f.id,
        "round_id": f.round_id,
        "name": f.original_name,
        "size": f.size,
        "kind": f.kind,
        "status": f.status,
        "sha256": f.sha256,
        "duplicate_of": f.duplicate_of,
        "error": f.error,
        "created_at": _dt(f.created_at),
        "fingerprint": (f.fingerprint or {}).get("strict"),
        "drift": f.drift,
        "tables": len((f.profile or {}).get("tables", [])),
        "sheets": [s["name"] for s in (f.profile or {}).get("meta", {}).get("sheets", [])],
    }
    if full:
        d["profile"] = f.profile
        d["fingerprint_full"] = f.fingerprint
    return d


def import_round(
    r: ImportRound, *, files: bool = True, customer_obj: Customer | None = None
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": r.id,
        "customer_id": r.customer_id,
        "label": r.label,
        "status": r.status,
        "target_schema": r.target_schema,
        "notes": r.notes,
        "summary": r.summary or {},
        "created_at": _dt(r.created_at),
        "updated_at": _dt(r.updated_at),
    }
    if files:
        d["files"] = [source_file(f) for f in r.files]
    if customer_obj is not None:
        d["customer"] = {"id": customer_obj.id, "code": customer_obj.code, "name": customer_obj.name}
    return d


def mapping_version(m: MappingVersion, *, document: bool = True) -> dict[str, Any]:
    pol = dict(m.policy or {})
    sample = pol.pop("sample", None)
    pol.pop("file_fingerprints", None)
    d: dict[str, Any] = {
        "id": m.id,
        "customer_id": m.customer_id,
        "round_id": m.round_id,
        "version": m.version,
        "status": m.status,
        "target_schema": m.target_schema,
        "confidence": m.confidence,
        "created_by": m.created_by,
        "model": m.model,
        "policy": pol,
        "open_questions": m.open_questions or [],
        "validation": m.validation,
        "parent_id": m.parent_id,
        "approved_by": m.approved_by,
        "approved_at": _dt(m.approved_at),
        "comment": m.comment,
        "created_at": _dt(m.created_at),
        "agent_log": m.agent_log or [],
        "sample": sample or [],
    }
    if document:
        d["document"] = m.document
    return d


def review_task(t: ReviewTask, **extra: Any) -> dict[str, Any]:
    return {
        "id": t.id,
        "round_id": t.round_id,
        "customer_id": t.customer_id,
        "mapping_version_id": t.mapping_version_id,
        "dataset_version_id": t.dataset_version_id,
        "kind": t.kind,
        "priority": t.priority,
        "status": t.status,
        "reasons": t.reasons,
        "details": t.details,
        "resolver": t.resolver,
        "comment": t.comment,
        "created_at": _dt(t.created_at),
        "resolved_at": _dt(t.resolved_at),
        **extra,
    }


def dataset_version(d: DatasetVersion) -> dict[str, Any]:
    return {
        "id": d.id,
        "round_id": d.round_id,
        "customer_id": d.customer_id,
        "mapping_version_id": d.mapping_version_id,
        "version": d.version,
        "status": d.status,
        "row_count": d.row_count,
        "stats": d.stats,
        "findings": d.findings,
        "parent_id": d.parent_id,
        "created_by": d.created_by,
        "note": d.note,
        "created_at": _dt(d.created_at),
    }


def change_set(c: ChangeSet) -> dict[str, Any]:
    return {
        "id": c.id,
        "dataset_version_id": c.dataset_version_id,
        "result_version_id": c.result_version_id,
        "kind": c.kind,
        "operations": c.operations,
        "impact": c.impact,
        "author": c.author,
        "note": c.note,
        "status": c.status,
        "created_at": _dt(c.created_at),
    }


def export_artifact(e: ExportArtifact) -> dict[str, Any]:
    return {
        "id": e.id,
        "dataset_version_id": e.dataset_version_id,
        "fmt": e.fmt,
        "row_count": e.row_count,
        "sha256": e.sha256,
        "created_at": _dt(e.created_at),
        "key": e.storage_key,
        "path": e.storage_key,  # backwards-compatible name; it is a storage key (ADR-012)
    }
