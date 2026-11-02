"""Could this source column hold that target field? A type-level fit, shared by the review questions
(which columns to offer as a choice, ADR-058) and the agent's grounding step (which columns to look at before
the model maps, ADR-059). Fields are known by their spec, never by name (rule 1)."""

from __future__ import annotations

from typing import Any

from forma.schema_registry import FieldSpec

_DATE_SEMANTICS = {"price.effective_from", "price.effective_to", "promo.start_date", "promo.end_date"}


def compatible(spec: FieldSpec, col: dict[str, Any]) -> bool:
    """``col`` is a profiled column as a dict (``ColumnProfile.to_dict()``)."""
    t = str(col.get("inferred_type") or "")
    if t == "empty":
        return False
    hints = col.get("hints") or []
    json_type = set(spec.json_type)
    if json_type & {"number", "integer"}:
        return t in ("int", "float") or "price_like" in hints
    if spec.fmt == "date" or (spec.semantic or "") in _DATE_SEMANTICS:
        return t == "date" or "thai_date" in hints or t == "string"
    if "boolean" in json_type:
        return t in ("bool", "string")
    return t != "int" or "sku_like" not in hints  # a stock count is not a name or a code
