"""Turn uncertain fields into bounded, reviewable answers. Answers produce new DSL versions."""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from forma.ai.heuristics import VAT_ASSUMED_QUESTION_RE
from forma.core.errors import Conflict, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import ImportRound, MappingVersion
from forma.mapping_dsl.models import CallExpr, ColExpr, ConstExpr, MappingDocument
from forma.schema_registry import FieldSpec, TargetSchema
from forma.services import mapping as ms
from forma.services.context import ServiceContext

MAX_CHOICES = 8
_DATE_SEMANTICS = {"price.effective_from", "price.effective_to", "promo.start_date", "promo.end_date"}


def _vat_flags(schema: TargetSchema, policy: dict[str, Any]) -> set[str]:
    """VAT fields beside a value that moved by exactly the VAT rate: only a person can settle those."""
    flags: set[str] = set()
    for check in (policy.get("field_review") or {}).get("checks") or []:
        if check.get("verdict") not in ("vat_added", "vat_removed") or not schema.has_field(check["pointer"]):
            continue
        spec = schema.field(check["pointer"])
        flag = schema.semantic_field("price.vat_included", in_collection=spec.in_collection)
        if flag is not None:
            flags.add(flag.pointer)
    return flags


def _col_leaves(expr: Any) -> list[str]:
    if isinstance(expr, ColExpr):
        return [expr.name]
    if isinstance(expr, CallExpr):
        return [n for a in expr.args for n in _col_leaves(a)]
    return []


def repoint(expr: Any, old: str, new: str) -> Any:
    """The same expression reading column ``new`` where it read ``old``."""
    if isinstance(expr, ColExpr):
        return expr.model_copy(update={"name": new}) if expr.name == old else expr
    if isinstance(expr, CallExpr):
        return expr.model_copy(update={"args": [repoint(a, old, new) for a in expr.args]})
    return expr


def _compatible(spec: FieldSpec, col: dict[str, Any]) -> bool:
    t = str(col.get("inferred_type") or "")
    if t == "empty":
        return False
    json_type = set(spec.json_type)
    if json_type & {"number", "integer"}:
        return t in ("int", "float") or "price_like" in (col.get("hints") or [])
    if spec.fmt == "date" or (spec.semantic or "") in _DATE_SEMANTICS:
        return t == "date" or "thai_date" in (col.get("hints") or []) or t == "string"
    if "boolean" in json_type:
        return t in ("bool", "string")
    return t != "int" or "sku_like" not in (col.get("hints") or [])  # a stock count is not a name or a code


def _table_columns(m: MappingVersion, doc: MappingDocument) -> dict[str, list[dict[str, Any]]]:
    """Per DSL source id, the profiled columns of the table it reads (empty when the round is gone)."""
    session = object_session(m)
    r = session.get(ImportRound, m.round_id) if session is not None and m.round_id else None
    if r is None:
        return {}
    tables: list[dict[str, Any]] = []
    for f in r.files:
        for t in (f.profile or {}).get("tables", []):
            tables.append({**t, "_file": f.original_name})
    out: dict[str, list[dict[str, Any]]] = {}
    for s in doc.sources:
        hit = None
        if s.sheet is not None and s.sheet.name:
            want = s.sheet.name.strip().lower()
            hit = next((t for t in tables if str(t.get("sheet", "")).strip().lower() == want), None)
        if hit is None and s.file.name_pattern:
            try:
                rx = re.compile(s.file.name_pattern)
                hit = next((t for t in tables if rx.search(t["_file"])), None)
            except re.error:
                hit = None
        if hit is None and len(tables) == 1:
            hit = tables[0]
        if hit is not None:
            out[s.id] = list(hit.get("columns", []))
    return out


def _choices(
    f: Any, spec: FieldSpec, source: str, columns: dict[str, list[dict[str, Any]]], taken: set[str]
) -> list[str]:
    """Column names a person can pick instead: one source column read now, others of a fitting type."""
    leaves = _col_leaves(f.expr)
    if len(leaves) != 1:
        return []
    current = leaves[0]
    others = [
        c["name"]
        for c in columns.get(source, [])
        if c["name"] != current and c["name"] not in taken and _compatible(spec, c)
    ]
    return [current, *others][:MAX_CHOICES] if others else []


