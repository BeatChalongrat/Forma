"""Typed business questions. A response changes one declared field, never executable code."""

from __future__ import annotations

from typing import Any

from forma.mapping_dsl.models import CallExpr, Expr, MappingDocument
from forma.schema_registry import TargetSchema

QUESTION_SCHEMA = {
    "type": "array",
    "maxItems": 12,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "target": {"type": "string"},
            "question": {"type": "string", "maxLength": 500},
            "why": {"type": "string", "maxLength": 1000},
            "kind": {"type": "string", "enum": ["boolean", "number", "text", "enum", "confirm"]},
            "action": {
                "type": "string",
                "enum": ["constant", "date_format", "value_map", "confirm", "request_source"],
            },
            "source_value": {"type": "string", "maxLength": 500},
            "options": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        },
        "required": ["target", "question", "why", "kind"],
    },
}


def validate(questions: Any, doc: MappingDocument, schema: TargetSchema) -> list[str]:
    from jsonschema import Draft202012Validator

    errors = [e.message for e in Draft202012Validator(QUESTION_SCHEMA).iter_errors(questions)]
    if errors:
        return errors
    mapped = {f.target for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)]}
    seen = set()
    for q in questions:
        target = q["target"]
        if target in seen or target not in mapped or not schema.has_field(target):
            errors.append(f"Clarification target {target} must identify one mapped field, once")
            continue
        seen.add(target)
        types = set(schema.field(target).json_type)
        expected = {
            "boolean": {"boolean"},
            "number": {"integer", "number"},
            "text": {"string"},
            "enum": {"string"},
            "confirm": types,
        }[q["kind"]]
        if not types & expected:
            errors.append(f"Clarification type for {target} must match the target schema")
        if q["kind"] == "enum" and len(set(q.get("options", []))) < 2:
            errors.append(f"Clarification {target} needs at least two distinct choices")
        if not q["question"].strip() or not q["why"].strip():
            errors.append(f"Clarification {target} must explain the missing business fact")
        if q.get("action") == "date_format" and schema.field(target).fmt != "date":
            errors.append(f"Clarification {target} is not a date field")
        if q.get("action") == "value_map" and not q.get("source_value"):
            errors.append(f"Clarification {target} needs the ambiguous source value")
        if q.get("action", "constant") == "constant":
            fm = next(
                f
                for f in [*doc.fields, *(f for c in doc.collections for f in c.fields)]
                if f.target == target
            )
            if derived_value(fm.expr):
                errors.append(
                    f"Clarification {target}: a constant would replace the entire calculation, "
                    "not its missing operand. Use action=request_source and kind=confirm to request "
                    "source repair, or model an explicit scoped decision."
                )
    return errors


def derived_value(expr: Expr) -> bool:
    """A generic constant answer cannot safely stand for an operand of a calculated field."""
    return isinstance(expr, CallExpr) and (
        expr.op in {"add", "subtract", "multiply", "divide", "vat_adjust"}
        or any(derived_value(arg) for arg in expr.args)
    )
