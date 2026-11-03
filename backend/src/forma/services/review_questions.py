"""Turn uncertain fields into bounded, reviewable answers. Answers produce new DSL versions."""

from __future__ import annotations

import datetime as dt
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
from forma.services import decision_actions
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


def _table_columns(
    m: MappingVersion, doc: MappingDocument, session: Session | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Per DSL source id, the profiled columns of the table it reads (empty when the round is gone)."""
    session = session if session is not None else object_session(m)
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


def unit_questions(
    doc: MappingDocument, schema: TargetSchema, columns: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    fields = [*doc.fields, *(f for c in doc.collections for f in c.fields)]
    specs = {f.pointer: f for f in schema.leaf_fields()}
    source_of = {f.target: doc.record.root_source for f in doc.fields}
    source_of.update({f.target: c.source for c in doc.collections for f in c.fields})
    items: list[dict[str, Any]] = []

    def defined_token(expr: Any, token: str) -> bool:
        if not isinstance(expr, CallExpr):
            return False
        if expr.op == "map_values" and token in (expr.params.get("mapping") or {}):
            return True
        if expr.op == "conditional" and len(expr.args) == 3:
            condition = expr.args[0]
            if (
                isinstance(condition, CallExpr)
                and condition.op == "eq"
                and any(isinstance(a, ConstExpr) and str(a.value) == token for a in condition.args)
            ):
                return True
        return any(defined_token(a, token) for a in expr.args)

    # Unknown unit tokens are evidence of missing business meaning, not a licence to guess.
    from forma.engine.thai import UNIT_SYNONYMS, unit_normalize_py

    for f in fields:
        spec = specs.get(f.target)
        if spec is None or spec.semantic != "unit.sell" or "unit_normalize" not in str(f.expr.model_dump()):
            continue
        col = decision_actions.column(f.expr)
        source = (col.source if col else None) or source_of.get(f.target, doc.record.root_source)
        profile = next((c for c in columns.get(source, []) if col and c["name"] == col.name), None)
        if not profile:
            continue
        unknown = [
            str(v[0])
            for v in profile.get("top_values", [])
            if v
            and str(v[0]).strip()
            and unit_normalize_py(v[0]) not in set(UNIT_SYNONYMS.values())
            and not defined_token(f.expr, str(v[0]))
        ]
        for token in unknown:
            q = {
                "id": f.target + ":value:" + stable_hash(token, 12),
                "target": f.target,
                "field": spec.to_dict(),
                "kind": "text",
                "action": "value_map",
                "source_value": token,
                "options": [],
                "question": f"หน่วย {token} หมายถึงอะไร?",
                "scope": "source_value",
                "allow_custom": True,
                "current": f.expr.model_dump(mode="json"),
                "evidence": list(f.evidence),
                "note": "เปลี่ยนเฉพาะรหัสนี้ โดยเก็บหน่วยอื่นตามเดิม",
                "why": "An unknown source unit needs an explicit definition",
                "contested": True,
            }
            if not any(i.get("source_value") == token and i.get("target") == f.target for i in items):
                items.append(q)
    return items


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
        if "boolean" in spec.json_type and f.expr.op == "const":
            kind = "boolean"  # a VAT flag beside a value that moved by the rate is settled with yes/no, however it is read
        elif f.expr.op == "const" and set(spec.json_type) & {"integer", "number"}:
            kind = "number"
        elif "boolean" in spec.json_type and f.target in vat_flags:
            kind = "confirm"  # A row-specific flag must never become one constant for every row.
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
        clarification = decision_actions.normalize(clarification, schema)
        target = clarification["target"]
        if target not in specs or clarification["question"] not in (m.open_questions or []):
            continue
        matched_field = next((f for f in fields if f.target == target), None)
        if matched_field is None:
            continue
        if (
            specs[target].semantic == "currency"
            and clarification.get("action") == "value_map"
            and isinstance(matched_field.expr, ConstExpr)
        ):
            # A symbol mentioned in prose is not a source-token expression. The current
            # candidate declares one currency for the delivery; ask explicitly at that scope.
            clarification = decision_actions.normalize({**clarification, "action": "constant"}, schema)
        items = [q for q in items if q["id"] != target]
        items.append(
            {
                "id": target + ":value:" + stable_hash(str(clarification.get("source_value")), 12)
                if clarification.get("action") == "value_map"
                else target,
                "field": specs[target].to_dict(),
                "kind": clarification["kind"],
                "options": clarification.get("options", []),
                "question": clarification["question"],
                "current": matched_field.expr.model_dump(mode="json", exclude_none=True),
                "evidence": list(matched_field.evidence),
                "note": clarification["why"],
                "contested": True,
                **{
                    k: clarification[k]
                    for k in ("action", "scope", "allow_custom", "source_value", "option_labels")
                    if k in clarification
                },
            }
        )
    decisions = (m.policy or {}).get("review_decisions")
    if decisions is None:  # Existing drafts predate the full-file decision catalogue.
        decisions = decision_actions.conflicting_items(doc, schema, (m.policy or {}).get("sample", []))
    for q in decisions:
        items.append(
            {
                **q,
                "field": specs[q["target"]].to_dict(),
                "current": {},
                "evidence": [q["why"]],
                "note": q["why"],
                "contested": True,
            }
        )
    for unit_question in unit_questions(doc, schema, columns):
        if not any(
            q.get("source_value") == unit_question["source_value"]
            and (q.get("target") or q["field"]["pointer"]) == unit_question["target"]
            for q in items
        ):
            items.append(unit_question)
    # The model and deterministic checks can independently ask the same business question.
    # Merge by meaning, not wording, and retire every equivalent prompt with one answer.
    unique: list[dict[str, Any]] = []
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if item.get("action") == "value_map":
            key = (item["field"]["pointer"], str(item.get("source_value")))
            existing = values.get(key)
            if existing is not None:
                existing.setdefault("related_questions", []).append(item.get("question"))
                continue
            values[key] = item
        unique.append(item)
    items = unique
    aliases = {p for q in items for p in q.get("related_questions", []) if p != q.get("question")}
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
        "open_questions": [p for p in m.open_questions or [] if p not in aliases],
        "reasons": (m.policy or {}).get("reasons", []),
        "sample": (m.policy or {}).get("sample", [])[:3],
    }


def answer_document(
    ctx: ServiceContext,
    m: MappingVersion,
    answers: dict[str, Any],
    actor: str,
    *,
    remember: list[str] | None = None,
    acknowledged: list[str] | None = None,
) -> tuple[dict[str, Any], list[Any], str]:
    if m.status != "proposed":
        raise Conflict("This version has already been reviewed. Reload the questions.")
    if (not answers and not acknowledged) or len(answers) > 100:
        raise ValidationFailed("Choose at least one answer")
    structured_prompts = {q["question"] for q in (m.policy or {}).get("clarifications", [])}
    structured_prompts.update(q["question"] for q in (m.policy or {}).get("review_decisions", []))
    if set(acknowledged or []) - set(m.open_questions or []) or structured_prompts & set(acknowledged or []):
        raise ValidationFailed(
            "Use the specific answer controls for structured decisions; only existing additional notes can be acknowledged"
        )
    catalogue = {q["id"]: q for q in questions(ctx, m)["questions"]}
    if answers.keys() - catalogue.keys():
        raise ValidationFailed("An answer refers to a field that is not awaiting a decision")
    if set(remember or []) - answers.keys() or any(
        catalogue[k].get("action") == "select_rows" for k in remember or []
    ):
        raise ValidationFailed(
            "Only answered field rules may be remembered; source-row exceptions are delivery-specific"
        )
    doc = MappingDocument.model_validate(m.document)
    vat_fields = {
        pointer for pointer, q in catalogue.items() if q["field"]["semantic"] == "price.vat_included"
    }
    for key, value in answers.items():
        q = catalogue[key]
        f = decision_actions.field_mapping(doc, q["field"]["pointer"])
        if f is None:
            raise ValidationFailed("This field is no longer mapped")
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
        if q.get("action"):
            if q["action"] == "constant" and _col_leaves(f.expr):
                # A model must ask about a code or a parsing rule when source values vary.
                source = next((c.source for c in doc.collections if f in c.fields), doc.record.root_source)
                cols = _table_columns(m, doc).get(source, [])
                selected = [c for c in cols if c["name"] in _col_leaves(f.expr)]
                if not selected or any(c.get("unique_count", 0) > 1 for c in selected):
                    raise ValidationFailed(
                        "This field varies by row; confirm its reading or define a source-value rule instead of replacing every row"
                    )
            if kind in ("text", "enum") and (
                not isinstance(value, str) or not value.strip() or len(value) > 5000
            ):
                raise ValidationFailed("Enter a nonempty value of at most 5000 characters")
            if kind == "enum" and value not in q.get("options", []) and not q.get("allow_custom"):
                raise ValidationFailed("Choose one of the offered values")
            decision_actions.apply(doc, q, value)
        elif kind == "choice":
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
    remaining = [
        prompt
        for prompt in remaining
        if prompt
        not in {
            p
            for k in answers
            for p in [catalogue[k].get("question"), *catalogue[k].get("related_questions", [])]
        }
    ]
    remaining = [prompt for prompt in remaining if prompt not in (acknowledged or [])]
    document = doc.model_dump(mode="json", exclude_none=True)
    return (
        document,
        remaining,
        stable_hash(
            [m.id, document, answers, sorted(set(remember or [])), sorted(set(acknowledged or []))], 64
        ),
    )


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
    *,
    remember: list[str] | None = None,
    acknowledged: list[str] | None = None,
) -> MappingVersion:
    # Serialize edits per round on Postgres, then re-read status after acquiring the lock.
    session.scalar(select(ImportRound).where(ImportRound.id == m.round_id).with_for_update())
    session.refresh(m)
    document, remaining, expected = answer_document(
        ctx, m, answers, actor, remember=remember, acknowledged=acknowledged
    )
    if digest != expected:
        raise Conflict("Your answers changed. Preview them again before saving.")
    from forma.services.review_evidence import validate_answer_effects

    catalogue = {q["id"]: q for q in questions(ctx, m)["questions"]}
    effect_check = validate_answer_effects(ctx, session, m, document, [catalogue[k] for k in answers])
    new = ms.save_mapping_edit(ctx, session, m.id, document, actor, remaining_questions=remaining)
    history = list((m.policy or {}).get("confirmed_decisions", []))
    for key, value in answers.items():
        q = catalogue[key]
        target = q["field"]["pointer"]
        before = decision_actions.field_mapping(MappingDocument.model_validate(m.document), target)
        after = decision_actions.field_mapping(MappingDocument.model_validate(document), target)
        history.append(
            {
                "question_id": key,
                "mapping_id": new.id,
                "answered_at": dt.datetime.now(dt.UTC).isoformat(),
                "target": target,
                "action": q.get(
                    "action",
                    "choice"
                    if q["kind"] == "choice"
                    else "confirm"
                    if q["kind"] == "confirm"
                    else "constant",
                ),
                "scope": q.get("scope", "constant_for_this_file"),
                "source_value": q.get("source_value"),
                "answer": value,
                "question": q.get("question"),
                "before": before.expr.model_dump(mode="json") if before else None,
                "after": after.expr.model_dump(mode="json") if after else None,
                "actor": actor,
                "origin_round": m.round_id,
            }
        )
        history[-1]["remember"] = key in (remember or [])
    new.policy = {
        **(new.policy or {}),
        "confirmed_decisions": history,
        "answer_effect_check": {
            **effect_check,
            "checked_at": dt.datetime.now(dt.UTC).isoformat(),
            "before_mapping_id": m.id,
            "mapping_id": new.id,
            "actor": actor,
            "digest": expected,
        },
        "acknowledged_questions": [
            *(m.policy or {}).get("acknowledged_questions", []),
            *[{"question": q, "actor": actor, "origin_round": m.round_id} for q in acknowledged or []],
        ],
    }
    from forma.services.rounds import audit

    audit(
        session,
        "review.answers",
        "mapping",
        new.id,
        actor=actor,
        round_id=m.round_id,
        parent_id=m.id,
        answers=len(answers),
        acknowledged=len(acknowledged or []),
        effect_check=new.policy["answer_effect_check"],
    )
    r = session.get(ImportRound, m.round_id)
    if r:
        r.summary = {**(r.summary or {}), "mapping_version_id": new.id}
        r.status = "blocked" if (new.policy or {}).get("decision") == "block" else "review"
    return new