def questions(ctx: ServiceContext, m: MappingVersion) -> dict[str, Any]:
    schema = ctx.registry.get_ref(m.target_schema)
    doc = MappingDocument.model_validate(m.document)
    specs = {f.pointer: f for f in schema.leaf_fields()}
    fields = [*doc.fields, *(f for c in doc.collections for f in c.fields)]
    source_of = {f.target: doc.record.root_source for f in doc.fields}
    source_of.update({f.target: c.source for c in doc.collections for f in c.fields})
    columns = _table_columns(m, doc)
    taken_by = {
        s: {n for f in fields if source_of.get(f.target) == s for n in _col_leaves(f.expr)} for s in columns
    }
    for spec_src in doc.sources:  # columns an unpivot consumes are not free either
        u = spec_src.table.unpivot
        if u is not None and spec_src.id in taken_by:
            taken_by[spec_src.id] |= {*u.id_columns, *(u.value_columns or [])}
    checks = (m.policy or {}).get("checks") or {}
    required = {
        str(value).rsplit(" (", 1)[0]
        for key in ("low_confidence_critical", "low_confidence_high")
        for value in checks.get(key, [])
    }
    has_checks = "low_confidence_critical" in checks or "low_confidence_high" in checks
    vat_flags = _vat_flags(schema, m.policy or {})
    items: list[dict[str, Any]] = []
    for f in fields:
        spec = specs.get(f.target)
        if spec is None:
            continue
        if f.target not in vat_flags:
            if not has_checks and f.confidence is not None and f.confidence >= 0.95:
                continue
            if has_checks and f.target not in required:
                continue
            if not has_checks and spec.risk not in ("critical", "high"):
                continue
        kind = "confirm"
        options: list[str] = []
        if "boolean" in spec.json_type and (f.expr.op == "const" or f.target in vat_flags):
            kind = "boolean"  # a VAT flag beside a value that moved by the rate is settled with yes/no, however it is read
        elif f.expr.op == "const" and set(spec.json_type) & {"integer", "number"}:
            kind = "number"
        else:
            src = source_of.get(f.target, doc.record.root_source)
            others = {n for n in taken_by.get(src, set()) if n not in _col_leaves(f.expr)}
            options = _choices(f, spec, src, columns, others)
            if options:
                kind = "choice"  # a decision with alternatives (ADR-058)
            elif spec.risk != "critical" and f.target not in vat_flags:
                continue  # nothing to decide: the field stays visible in the field review, unasked
        items.append(
            {
                "id": f.target,
                "field": spec.to_dict(),
                "kind": kind,
                "options": options,
                "current": f.expr.model_dump(mode="json", exclude_none=True),
                "evidence": f.evidence,
                "note": f.note,
            }
        )
    items.sort(key=lambda item: (item["kind"] == "confirm", item["field"]["risk"] != "critical"))
    return {
        "mapping_id": m.id,
        "round_id": m.round_id,
        "status": m.status,
        "blocked": (m.policy or {}).get("decision") == "block",
        "questions": items,
        "open_questions": m.open_questions or [],
        "reasons": (m.policy or {}).get("reasons", []),
        "sample": (m.policy or {}).get("sample", [])[:3],
    }


def answer_document(
    ctx: ServiceContext,
    m: MappingVersion,
    answers: dict[str, Any],
    actor: str,
) -> tuple[dict[str, Any], list[Any], str]:
    if m.status != "proposed":
        raise Conflict("This version has already been reviewed. Reload the questions.")
    if not answers or len(answers) > 100:
        raise ValidationFailed("Choose at least one answer")
    catalogue = {q["id"]: q for q in questions(ctx, m)["questions"]}
    if answers.keys() - catalogue.keys():
        raise ValidationFailed("An answer refers to a field that is not awaiting a decision")
    doc = MappingDocument.model_validate(m.document)
    vat_fields = {
        pointer for pointer, q in catalogue.items() if q["field"]["semantic"] == "price.vat_included"
    }
    for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)]:
        if f.target not in answers:
            continue
        q, value = catalogue[f.target], answers[f.target]
        kind, spec = q["kind"], q["field"]
        if kind == "boolean" and not isinstance(value, bool):
            raise ValidationFailed("Choose yes or no")
        if kind == "number":
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValidationFailed("Enter a finite number")
            if "integer" in spec["json_type"] and value != int(value):
                raise ValidationFailed("Enter a whole number")
            if spec["semantic"] == "unit.pack_size" and value <= 0:
                raise ValidationFailed("Units per pack must be greater than zero")
        if kind == "confirm" and value != "confirm":
            raise ValidationFailed("Use the mapping editor to change the source column")
        if kind == "choice":
            if not isinstance(value, str) or value not in q["options"]:
                raise ValidationFailed("Choose one of the offered columns")
            f.expr = repoint(f.expr, _col_leaves(f.expr)[0], value)
        elif kind != "confirm":
            f.expr = ConstExpr(value=value)
        f.confidence = 1.0
        f.evidence = [*f.evidence, f"Confirmed by {actor} in question review"]
    # Only the mapper's known inclusivity question is resolved by these answers.
    # Free-form VAT rate/registration questions remain open; multiple price fields
    # must all be answered before retiring an unstructured inclusivity question.
    vat_resolved = bool(vat_fields) and vat_fields <= answers.keys()
    remaining = [
        q
        for q in (m.open_questions or [])
        if not (vat_resolved and isinstance(q, str) and VAT_ASSUMED_QUESTION_RE.fullmatch(q))
    ]
    document = doc.model_dump(mode="json", exclude_none=True)
    return document, remaining, stable_hash([m.id, document, answers], 64)


def apply_answers(
    ctx: ServiceContext,
    session: Session,
    m: MappingVersion,
    answers: dict[str, Any],
    digest: str,
    actor: str,
) -> MappingVersion:
    # Serialize edits per round on Postgres, then re-read status after acquiring the lock.
    session.scalar(select(ImportRound).where(ImportRound.id == m.round_id).with_for_update())
    session.refresh(m)
    document, remaining, expected = answer_document(ctx, m, answers, actor)
    if digest != expected:
        raise Conflict("Your answers changed. Preview them again before saving.")
    new = ms.save_mapping_edit(ctx, session, m.id, document, actor, remaining_questions=remaining)
    r = session.get(ImportRound, m.round_id)
    if r:
        r.summary = {**(r.summary or {}), "mapping_version_id": new.id}
        r.status = "blocked" if (new.policy or {}).get("decision") == "block" else "review"
    return new
