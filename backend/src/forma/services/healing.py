"""Self-healing review (ADR-021): when a customer's format drifts and FORMA proposes a new mapping, show only
what changed against the approved mapping — field by field — and check each change on the data, not on
the column name. ``NetPrice`` replacing ``SellingPrice`` is not "the same field" because the header looks
alike: the values of the same SKUs are compared with last round's published data. Equal values → safe;
a steady 7 % gap → probably VAT included/excluded; a steady other ratio → scaled (unit, pack, currency);
anything else → the meaning changed. The policy turns risky checks into review reasons (rule 6).
"""

from __future__ import annotations

import statistics
from typing import Any

from forma.engine.thai import VAT_RATE
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema
from forma.services.sandbox import _identity, _values

MIN_PAIRS = 3  # SKUs present in both rounds needed before the data is allowed to speak
SAME_SHARE = 0.9  # this share of equal values = same meaning
STEADY_SHARE = 0.8  # this share of ratios within 1 % of the median = one consistent factor
RISKY = ("vat_added", "vat_removed", "scaled", "different")
STEADY = ("vat_added", "vat_removed", "scaled")  # one factor on every SKU: the file changed, not the data


# --------------------------------------------------------------------------------------------
# what changed in the mapping, per target field
# --------------------------------------------------------------------------------------------
def _fields(doc: MappingDocument) -> dict[str, dict[str, Any]]:
    root = doc.record.root_source
    out: dict[str, dict[str, Any]] = {}
    for f in doc.fields:
        out[f.target] = {
            "expr": f.expr.model_dump(mode="json", exclude_none=True),
            "source": root,
            "confidence": f.confidence,
            "evidence": f.evidence,
        }
    for c in doc.collections:
        for f in c.fields:
            out[f.target] = {
                "expr": f.expr.model_dump(mode="json", exclude_none=True),
                "source": c.source,
                "collection": c.target,
                "confidence": f.confidence,
                "evidence": f.evidence,
            }
    return out


def _source_sheet(doc: MappingDocument, sid: str) -> str | None:
    s = next((x for x in doc.sources if x.id == sid), None)
    return (s.sheet.name if s and s.sheet else None) or sid


def _reads(expr: Any, default: str) -> list[str]:
    """Columns an expression reads, as ``source.column`` — what changes when a header is renamed."""
    out: list[str] = []
    if isinstance(expr, dict):
        if expr.get("op") == "col" and expr.get("name"):
            out.append(f"{expr.get('source') or default}.{expr['name']}")
        for v in expr.values():
            out.extend(_reads(v, default))
    elif isinstance(expr, list):
        for v in expr:
            out.extend(_reads(v, default))
    return out


def field_changes(old: MappingDocument, new: MappingDocument) -> list[dict[str, Any]]:
    a, b = _fields(old), _fields(new)
    out: list[dict[str, Any]] = []
    for target in sorted(set(a) | set(b)):
        x, y = a.get(target), b.get(target)
        if x is None and y is not None:
            status = "added"
        elif y is None and x is not None:
            status = "removed"
        elif x is not None and y is not None:
            same_table = _source_sheet(old, x["source"]) == _source_sheet(new, y["source"])
            status = "unchanged" if x["expr"] == y["expr"] and same_table else "changed"
        else:  # pragma: no cover - a key is in a or b
            continue
        out.append(
            {
                "pointer": target,
                "status": status,
                "before": None
                if x is None
                else {
                    "expr": x["expr"],
                    "reads": _reads(x["expr"], _source_sheet(old, x["source"]) or ""),
                    "collection": x.get("collection"),
                },
                "after": None
                if y is None
                else {
                    "expr": y["expr"],
                    "reads": _reads(y["expr"], _source_sheet(new, y["source"]) or ""),
                    "collection": y.get("collection"),
                    "confidence": y["confidence"],
                    "evidence": y["evidence"],
                },
            }
        )
    return out


