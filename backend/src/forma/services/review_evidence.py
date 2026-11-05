"""Bounded, deterministic review evidence. No model-generated facts or business decisions."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from forma.core.errors import ValidationFailed
from forma.db.models import ImportRound, MappingVersion
from forma.engine.transform import ROW_COL, TransformResult, _get_pointer, _source_columns, load_source_frame
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import PROVENANCE_KEY, TargetSchema
from forma.services import mapping, sandbox
from forma.services.context import ServiceContext
from forma.services.lineage import _expr_columns, _Sources

SAMPLE_LIMIT = 50


def validate_answer_effects(
    ctx: ServiceContext,
    session: Session,
    m: MappingVersion,
    document: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute the complete delivery and reject new invalid records or unrelated changes."""
    r = session.get(ImportRound, m.round_id)
    if r is None:
        raise ValidationFailed("This mapping has no source delivery")
    schema = ctx.registry.get_ref(m.target_schema)
    before, old_errors = mapping.simulate(
        ctx, MappingDocument.model_validate(m.document), schema, r.files, limit=None
    )
    after, errors = mapping.simulate(
        ctx, MappingDocument.model_validate(document), schema, r.files, limit=None
    )
    if before is None or after is None or old_errors or errors:
        raise ValidationFailed(
            "Could not verify the complete delivery", details={"errors": old_errors + errors}
        )
    return _validate_answer_results(schema, before, after, decisions)


