"""Evidence-bound editorial sections and optimistic, section-level revisions."""

from __future__ import annotations

import copy
import re
from typing import Any

from forma.core.errors import ValidationFailed
from forma.reporting.registry import digest

TEXT = {"type": "string", "minLength": 1, "maxLength": 220}
SECTION = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_-]{0,39}$"},
        "layout": {"enum": ["summary", "findings", "actions", "comparison"]},
        "title": TEXT,
        "points": {"type": "array", "minItems": 1, "maxItems": 4, "items": TEXT},
        "evidence_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "uniqueItems": True,
            "items": {"type": "integer", "minimum": 1},
        },
    },
    "required": ["id", "layout", "title", "points", "evidence_ids"],
    "additionalProperties": False,
}
PROPERTIES = {
    "brief": {
        "type": "object",
        "properties": {"title": TEXT, "audience": TEXT, "purpose": TEXT},
        "required": ["title", "audience", "purpose"],
        "additionalProperties": False,
    },
    "sections": {"type": "array", "minItems": 1, "maxItems": 12, "items": SECTION},
    "template_attachment_id": {"type": "string", "maxLength": 40},
    "base_version": {"type": "integer", "minimum": 1},
    "section_patches": {"type": "array", "minItems": 1, "maxItems": 12, "items": SECTION},
}
TOKEN = re.compile(r"\{\{e(\d+)\.r(\d+)\.([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def resolve(value: str, sources: list[dict[str, Any]], allowed: list[int]) -> str:
    """Model chooses a reference. Only the server inserts its value; no executable expressions."""
    rest = TOKEN.sub("", value)
    if re.search(r"\d|[{}]", rest):
        raise ValidationFailed(
            "Use {{e<EVIDENCE_ID>.r<ZERO_BASED_ROW>.<COLUMN>}} for every factual number; do not type numbers"
        )

    def replace(match: re.Match[str]) -> str:
        eid, row, key = int(match[1]), int(match[2]), match[3]
        source = next((s for s in sources if s.get("evidence_id") == eid), None)
        if eid not in allowed or source is None or row >= len(source["report"]["rows"]):
            raise ValidationFailed("Narrative refers to missing or uncited evidence")
        if key not in {c["key"] for c in source["report"]["columns"]}:
            raise ValidationFailed("Narrative refers to a column outside the source contract")
        item = source["report"]["rows"][row].get(key)
        if item is None or isinstance(item, (dict, list)):
            raise ValidationFailed("A narrative fact is unknown; ask for clarification instead")
        return str(item)

    result = TOKEN.sub(replace, value)
    if len(result) > 320:
        raise ValidationFailed("Shorten the section text to keep the report readable")
    return result


def compose(args: dict[str, Any], spec: dict[str, Any]) -> None:
    sources = spec["sources"]
    available = [s["evidence_id"] for s in sources]
    brief = args.get("brief")
    if brief:
        spec["brief"] = {k: resolve(v, sources, available) for k, v in brief.items()}
        spec["title"] = spec["brief"]["title"]
    sections = []
    for section in args.get("sections", []):
        if not set(section["evidence_ids"]) <= set(available):
            raise ValidationFailed("Every section must cite current, complete report evidence")
        sections.append(
            {
                **copy.deepcopy(section),
                "title": resolve(section["title"], sources, section["evidence_ids"]),
                "points": [resolve(v, sources, section["evidence_ids"]) for v in section["points"]],
                "authored": copy.deepcopy(section),
                "interpretation": "recommendation"
                if section["layout"] == "actions"
                else "analysis_requires_review",
            }
        )
    if len({s["id"] for s in sections}) != len(sections):
        raise ValidationFailed("Report section IDs must be unique")
    spec["sections"] = sections


def revise(previous: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(previous)
    ids = {s["id"] for s in previous.get("sections", [])}
    if (
        not patches
        or len({p["id"] for p in patches}) != len(patches)
        or any(p["id"] not in ids for p in patches)
    ):
        raise ValidationFailed(
            "Patch distinct existing section IDs from inspect_document; no other sections will be replaced"
        )
    temp = {"sources": result["sources"]}
    compose({"sections": patches}, temp)
    replacements = {s["id"]: s for s in temp["sections"]}
    changes = []
    for i, section in enumerate(result["sections"]):
        if section["id"] in replacements:
            replacement = replacements[section["id"]]
            changes.append(
                {
                    "section_id": section["id"],
                    "before": section,
                    "after": replacement,
                    "before_sha256": digest(section),
                    "after_sha256": digest(replacement),
                }
            )
            result["sections"][i] = replacement
    result["revision"] = {
        "parent_snapshot_id": previous["snapshot_id"],
        "changes": changes,
        "data_changed": False,
        "scope": "logical sections; page numbers may change when content reflows",
    }
    result.pop("snapshot_id", None)
    return result
