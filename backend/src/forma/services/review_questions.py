"""Turn uncertain fields into bounded, reviewable answers. Answers produce new DSL versions."""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.core.errors import Conflict, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import ImportRound, MappingVersion
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema
from forma.services import mapping as ms
from forma.services.context import ServiceContext


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


def questions(ctx: ServiceContext, m: MappingVersion) -> dict[str, Any]:
    schema = ctx.registry.get_ref(m.target_schema)
    doc = MappingDocument.model_validate(m.document)
    specs = {f.pointer: f for f in schema.leaf_fields()}
    fields = [*doc.fields, *(f for c in doc.collections for f in c.fields)]
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
        if f.expr.op == "const" and "boolean" in spec.json_type:
            kind = "boolean"
        elif f.expr.op == "const" and set(spec.json_type) & {"integer", "number"}:
            kind = "number"
        items.append(
            {
                "id": f.target,
                "field": spec.to_dict(),
                "kind": kind,
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
        if kind != "confirm":
            from forma.mapping_dsl.models import ConstExpr

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
        if not (
            vat_resolved
            and isinstance(q, str)
            and re.fullmatch(
                r"Price column '.+': no VAT marker in headers; assumed VAT-inclusive "
                r"\(Thai retail convention\) — confirm",
                q,
            )
        )
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
