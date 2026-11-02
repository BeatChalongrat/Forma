"""Comparable readings, not merely comparable column names (ADR-060)."""

from __future__ import annotations

from typing import Any

from forma.mapping_dsl.models import MappingDocument


def reading_signature(doc: MappingDocument, target: str) -> dict[str, Any]:
    """Preserve operations, constants, file/range, joins and filters; ignore source aliases.

    Agreement is a consistency signal, not an independent second opinion: the model has
    already seen the deterministic draft. Even identical signatures need execution checks.
    """
    sources = {
        s.id: s.model_dump(mode="json", exclude={"id", "description", "role"}, exclude_none=True)
        for s in doc.sources
    }

    def canonical(value: Any, default: str) -> Any:
        if isinstance(value, list):
            return [canonical(v, default) for v in value]
        if not isinstance(value, dict):
            return value
        out = {k: canonical(v, default) for k, v in value.items()}
        if value.get("op") == "col":
            out["source"] = sources.get(value.get("source") or default, {"unresolved": True})
        return out

    for fm in doc.fields:
        if fm.target == target:
            return {
                "expr": canonical(fm.expr.model_dump(mode="json", exclude_none=True), doc.record.root_source),
                "source": sources.get(doc.record.root_source),
                "dedupe": doc.record.dedupe.model_dump(mode="json"),
                "filters": canonical(
                    [x.model_dump(mode="json") for x in doc.record.filters], doc.record.root_source
                ),
                "joins": [
                    {
                        **j.model_dump(mode="json", exclude={"left", "right"}),
                        "left": sources.get(j.left),
                        "right": sources.get(j.right),
                    }
                    for j in doc.joins
                ],
            }
    for cm in doc.collections:
        for fm in cm.fields:
            if fm.target == target:
                return {
                    "expr": canonical(fm.expr.model_dump(mode="json", exclude_none=True), cm.source),
                    "source": sources.get(cm.source),
                    "root": sources.get(doc.record.root_source),
                    "record": canonical(
                        doc.record.model_dump(mode="json", exclude={"root_source"}), doc.record.root_source
                    ),
                    "joins": [
                        {
                            **j.model_dump(mode="json", exclude={"left", "right"}),
                            "left": sources.get(j.left),
                            "right": sources.get(j.right),
                        }
                        for j in doc.joins
                    ],
                    "collection": canonical(
                        cm.model_dump(mode="json", exclude={"fields", "source"}), cm.source
                    ),
                }
    return {}
