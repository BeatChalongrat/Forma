"""Turn uncertain fields into bounded, reviewable answers. Answers produce new DSL versions."""

from __future__ import annotations

import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from forma.ai.fit import compatible as _compatible
from forma.ai.heuristics import VAT_ASSUMED_QUESTION_RE
from forma.auth import Principal
from forma.core.errors import Conflict, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import Customer, ImportRound, MappingVersion, ReviewTask
from forma.mapping_dsl.models import CallExpr, ColExpr, ConstExpr, MappingDocument
from forma.schema_registry import FieldSpec, TargetSchema
from forma.services import mapping as ms
from forma.services.context import ServiceContext

MAX_CHOICES = 8


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


def _table_columns(m: MappingVersion, doc: MappingDocument) -> dict[str, list[dict[str, Any]]]:
    """Per DSL source id, the profiled columns of the table it reads (empty when the round is gone)."""
    session = object_session(m)
    r = session.get(ImportRound, m.round_id) if session is not None and m.round_id else None
    if r is None:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for s in doc.sources:
        file = ms._match_file(s, [f for f in r.files if f.status == "profiled"])
        if file is None:
            continue
        tables = list((file.profile or {}).get("tables", []))
        if s.sheet is not None and s.sheet.name:
            want = s.sheet.name.strip().lower()
            tables = [t for t in tables if str(t.get("sheet", "")).strip().lower() == want]
        elif s.sheet is not None and s.sheet.index is not None:
            tables = [t for t in tables if t.get("sheet_index") == s.sheet.index]
        elif s.sheet is not None and s.sheet.match:
            tables = [t for t in tables if re.search(s.sheet.match, str(t.get("sheet", "")), re.IGNORECASE)]
        if s.table.header_row != "auto":
            exact = [t for t in tables if (t.get("table") or {}).get("header_row") == s.table.header_row]
            if exact:
                tables = exact
        if len(tables) == 1:
            out[s.id] = list(tables[0].get("columns", []))
    return out


def _choices(
    f: Any,
    spec: FieldSpec,
    source: str,
    columns: dict[str, list[dict[str, Any]]],
    taken: set[str],
    first: list[str] | None = None,
) -> list[str]:
    """Column names a person can pick instead: one source column read now, others of a fitting type.
    ``first`` are columns offered before the rest (what FORMA's own matcher read, ADR-059)."""
    leaves = _col_leaves(f.expr)
    if len(leaves) != 1:
        return []
    current = leaves[0]
    names = {c["name"] for c in columns.get(source, [])}
    preferred = [n for n in (first or []) if n in names and n != current]
    others = [
        c["name"]
        for c in columns.get(source, [])
        if c["name"] != current
        and c["name"] not in taken
        and c["name"] not in preferred
        and _compatible(spec, c)
    ]
    return [current, *preferred, *others][:MAX_CHOICES] if others or preferred else []


def _matcher_columns(reads: list[str]) -> list[str]:
    """``sheet.column`` as the agreement stores it → column names."""
    return [r.split(".", 1)[1] if "." in r else r for r in reads]


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
    if any(isinstance(q, str) and VAT_ASSUMED_QUESTION_RE.fullmatch(q) for q in m.open_questions or []):
        # the matcher assumed VAT-inclusive and asked; a model's confidence cannot settle that (ADR-060), so
        # the card must offer the yes/no for every VAT flag, or the question could never be answered
        vat_flags |= set(schema.semantic_pointers("price.vat_included"))
    disagree = ((m.policy or {}).get("agreement") or {}).get("disagree") or {}
    items: list[dict[str, Any]] = []
    for f in fields:
        spec = specs.get(f.target)
        if spec is None:
            continue
        # FORMA's matcher and the model read this field from different columns (ADR-059): a person decides,
        # however sure the model was — unless a person already approved this exact reading (confidence 1.0)
        contested = f.target in disagree and spec.risk in ("critical", "high") and (f.confidence or 0) < 1.0
        if f.target not in vat_flags and not contested:
            if not has_checks and f.confidence is not None and f.confidence >= 0.95:
                continue
            if has_checks and f.target not in required:
                continue
            if not has_checks and spec.risk not in ("critical", "high"):
                continue
        kind = "confirm"
        options: list[str] = []
        matcher = _matcher_columns((disagree.get(f.target) or {}).get("matcher") or []) if contested else []
        if "boolean" in spec.json_type and (f.expr.op == "const" or f.target in vat_flags):
            kind = "boolean"  # a VAT flag beside a value that moved by the rate is settled with yes/no, however it is read
        elif f.expr.op == "const" and set(spec.json_type) & {"integer", "number"}:
            kind = "number"
        else:
            src = source_of.get(f.target, doc.record.root_source)
            others = {n for n in taken_by.get(src, set()) if n not in _col_leaves(f.expr)}
            options = _choices(f, spec, src, columns, others, first=matcher)
            if options:
                kind = "choice"  # a decision with alternatives (ADR-058)
            elif spec.risk != "critical" and f.target not in vat_flags and not contested:
                continue  # nothing to decide: the field stays visible in the field review, unasked
        evidence = list(f.evidence)
        if contested:
            evidence.append(
                "FORMA's matcher read " + ", ".join(matcher or ["something else"]) + " for this field"
            )
        items.append(
            {
                "id": f.target,
                "field": spec.to_dict(),
                "kind": kind,
                "options": options,
                "current": f.expr.model_dump(mode="json", exclude_none=True),
                "evidence": evidence,
                "note": f.note,
                "contested": contested,
            }
        )
    for clarification in (m.policy or {}).get("clarifications", []):
        target = clarification["target"]
        if target not in specs or clarification["question"] not in (m.open_questions or []):
            continue
        matched_field = next((f for f in fields if f.target == target), None)
        if matched_field is None:
            continue
        items = [q for q in items if q["id"] != target]
        items.append(
            {
                "id": target,
                "field": specs[target].to_dict(),
                "kind": clarification["kind"],
                "options": clarification.get("options", []),
                "question": clarification["question"],
                "current": matched_field.expr.model_dump(mode="json", exclude_none=True),
                "evidence": list(matched_field.evidence),
                "note": clarification["why"],
                "contested": True,
                "scope": "constant_for_this_file",
            }
        )
    items.sort(
        key=lambda item: (
            not item["contested"],
            item["kind"] == "confirm",
            item["field"]["risk"] != "critical",
        )
    )
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
        elif kind in ("text", "enum"):
            if not isinstance(value, str) or not value.strip() or len(value) > 5000:
                raise ValidationFailed("Enter a nonempty value of at most 5000 characters")
            if kind == "enum" and value not in q["options"]:
                raise ValidationFailed("Choose one of the offered values")
            f.expr = ConstExpr(value=value)
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
    answered_prompts = {
        q["question"] for q in (m.policy or {}).get("clarifications", []) if q["target"] in answers
    }
    remaining = [q for q in remaining if q not in answered_prompts]
    document = doc.model_dump(mode="json", exclude_none=True)
    return document, remaining, stable_hash([m.id, document, answers], 64)


