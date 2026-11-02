"""Typed business questions. A response changes one declared field, never executable code."""

from __future__ import annotations

from typing import Any

from forma.mapping_dsl.models import MappingDocument
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
            "kind": {"type": "string", "enum": ["boolean", "number", "text", "enum"]},
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
        }[q["kind"]]
        if not types & expected:
            errors.append(f"Clarification type for {target} must match the target schema")
        if q["kind"] == "enum" and len(set(q.get("options", []))) < 2:
            errors.append(f"Clarification {target} needs at least two distinct choices")
        if not q["question"].strip() or not q["why"].strip():
            errors.append(f"Clarification {target} must explain the missing business fact")
    return errors