def _validate_answer_results(
    schema: TargetSchema,
    before: TransformResult,
    after: TransformResult,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check full results; preview and effect validation must use the same execution."""
    from forma.ai.plausibility import values_at
    from forma.core.hashing import stable_hash

    if after.stats.get("invalid_records", 0) > before.stats.get("invalid_records", 0):
        raise ValidationFailed(
            "These answers introduce invalid output records",
            details={"findings": [f.to_dict() for f in after.errors]},
        )
    old_findings = {(f.kind, f.target, f.message) for f in before.errors}
    introduced = [f.to_dict() for f in after.errors if (f.kind, f.target, f.message) not in old_findings]
    if introduced:
        raise ValidationFailed(
            "These answers introduce new validation errors", details={"findings": introduced}
        )

    def indexed(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {stable_hash([_get_pointer(rec, p) for p in schema.identity_keys]): rec for rec in records}

    old, new = indexed(before.records), indexed(after.records)

    def identity_counts(records: list[dict[str, Any]]) -> Counter[str]:
        return Counter(stable_hash([_get_pointer(rec, p) for p in schema.identity_keys]) for rec in records)

    if old.keys() != new.keys() or identity_counts(before.records) != identity_counts(after.records):
        raise ValidationFailed(
            "These answers change record identities or remove records; use the mapping editor"
        )
    targets = {q["field"]["pointer"] for q in decisions if q.get("action") != "select_rows"}
    row_choices = [q for q in decisions if q.get("action") == "select_rows"]
    for key, rec in old.items():
        # Equal aggregate error counts can hide fixing one row while breaking another.
        old_record_errors = {(e["pointer"], e["validator"]) for e in schema.validate_record(rec)}
        new_record_errors = {(e["pointer"], e["validator"]) for e in schema.validate_record(new[key])}
        if new_record_errors - old_record_errors:
            raise ValidationFailed(
                "These answers introduce an invalid field in a previously valid record",
                details={"identity": key, "errors": sorted(new_record_errors - old_record_errors)},
            )
        for spec in schema.leaf_fields():
            if spec.pointer in targets:
                continue
            allowed = any(
                (
                    spec.pointer.startswith(q["collection"] + "/")
                    if q.get("collection")
                    else spec.pointer in q.get("affected_fields", [q.get("target")])
                )
                and all(_get_pointer(rec, p) == v for p, v in q["identity"].items())
                for q in row_choices
            )
            if not allowed and list(values_at(rec, spec.pointer)) != list(values_at(new[key], spec.pointer)):
                raise ValidationFailed(
                    "These answers alter an unrelated field", details={"field": spec.pointer, "identity": key}
                )
    return {
        "complete": True,
        "passed": True,
        "records_checked": len(after.records),
        "remaining_errors": len(after.errors),
    }


def overview(ctx: ServiceContext, session: Session, m: MappingVersion) -> dict[str, Any]:
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    schema = ctx.registry.get_ref(m.target_schema)
    policy = m.policy or {}
    review = policy.get("field_review") or {}
    changes = []
    for f in review.get("fields", []):
        check = f.get("check") or {}
        if f["status"] == "unchanged" and check.get("verdict") in (None, "same", "no_data"):
            continue
        try:
            spec = schema.field(f["pointer"]).to_dict()
        except Exception:
            continue
        changes.append({**f, "field": spec})
    changes.sort(
        key=lambda f: (
            (f.get("check") or {}).get("verdict") in (None, "same", "no_data"),
            f.get("risk") not in ("critical", "high"),
            f["pointer"],
        )
    )
    return {
        "context": {
            "customer": {"code": r.customer.code, "name": r.customer.name} if r else None,
            "round_label": r.label if r else None,
            "files": [{"id": f.id, "name": f.original_name} for f in r.files] if r else [],
            "mapping_version": m.version,
        },
        "changes": changes,
        "comparison_dataset_id": review.get("compared_with"),
        "checks": {**(policy.get("checks") or {}), "verification": policy.get("verification")},
        "impact": policy.get("impact"),
        "fields": [f.to_dict() for f in schema.leaf_fields()],
    }


def evidence(ctx: ServiceContext, session: Session, m: MappingVersion, pointer: str) -> dict[str, Any]:
    """Read actual mapped outputs and the cells identified by engine provenance, on demand."""
    doc = MappingDocument.model_validate(m.document)
    schema = ctx.registry.get_ref(m.target_schema)
    root = doc.record.root_source
    options = [(f, root, None) for f in doc.fields] + [
        (f, c.source, c.target) for c in doc.collections for f in c.fields
    ]
    hit = next((entry for entry in options if entry[0].target == pointer), None)
    if hit is None:
        raise ValidationFailed("This field is not mapped in this version")
    fm, default, collection = hit
    spec = schema.field(pointer)
    r = session.get(ImportRound, m.round_id) if m.round_id else None
    if r is None:
        raise ValidationFailed("This mapping has no source delivery")
    doc.provenance = True  # preview only; never modify the stored document
    sim, _problems = mapping.simulate(ctx, doc, schema, r.files, limit=None)
    result: dict[str, Any] = {
        "field": pointer,
        "examples": [],
        "sample_limit": SAMPLE_LIMIT,
        "sample_records": len(sim.records) if sim else 0,
        "execution_scope": "complete_delivery",
        "constant": fm.expr.op == "const",
        "unavailable": sim is None or not sim.records,
        "context_only": False,
    }
    if sim is None or not sim.records:
        return result
    bindings, _ = mapping.bind_sources(doc, r.files, resolve=ctx.storage.local_path)
    sources = _Sources(doc, bindings)
    expr = fm.expr.model_dump(mode="json", exclude_none=True)
    cols = _expr_columns(expr, default)
    # A constant VAT flag has no source cell. Show price cells as context, never as VAT proof.
    if not cols and spec.semantic == "price.vat_included":
        amount = next(
            (
                f
                for f, sid, c in options
                if sid == default and c == collection and schema.field(f.target).semantic == "price.amount"
            ),
            None,
        )
        if amount:
            cols = _expr_columns(amount.expr.model_dump(mode="json", exclude_none=True), default)
            result["context_only"] = True
    # Verify that the lineage extractor and engine used the same rows. Header relocation and
    # filtered sources must not silently point users at unrelated cells.
    frames = {}
    for sid, _ in cols:
        if sid not in frames and sid in bindings:
            source = next(s for s in doc.sources if s.id == sid)
            frames[sid] = load_source_frame(
                source, bindings[sid], limit=None, expected_columns=_source_columns(doc, sid)
            )
    for rec in sim.records:
        lin = (rec.get(PROVENANCE_KEY) or {}).get("lineage") or {}
        joined = lin.get("joined") or {}
        locations = (
            list(
                enumerate(
                    (lin.get("item_groups") or {}).get(collection)
                    or (lin.get("items") or {}).get(collection, [])
                )
            )
            if collection
            else [(None, lin.get("root_row"))]
        )
        for index, row in locations:
            rows = {root: lin.get("root_row"), **joined, default: row}
            actual = pointer.replace("/*/", f"/{index}/", 1) if index is not None else pointer
            cells = []
            for sid, name in cols:
                source_rows = rows.get(sid)
                frame = frames.get(sid)
                if frame is None or source_rows is None:
                    continue
                for source_row in source_rows if isinstance(source_rows, list) else [source_rows]:
                    extracted = frame.filter(frame[ROW_COL] == source_row)
                    loc = sources.get(sid)
                    if loc is None or extracted.height != 1:
                        continue
                    col = sources._column(loc, name)
                    cell = sources.cell(sid, source_row, name)
                    if cell is None or col not in extracted.columns or cell["raw"] != extracted[col][0]:
                        continue
                    if cell.get("verified") is False:
                        continue
                    cells.append(cell)
            result["examples"].append(
                {
                    "identity": sandbox._identity(rec, schema),
                    "item": index,
                    "value": _get_pointer(rec, actual),
                    "cells": cells,
                    "source_complete": len(cells)
                    == sum(len(rows[sid]) if isinstance(rows.get(sid), list) else 1 for sid, _ in cols)
                    and bool(cols),
                }
            )
            if len(result["examples"]) >= 5:
                return result
    return result


def answer_impact(
    ctx: ServiceContext,
    session: Session,
    m: MappingVersion,
    document: dict[str, Any],
    answers: dict[str, Any],
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute each version once, validate all records, then bound only the displayed diff."""
    if m.round_id is None:
        raise ValidationFailed("This mapping has no source delivery")
    before, old_result = mapping.preview_execution(ctx, session, m.round_id, m.document, limit=SAMPLE_LIMIT)
    after, new_result = mapping.preview_execution(ctx, session, m.round_id, document, limit=SAMPLE_LIMIT)
    from forma.services.review_questions import questions

    catalogue = {q["id"]: q for q in questions(ctx, m)["questions"]}
    schema = ctx.registry.get_ref(m.target_schema)
    try:
        if old_result is None or new_result is None:
            raise ValidationFailed("Could not verify the complete delivery")
        checked = _validate_answer_results(
            schema,
            old_result,
            new_result,
            decisions if decisions is not None else [catalogue[k] for k in answers],
        )
    except ValidationFailed as exc:
        checked = {"complete": True, "passed": False, "error": str(exc)}
        after["ok"] = False
        after["errors"] = [str(exc)]
    counts, detail = sandbox.compare_records(
        schema,
        old_result.records if old_result is not None else [],
        new_result.records if new_result is not None else [],
        cap=SAMPLE_LIMIT,
    )
    return {
        "preview": after,
        "before": before.get("records", [])[:3],
        "comparison": {
            "validation": checked,
            "counts": counts,
            "rows": detail["changed"],
            "sample_limit": SAMPLE_LIMIT,
            "before_records": len(old_result.records) if old_result is not None else 0,
            "after_records": len(new_result.records) if new_result is not None else 0,
            "scope": "complete_delivery",
            "reliable": bool(before.get("ok") and after.get("ok")),
            "confirmed_fields": list(answers),
        },
    }
