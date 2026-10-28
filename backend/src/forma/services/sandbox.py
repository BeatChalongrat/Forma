"""Mapping simulation sandbox (ADR-018): before a new mapping replaces the approved one, both run on the
same real files and FORMA compares what each produces — rows, lost and gained records, errors, invalid
prices, duplicates, and every changed value. The policy (``PolicyEngine.sandbox_problems``) decides
whether the difference may pass; a mapping that would make the data worse is blocked.

Fields are found by meaning (ADR-017): "price" is whatever the schema tags ``price.amount``.
"""

from __future__ import annotations

import re
from typing import Any

import orjson

from forma.db.models import SourceFile
from forma.engine.transform import TransformResult, _get_pointer
from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import INTERNAL_KEYS, TargetSchema
from forma.services.context import ServiceContext

SANDBOX_PREFIX = "sandbox"
DETAIL_CAP = 5000  # rows kept per drill-down list
LARGE_MOVE_PCT = 20.0
_MORE_RE = re.compile(r"^\.\.\. (\d+) more schema validation errors")


def details_key(customer_id: str, mapping_version_id: str) -> str:
    return f"{SANDBOX_PREFIX}/{customer_id}/{mapping_version_id}.json"


# --------------------------------------------------------------------------------------------
# one side: what a mapping produces
# --------------------------------------------------------------------------------------------
def _values(rec: dict[str, Any], pointer: str) -> list[Any]:
    """All values at a catalogue pointer (``/prices/*/amount`` expands over the collection)."""
    head, sep, tail = pointer.partition("/*/")
    if not sep:
        return [_get_pointer(rec, pointer)]
    items = _get_pointer(rec, head)
    if not isinstance(items, list):
        return []
    return [v for it in items if isinstance(it, dict) for v in _values(it, "/" + tail)]


def _has_valid_price(rec: dict[str, Any], pointers: list[str]) -> bool:
    vals = [v for p in pointers for v in _values(rec, p)]
    return bool(vals) and all(isinstance(v, int | float) and not isinstance(v, bool) and v >= 0 for v in vals)


def side_metrics(res: TransformResult | None, problems: list[str], schema: TargetSchema) -> dict[str, Any]:
    if res is None:
        return {"ok": False, "problems": problems[:10]}
    schema_errors = 0
    duplicates = missing_identity = 0
    for f in res.findings:
        if f.kind == "validation":
            m = _MORE_RE.match(f.message)
            schema_errors += int(m.group(1)) if m else 1
        elif f.kind == "dedupe" and "duplicate" in f.message:
            duplicates += int(f.details.get("count", 0))
        elif f.kind == "dedupe" and "null identity" in f.message:
            missing_identity += int(f.details.get("count", 0))
    price_ptrs = sorted(schema.semantic_pointers("price.amount"))
    invalid_prices = sum(1 for r in res.records if not _has_valid_price(r, price_ptrs)) if price_ptrs else 0
    return {
        "ok": True,
        "rows_in": res.stats.get("rows_in", 0),
        "rows_out": len(res.records),
        "errors": sum(1 for f in res.findings if f.severity == "error"),
        "warnings": sum(1 for f in res.findings if f.severity == "warning"),
        "schema_errors": schema_errors,
        "duplicates": duplicates,
        "missing_identity": missing_identity,
        "invalid_prices": invalid_prices,
    }


# --------------------------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------------------------
def _identity(rec: dict[str, Any], schema: TargetSchema) -> str:
    return "|".join(str(_get_pointer(rec, k)) for k in schema.identity_keys)


