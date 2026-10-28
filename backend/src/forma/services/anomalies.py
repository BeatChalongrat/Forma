"""Change intelligence & anomaly detection (ADR-022): nobody can read 100,000 rows every two weeks, so FORMA
points at the few that look wrong — prices that jump against last round, zero or negative prices, prices far
from the rest of their category, discounts beyond the business rule, promotions that end before they start,
stock that collapses or goes negative, SKUs that vanish in bulk, and a row count unlike the customer's usual.

Thresholds live in the policy (``anomaly``, editable in Settings). Fields are found by meaning (ADR-017).
Deterministic: same records + same policy ⇒ the same anomalies in the same order.
"""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Any

from forma.schema_registry import TargetSchema
from forma.services.sandbox import _identity, _values

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MAX_ITEMS = 500
MIN_CATEGORY = 8  # prices compared within a category only when it has this many SKUs


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _nums(rec: dict[str, Any], pointers: list[str]) -> list[float]:
    return [n for p in pointers for v in _values(rec, p) if (n := _num(v)) is not None]


def _first_text(rec: dict[str, Any], pointers: list[str]) -> str | None:
    for p in pointers:
        for v in _values(rec, p):
            if isinstance(v, list) and v:
                return str(v[0])
            if v not in (None, "", []):
                return str(v)
    return None


def _items(rec: dict[str, Any], collection: str | None) -> list[dict[str, Any]]:
    if not collection:
        return []
    cur: Any = rec
    for part in [p for p in collection.split("/") if p]:
        cur = cur.get(part) if isinstance(cur, dict) else None
    return [x for x in cur if isinstance(x, dict)] if isinstance(cur, list) else []


def _leaf(schema: TargetSchema, semantic: str) -> str | None:
    """The item key of a collection field by meaning (``/promotions/*/discount_percent`` → discount_percent)."""
    ptr = next((p for p in sorted(schema.semantic_pointers(semantic)) if "/*/" in p), None)
    return ptr.rsplit("/", 1)[-1] if ptr else None


