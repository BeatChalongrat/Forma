"""Validated decision actions. Labels are presentation; answers modify a bounded DSL operation."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from forma.core.errors import ValidationFailed
from forma.core.hashing import stable_hash
from forma.engine.transform import ROW_COL, _get_pointer
from forma.mapping_dsl.models import CallExpr, ColExpr, ConstExpr, Expr, MappingDocument, Op
from forma.schema_registry import PROVENANCE_KEY, TargetSchema

DATE_ORDERS = {"DMY": "%d/%m/%Y", "MDY": "%m/%d/%Y", "YMD": "%Y/%m/%d"}


def field_mapping(doc: MappingDocument, pointer: str) -> Any:
    return next(
        (f for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)] if f.target == pointer),
        None,
    )


def column(expr: Any) -> ColExpr | None:
    if isinstance(expr, ColExpr):
        return expr
    if isinstance(expr, CallExpr):
        leaves = [col for arg in expr.args if (col := column(arg)) is not None]
        unique = {col.name: col for col in leaves}
        return next(iter(unique.values())) if len(unique) == 1 else None
    return None


def normalize(question: dict[str, Any], schema: TargetSchema) -> dict[str, Any]:
    q = dict(question)
    spec = schema.field(q["target"])
    action = q.get("action", "constant")
    if "action" not in q and spec.fmt == "date" and q["kind"] == "enum":
        action = "date_format"
    q["action"] = action
    if action == "date_format":
        q.update(
            kind="enum",
            options=list(DATE_ORDERS),
            option_labels={
                "DMY": "วัน / เดือน / ปี · 03/04 = 3 เมษายน",
                "MDY": "เดือน / วัน / ปี · 03/04 = 4 มีนาคม",
                "YMD": "ปี / เดือน / วัน · 2026/04/03 = 3 เมษายน",
            },
            scope="source_column",
            allow_custom=False,
        )
    elif action == "value_map":
        q.update(
            kind="number" if {"number", "integer"} & set(spec.json_type) else "text",
            scope="source_value",
            allow_custom=True,
        )
    elif action == "confirm":
        q.update(kind="confirm", scope="mapping")
    elif action == "request_source":
        q.update(kind="confirm", scope="source_file", allow_custom=False, options=[])
    else:
        q["scope"] = "constant_for_this_file"
        # A model's suggested answers cannot narrow the actual target contract.
        q["allow_custom"] = spec.enum is None and q["kind"] in ("text", "enum")
        if spec.semantic == "currency" and not spec.enum:
            q.update(kind="text", allow_custom=True)
    return q


def validate_value(value: Any, spec: dict[str, Any]) -> None:
    types = spec.get("json_type", [])
    contract: dict[str, Any] = {"type": types} if types else {}
    if spec.get("enum"):
        contract["enum"] = spec["enum"]
    if spec.get("format"):
        contract["format"] = spec["format"]
    errors = list(Draft202012Validator(contract, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise ValidationFailed(
            "Answer does not satisfy the target field", details={"errors": [e.message for e in errors]}
        )


def apply(doc: MappingDocument, q: dict[str, Any], value: Any) -> None:
    action = q["action"]
    if action == "request_source":
        raise ValidationFailed(
            "Provide or repair the source file, then run mapping again; this request cannot be answered by confirming a value"
        )
    target = q.get("target") or q["field"]["pointer"]
    fm = field_mapping(doc, target)
    if action == "select_rows":
        if value not in q["options"]:
            raise ValidationFailed("Choose one of the source rows shown")
        selected = q["row_options"][value]
        excluded = [n for opt, n in q["row_options"].items() if opt != value and n != selected]
        container = (
            doc.source(q["source"]).table
            if q.get("source")
            else next(c for c in doc.collections if c.target == q["collection"])
        )
        predicates: list[Expr] = [
            CallExpr(op=Op.NEQ, args=[ColExpr(name=ROW_COL), ConstExpr(value=n)]) for n in excluded
        ]
        if container.filter is not None:
            predicates.insert(0, container.filter)
        container.filter = predicates[0] if len(predicates) == 1 else CallExpr(op=Op.AND, args=predicates)
        return
    if fm is None:
        raise ValidationFailed("The field is no longer mapped")
    if action == "date_format":
        if value not in DATE_ORDERS:
            raise ValidationFailed("Choose DMY, MDY or YMD")
        col = column(fm.expr)
        if col is None:
            raise ValidationFailed("A date format needs one source column; use the mapping editor")
        # Preserve source values and parse every row, instead of assigning one fixed date.
        fm.expr = CallExpr(
            op=Op.PARSE_DATE_TH,
            args=[col],
            params={"dayfirst": value != "MDY", "formats": [DATE_ORDERS[value]]},
        )
    elif action == "value_map":
        validate_value(value, q["field"])
        col = column(fm.expr)
        source_value = q.get("source_value")
        if col is None or source_value is None:
            raise ValidationFailed("A value decision needs one source column and a source value")
        token_predicates: list[Expr] = []

        def matching_predicates(expr: Expr) -> None:
            if not isinstance(expr, CallExpr):
                return
            if expr.op == Op.CONDITIONAL and expr.args:
                condition = expr.args[0]
                if isinstance(condition, CallExpr) and (
                    (
                        condition.op == Op.CONTAINS
                        and condition.params.get("literal") is True
                        and condition.params.get("pattern") == str(source_value)
                    )
                    or (
                        condition.op == Op.EQ
                        and any(
                            isinstance(a, ConstExpr) and a.value == str(source_value) for a in condition.args
                        )
                    )
                ):
                    token_predicates.append(condition)
            for arg in expr.args:
                matching_predicates(arg)

        # A currency symbol can be embedded in a price rather than occupying the entire cell.
        # Reuse the candidate's explicit literal predicate; never invent a broader match.
        matching_predicates(fm.expr)
        unique = {stable_hash(p.model_dump(mode="json")): p for p in token_predicates}
        if len(unique) > 1:
            raise ValidationFailed(
                "This token has multiple source conditions; choose the rule in the mapping editor"
            )
        condition = (
            next(iter(unique.values()))
            if unique
            else CallExpr(
                op=Op.EQ, args=[CallExpr(op=Op.TRIM, args=[col]), ConstExpr(value=str(source_value))]
            )
        )
        fm.expr = CallExpr(
            op=Op.CONDITIONAL,
            args=[
                condition,
                ConstExpr(value=value),
                fm.expr,
            ],
        )
    elif action == "constant":
        from forma.ai.clarifications import derived_value

        if derived_value(fm.expr):
            raise ValidationFailed(
                "A constant answer cannot replace a calculated field; repair its source inputs or use the mapping editor"
            )
        validate_value(value, q["field"])
        fm.expr = ConstExpr(value=value)
    elif action != "confirm":
        raise ValidationFailed("Unsupported decision action")
    fm.confidence = 1.0


def conflicting_items(
    doc: MappingDocument, schema: TargetSchema, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Source-row choices are scoped to this immutable delivery and can never become format memory."""
    questions = []
    for cm in doc.collections:
        keys = schema.collection_identity_keys(cm.target)
        if not keys:
            continue
        for record in records:
            items = _get_pointer(record, cm.target) or []
            lineage = record.get(PROVENANCE_KEY, {}).get("lineage", {}).get("items", {}).get(cm.target, [])
            if len(items) != len(lineage):
                continue
            groups: dict[str, list[int]] = {}
            for i, item in enumerate(items):
                key = stable_hash([_get_pointer(item, k) for k in keys])
                groups.setdefault(key, []).append(i)
            for indices in groups.values():
                if len(indices) < 2 or len({stable_hash(items[i]) for i in indices}) < 2:
                    continue
                fm = next(
                    (
                        f
                        for f in cm.fields
                        if len(
                            {
                                stable_hash(_get_pointer(items[i], f.target[len(cm.target) + 2 :]))
                                for i in indices
                            }
                        )
                        > 1
                    ),
                    None,
                )
                if fm is None:
                    continue
                identity = {p: _get_pointer(record, p) for p in schema.identity_keys}
                identity_text = " · ".join(str(v) for v in identity.values())
                qid = "row:" + stable_hash([cm.target, identity, [lineage[i] for i in indices]], 24)
                options = {str(lineage[i]): lineage[i] for i in indices}
                questions.append(
                    {
                        "id": qid,
                        "target": fm.target,
                        "kind": "enum",
                        "action": "select_rows",
                        "question": f"ข้อมูลของรายการ {identity_text} มีหลายค่าที่ขัดแย้งกัน รายการไหนถูกต้อง?",
                        "why": "Choose the authoritative source item; only conflicting alternatives in this delivery are excluded.",
                        "options": list(options),
                        "row_options": options,
                        "collection": cm.target,
                        "option_labels": {
                            str(lineage[i]): f"รายการต้นทางลำดับ {lineage[i] + 1} · "
                            + ", ".join(f"{k}: {v}" for k, v in items[i].items())
                            for i in indices
                        },
                        "scope": "record_in_this_delivery",
                        "identity": identity,
                        "allow_custom": False,
                    }
                )
    return questions


