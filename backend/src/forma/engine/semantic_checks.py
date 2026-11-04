"""Deterministic semantic risks from actual source/output pairs, independent of model confidence."""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from forma.mapping_dsl.models import CallExpr, ColExpr, Expr, MappingDocument
from forma.schema_registry import TargetSchema


def column_refs(expr: Expr, default: str) -> list[str]:
    if isinstance(expr, ColExpr):
        return [f"{expr.source or default}.{expr.name}"]
    if isinstance(expr, CallExpr):
        return list(dict.fromkeys(ref for arg in expr.args for ref in column_refs(arg, default)))
    return []


def identity_warnings(
    output: pl.DataFrame, mapping: MappingDocument, schema: TargetSchema
) -> list[dict[str, Any]]:
    """Check row-aligned root identities before dedupe hides collisions. Warning, not invented policy.

    Only one-source identities are compared. Composite/lookup identities are explicitly outside this
    check; the pinned schema and existing join/contract checks still apply to them.
    """
    findings = []
    for fm in mapping.fields:
        if fm.target not in schema.identity_keys or fm.target not in output.columns:
            continue
        refs = column_refs(fm.expr, mapping.record.root_source)
        if len(refs) != 1 or refs[0] not in output.columns:
            continue
        affected = 0
        examples: list[dict[str, str]] = []
        for source, value in output.select(refs[0], fm.target).iter_rows():
            raw = str(source).strip() if source is not None else ""
            text = str(value).strip() if value is not None else ""
            if (
                re.fullmatch(r"0\d+", raw)
                and text != raw
                and re.fullmatch(r"\d+", text)
                and raw.lstrip("0") == text.lstrip("0")
            ):
                affected += 1
                if len(examples) < 3:
                    examples.append({"source": raw[:200], "output": text[:200]})
        if affected:
            findings.append(
                {
                    "target": fm.target,
                    "message": (
                        f"{fm.target}: leading zeros removed from {affected} source identifiers; "
                        "verify the target contract explicitly requires this change"
                    ),
                    "details": {
                        "source_column": refs[0],
                        "affected_rows": affected,
                        "examples": examples,
                        "check": "identity_leading_zeros",
                    },
                }
            )
    return findings
