"""Bounded observations, not business labels. Usable by profiling and read-only agents."""

from __future__ import annotations

import math
import re
from collections import Counter
from itertools import combinations
from typing import Any

import polars as pl

from forma.engine.thai import parse_number_th_py

SAMPLE_LIMIT = 400
RELATION_ROWS = 2000
JOIN_ROWS = 50000


def spread_indices(size: int, limit: int) -> list[int]:
    if size <= limit:
        return list(range(size))
    return sorted({i * (size - 1) // (limit - 1) for i in range(limit)})


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = parse_number_th_py(str(value))
    return float(number) if number is not None and math.isfinite(number) else None


def column_observations(series: pl.Series) -> dict[str, Any]:
    values = [series[i] for i in spread_indices(len(series), SAMPLE_LIMIT)]
    present = [v for v in values if v is not None and str(v).strip()]
    numbers = [n for v in present if (n := finite_number(v)) is not None]
    zeros = [str(v) for v in present if re.fullmatch(r"0\d+", str(v).strip())]
    ambiguous = [str(v) for v in present if re.fullmatch(r"[+-]?\d{1,3}[,.]\d{3}", str(v).strip())]
    denominator = len(present)
    return {
        "scope": {
            "rows": len(series),
            "sampled_rows": len(values),
            "complete": len(values) == len(series),
            "strategy": "evenly_spaced_including_tail",
        },
        "representative_values": list(dict.fromkeys(str(v)[:200] for v in present))[:12],
        "numeric_parse_ratio": round(len(numbers) / denominator, 4) if denominator else None,
        "sample_null_count": sum(v is None for v in values),
        "sample_blank_string_count": sum(isinstance(v, str) and not v.strip() for v in values),
        "integer_ratio_of_numeric": round(sum(n.is_integer() for n in numbers) / len(numbers), 4)
        if numbers
        else None,
        "numeric_min": min(numbers) if numbers else None,
        "numeric_max": max(numbers) if numbers else None,
        "negative_count": sum(n < 0 for n in numbers),
        "zero_count": sum(n == 0 for n in numbers),
        "leading_zero_examples": list(dict.fromkeys(zeros))[:4],
        "ambiguous_separator_examples": list(dict.fromkeys(ambiguous))[:4],
        "caution": (
            "Sample statistics are hypotheses. Decimal values do not establish money; integers "
            "do not establish quantity. Numeric parsing uses FORMA's default parser; "
            "inspect locale before choosing a conversion."
        ),
    }


def table_observations(frame: pl.DataFrame, columns: list[str] | None = None) -> dict[str, Any]:
    """Co-occurrence and numeric relationships retain row alignment. Never infer units from ratios."""
    indices = spread_indices(frame.height, RELATION_ROWS)
    sample = frame[indices] if indices else frame.head(0)
    if columns is not None and (
        not 1 <= len(columns) <= 12
        or len(set(columns)) != len(columns)
        or any(c not in frame.columns for c in columns)
    ):
        raise ValueError("Choose 1–12 distinct existing columns")
    columns = columns if columns is not None else frame.columns[:12]
    rows = sample.select(columns).to_dicts()
    key_candidates = []
    for names in combinations(columns[:8], 2):
        keys = [tuple(r[c] for c in names) for r in rows]
        if not keys or any(any(v is None or str(v).strip() == "" for v in k) for k in keys):
            continue
        if len(set(keys)) == len(keys) and all(len({r[c] for r in rows}) < len(rows) for c in names):
            key_candidates.append(list(names))
    relationships = []
    for left, right in combinations(columns, 2):
        pairs = [
            (a, b)
            for r in rows
            if (a := finite_number(r[left])) is not None
            and (b := finite_number(r[right])) is not None
            and a != 0
        ]
        if len(pairs) < 3 or len({a for a, _ in pairs}) < 3:
            continue
        ratios = [b / a for a, b in pairs]
        factor = sorted(ratios)[len(ratios) // 2]
        matches = sum(math.isclose(b, a * factor, rel_tol=1e-7, abs_tol=1e-8) for a, b in pairs)
        if math.isfinite(factor) and matches / len(pairs) >= 0.95:
            relationships.append(
                {
                    "left": left,
                    "right": right,
                    "right_over_left": factor,
                    "matched_rows": matches,
                    "compared_rows": len(pairs),
                    "example": {left: pairs[0][0], right: pairs[0][1]},
                }
            )
    return {
        "scope": {
            "rows": frame.height,
            "sampled_rows": len(rows),
            "columns": columns,
            "available_columns": frame.width,
            "complete": len(rows) == frame.height and len(columns) == frame.width,
        },
        "composite_key_candidates": key_candidates[:8],
        "numeric_relationships": relationships[:12],
        "caution": (
            "Observed associations only. Key candidates need full join checks; "
            "ratios do not prove pack size, currency, tax or business meaning."
        ),
    }


def join_observations(
    left: pl.DataFrame, right: pl.DataFrame, left_keys: list[str], right_keys: list[str]
) -> dict[str, Any]:
    """Measure a proposed key, including composite keys; no automatic join selection."""
    if not 1 <= len(left_keys) <= 3 or len(left_keys) != len(right_keys):
        raise ValueError("Provide 1–3 matching left_keys and right_keys")
    for frame, keys in ((left, left_keys), (right, right_keys)):
        if len(set(keys)) != len(keys) or any(k not in frame.columns for k in keys):
            raise ValueError("Join keys must be distinct existing column names")

    def counts(frame: pl.DataFrame, keys: list[str]) -> tuple[Counter[tuple[str, ...]], int]:
        result: Counter[tuple[str, ...]] = Counter()
        missing = 0
        for row in frame.head(JOIN_ROWS).select(keys).iter_rows():
            if any(v is None or not str(v).strip() for v in row):
                missing += 1
            else:
                result[tuple(str(v).strip().upper() for v in row)] += 1
        return result, missing

    lc, lm = counts(left, left_keys)
    rc, rm = counts(right, right_keys)
    common = lc.keys() & rc.keys()
    return {
        "scope": {
            "left_rows": left.height,
            "right_rows": right.height,
            "left_rows_checked": min(left.height, JOIN_ROWS),
            "right_rows_checked": min(right.height, JOIN_ROWS),
            "complete": max(left.height, right.height) <= JOIN_ROWS,
            "normalization": "trim_upper; leading zeros preserved",
        },
        "left_keys": left_keys,
        "right_keys": right_keys,
        "left_missing_key_rows": lm,
        "right_missing_key_rows": rm,
        "left_duplicate_keys": sum(n > 1 for n in lc.values()),
        "right_duplicate_keys": sum(n > 1 for n in rc.values()),
        "matched_left_rows": sum(lc[k] for k in common),
        "unmatched_left_rows": sum(n for k, n in lc.items() if k not in rc),
        "unmatched_right_rows": sum(n for k, n in rc.items() if k not in lc),
        "inner_join_rows": sum(lc[k] * rc[k] for k in common),
        "cardinality": ("N" if any(n > 1 for n in lc.values()) else "1")
        + ":"
        + ("N" if any(n > 1 for n in rc.values()) else "1"),
        "unmatched_left_examples": [list(k) for k in lc if k not in rc][:4],
        "caution": (
            "Metrics apply to checked rows only. A matching key is not proof that the tables "
            "mean the same thing. Confirm grain, units and filters before mapping."
        ),
    }
