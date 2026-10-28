"""Compile Mapping DSL expressions to Polars expressions.

Every DSL op has exactly one compiler branch here. Nothing is ``eval``'d; an unknown op is a hard
error at validation time, long before data is touched.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from forma.engine import thai, thai_vec
from forma.mapping_dsl.models import CallExpr, ColExpr, ConstExpr, Expr, Op

_THAI_DIGITS_FROM = list("๐๑๒๓๔๕๖๗๘๙")
_THAI_DIGITS_TO = list("0123456789")


class CompileError(ValueError):
    pass


@dataclass
class CompileContext:
    """Resolves ``col`` references to physical column names.

    Frames are prefixed ``<source_id>.<column>``; ``default_source`` is used when ``ColExpr.source``
    is empty. ``columns`` is the set of physical columns available in the frame being compiled.
    """

    default_source: str
    columns: set[str]
    lookups: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def physical(self, c: ColExpr) -> str:
        src = c.source or self.default_source
        name = f"{src}.{c.name}"
        if name in self.columns:
            return name
        # tolerant matching: case/whitespace-insensitive
        want = _norm(c.name)
        for col in self.columns:
            s, _, n = col.partition(".")
            if s == src and _norm(n) == want:
                return col
        self.missing.append(name)
        return name


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def compile_expr(e: Expr, ctx: CompileContext) -> pl.Expr:
    if isinstance(e, ColExpr):
        name = ctx.physical(e)
        if name not in ctx.columns:
            return pl.lit(None, dtype=pl.String).alias(name)
        return pl.col(name)
    if isinstance(e, ConstExpr):
        return pl.lit(e.value)
    if isinstance(e, CallExpr):
        return _compile_call(e, ctx)
    raise CompileError(f"unknown expression node {type(e)!r}")


def _args(e: CallExpr, ctx: CompileContext) -> list[pl.Expr]:
    return [compile_expr(a, ctx) for a in e.args]


def _as_str(x: pl.Expr) -> pl.Expr:
    return x.cast(pl.String, strict=False)


def _as_num(x: pl.Expr) -> pl.Expr:
    """Numeric view of any expression (strings go through the Thai-aware number parser)."""
    return pl.when(_as_str(x).is_null()).then(None).otherwise(_parse_number(x)).cast(pl.Float64, strict=False)


def _parse_number(x: pl.Expr) -> pl.Expr:
    s = _as_str(x).str.strip_chars()
    s = s.str.replace_many(_THAI_DIGITS_FROM, _THAI_DIGITS_TO)
    # (123) → -123
    neg = s.str.starts_with("(") & s.str.ends_with(")")
    s = pl.when(neg).then(pl.lit("-") + s.str.slice(1, s.str.len_chars() - 2)).otherwise(s)
    s = s.str.replace_all(r"(?i)[,\s฿$€£]|บาท|thb|baht", "").str.strip_chars_end("%")
    s = pl.when(s.str.contains(r"^[-+]?\d+(\.\d+)?$")).then(s).otherwise(None)
    return s.cast(pl.Float64, strict=False)


def _compile_call(e: CallExpr, ctx: CompileContext) -> pl.Expr:
    op, p = e.op, e.params
    a = _args(e, ctx)

    match op:
        case Op.COALESCE:
            return pl.coalesce(a)
        case Op.CONCAT:
            sep = str(p.get("separator", ""))
            return pl.concat_str([_as_str(x).fill_null("") for x in a], separator=sep)
        case Op.TRIM:
            return _as_str(a[0]).str.strip_chars()
        case Op.UPPER:
            return _as_str(a[0]).str.to_uppercase()
        case Op.LOWER:
            return _as_str(a[0]).str.to_lowercase()
        case Op.NORMALIZE_WHITESPACE:
            return _as_str(a[0]).str.replace_all(r"\s+", " ").str.strip_chars()
        case Op.STRIP_LEADING_ZEROS:
            # Polars' regex engine has no look-around: keep the first non-zero digit via a capture group.
            s = _as_str(a[0]).str.replace(r"^0+(\d)", "${1}")
            return s
        case Op.CAST:
            to = p.get("to")
            if to == "string":
                return _as_str(a[0])
            if to == "int":
                return _as_num(a[0]).round(0).cast(pl.Int64, strict=False)
            if to == "float":
                return _as_num(a[0])
            if to == "bool":
                return _py_map(a[0], thai.parse_bool_py, pl.Boolean)
            if to == "date":
                return _parse_date(a[0], formats=None, assume="auto", dayfirst=True)
            raise CompileError(f"cast: unsupported target {to!r}")
        case Op.REGEX_EXTRACT:
            return _as_str(a[0]).str.extract(str(p["pattern"]), int(p.get("group", 1)))
        case Op.REGEX_REPLACE:
            return _as_str(a[0]).str.replace_all(str(p["pattern"]), str(p.get("replacement", "")))
        case Op.SPLIT:
            parts = _as_str(a[0]).str.split(str(p.get("separator", ",")))
            parts = parts.list.eval(pl.element().str.strip_chars()).list.eval(
                pl.element().filter(pl.element() != "")
            )
            if "index" in p:
                return parts.list.get(int(p["index"]), null_on_oob=True)
            return parts
        case Op.MAP_VALUES:
            mapping: dict[str, Any] = {str(k).strip().lower(): v for k, v in p["mapping"].items()}
            key = _as_str(a[0]).str.strip_chars().str.to_lowercase()
            default = p.get("default", None)
            keep = bool(p.get("keep_unmapped", False))
            if p.get("fuzzy"):
                # exact → longest key contained in value → value contained in key
                items = sorted(mapping.items(), key=lambda kv: -len(kv[0]))

                def _fuzzy(v: Any) -> Any:
                    sv = str(v).strip().lower()
                    if sv in mapping:
                        return mapping[sv]
                    for k, out in items:
                        if k and (k in sv or (len(sv) >= 3 and sv in k)):
                            return out
                    return None

                base = _py_map(key, _fuzzy, _pl_type_of(list(mapping.values())))
            else:
                base = key.replace_strict(
                    mapping, default=None, return_dtype=_pl_type_of(list(mapping.values()))
                )
            if keep:
                return pl.coalesce([base, _as_str(a[0])])
            return base.fill_null(default) if default is not None else base
        case Op.LOOKUP:
            table = p.get("table")
            if isinstance(table, dict):
                m = {str(k).strip().lower(): v for k, v in table.items()}
                key = _as_str(a[0]).str.strip_chars().str.to_lowercase()
                return key.replace_strict(
                    m, default=p.get("default"), return_dtype=_pl_type_of(list(m.values()))
                )
            lk = ctx.lookups.get(str(p.get("source")))
            if lk is None:
                raise CompileError(f"lookup source {p.get('source')!r} not available")
            m = {str(k).strip().lower(): v for k, v in lk.items()}
            key = _as_str(a[0]).str.strip_chars().str.to_lowercase()
            return key.replace_strict(m, default=p.get("default"), return_dtype=_pl_type_of(list(m.values())))
        case Op.PARSE_NUMBER_TH:
            x = _as_num(a[0])
            if p.get("percent_to_fraction"):
                x = x / 100.0
            return x
        case Op.PARSE_DATE_TH:
            formats = p.get("formats")
            assume = str(p.get("assume_year", "auto"))
            dayfirst = bool(p.get("dayfirst", True))
            return _parse_date(a[0], formats=formats, assume=assume, dayfirst=dayfirst)
        case Op.PARSE_BOOL:
            return _py_map(a[0], thai.parse_bool_py, pl.Boolean)
        case Op.VAT_ADJUST:
            rate = float(p.get("rate", 0.07))
            x = _as_num(a[0])
            if p["mode"] == "add":
                return (x * (1 + rate)).round(4)
            return (x / (1 + rate)).round(4)
        case Op.UNIT_NORMALIZE:
            default = p.get("default")
            extra = p.get("synonyms") or {}
            if not thai_vec.ENABLED:
                return _py_map(
                    a[0], lambda v: thai.unit_normalize_py(v, default=default, extra=extra), pl.String
                )
            return thai_vec.unit_normalize_expr(a[0], default=default, extra=extra)
        case Op.PACK_SIZE:
            if not thai_vec.ENABLED:
                return _py_map(a[0], thai.pack_size_py, pl.Int64)
            return thai_vec.pack_size_expr(a[0])
        case Op.ADD:
            out = _as_num(a[0])
            for x in a[1:]:
                out = out + _as_num(x)
            return out
        case Op.SUBTRACT:
            return _as_num(a[0]) - _as_num(a[1])
        case Op.MULTIPLY:
            out = _as_num(a[0])
            for x in a[1:]:
                out = out * _as_num(x)
            return out
        case Op.DIVIDE:
            d = _as_num(a[1])
            return pl.when(d == 0).then(None).otherwise(_as_num(a[0]) / d)
        case Op.ROUND:
            return _as_num(a[0]).round(int(p.get("decimals", 2)))
        case Op.CONDITIONAL:
            cond = a[0].cast(pl.Boolean, strict=False).fill_null(False)
            then = a[1]
            otherwise = a[2] if len(a) > 2 else pl.lit(None)
            return pl.when(cond).then(then).otherwise(otherwise)
        case Op.DEFAULT:
            return a[0].fill_null(pl.lit(p.get("value")))
        case Op.NULL_IF:
            vals = [str(v).strip().lower() for v in p.get("values", ["", "-", "n/a", "null", "none", "0"])]
            s = _as_str(a[0])
            return pl.when(s.str.strip_chars().str.to_lowercase().is_in(vals)).then(None).otherwise(a[0])
        case Op.TO_LIST:
            return pl.concat_list([_as_str(x) for x in a]).list.drop_nulls()
        case Op.EQ:
            return _cmp(a[0], a[1], "eq")
        case Op.NEQ:
            return _cmp(a[0], a[1], "neq")
        case Op.GT:
            return _as_num(a[0]) > _as_num(a[1])
        case Op.GTE:
            return _as_num(a[0]) >= _as_num(a[1])
        case Op.LT:
            return _as_num(a[0]) < _as_num(a[1])
        case Op.LTE:
            return _as_num(a[0]) <= _as_num(a[1])
        case Op.IS_NULL:
            return a[0].is_null()
        case Op.NOT_NULL:
            return a[0].is_not_null()
        case Op.AND:
            out = a[0].cast(pl.Boolean, strict=False).fill_null(False)
            for x in a[1:]:
                out = out & x.cast(pl.Boolean, strict=False).fill_null(False)
            return out
        case Op.OR:
            out = a[0].cast(pl.Boolean, strict=False).fill_null(False)
            for x in a[1:]:
                out = out | x.cast(pl.Boolean, strict=False).fill_null(False)
            return out
        case Op.NOT:
            return ~a[0].cast(pl.Boolean, strict=False).fill_null(False)
        case Op.CONTAINS:
            return (
                _as_str(a[0])
                .str.contains(str(p["pattern"]), literal=bool(p.get("literal", False)))
                .fill_null(False)
            )
        case Op.STARTS_WITH:
            return _as_str(a[0]).str.starts_with(str(p["pattern"])).fill_null(False)
        case Op.IN:
            vals = [str(v).strip().lower() for v in p["values"]]
            return _as_str(a[0]).str.strip_chars().str.to_lowercase().is_in(vals).fill_null(False)
    raise CompileError(f"op {op} has no compiler branch")


def _parse_date(x: pl.Expr, *, formats: list[str] | None, assume: str, dayfirst: bool) -> pl.Expr:
    """Dates stay on the Python reference parser: it is regex-based and measured as fast as a Polars
    fast path (docs/PERFORMANCE.md), so one implementation is kept (ADR-007)."""

    def _py(v: Any) -> Any:
        return thai.parse_date_th_py(v, formats=formats, assume_year=assume, dayfirst=dayfirst)

    return _py_map(x, _py, pl.Date)


def _cmp(x: pl.Expr, y: pl.Expr, kind: str) -> pl.Expr:
    sx, sy = _as_str(x).str.strip_chars().str.to_lowercase(), _as_str(y).str.strip_chars().str.to_lowercase()
    out = sx == sy if kind == "eq" else sx != sy
    return out.fill_null(False)


def _pl_type_of(values: list[Any]) -> pl.DataType:
    vals = [v for v in values if v is not None]
    if vals and all(isinstance(v, bool) for v in vals):
        return pl.Boolean()
    if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return pl.Int64()
    if vals and all(isinstance(v, int | float) and not isinstance(v, bool) for v in vals):
        return pl.Float64()
    return pl.String()


def _py_map(x: pl.Expr, fn: Any, dtype: Any) -> pl.Expr:
    def _f(v: Any) -> Any:
        try:
            return fn(v)
        except Exception:
            return None

    return x.map_elements(_f, return_dtype=dtype, skip_nulls=True)


def python_value(value: Any) -> Any:
    """Make a Polars scalar JSON-friendly."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value