def detect(
    schema: TargetSchema,
    records: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    previous: list[dict[str, Any]] | None = None,
    history_rows: list[int] | None = None,
) -> dict[str, Any]:
    """Anomalies of one dataset: record-level (with identity and pointer) and dataset-level."""
    cfg = policy
    items: list[dict[str, Any]] = []
    dataset: list[dict[str, Any]] = []
    prices = sorted(schema.semantic_pointers("price.amount"))
    stock = sorted(schema.semantic_pointers("stock.qty"))
    categories = sorted(schema.semantic_pointers("category_path"))
    promo_coll = schema.collection_for("promotions")
    pct_key, amt_key = _leaf(schema, "discount.percent"), _leaf(schema, "discount.amount")
    special_key = _leaf(schema, "promo.price")
    start_key, end_key = _leaf(schema, "promo.start_date"), _leaf(schema, "promo.end_date")

    def add(rec_id: str, kind: str, severity: str, message: str, **extra: Any) -> None:
        items.append({"identity": rec_id, "kind": kind, "severity": severity, "message": message, **extra})

    # ---- within this round ----------------------------------------------------------------------
    by_category: dict[str, list[tuple[str, float]]] = {}
    for rec in records:
        k = _identity(rec, schema)
        ps = _nums(rec, prices)
        for p in ps:
            if p <= 0:
                add(k, "price_not_positive", "high", f"price is {p:g}", pointer=prices[0], value=p)
                break
        if ps and ps[0] > 0 and categories and (cat := _first_text(rec, categories)):  # ≤ 0 is flagged above
            by_category.setdefault(cat, []).append((k, ps[0]))
        base = ps[0] if ps else None
        for it in _items(rec, promo_coll):
            pct = _num(it.get(pct_key)) if pct_key else None
            if pct is not None and pct > float(cfg["max_discount_pct"]):
                add(
                    k,
                    "discount_too_high",
                    "high",
                    f"discount {pct:g}% is above {cfg['max_discount_pct']}%",
                    value=pct,
                )
            amt = _num(it.get(amt_key)) if amt_key else None
            if amt is not None and base is not None and amt >= base > 0:
                add(
                    k,
                    "discount_exceeds_price",
                    "high",
                    f"discount {amt:g} is not below the price {base:g}",
                    value=amt,
                )
            special = _num(it.get(special_key)) if special_key else None
            if special is not None and base is not None and special >= base > 0:
                add(
                    k,
                    "promo_price_not_lower",
                    "medium",
                    f"special price {special:g} ≥ regular price {base:g}",
                    value=special,
                )
            s, e = (it.get(start_key) if start_key else None), (it.get(end_key) if end_key else None)
            if isinstance(s, str) and isinstance(e, str) and e < s:
                add(k, "promo_dates_reversed", "high", f"promotion ends {e} before it starts {s}")
        qs = _nums(rec, stock)
        if any(q < 0 for q in qs):
            add(k, "stock_negative", "high", f"stock is negative ({min(qs):g})", value=min(qs))

    # prices far from the rest of their category (robust: median and MAD, not mean and stdev)
    limit = float(cfg["outlier_mad"])
    for cat, vals in sorted(by_category.items()):
        if len(vals) < MIN_CATEGORY:
            continue
        xs = [v for _, v in vals]
        med = statistics.median(xs)
        mad = statistics.median(abs(x - med) for x in xs)
        if mad <= 0:
            continue
        for k, v in vals:
            z = abs(v - med) / (1.4826 * mad)
            if z > limit:
                add(
                    k,
                    "price_outlier",
                    "medium",
                    f"price {v:g} is far from category {cat!r} (median {med:g})",
                    value=v,
                    expected=med,
                    score=round(z, 1),
                )

    # ---- against last round ----------------------------------------------------------------------
    if previous is not None:
        old = {_identity(r, schema): r for r in previous}
        cur = {_identity(r, schema): r for r in records}
        thr = float(cfg["max_price_change_pct"])
        drop = float(cfg["max_stock_drop_pct"])
        for k in sorted(set(old) & set(cur)):
            a, b = _nums(old[k], prices), _nums(cur[k], prices)
            if a and b and a[0] > 0:
                pct = (b[0] - a[0]) / a[0] * 100
                if abs(pct) >= thr:
                    add(
                        k,
                        "price_jump",
                        "high" if abs(pct) >= 2 * thr else "medium",
                        f"price {a[0]:g} → {b[0]:g} ({pct:+.1f}%)",
                        value=b[0],
                        expected=a[0],
                        score=round(abs(pct), 1),
                    )
            sa, sb = sum(_nums(old[k], stock)), sum(_nums(cur[k], stock))
            if sa >= 10 and sb >= 0 and (sa - sb) / sa * 100 >= drop:
                add(
                    k,
                    "stock_drop",
                    "medium",
                    f"stock {sa:g} → {sb:g} (−{(sa - sb) / sa:.0%})",
                    value=sb,
                    expected=sa,
                )
        removed = len(set(old) - set(cur))
        if old and removed / len(old) * 100 > float(cfg["max_removed_pct"]):
            dataset.append(
                {
                    "kind": "skus_removed",
                    "severity": "high",
                    "message": f"{removed} of {len(old)} SKUs ({removed / len(old):.0%}) are gone since last round",
                    "value": removed,
                }
            )

    # ---- against the customer's usual size --------------------------------------------------------
    baseline = None
    if history_rows:
        avg = sum(history_rows) / len(history_rows)
        baseline = {"rounds": len(history_rows), "avg_rows": round(avg, 1), "current_rows": len(records)}
        if avg and abs(len(records) - avg) / avg * 100 > float(cfg["row_count_tolerance_pct"]):
            dataset.append(
                {
                    "kind": "row_count",
                    "severity": "high",
                    "message": f"{len(records)} records vs {avg:.0f} on average over the last "
                    f"{len(history_rows)} round(s)",
                    "value": len(records),
                    "expected": round(avg, 1),
                }
            )

    items.sort(
        key=lambda a: (SEVERITY_ORDER[a["severity"]], -float(a.get("score") or 0), a["identity"], a["kind"])
    )
    flagged = {a["identity"] for a in items}
    counts = Counter(a["severity"] for a in [*items, *dataset])
    n = len(records)
    normal = round(100 * (1 - len(flagged) / n), 1) if n else 100.0
    high = counts.get("high", 0)
    return {
        "records": n,
        "anomalous_records": len(flagged),
        "normal_pct": normal,
        "high": high,
        "medium": counts.get("medium", 0),
        "low": counts.get("low", 0),
        "by_kind": dict(sorted(Counter(a["kind"] for a in items).items())),
        "dataset": dataset,
        "items": items[:MAX_ITEMS],
        "baseline": baseline,
        "headline": (
            f"{len(items) + len(dataset)} anomalies on {len(flagged)} records — {normal:g}% look normal"
            + (f"; {high} high-severity to check before publishing" if high else "")
            if items or dataset
            else f"nothing unusual in {n} records"
        ),
    }
