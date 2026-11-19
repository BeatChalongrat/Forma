"""Row accounting: where every source row went, and whether the file's own totals agree with what was read.

The engine fills a ledger while it runs; nothing here changes a record. Two facts about the *file* (not the
mapping) become ``source_review`` findings, so a reused mapping cannot wave them through: a total row whose
numbers match no sum of the rows read, and rows that look like data after the blank-row stop.
A matching total is evidence the table was read whole, not proof that a column means what the mapping says.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_left
from typing import Any

import polars as pl

from forma.engine.thai import THAI_DIGITS, parse_number_th_py

VERSION = 1
NUMERIC_SHARE = 0.9  # share of a column's filled cells that must read as numbers before its total is checked
MAX_EXAMPLES = 3
_DECIMALS_RE = re.compile(r"[.,](\d+)\D*$")


def _decimals(text: str) -> int:
    """Decimal places a written total shows. A group of exactly three digits is a thousands group."""
    m = _DECIMALS_RE.search(text.strip().translate(THAI_DIGITS))
    if not m or len(m.group(1)) == 3:
        return 0
    return len(m.group(1))


def _close(total: float, value: float, decimals: int) -> bool:
    # a total is printed rounded; the rows may carry more places than it shows
    return abs(total - value) <= 0.5 * 10.0**-decimals + 1e-6 * max(1.0, abs(value))


def _running(values: list[float]) -> list[float]:
    out, acc = [0.0], 0.0
    for v in values:
        acc += v
        out.append(acc)
    return out


def check_totals(
    kept: pl.DataFrame, totals: pl.DataFrame, columns: list[str], row_col: str
) -> dict[str, Any]:
    """Compare each dropped total row with the rows that were kept.

    A total may be a subtotal (rows since the previous total), a running or grand total (every row above) or
    a count of rows, and a sheet that lists a row twice usually totals it once. One number agreeing with the
    sum of any numeric column confirms the row: the others may be averages or percentages, and hand-made
    sheets write totals a column off. A row whose numbers agree with nothing is contradicted. A total under a
    column that is not numeric is compared but never reported as that column's sum.
    """
    report: dict[str, Any] = {"rows": totals.height, "confirmed": 0, "unchecked": 0, "contradicted": []}
    if not totals.height:
        return report
    rows = kept[row_col].to_list()
    # exact repeats of a row: the engine drops them later, and the file's own total often leaves them out
    first_seen = kept.select(pl.struct(columns).is_first_distinct().alias("f"))["f"].to_list()
    prefix: dict[str, list[float]] = {}
    distinct: dict[str, list[float]] = {}
    for c in columns:
        raw = kept[c].to_list()
        filled = [v for v in raw if v is not None and str(v).strip()]
        values = [parse_number_th_py(v) for v in raw]
        numeric = sum(v is not None for v in values)
        if not filled or numeric / len(filled) < NUMERIC_SHARE:
            continue
        prefix[c] = _running([v or 0.0 for v in values])
        distinct[c] = _running(
            [(v or 0.0) if new else 0.0 for v, new in zip(values, first_seen, strict=True)]
        )
    previous = -1
    for item in totals.sort(row_col).iter_rows(named=True):
        at = int(item[row_col])
        lo, hi = bisect_left(rows, previous + 1), bisect_left(rows, at)
        previous = at
        sums = {
            c: (
                running[hi] - running[lo],
                running[hi],
                running[-1],
                distinct[c][hi] - distinct[c][lo],
                distinct[c][hi],
                distinct[c][-1],
            )
            for c, running in prefix.items()
        }
        counts = (float(hi - lo), float(hi))
        checked: list[dict[str, Any]] = []
        agreed = False
        for c in columns:
            total = parse_number_th_py(item[c])
            if total is None:
                continue
            places = _decimals(str(item[c]))
            # a hand-made sheet often writes its totals one column off: any column's sum of these rows counts
            if any(_close(total, v, places) for group in (*sums.values(), counts) for v in group):
                agreed = True
                break
            if c in sums:
                nearest = min(sums[c][:2], key=lambda v: abs(total - v))
                checked.append({"column": c, "total": total, "rows_sum": round(nearest, 6)})
        if agreed:
            report["confirmed"] += 1
        elif checked:
            label = next((str(item[c]).strip() for c in columns if item[c] is not None), "")
            report["contradicted"].append(
                {"sheet_row": at + 1, "label": label[:60], "columns": checked[:MAX_EXAMPLES]}
            )
        else:
            report["unchecked"] += 1
    return report


def after_stop(
    tail: pl.DataFrame, head: pl.DataFrame, columns: list[str], row_col: str, total_label: str
) -> dict[str, Any]:
    """Rows cut off by the blank-row stop. A row as full as half a typical data row is counted as data-like;
    a note, a signature line or a total row is not."""
    filled = pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int32) for c in columns])
    typical = head.select(filled.alias("n")).filter(pl.col("n") > 0)["n"].median() if head.height else None
    need = max(2, math.ceil(float(typical or 0) / 2))  # type: ignore[arg-type]
    first = pl.coalesce([pl.col(c) for c in columns]).str.to_lowercase().str.strip_chars()
    rows = tail.with_columns(
        filled.alias("__n__"), first.str.contains(total_label).fill_null(False).alias("__t__")
    )
    data_like = rows.filter((pl.col("__n__") >= need) & ~pl.col("__t__"))
    return {
        "rows": rows.filter(pl.col("__n__") > 0).height,
        "data_like": data_like.height,
        "first_sheet_row": int(data_like[row_col][0]) + 1 if data_like.height else None,
    }


def _number(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def source_findings(source_id: str, audit: dict[str, Any]) -> list[dict[str, Any]]:
    """What a person must look at in this source, as keyword arguments of a ``source_review`` finding."""
    out: list[dict[str, Any]] = []
    for item in (audit.get("totals") or {}).get("contradicted", [])[:MAX_EXAMPLES]:
        col = item["columns"][0]
        out.append(
            {
                "message": (
                    f"{source_id}: total on sheet row {item['sheet_row']} says {col['column']} = "
                    f"{_number(col['total'])} but the rows read add up to {_number(col['rows_sum'])}; "
                    "rows may be missing or a number was read differently"
                ),
                "details": {"check": "totals_row", "source": source_id, **item},
            }
        )
    cut = audit.get("after_blank_stop") or {}
    if cut.get("data_like"):
        out.append(
            {
                "message": (
                    f"{source_id}: {cut['data_like']} rows that look like data follow a blank gap "
                    f"(from sheet row {cut['first_sheet_row']}) and were not read; "
                    "confirm the table ends there"
                ),
                "details": {"check": "rows_after_blank_stop", "source": source_id, **cut},
            }
        )
    return out


def root_ledger(
    *,
    source: str,
    rows_in: int,
    join_added: int,
    join_dropped: int,
    filtered: int,
    duplicate_identity: int,
    null_identity: int,
    rows_out: int,
) -> dict[str, Any]:
    expected = rows_in + join_added - join_dropped - filtered - duplicate_identity - null_identity
    return {
        "source": source,
        "rows_in": rows_in,
        "join_added": join_added,
        "join_dropped": join_dropped,
        "filtered": filtered,
        "duplicate_identity": duplicate_identity,
        "null_identity": null_identity,
        "rows_out": rows_out,
        "balanced": expected == rows_out,
    }
