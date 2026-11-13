"""Regression coverage for numeric DSL composition that previously exhausted worker RAM."""

import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import psutil
import pytest

from forma.engine.ops import CompileContext, compile_expr
from forma.mapping_dsl.models import Expr, call, col, const


def evaluate(frame: pl.DataFrame, expr: Expr, *, streaming: bool = False) -> list[Any]:
    ctx = CompileContext(default_source="s", columns=set(frame.columns))
    query = frame.lazy().select(compile_expr(expr, ctx).alias("result"))
    return query.collect(engine="streaming" if streaming else "auto")["result"].to_list()


@pytest.mark.parametrize("streaming", [False, True])
def test_parse_then_arithmetic_preserves_locale_nulls_and_literal_broadcast(streaming: bool) -> None:
    frame = pl.DataFrame(
        {"s.qty": ["๑๙", "1.500,50", "(20)", None, "bad", "200.00-"], "s.reserved": ["1"] * 6}
    )
    parsed = call("parse_number_th", col("qty"))
    difference = call("subtract", parsed, call("parse_number_th", col("reserved")))
    expr = call("divide", call("multiply", difference, const("2")), const(2))
    assert evaluate(frame, expr, streaming=streaming) == [18, 1499.5, -21, None, None, -201]


def test_nested_rounding_vat_and_cast_keep_numeric_intermediates() -> None:
    frame = pl.DataFrame({"s.amount": ["1,000", None]})
    value = call("parse_number_th", col("amount"))
    for _ in range(8):
        value = call("round", call("add", value, const("1")), decimals=2)
    value = call("cast", call("vat_adjust", value, mode="add", rate=0.07), to="float")
    assert evaluate(frame, value) == [1078.56, None]


@pytest.mark.parametrize("streaming", [False, True])
def test_per_row_unit_conversion_beyond_initial_sample(streaming: bool) -> None:
    frame = pl.DataFrame(
        {"s.qty": ["10"] * 620, "s.unit": ["PCS"] * 600 + ["CTN"] * 20, "s.factor": ["6"] * 620}
    )
    qty = call("parse_number_th", col("qty"))
    expr = call(
        "conditional",
        call("eq", col("unit"), const("CTN")),
        call("multiply", qty, call("parse_number_th", col("factor"))),
        qty,
    )
    assert evaluate(frame, expr, streaming=streaming) == [10.0] * 600 + [60.0] * 20


def test_formula_fallback_zero_division_and_empty_frames() -> None:
    frame = pl.DataFrame(
        {"s.available": ["18", None, "bad"], "s.on_hand": ["19", "26", "3"], "s.reserved": ["1", "2", "3"]}
    )
    difference = call(
        "subtract", call("parse_number_th", col("on_hand")), call("parse_number_th", col("reserved"))
    )
    value = call("coalesce", call("parse_number_th", col("available")), difference)
    assert evaluate(frame, value) == [18, 24, 0]
    assert evaluate(frame, call("divide", const(12), value)) == [12 / 18, 0.5, None]
    assert evaluate(frame.clear(), value) == []


def test_small_numeric_values_decimal_and_nonfinite_values() -> None:
    expr = call("multiply", call("parse_number_th", col("v")), const(2))
    assert evaluate(
        pl.DataFrame({"s.v": [1e-12, 1e20, float("nan"), float("inf"), -float("inf"), None]}), expr
    ) == [2e-12, 2e20, None, None, None, None]
    assert evaluate(pl.DataFrame({"s.v": [Decimal("1.25"), None]}), expr) == [2.5, None]


def test_numeric_constant_fields_broadcast_to_all_records() -> None:
    frame = pl.DataFrame({"s.id": ["a", "b", "c"]})
    ctx = CompileContext(default_source="s", columns=set(frame.columns))
    value = compile_expr(call("parse_number_th", const("1.500,50")), ctx)
    assert frame.with_columns(value.alias("amount"))["amount"].to_list() == [1500.5] * 3


def test_composed_expression_in_grouped_aggregation_and_comparison() -> None:
    frame = pl.DataFrame(
        {"warehouse": ["A", "A", "B"], "s.qty": ["10", "20", "5"], "s.reserved": ["1", "2", "1"]}
    )
    expr = call("subtract", call("parse_number_th", col("qty")), call("parse_number_th", col("reserved")))
    ctx = CompileContext(default_source="s", columns=set(frame.columns))
    assert frame.group_by("warehouse").agg(compile_expr(expr, ctx).sum().alias("n")).sort("warehouse")[
        "n"
    ].to_list() == [27, 4]
    assert evaluate(frame, call("gt", expr, const(5))) == [True, True, False]


def test_nested_numeric_execution_stays_within_resource_budget() -> None:
    # Isolate the failure: a regression must fail this test, not OOM the whole pytest runner.
    code = """
import json
import polars as pl
from forma.engine.ops import CompileContext, compile_expr
from forma.mapping_dsl.models import call, col, const
frame = pl.DataFrame({'s.qty': ['19'] * 100_000, 's.reserved': ['1'] * 100_000})
expr = call('subtract', call('parse_number_th', col('qty')), call('parse_number_th', col('reserved')))
for _ in range(12):
    expr = call('add', call('parse_number_th', expr), const(1))
ctx = CompileContext('s', set(frame.columns))
out = frame.lazy().select(compile_expr(expr, ctx).alias('result')).collect(engine='streaming')['result']
assert len(out) == 100_000 and out.min() == out.max() == 30.0
print(json.dumps({'rows': len(out), 'value': out[0]}))
"""
    env = {**os.environ, "POLARS_MAX_THREADS": "2"}
    env["PYTHONPATH"] = (
        str(Path(__file__).resolve().parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    with subprocess.Popen(
        [sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as child:
        process = psutil.Process(child.pid)
        deadline = time.monotonic() + 45
        peak = 0
        try:
            while child.poll() is None:
                try:
                    peak = max(peak, process.memory_info().rss)
                except psutil.NoSuchProcess:
                    break
                assert peak < 512 * 1024**2, f"Numeric expression exceeded 512 MiB: {peak} bytes"
                assert time.monotonic() < deadline, "Numeric composition exceeded 45 seconds"
                time.sleep(0.02)
            stdout, stderr = child.communicate(timeout=5)
            assert child.returncode == 0, stderr
            assert json.loads(stdout) == {"rows": 100_000, "value": 30.0}
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