def conflicting_records(findings: list[Any]) -> list[dict[str, Any]]:
    """Root/lookup conflicts become immutable source-row decisions, never SKU-to-price value maps."""
    questions = []
    for finding in findings:
        for choice in finding.details.get("source_choices", []):
            rows = choice["rows"]
            if len(rows) < 2 or any(not isinstance(row["row"], int) for row in rows):
                continue
            label = " · ".join(str(v) for v in choice["identity"].values())
            options = {str(row["row"]): row["row"] for row in rows}
            questions.append(
                {
                    "id": "row:" + stable_hash(choice, 24),
                    "target": choice["target"],
                    "kind": "enum",
                    "action": "select_rows",
                    "source": choice["source"],
                    "affected_fields": sorted({p for row in rows for p in row["values"]}),
                    "identity": choice["identity"],
                    "question": f"รายการ {label} มีข้อมูลขัดแย้งกัน ให้ใช้รายการต้นทางใด?",
                    "why": "Choose one source row for this record; other records are unchanged.",
                    "options": list(options),
                    "row_options": options,
                    "option_labels": {
                        str(row["row"]): " · ".join(f"{k.lstrip('/')}: {v}" for k, v in row["values"].items())
                        for row in rows
                    },
                    "scope": "record_in_this_delivery",
                    "allow_custom": False,
                }
            )
    return questions
