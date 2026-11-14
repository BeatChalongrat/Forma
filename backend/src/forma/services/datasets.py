"""Dataset versions: transform, edit (change sets), persistent overrides, change intelligence, export."""

from __future__ import annotations

import contextlib
import copy
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import orjson
import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.errors import Conflict, NotFound, ValidationFailed
from forma.core.hashing import stable_hash
from forma.core.ids import new_id
from forma.db.models import ChangeSet, DatasetVersion, ExportArtifact, ImportRound, Override
from forma.engine.ops import CompileContext, compile_expr
from forma.engine.transform import Finding, TransformEngine, _get_pointer, _set_pointer
from forma.export.exporter import FORMATS, write_export
from forma.mapping_dsl.models import CallExpr, ColExpr, ConstExpr, MappingDocument
from forma.schema_registry import INTERNAL_KEYS, PROVENANCE_KEY, TargetSchema
from forma.services.context import ServiceContext
from forma.services.mapping import bind_sources, get_mapping
from forma.services.round_lifecycle import lock_round
from forma.services.rounds import audit, get_round


# --------------------------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------------------------
def write_version_object(ctx: ServiceContext, key: str, records: list[dict[str, Any]]) -> int:
    """Write a dataset version as JSONL to storage (temp file → put). Returns the byte size."""
    tmp = ctx.tmp_dir / (key.replace("/", "_") + ".tmp")
    try:
        with tmp.open("wb") as f:
            for r in records:
                f.write(orjson.dumps(r) + b"\n")
        size = tmp.stat().st_size
        ctx.storage.put(key, tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return size


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream a JSONL dataset one record at a time (exports never materialise the dataset twice)."""
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                yield orjson.loads(line)


def read_records(path: Path) -> list[dict[str, Any]]:
    return list(iter_records(path))


def records_of(ctx: ServiceContext, dv: DatasetVersion) -> list[dict[str, Any]]:
    return read_records(ctx.records_path(dv))


def _new_version(
    ctx: ServiceContext,
    session: Session,
    records: list[dict[str, Any]],
    *,
    stamp_meta: bool,
    **fields: Any,
) -> DatasetVersion:
    """Atomic dataset version creation (ADR-015): the JSONL object is written *first*, the DB row last;
    a failure in between deletes the object so no partial version is ever visible."""
    dv_id = new_id("dsv")
    key = ctx.dataset_key(fields["customer_id"], dv_id)
    if stamp_meta:
        for d in records:
            d.setdefault(PROVENANCE_KEY, {})["dataset_version_id"] = dv_id
    write_version_object(ctx, key, records)
    session.info.setdefault("rollback_objects", []).append((ctx.storage, key))
    try:
        dv = DatasetVersion(id=dv_id, records_key=key, row_count=len(records), **fields)
        session.add(dv)
        session.flush()
    except BaseException:
        with contextlib.suppress(Exception):
            ctx.storage.delete(key)
        raise
    return dv


def get_dataset(session: Session, dataset_id: str) -> DatasetVersion:
    d = session.get(DatasetVersion, dataset_id)
    if d is None:
        raise NotFound(f"dataset version {dataset_id} not found")
    return d


def _writable_dataset(session: Session, dataset_id: str) -> DatasetVersion:
    dv = get_dataset(session, dataset_id)
    lock_round(session, dv.round_id)
    session.refresh(dv)
    latest = session.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.round_id == dv.round_id)
        .order_by(DatasetVersion.version.desc())
        .limit(1)
    )
    if latest and latest.id != dv.id:
        raise Conflict(
            "ข้อมูลมีเวอร์ชันใหม่แล้ว กรุณาเปิดข้อมูลล่าสุดก่อนบันทึก",
            details={"latest_dataset_id": latest.id, "latest_version": latest.version},
        )
    return dv


# --------------------------------------------------------------------------------------------
# Transform → dataset version
# --------------------------------------------------------------------------------------------
def transform_round(
    ctx: ServiceContext,
    session: Session,
    round_id: str,
    *,
    mapping_version_id: str | None = None,
    actor: str = "system",
    apply_overrides: bool = True,
) -> DatasetVersion:
    r = get_round(session, round_id)
    from forma.fingerprint.fingerprint import compare
    from forma.services import format_memory

    target = ctx.registry.get_ref(r.target_schema)
    current_fp = format_memory.files_fingerprint(r.files)
    selected_id = mapping_version_id or (r.summary or {}).get("mapping_version_id")
    mv = (
        get_mapping(session, selected_id)
        if selected_id
        else format_memory.select_baseline(session, r.customer_id, target, current_fp)
    )
    if mv is None:
        raise ValidationFailed("no approved mapping for this customer; approve a mapping first")
    if mv.customer_id != r.customer_id or not format_memory.compatible(mv, target):
        raise ValidationFailed("mapping belongs to another customer or target schema version")
    if not mapping_version_id and not (
        mv.status == "approved" or (mv.status == "superseded" and mv.approved_at is not None)
    ):
        raise Conflict("mapping is not approved; approve a mapping first")
    if not selected_id:
        previous_fp = format_memory.mapping_fingerprint(mv)
        if (
            not previous_fp
            or not current_fp
            or compare(previous_fp, current_fp).classification not in ("identical", "header_moved")
        ):
            raise ValidationFailed("no approved mapping matches this layout; run mapping first")
    schema = ctx.registry.get_ref(mv.target_schema)
    doc = MappingDocument.model_validate(mv.document)
    bindings, problems = bind_sources(doc, r.files, resolve=ctx.storage.local_path)
    if problems:
        raise ValidationFailed("cannot bind mapping sources to files", details={"problems": problems})
    engine = TransformEngine(schema, provenance_meta={"customer_id": r.customer_id, "import_round_id": r.id})
    res = engine.run(doc, bindings)
    records = res.records
    findings = [f.to_dict() for f in res.findings]
    stats = dict(res.stats)
    override_impact: dict[str, Any] = {}
    if apply_overrides:
        records, override_impact = apply_persistent_overrides(session, r.customer_id, schema, records)
        if override_impact.get("rows_affected") or override_impact.get("rows_deleted"):
            errors = schema_errors(schema, records)  # validate what the overrides produced
            findings = _with_contract_findings(
                _with_schema_findings(findings, errors, schema), records, schema
            )
            stats["invalid_records"] = _invalid_count(errors)
    r = lock_round(session, r.id)
    prev_version = session.scalar(
        select(DatasetVersion).where(DatasetVersion.round_id == r.id).order_by(DatasetVersion.version.desc())
    )
    dv = _new_version(
        ctx,
        session,
        records,
        stamp_meta=True,
        round_id=r.id,
        customer_id=r.customer_id,
        mapping_version_id=mv.id,
        version=(prev_version.version + 1) if prev_version else 1,
        status="draft",
        stats={**stats, "overrides": override_impact},
        findings=findings,
        parent_id=prev_version.id if prev_version else None,
        created_by=actor,
    )
    r.status = "transformed"
    r.summary = {
        **(r.summary or {}),
        "dataset_version_id": dv.id,
        "rows": len(records),
        "errors": sum(1 for f in findings if f["severity"] == "error"),
    }
    from forma.services import verdict_flow

    verdict_flow.attach_to_dataset(ctx, session, dv, r)  # the conclusion of the data stage (ADR-036)
    audit(session, "dataset.transform", "dataset", dv.id, actor=actor, round_id=r.id, rows=len(records))
    return dv


# --------------------------------------------------------------------------------------------
# Edits (change sets) — one-time corrections & persistent overrides
# --------------------------------------------------------------------------------------------
# operation shapes:
#   {"kind": "set_cell", "identity": "<sku>", "target": "/product_name", "value": "..."}
#   {"kind": "set_field", "target": "/status", "value": "inactive", "where": <DSL bool expr over pointers>}
#   {"kind": "scale_field", "target": "/prices/*/amount", "factor": 0.95, "where": <expr>}
#   {"kind": "delete_rows", "where": <expr>}  |  {"kind": "delete_row", "identity": "<sku>"}
#   {"kind": "set_item", "identity": "<sku>", "target": "/prices/0/amount", "value": 100}
# set_field / scale_field / delete_rows also take "identities": [...] — the rows picked in the editor
# (combined with "where" when both are given).


def _identity_of(rec: dict[str, Any], schema: TargetSchema) -> str:
    return "|".join(str(_get_pointer(rec, k)) for k in schema.identity_keys)


# --------------------------------------------------------------------------------------------
# Schema conformance (ADR-026): a record that breaks the target contract is never approved or exported
# --------------------------------------------------------------------------------------------
SCHEMA_FINDINGS_CAP = 200


def schema_errors(schema: TargetSchema, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every error of every record as exported: row, identity, pointer, message."""
    out: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        errs = schema.validate_record(rec)
        if errs:
            ident = _identity_of(rec, schema)
            out.extend({"row": i, "identity": ident, **e} for e in errs)
    return out


def new_schema_errors(
    schema: TargetSchema, before: list[dict[str, Any]], after_errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The errors an edit introduces: present after it, absent from the same record before it. Only the
    records that are invalid after the edit are validated again, so fixing one row of many is allowed."""
    if not after_errors:
        return []
    wanted = {e["identity"] for e in after_errors}
    old = {ident: r for r in before if (ident := _identity_of(r, schema)) in wanted}
    known = {
        (ident, e["pointer"], e["message"]) for ident, r in old.items() for e in schema.validate_record(r)
    }
    return [e for e in after_errors if (e["identity"], e["pointer"], e["message"]) not in known]


def _invalid_count(errors: list[dict[str, Any]]) -> int:
    return len({e["row"] for e in errors})


def _with_contract_findings(
    findings: list[dict[str, Any]], records: list[dict[str, Any]], schema: TargetSchema
) -> list[dict[str, Any]]:
    """Replace a version's business-check findings with the checks of its current records (ADR-060): an
    override or an edit can break an invariant the mapping satisfied, and the contract decides, not the edit."""
    from forma.ai.contract_checks import evaluate

    kept = [f for f in findings or [] if f.get("kind") != "contract_check"]
    fresh = [
        Finding("contract_check", "error", **item).to_dict() for item in evaluate(records, schema.document)
    ]
    return [*kept, *fresh]


def _with_schema_findings(
    findings: list[dict[str, Any]], errors: list[dict[str, Any]], schema: TargetSchema
) -> list[dict[str, Any]]:
    """Replace a version's validation findings with the errors of its current records."""
    kept = [f for f in findings or [] if f.get("kind") != "validation"]
    fresh = [
        Finding(
            "validation",
            "error",
            f"{e['pointer']}: {e['message']}",
            target=schema.wildcard(e["pointer"]),
            row=e["row"],
            identity=e["identity"],
        ).to_dict()
        for e in errors[:SCHEMA_FINDINGS_CAP]
    ]
    if len(errors) > SCHEMA_FINDINGS_CAP:
        more = len(errors) - SCHEMA_FINDINGS_CAP
        fresh.append(Finding("validation", "error", f"... {more} more schema validation errors").to_dict())
    return kept + fresh


def invalid_records(dv: DatasetVersion) -> int:
    """How many records of this version do not match the target schema."""
    stats = dv.stats or {}
    if "invalid_records" in stats:
        return int(stats["invalid_records"])
    n = 0  # versions made before the count was kept: their validation findings, one per record
    for f in dv.findings or []:
        if f.get("kind") == "validation" and f.get("severity") == "error":
            more = re.match(r"\.\.\. (\d+) more", str(f.get("message", "")))
            n += int(more.group(1)) if more else 1
    return n


def _require_valid(dv: DatasetVersion, action: str) -> None:
    bad = invalid_records(dv)
    if bad:
        sample = [
            {"identity": f.get("identity"), "message": f.get("message")}
            for f in dv.findings or []
            if f.get("kind") == "validation" and f.get("row") is not None
        ][:10]
        raise Conflict(
            f"{bad} record{'s do' if bad != 1 else ' does'} not match the target format; "
            f"fix or remove them before {action}",
            details={"invalid_records": bad, "errors": sample},
        )


def derived_columns(schema: TargetSchema) -> list[dict[str, Any]]:
    """Per-record figures over collections that a `where` can test — e.g. ``count(/promotions)`` (is the
    SKU in a promotion?) or ``sum(/inventory/*/qty)`` (total stock). Names come from the schema."""
    out: list[dict[str, Any]] = []
    leaves = schema.leaf_fields()
    for c in schema.collections():
        out.append({"name": f"count({c.pointer})", "collection": c.pointer, "key": None, "fn": "count"})
        for f in leaves:
            if f.in_collection == c.pointer and {"number", "integer"} & set(f.json_type):
                key = f.pointer.rsplit("/", 1)[-1]
                for fn in ("sum", "min", "max"):
                    out.append({"name": f"{fn}({f.pointer})", "collection": c.pointer, "key": key, "fn": fn})
    return out


def where_names(where: Any) -> set[str]:
    """Column names a `where` expression reads (to compute only the derived figures it needs)."""
    out: set[str] = set()
    if isinstance(where, dict):
        if where.get("op") == "col" and where.get("name"):
            out.add(str(where["name"]))
        for v in where.values():
            out |= where_names(v)
    elif isinstance(where, list):
        for v in where:
            out |= where_names(v)
    return out


def _aggregate(rec: dict[str, Any], d: dict[str, Any]) -> float | None:
    items = _get_pointer(rec, d["collection"])
    items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    if d["fn"] == "count":
        return float(len(items))
    nums = [
        float(v) for it in items if isinstance(v := it.get(d["key"]), int | float) and not isinstance(v, bool)
    ]
    if not nums:
        return None
    return float(sum(nums)) if d["fn"] == "sum" else min(nums) if d["fn"] == "min" else max(nums)


def _flat_frame(
    records: list[dict[str, Any]], schema: TargetSchema, need: set[str] | None = None
) -> pl.DataFrame:
    """Root-level leaf pointers as columns (used to evaluate `where` expressions), plus the derived
    collection figures named in ``need``."""
    cols = [f.pointer for f in schema.leaf_fields() if f.in_collection is None]
    derived = [d for d in derived_columns(schema) if need and d["name"] in need]
    data: dict[str, list[Any]] = {c: [] for c in cols}
    data["__i__"] = []
    numbers: dict[str, list[Any]] = {d["name"]: [] for d in derived}
    for i, r in enumerate(records):
        data["__i__"].append(i)
        for c in cols:
            v = _get_pointer(r, c)
            data[c].append(orjson.dumps(v).decode() if isinstance(v, list | dict) else v)
        for d in derived:
            numbers[d["name"]].append(_aggregate(r, d))
    return pl.DataFrame(
        {
            **{
                k: pl.Series(k, v, dtype=pl.String if k != "__i__" else pl.Int64, strict=False)
                for k, v in data.items()
            },
            **{k: pl.Series(k, v, dtype=pl.Float64) for k, v in numbers.items()},
        }
    )


def _where_mask(frame: pl.DataFrame, where: dict[str, Any] | None) -> list[int]:
    if not where:
        return frame["__i__"].to_list()
    expr = _parse_expr(where)
    ctx = CompileContext(default_source="", columns=set(frame.columns))
    # ColExpr names are pointers; physical names are the same, but CompileContext prefixes "<source>." → emulate
    renamed = frame.rename({c: f".{c}" for c in frame.columns if c != "__i__"})
    ctx = CompileContext(default_source="", columns=set(renamed.columns))
    mask = compile_expr(expr, ctx).cast(pl.Boolean, strict=False).fill_null(False)
    return renamed.filter(mask)["__i__"].to_list()


def _parse_expr(d: dict[str, Any]) -> ColExpr | ConstExpr | CallExpr:
    op = d.get("op")
    if op == "col":
        return ColExpr.model_validate(d)
    if op == "const":
        return ConstExpr.model_validate(d)
    return CallExpr.model_validate(d)


def apply_operations(
    records: list[dict[str, Any]], operations: list[dict[str, Any]], schema: TargetSchema
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pure function: returns (new_records, impact). Never mutates the input."""
    recs = copy.deepcopy(records)
    by_identity = {_identity_of(r, schema): i for i, r in enumerate(recs)}
    changes: list[dict[str, Any]] = []
    deleted: set[int] = set()
    frame_cache: pl.DataFrame | None = None
    need = set().union(*(where_names(op.get("where")) for op in operations)) if operations else set()

    def frame() -> pl.DataFrame:
        nonlocal frame_cache
        if frame_cache is None:
            frame_cache = _flat_frame(recs, schema, need)
        return frame_cache

    for op in operations:
        kind = op.get("kind")
        if kind in ("set_cell", "set_item"):
            i = by_identity.get(str(op.get("identity")))
            if i is None:
                continue
            before = _get_pointer(recs[i], op["target"])
            _set_pointer(recs[i], op["target"], op.get("value"))
            changes.append(
                {
                    "row": i,
                    "identity": op["identity"],
                    "target": op["target"],
                    "before": before,
                    "after": op.get("value"),
                }
            )
        elif kind == "delete_row":
            i = by_identity.get(str(op.get("identity")))
            if i is not None:
                deleted.add(i)
                changes.append(
                    {"row": i, "identity": op["identity"], "target": None, "before": "row", "after": None}
                )
        elif kind in ("set_field", "scale_field", "delete_rows"):
            idxs = _where_mask(frame(), op.get("where"))
            if op.get("identities") is not None:
                picked = {str(x) for x in op["identities"]}
                idxs = [i for i in idxs if _identity_of(recs[i], schema) in picked]
            for i in idxs:
                if kind == "delete_rows":
                    deleted.add(i)
                    changes.append(
                        {
                            "row": i,
                            "identity": _identity_of(recs[i], schema),
                            "target": None,
                            "before": "row",
                            "after": None,
                        }
                    )
                    continue
                target = op["target"]
                for ptr in _expand_pointer(recs[i], target):
                    before = _get_pointer(recs[i], ptr)
                    if kind == "set_field":
                        after = op.get("value")
                    else:
                        if not isinstance(before, int | float):
                            continue
                        after = round(before * float(op.get("factor", 1.0)), 4)
                    _set_pointer(recs[i], ptr, after)
                    changes.append(
                        {
                            "row": i,
                            "identity": _identity_of(recs[i], schema),
                            "target": ptr,
                            "before": before,
                            "after": after,
                        }
                    )
            frame_cache = None
        else:
            raise ValidationFailed(f"unknown operation kind {kind!r}")
    out = [r for i, r in enumerate(recs) if i not in deleted]
    impact = {
        "rows_affected": len({c["row"] for c in changes}),
        "cells_changed": sum(1 for c in changes if c["target"]),
        "rows_deleted": len(deleted),
        "fields": sorted({c["target"] for c in changes if c["target"]}),
        "sample": changes[:25],
    }
    return out, impact


def _expand_pointer(rec: dict[str, Any], pointer: str) -> list[str]:
    """'/prices/*/amount' → ['/prices/0/amount', '/prices/1/amount'] for this record."""
    if "/*/" not in pointer:
        return [pointer]
    head, tail = pointer.split("/*/", 1)
    arr = _get_pointer(rec, head)
    if not isinstance(arr, list):
        return []
    return [f"{head}/{i}/{tail}" for i in range(len(arr))]


def preview_changes(
    ctx: ServiceContext, session: Session, dataset_id: str, operations: list[dict[str, Any]]
) -> dict[str, Any]:
    dv = get_dataset(session, dataset_id)
    schema = ctx.registry.get_ref(get_mapping(session, dv.mapping_version_id).target_schema)
    records = records_of(ctx, dv)
    new, impact = apply_operations(records, operations, schema)
    after = schema_errors(schema, new)
    return {
        "impact": impact,
        "validation_errors": new_schema_errors(schema, records, after)[:50],  # these block the save
        "invalid_before": invalid_records(dv),
        "invalid_after": _invalid_count(after),
        "rows_after": len(new),
    }


def apply_changes(
    ctx: ServiceContext,
    session: Session,
    dataset_id: str,
    operations: list[dict[str, Any]],
    *,
    kind: str = "one_time",
    actor: str = "operator",
    note: str | None = None,
) -> tuple[DatasetVersion, ChangeSet]:
    dv = _writable_dataset(session, dataset_id)
    if kind == "persistent" and any(op.get("kind") not in ("set_cell", "set_field") for op in operations):
        raise ValidationFailed("กฎสำหรับรอบถัดไปรองรับการกำหนดค่าเท่านั้น การแก้แบบนี้ให้บันทึกเฉพาะครั้งนี้")
    mv = get_mapping(session, dv.mapping_version_id)
    schema = ctx.registry.get_ref(mv.target_schema)
    records = records_of(ctx, dv)
    new, impact = apply_operations(records, operations, schema)
    after = schema_errors(schema, new)
    added = new_schema_errors(schema, records, after)
    if added:  # errors the records already had do not block fixing others one at a time
        raise ValidationFailed("edits would violate the target schema", details={"errors": added[:50]})
    nv = _new_version(
        ctx,
        session,
        new,
        stamp_meta=True,
        round_id=dv.round_id,
        customer_id=dv.customer_id,
        mapping_version_id=dv.mapping_version_id,
        version=dv.version + 1,
        status="draft",
        stats={**dv.stats, "last_change": impact, "invalid_records": _invalid_count(after)},
        findings=_with_contract_findings(_with_schema_findings(dv.findings, after, schema), new, schema),
        parent_id=dv.id,
        created_by=actor,
        note=note,
    )
    dv.status = "superseded" if dv.status == "draft" else dv.status
    cs = ChangeSet(
        dataset_version_id=dv.id,
        result_version_id=nv.id,
        customer_id=dv.customer_id,
        kind=kind,
        operations=operations,
        impact=impact,
        author=actor,
        note=note,
    )
    session.add(cs)
    if kind == "persistent":
        for op in operations:
            if op.get("kind") == "set_field" and op.get("identities") is not None:
                # rows picked by hand: one override per record, so a later import finds them by identity
                for ident in op["identities"]:
                    session.add(
                        Override(
                            customer_id=dv.customer_id,
                            schema_ref=schema.ref,
                            schema_hash=stable_hash(schema.document),
                            target=op["target"],
                            where={"identity": str(ident), "predicate": op.get("where")},
                            value=op.get("value"),
                            author=actor,
                            note=note,
                        )
                    )
            elif op.get("kind") in ("set_cell", "set_field"):
                session.add(
                    Override(
                        customer_id=dv.customer_id,
                        schema_ref=schema.ref,
                        schema_hash=stable_hash(schema.document),
                        target=op["target"],
                        where={"identity": op["identity"]} if "identity" in op else (op.get("where") or {}),
                        value=op.get("value"),
                        author=actor,
                        note=note,
                    )
                )
    r = session.get(ImportRound, dv.round_id)
    if r:
        errors = sum(1 for f in nv.findings if f["severity"] == "error")
        r.status = "transformed"
        r.summary = {**(r.summary or {}), "dataset_version_id": nv.id, "rows": len(new), "errors": errors}
    audit(
        session,
        "dataset.edit",
        "dataset",
        nv.id,
        actor=actor,
        parent=dv.id,
        kind=kind,
        **{k: v for k, v in impact.items() if k != "sample"},
    )
    return nv, cs


def apply_persistent_overrides(
    session: Session, customer_id: str, schema: TargetSchema, records: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rules = session.scalars(
        select(Override)
        .where(Override.customer_id == customer_id, Override.active.is_(True))
        .order_by(Override.created_at, Override.id)
    ).all()
    if not rules:
        return records, {"rules": 0}
    ops: list[dict[str, Any]] = []
    skipped = 0
    for o in rules:
        if o.schema_ref and (o.schema_ref != schema.ref or o.schema_hash != stable_hash(schema.document)):
            skipped += 1
            continue
        if "identity" in (o.where or {}):
            ops.append(
                {
                    "kind": "set_field",
                    "identities": [str(o.where["identity"])],
                    "where": o.where.get("predicate"),
                    "target": o.target,
                    "value": o.value,
                }
            )
        else:
            ops.append({"kind": "set_field", "target": o.target, "value": o.value, "where": o.where or None})
    new, impact = apply_operations(records, ops, schema)
    return new, {
        "rules": len(ops),
        "skipped_schema": skipped,
        **{k: v for k, v in impact.items() if k != "sample"},
    }


def rollback(ctx: ServiceContext, session: Session, dataset_id: str, actor: str) -> DatasetVersion:
    """Rollback = new version whose content equals the parent (history is never rewritten)."""
    dv = _writable_dataset(session, dataset_id)
    if not dv.parent_id:
        raise Conflict("this version has no parent to roll back to")
    parent = get_dataset(session, dv.parent_id)
    records = records_of(ctx, parent)
    nv = _new_version(
        ctx,
        session,
        records,
        stamp_meta=True,
        round_id=dv.round_id,
        customer_id=dv.customer_id,
        mapping_version_id=parent.mapping_version_id,
        version=dv.version + 1,
        status="draft",
        stats=parent.stats,
        findings=parent.findings,
        parent_id=dv.id,
        created_by=actor,
        note=f"rollback to v{parent.version}",
    )
    dv.status = "superseded" if dv.status == "draft" else dv.status
    r = get_round(session, dv.round_id)
    r.status = "transformed"
    r.summary = {
        **(r.summary or {}),
        "dataset_version_id": nv.id,
        "rows": nv.row_count,
        "errors": sum(1 for f in nv.findings if f.get("severity") == "error"),
    }
    audit(session, "dataset.rollback", "dataset", nv.id, actor=actor, to=parent.id)
    return nv


def anomaly_report(ctx: ServiceContext, session: Session, dv: DatasetVersion) -> dict[str, Any]:
    """Anomalies of a dataset version: within the round, vs last published round, vs the usual size."""
    from forma.services import anomalies

    cfg = ctx.policy.policy["anomaly"]
    schema = ctx.registry.get_ref(get_mapping(session, dv.mapping_version_id).target_schema)
    prev = previous_published(session, dv.customer_id, dv.round_id)
    history = [
        d.row_count
        for d in session.scalars(
            select(DatasetVersion)
            .where(
                DatasetVersion.customer_id == dv.customer_id,
                DatasetVersion.round_id != dv.round_id,
                DatasetVersion.status.in_(["approved", "published"]),
            )
            .order_by(DatasetVersion.created_at.desc())
        )
    ][: int(cfg["history_rounds"])]
    return anomalies.detect(
        schema,
        records_of(ctx, dv),
        cfg,
        previous=records_of(ctx, prev) if prev is not None else None,
        history_rows=history or None,
    )


def approve_dataset(
    session: Session,
    dataset_id: str,
    actor: str,
    *,
    ctx: ServiceContext | None = None,
    acknowledge_anomalies: bool = False,
    comment: str | None = None,
) -> DatasetVersion:
    dv = _writable_dataset(session, dataset_id)
    _require_valid(dv, "approving")  # the contract is not negotiable: no acknowledgement overrides it
    if ctx is not None and ctx.settings.pilot_mode and actor in ("policy", "system", "ai"):
        raise Conflict("Pilot: a person must approve this dataset.")
    acknowledged = None
    if ctx is not None and ctx.policy.policy["anomaly"]["block_approval_on_high"]:
        rep = anomaly_report(ctx, session, dv)
        if rep["high"] and not acknowledge_anomalies:
            # the policy decides (rule 6): unusual data reaches publication only when a person says so
            raise Conflict(
                f"{rep['high']} high-severity anomalies; review them and acknowledge to approve",
                details={"high": rep["high"], "headline": rep["headline"]},
            )
        acknowledged = rep["high"] or None
    for other in session.scalars(
        select(DatasetVersion).where(
            DatasetVersion.round_id == dv.round_id,
            DatasetVersion.id != dv.id,
            DatasetVersion.status.in_(["approved", "published"]),
        )
    ):
        other.status = "superseded"
    dv.status = "approved"
    r = session.get(ImportRound, dv.round_id)
    if r:
        r.status = "approved_data"
    audit(
        session,
        "dataset.approve",
        "dataset",
        dv.id,
        actor=actor,
        **({"acknowledged_anomalies": acknowledged, "comment": comment} if acknowledged else {}),
    )
    return dv


# --------------------------------------------------------------------------------------------
# Change intelligence (round vs previous published round of the same customer)
# --------------------------------------------------------------------------------------------
def previous_published(session: Session, customer_id: str, before_round_id: str) -> DatasetVersion | None:
    rounds = session.scalars(
        select(ImportRound)
        .where(ImportRound.customer_id == customer_id, ImportRound.id != before_round_id)
        .order_by(ImportRound.created_at.desc())
    ).all()
    for r in rounds:
        d = session.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.round_id == r.id, DatasetVersion.status.in_(["published", "approved"]))
            .order_by(DatasetVersion.version.desc())
        )
        if d:
            return d
    return None


def diff_datasets(
    schema: TargetSchema, old: list[dict[str, Any]], new: list[dict[str, Any]]
) -> dict[str, Any]:
    o = {_identity_of(r, schema): r for r in old}
    n = {_identity_of(r, schema): r for r in new}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed: list[dict[str, Any]] = []
    price_changes: list[dict[str, Any]] = []
    promo_changes = 0
    # which pointers are prices / promotions comes from the schema's semantics, not from field names
    price_ptrs = schema.semantic_pointers("price.amount")
    promo_coll = schema.collection_for("promotions")
    for k in set(o) & set(n):
        a, b = o[k], n[k]
        deltas = _record_delta(a, b)
        if deltas:
            changed.append({"identity": k, "changes": deltas[:20]})
            for d in deltas:
                if (
                    schema.wildcard(d["pointer"]) in price_ptrs
                    and isinstance(d["before"], int | float)
                    and isinstance(d["after"], int | float)
                ):
                    price_changes.append(
                        {
                            "identity": k,
                            "before": d["before"],
                            "after": d["after"],
                            "pct": round((d["after"] - d["before"]) / d["before"] * 100, 2)
                            if d["before"]
                            else None,
                        }
                    )
                if promo_coll and (d["pointer"] + "/").startswith(promo_coll + "/"):
                    promo_changes += 1
    big = [p for p in price_changes if p["pct"] is not None and abs(p["pct"]) >= 20]
    return {
        "previous_rows": len(old),
        "current_rows": len(new),
        "added": added[:500],
        "removed": removed[:500],
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "changed": changed[:300],
        "price_changes": price_changes[:300],
        "price_change_count": len(price_changes),
        "large_price_moves": big[:100],
        "promotion_changes": promo_changes,
    }


def _record_delta(a: dict[str, Any], b: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    keys = (set(a) | set(b)) - set(INTERNAL_KEYS)
    for k in sorted(keys):
        va, vb = a.get(k), b.get(k)
        ptr = f"{prefix}/{k}"
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(_record_delta(va, vb, ptr))
        elif isinstance(va, list) and isinstance(vb, list) and va and isinstance(va[0], dict):
            for i, (x, y) in enumerate(zip(va, vb, strict=False)):
                out.extend(_record_delta(x, y, f"{ptr}/{i}"))
            if len(va) != len(vb):
                out.append({"pointer": ptr, "before": len(va), "after": len(vb), "kind": "length"})
        elif va != vb:
            out.append({"pointer": ptr, "before": va, "after": vb})
    return out


def change_report(ctx: ServiceContext, session: Session, dataset_id: str) -> dict[str, Any]:
    dv = get_dataset(session, dataset_id)
    schema = ctx.registry.get_ref(get_mapping(session, dv.mapping_version_id).target_schema)
    prev = previous_published(session, dv.customer_id, dv.round_id)
    cur = records_of(ctx, dv)
    anomalies = anomaly_report(ctx, session, dv)
    if prev is None:
        return {"has_previous": False, "current_rows": len(cur), "anomalies": anomalies}
    old = records_of(ctx, prev)
    return {
        "has_previous": True,
        "previous_dataset_id": prev.id,
        "previous_round_id": prev.round_id,
        **diff_datasets(schema, old, cur),
        "anomalies": anomalies,
    }


# --------------------------------------------------------------------------------------------
# Export (atomic publish)
# --------------------------------------------------------------------------------------------
def export_dataset(
    ctx: ServiceContext, session: Session, dataset_id: str, fmt: str, actor: str = "operator"
) -> ExportArtifact:
    if fmt not in FORMATS:
        raise ValidationFailed(f"format must be one of {FORMATS}")
    dv = _writable_dataset(session, dataset_id)
    # exporting publishes: only data a human approved may leave (approved_data → published)
    if dv.status not in ("approved", "published"):
        raise Conflict(f"dataset is {dv.status}; approve it before exporting", details={"status": dv.status})
    if ctx.settings.pilot_mode:
        from forma.db.models import AuditLog

        approval = session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "dataset.approve",
                AuditLog.entity_id == dv.id,
                AuditLog.actor.not_in(["policy", "system", "ai"]),
            )
            .limit(1)
        )
        if approval is None:
            raise Conflict("Pilot: this exact dataset version needs a person's approval before export.")
    _require_valid(dv, "exporting")  # also for versions approved before the gate existed
    schema = ctx.registry.get_ref(get_mapping(session, dv.mapping_version_id).target_schema)
    src = ctx.records_path(dv)
    # Unique and short enough for Windows storage roots; round ID already lives in the manifest/DB.
    name = f"{dv.id}_{new_id('exp')}.{fmt}"
    key = ctx.export_key(dv.customer_id, name)
    tmp = ctx.tmp_dir / name
    try:
        _, digest, _size = write_export(lambda: iter_records(src), fmt, schema, tmp)
        ctx.storage.put(key, tmp)  # atomic publish: the object appears only once fully written
        session.info.setdefault("rollback_objects", []).append((ctx.storage, key))
    finally:
        tmp.unlink(missing_ok=True)
    art = ExportArtifact(
        dataset_version_id=dv.id,
        customer_id=dv.customer_id,
        fmt=fmt,
        storage_key=key,
        row_count=dv.row_count,
        sha256=digest,
    )
    session.add(art)
    if dv.status == "approved":
        dv.status = "published"
    r = session.get(ImportRound, dv.round_id)
    if r:
        r.status = "published"
    audit(session, "dataset.export", "export", dv.id, actor=actor, fmt=fmt, rows=dv.row_count)
    return art


def lineage(ctx: ServiceContext, session: Session, dataset_id: str, identity: str) -> dict[str, Any]:
    """Trace one record back to its source rows and the mapping expression per field."""
    dv = get_dataset(session, dataset_id)
    mv = get_mapping(session, dv.mapping_version_id)
    schema = ctx.registry.get_ref(mv.target_schema)
    doc = MappingDocument.model_validate(mv.document)
    rec = next((r for r in records_of(ctx, dv) if _identity_of(r, schema) == identity), None)
    if rec is None:
        raise NotFound(f"record {identity} not in dataset")
    fields = [
        {
            "target": f.target,
            "expr": f.expr.model_dump(mode="json", exclude_none=True),
            "confidence": f.confidence,
            "evidence": f.evidence,
        }
        for f in doc.fields
    ]
    collections = [
        {
            "target": c.target,
            "source": c.source,
            "link": [k.model_dump() for k in c.link],
            "fields": [
                {
                    "target": f.target,
                    "expr": f.expr.model_dump(mode="json", exclude_none=True),
                    "confidence": f.confidence,
                    "evidence": f.evidence,
                }
                for f in c.fields
            ],
        }
        for c in doc.collections
    ]
    edits = [
        cs
        for cs in session.scalars(select(ChangeSet).where(ChangeSet.customer_id == dv.customer_id))
        if any(c.get("identity") == identity for c in cs.impact.get("sample", []))
    ]
    return {
        "record": rec,
        "source_meta": rec.get(PROVENANCE_KEY),
        "mapping_version": mv.id,
        "fields": fields,
        "collections": collections,
        "edits": [
            {
                "change_set": cs.id,
                "kind": cs.kind,
                "author": cs.author,
                "at": cs.created_at.isoformat(),
                "changes": [c for c in cs.impact.get("sample", []) if c.get("identity") == identity],
            }
            for cs in edits
        ],
    }
