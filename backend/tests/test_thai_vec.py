"""The vectorised Thai rules must agree with the Python reference on every input (ADR-007)."""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from forma.engine import thai, thai_vec
from forma.engine.ops import CompileContext, compile_expr
from forma.mapping_dsl.models import CallExpr, ColExpr, Op

DATES = [
    "2026-01-05",
    "2569-01-05",
    "2026-01-05 10:30",
    "2026-01-05T10:30:00",
    "05/01/2569",
    "5/1/2569",
    "5-1-2026",
    "05.01.2026",
    "13/5/2026",
    "5/13/2026",  # month > 12 → swapped
    "31/02/2569",  # impossible → None
    "1/10/2569 09:00",
    "1/10/69",  # two-digit year → Python path
    "31 ต.ค. 69",
    "15 มกราคม 2569",
    "15 Jan 2026",
    "45000",  # excel serial
    "๐๕/๐๑/๒๕๖๙",
    "x",
    "",
    "  ",
    "2026/01/05",
    "20260105",
    None,
]
PACKS = [
    "1x12",
    "12 x 1",
    "ลัง(24)",
    "24 ชิ้น/ลัง",
    "12's",
    "pack 6",
    "แพ็ค: 10",
    "ctn=48",
    "12",
    "0",
    "ชิ้น",
    "",
    None,
    "3*4",
    "๑๒",
    "box 5 x 2",
]
UNITS = [
    "pcs",
    "PCS",
    "ชิ้น",
    "ลัง(12)",
    "12 ชิ้น/ลัง",
    "แพ็ค 6",
    "kg.",
    "กก.",
    "Carton",
    "ขวด",
    "",
    None,
    "zzz",
    "BOX",
    "dz",
    "12",
    "1x12 ชิ้น",
    "unit",
]


def _run(values: list[object], expr: pl.Expr) -> list[object]:
    df = pl.DataFrame({"v": values}, schema={"v": pl.String})
    return df.select(expr.alias("o"))["o"].to_list()


def test_parse_date_op_equals_python_reference() -> None:
    expr = CallExpr(op=Op.PARSE_DATE_TH, args=[ColExpr(name="v")], params={})
    ctx = CompileContext(default_source="s", columns={"s.v"})
    df = pl.DataFrame({"s.v": DATES}, schema={"s.v": pl.String})
    got = df.select(compile_expr(expr, ctx).alias("o"))["o"].to_list()
    want = [thai.parse_date_th_py(v) for v in DATES]
    assert got == want
    assert got[0] == dt.date(2026, 1, 5) and got[1] == dt.date(2026, 1, 5) and got[10] is None
    assert got[12] == dt.date(2026, 10, 1)  # two-digit BE year via the Python fallback
    # explicit formats keep the Python path
    expr2 = CallExpr(op=Op.PARSE_DATE_TH, args=[ColExpr(name="v")], params={"formats": ["%Y%m%d"]})
    got2 = df.select(compile_expr(expr2, ctx).alias("o"))["o"].to_list()
    assert got2[22] == dt.date(2026, 1, 5)


def test_pack_size_expr_equals_python() -> None:
    got = _run(PACKS, thai_vec.pack_size_expr(pl.col("v")))
    want = [thai.pack_size_py(v) for v in PACKS]
    assert got == want, list(zip(PACKS, got, want, strict=True))
    assert got[0] == 12 and got[2] == 24 and got[9] is None


@pytest.mark.parametrize("default", [None, "PCS"])
def test_unit_normalize_expr_equals_python(default: str | None) -> None:
    extra = {"ถาด": "TRAY"}
    got = _run([*UNITS, "ถาด"], thai_vec.unit_normalize_expr(pl.col("v"), default=default, extra=extra))
    want = [thai.unit_normalize_py(v, default=default, extra=extra) for v in [*UNITS, "ถาด"]]
    assert got == want, list(zip([*UNITS, "ถาด"], got, want, strict=True))
    assert got[-1] == "TRAY"
