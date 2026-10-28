"""Vectorised (Polars) implementations of the Thai unit rules in ``engine/thai.py`` (ADR-007, ADR-010).

The Python functions stay the reference semantics; ``tests/test_thai_vec.py`` asserts the two agree
on every input in the Thai test tables. Dates deliberately stay in Python: the regex parser was
measured as fast as a Polars fast path (docs/PERFORMANCE.md), so only one implementation exists.
"""

from __future__ import annotations

import os

import polars as pl

from forma.engine import thai

_THAI_FROM = list("๐๑๒๓๔๕๖๗๘๙")
_THAI_TO = list("0123456789")
_CANONICAL_UNITS = sorted(set(thai.UNIT_SYNONYMS.values()))
# FORMA_VEC_OPS=0 forces the Python reference paths (A/B measurements, escape hatch).
ENABLED = os.environ.get("FORMA_VEC_OPS", "1") != "0"


def _clean(x: pl.Expr) -> pl.Expr:
    return x.cast(pl.String, strict=False).str.strip_chars().str.replace_many(_THAI_FROM, _THAI_TO)


_PACK_PATTERNS = [
    (r"(\d+)\s*[xX×*]\s*(\d+)", 2),
    (r"\((\d+)\)", 1),
    (r"(?i)(\d+)\s*(?:ชิ้น|pcs|pc|ea|units?)\s*(?:/|ต่อ|per)\s*\S+", 1),
    (r"(\d+)\s*'s", 1),
    (r"(?i)(?:pack|pk|แพ็ค|แพค|บรรจุ|ctn|carton|case|box|ลัง|กล่อง|หีบ|dz|dozen|โหล|set|ชุด)\s*[:=]?\s*(\d+)", 1),
    (r"^(\d+)$", 1),
]


def pack_size_expr(x: pl.Expr) -> pl.Expr:
    """Same rule order as ``pack_size_py``: first matching pattern wins; ``1x12`` → max(1, 12)."""
    s = _clean(x)
    candidates: list[pl.Expr] = []
    for pattern, groups in _PACK_PATTERNS:
        if groups == 2:
            g1 = s.str.extract(pattern, 1).cast(pl.Int64, strict=False)
            g2 = s.str.extract(pattern, 2).cast(pl.Int64, strict=False)
            candidates.append(pl.max_horizontal(g1, g2))
        else:
            candidates.append(s.str.extract(pattern, 1).cast(pl.Int64, strict=False))
    v = pl.coalesce(candidates)
    return pl.when(v > 0).then(v).otherwise(None)


def unit_normalize_expr(
    x: pl.Expr, *, default: str | None = None, extra: dict[str, str] | None = None
) -> pl.Expr:
    """Same lookup order as ``unit_normalize_py``: exact → stripped of digits/punctuation → first known
    token → already-canonical code → default (or the upper-cased input)."""
    table = {**thai.UNIT_SYNONYMS, **{k.lower(): v for k, v in (extra or {}).items()}}
    raw = x.cast(pl.String, strict=False).str.strip_chars()
    s = raw.str.to_lowercase()
    exact = s.replace_strict(table, default=None, return_dtype=pl.String)
    s2 = s.str.replace_all(r"[\d\s()xX×*./'-]+", " ").str.strip_chars()
    stripped = s2.replace_strict(table, default=None, return_dtype=pl.String)
    tokens = (
        s.str.extract_all(r"[^\s/()]+")
        .list.eval(pl.element().replace_strict(table, default=None, return_dtype=pl.String))
        .list.drop_nulls()
        .list.first()
    )
    up = s.str.to_uppercase()
    canonical = pl.when(up.is_in(_CANONICAL_UNITS)).then(up).otherwise(None)
    fallback = pl.lit(default) if default is not None else raw.str.to_uppercase()
    out = pl.coalesce([exact, stripped, tokens, canonical, fallback])
    # None / "" → default (Python returns ``default`` for empty input, even when it is None)
    return pl.when(raw.is_null() | (raw == "")).then(pl.lit(default)).otherwise(out)