# --------------------------------------------------------------------------------------------
# does the new column mean the same thing? — compared on the same SKUs, last round vs this one
# --------------------------------------------------------------------------------------------
def _first(rec: dict[str, Any], pointer: str) -> Any:
    return next((v for v in _values(rec, pointer) if v is not None), None)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def data_check(
    schema: TargetSchema, pointer: str, before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> dict[str, Any]:
    o = {_identity(r, schema): r for r in before}
    pairs = []
    for r in after:
        k = _identity(r, schema)
        if k in o:
            a, b = _first(o[k], pointer), _first(r, pointer)
            if a is not None and b is not None:
                pairs.append((k, a, b))
    if len(pairs) < MIN_PAIRS:
        return {
            "verdict": "no_data",
            "pairs": len(pairs),
            "summary": "not enough SKUs in both rounds to compare the values",
        }
    equal = sum(1 for _, a, b in pairs if a == b or (_num(a) is not None and _num(a) == _num(b)))
    share = equal / len(pairs)
    examples = [{"identity": k, "before": a, "after": b} for k, a, b in pairs[:5]]
    base = {"pairs": len(pairs), "equal_share": round(share, 4), "examples": examples}
    if share >= SAME_SHARE:
        return {**base, "verdict": "same", "summary": f"{share:.0%} of {len(pairs)} SKUs have the same value"}
    ratios = [nb / na for _, a, b in pairs if (na := _num(a)) and (nb := _num(b)) is not None]
    if len(ratios) >= MIN_PAIRS:
        ratio = statistics.median(ratios)
        steady = sum(1 for x in ratios if ratio and abs(x / ratio - 1) <= 0.01) / len(ratios)
        # a steady factor of one is not a factor: most values are the same and a few moved (a price change)
        if steady >= STEADY_SHARE and abs(ratio - 1.0) >= 0.01:
            if abs(ratio - (1 + VAT_RATE)) < 0.005:
                return {
                    **base,
                    "verdict": "vat_added",
                    "ratio": round(ratio, 4),
                    "summary": f"values are {VAT_RATE:.0%} higher than last round — VAT now included?",
                }
            if abs(ratio - 1 / (1 + VAT_RATE)) < 0.005:
                return {
                    **base,
                    "verdict": "vat_removed",
                    "ratio": round(ratio, 4),
                    "summary": f"values are {VAT_RATE:.0%} lower than last round — VAT now excluded?",
                }
            return {
                **base,
                "verdict": "scaled",
                "ratio": round(ratio, 4),
                "summary": f"values are ×{ratio:.3g} of last round's on {steady:.0%} of SKUs — unit, pack or currency?",
            }
    k, a, b = next((x for x in pairs if x[1] != x[2]), pairs[0])
    numeric = _num(a) is not None and _num(b) is not None
    return {
        **base,
        "verdict": "different",
        "summary": f"only {share:.0%} of {len(pairs)} SKUs keep their value — the column may mean something else"
        if numeric
        else f"{1 - share:.0%} of {len(pairs)} SKUs now have another value (e.g. {k}: {a} → {b})",
    }


def review(
    schema: TargetSchema,
    old: MappingDocument,
    new: MappingDocument,
    before: list[dict[str, Any]] | None,
    after: list[dict[str, Any]] | None,
    *,
    risk_of: Any = None,
    check_unchanged: bool = False,
) -> dict[str, Any]:
    """Every changed field with its data check; ``checks`` is what the policy reads.

    With ``check_unchanged`` the critical and high fields the mapping did not touch are checked too (ADR-056):
    a layout that did not move can still carry prices with the VAT taken out or in satang. Values that merely
    differ are then ``data_changed``, which nobody acts on here, because a new price list is the anomaly layer's
    business; a steady factor on every SKU is the mapping's."""
    changes = field_changes(old, new)
    for c in changes:
        c["risk"] = risk_of(c["pointer"]) if risk_of else None
        if c["status"] in ("changed", "added") and before and after:
            c["check"] = data_check(schema, c["pointer"], before, after)
        elif (
            check_unchanged
            and c["status"] == "unchanged"
            and c.get("risk") in ("critical", "high")
            and before
            and after
        ):
            check = data_check(schema, c["pointer"], before, after)
            if check["verdict"] == "different":
                check = {**check, "verdict": "data_changed", "summary": check["summary"].split(" — ")[0]}
            c["check"] = check
        else:
            c["check"] = None
    risky = [
        c
        for c in changes
        if c["status"] != "unchanged"
        and (
            (c["check"] and c["check"]["verdict"] in RISKY)
            or c["status"] == "removed"
            or c.get("risk") in ("critical", "high")
        )
    ]
    return {
        "counts": {
            s: sum(1 for c in changes if c["status"] == s)
            for s in ("unchanged", "changed", "added", "removed")
        },
        "needs_decision": [c["pointer"] for c in risky],
        "fields": changes,
        "checks": [
            {"pointer": c["pointer"], "risk": c.get("risk"), **c["check"]}
            for c in changes
            if c["check"] is not None
        ],
    }