def _delta(a: Any, b: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Leaf-level differences between two records (engine-internal keys ignored)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[dict[str, Any]] = []
        for k in sorted((set(a) | set(b)) - (set(INTERNAL_KEYS) if not prefix else set())):
            out.extend(_delta(a.get(k), b.get(k), f"{prefix}/{k}"))
        return out
    if isinstance(a, list) and isinstance(b, list) and (a or b) and all(isinstance(x, dict) for x in a + b):
        out = []
        for i in range(max(len(a), len(b))):
            out.extend(_delta(a[i] if i < len(a) else None, b[i] if i < len(b) else None, f"{prefix}/{i}"))
        return out
    return [] if a == b else [{"pointer": prefix, "before": a, "after": b}]


def compare_records(
    schema: TargetSchema, old: list[dict[str, Any]], new: list[dict[str, Any]], *, cap: int = DETAIL_CAP
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """(counts, drill-down lists) for the same input read by two mappings."""
    o = {_identity(r, schema): r for r in old}
    n = {_identity(r, schema): r for r in new}
    price_ptrs = schema.semantic_pointers("price.amount")
    lost = sorted(set(o) - set(n))
    gained = sorted(set(n) - set(o))
    changed: list[dict[str, Any]] = []
    prices: list[dict[str, Any]] = []
    changed_n = price_n = large_n = 0
    for k in sorted(set(o) & set(n)):
        deltas = _delta(o[k], n[k])
        if not deltas:
            continue
        changed_n += 1
        if len(changed) < cap:
            changed.append({"identity": k, "changes": deltas[:20], "fields": len(deltas)})
        for d in deltas:
            if schema.wildcard(d["pointer"]) not in price_ptrs:
                continue
            price_n += 1
            before, after = d["before"], d["after"]
            pct = (
                round((after - before) / before * 100, 2)
                if isinstance(before, int | float) and isinstance(after, int | float) and before
                else None
            )
            if pct is not None and abs(pct) >= LARGE_MOVE_PCT:
                large_n += 1
            if len(prices) < cap:
                prices.append(
                    {"identity": k, "pointer": d["pointer"], "before": before, "after": after, "pct": pct}
                )
    counts = {
        "lost": len(lost),
        "gained": len(gained),
        "changed_records": changed_n,
        "changed_prices": price_n,
        "large_price_moves": large_n,
    }
    details = {
        "lost": [{"identity": k} for k in lost[:cap]],
        "gained": [{"identity": k} for k in gained[:cap]],
        "changed": changed,
        "price_changes": prices,
    }
    return counts, details


def override_conflicts(
    schema: TargetSchema,
    old: list[dict[str, Any]],
    new: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
    *,
    cap: int = 200,
) -> list[dict[str, Any]]:
    """Persistent overrides (edits re-applied on every import) that the new mapping collides with: the
    record they pin is gone, the mapping now produces a different value underneath them (the override
    would hide the change), a rule matches other records than before, or the field is no longer filled."""
    if not overrides:
        return []
    from forma.services.datasets import _flat_frame, _where_mask, where_names

    o = {_identity(r, schema): r for r in old}
    n = {_identity(r, schema): r for r in new}
    frames: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for ov in overrides:
        target, where = str(ov["target"]), ov.get("where") or {}
        base = {"override_id": ov.get("id"), "target": target, "value": ov.get("value")}
        if "identity" in where:
            ident = str(where["identity"])
            if ident not in n:
                out.append(
                    {
                        **base,
                        "identity": ident,
                        "kind": "no_record",
                        "message": f"{ident}: the new mapping produces no such record, so the override on "
                        f"{target} would apply to nothing",
                    }
                )
            elif ident in o and _get_pointer(o[ident], target) != _get_pointer(n[ident], target):
                before, after = _get_pointer(o[ident], target), _get_pointer(n[ident], target)
                out.append(
                    {
                        **base,
                        "identity": ident,
                        "kind": "value_changed",
                        "before": before,
                        "after": after,
                        "message": f"{ident}: the new mapping changes {target} ({before} → {after}), but a "
                        f"persistent override replaces it with {ov.get('value')}, hiding the change",
                    }
                )
        else:
            if "old" not in frames:
                need = set().union(*(where_names(x.get("where")) for x in overrides))
                frames["old"], frames["new"] = _flat_frame(old, schema, need), _flat_frame(new, schema, need)
            try:
                hit_old = {_identity(old[i], schema) for i in _where_mask(frames["old"], where or None)}
                hit_new = {_identity(new[i], schema) for i in _where_mask(frames["new"], where or None)}
            except Exception:
                continue
            if hit_old != hit_new:
                out.append(
                    {
                        **base,
                        "kind": "scope_changed",
                        "before": len(hit_old),
                        "after": len(hit_new),
                        "message": f"a rule setting {target} matched {len(hit_old)} records with the approved "
                        f"mapping and {len(hit_new)} with the new one",
                    }
                )
        if len(out) >= cap:
            break
    targets = sorted({str(ov["target"]) for ov in overrides})
    for t in targets:
        filled_before = any(v is not None for r in old for v in _values(r, t))
        if filled_before and not any(v is not None for r in new for v in _values(r, t)):
            out.append(
                {
                    "override_id": None,
                    "target": t,
                    "kind": "field_dropped",
                    "message": f"the new mapping no longer fills {t}; overrides still set it",
                }
            )
    return out[:cap]


def run(
    ctx: ServiceContext,
    schema: TargetSchema,
    new_doc: MappingDocument,
    baseline_doc: MappingDocument,
    files: list[SourceFile],
    *,
    limit: int | None,
    overrides: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Run both mappings on the same files. Returns (report without details, details)."""
    from forma.services.mapping import simulate

    new_res, new_problems = simulate(ctx, new_doc, schema, files, limit=limit)
    old_res, old_problems = simulate(ctx, baseline_doc, schema, files, limit=limit)
    new_m = side_metrics(new_res, new_problems, schema)
    old_m = side_metrics(old_res, old_problems, schema)
    report: dict[str, Any] = {"rows_limit": limit, "old": old_m, "new": new_m, "delta": None}
    details: dict[str, list[dict[str, Any]]] = {"lost": [], "gained": [], "changed": [], "price_changes": []}
    if new_res is not None and old_res is not None:
        counts, details = compare_records(schema, old_res.records, new_res.records)
        report["delta"] = {
            **counts,
            "new_errors": max(0, new_m["errors"] - old_m["errors"]),
            "new_invalid_prices": max(0, new_m["invalid_prices"] - old_m["invalid_prices"]),
            "new_duplicates": max(0, new_m["duplicates"] - old_m["duplicates"]),
        }
        conflicts = override_conflicts(schema, old_res.records, new_res.records, overrides or [])
        report["override_conflicts"] = conflicts
        report["delta"]["override_conflicts"] = len(conflicts)
    return report, details


def store_details(ctx: ServiceContext, key: str, details: dict[str, list[dict[str, Any]]]) -> None:
    """Drill-down lists go to object storage (written before the row that points at them)."""
    ctx.storage.put(key, orjson.dumps(details, default=str))


def load_details(ctx: ServiceContext, key: str) -> dict[str, list[dict[str, Any]]]:
    data = orjson.loads(ctx.storage.get(key))
    return data if isinstance(data, dict) else {}