GROUPABLE = ("choice", "boolean")


def groups(ctx: ServiceContext, session: Session, p: Principal) -> list[dict[str, Any]]:
    """The same question asked by several files at once (ADR-059): a choice over the same columns for the
    same field, or the same yes/no, across the open review tasks a person may see. One answer settles them all;
    the groups go to the top of the queue because they teach the most per click."""
    q = select(ReviewTask).where(
        ReviewTask.status == "open",
        ReviewTask.kind.in_(("mapping", "drift")),
        ReviewTask.mapping_version_id.is_not(None),
    )
    if p.customers is not None:
        q = q.where(ReviewTask.customer_id.in_(sorted(p.customers)))
    tasks = list(session.scalars(q))
    if not tasks:
        return []
    mappings = {
        m.id: m
        for m in session.scalars(
            select(MappingVersion).where(
                MappingVersion.id.in_([t.mapping_version_id for t in tasks if t.mapping_version_id]),
                MappingVersion.status == "proposed",
            )
        )
    }
    codes = {
        c.id: c.code
        for c in session.scalars(select(Customer).where(Customer.id.in_({t.customer_id for t in tasks})))
    }
    buckets: dict[str, dict[str, Any]] = {}
    for t in tasks:
        m = mappings.get(t.mapping_version_id or "")
        if m is None:
            continue
        for item in questions(ctx, m)["questions"]:
            if item["kind"] not in GROUPABLE:
                continue
            same = sorted(item["options"]) if item["kind"] == "choice" else item["current"]
            # Identical wording does not make VAT, units or prices a shared business fact.
            # Only batch within the same customer and exact target contract/read operation.
            schema = ctx.registry.get_ref(m.target_schema)
            from forma.ai.evidence import reading_signature

            document = MappingDocument.model_validate(m.document)
            key = stable_hash(
                [
                    m.customer_id,
                    m.target_schema,
                    stable_hash(schema.document),
                    item["kind"],
                    item["id"],
                    same,
                    reading_signature(document, item["id"]),
                ],
                16,
            )
            b = buckets.setdefault(
                key,
                {
                    "key": key,
                    "kind": item["kind"],
                    "field": item["field"],
                    "options": item["options"],
                    "current": item["current"],
                    "members": [],
                },
            )
            b["members"].append(
                {
                    "task_id": t.id,
                    "mapping_id": m.id,
                    "round_id": t.round_id,
                    "customer_code": codes.get(t.customer_id, "?"),
                }
            )
    out = [b for b in buckets.values() if len({x["mapping_id"] for x in b["members"]}) >= 2]
    out.sort(key=lambda b: (-len(b["members"]), b["field"]["risk"] != "critical", b["field"]["pointer"]))
    return out


def answer_group(
    ctx: ServiceContext, session: Session, p: Principal, key: str, answer: Any, actor: str
) -> list[str]:
    """Apply one answer to every member of a question group; returns the new mapping version ids."""
    group = next((g for g in groups(ctx, session, p) if g["key"] == key), None)
    if group is None:
        raise Conflict("This group no longer exists; the questions changed. Reload.")
    out: list[str] = []
    for member in group["members"]:
        m = session.get(MappingVersion, member["mapping_id"])
        if m is None or m.status != "proposed":
            continue
        answers = {group["field"]["pointer"]: answer}
        _, _, digest = answer_document(ctx, m, answers, actor)
        out.append(apply_answers(ctx, session, m, answers, digest, actor).id)
    return out


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
