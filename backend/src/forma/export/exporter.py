"""Export validated records as JSON / JSONL / CSV (flattened per schema output variant)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import orjson

from forma.schema_registry import TargetSchema

FORMATS = ("json", "jsonl", "csv")


def export_bytes(records: list[dict[str, Any]], fmt: str, schema: TargetSchema) -> bytes:
    if fmt == "json":
        return orjson.dumps(records, option=orjson.OPT_INDENT_2)
    if fmt == "jsonl":
        return b"".join(orjson.dumps(r) + b"\n" for r in records)
    if fmt == "csv":
        return _to_csv(records, schema)
    raise ValueError(f"unsupported export format {fmt!r}")


def export_file(records: list[dict[str, Any]], fmt: str, schema: TargetSchema, dest: Path) -> Path:
    """Atomic publish: write to a temp file, then rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(export_bytes(records, fmt, schema))
    tmp.replace(dest)
    return dest


def flatten_record(
    rec: dict[str, Any], *, explode: str | None, array_join: str = "|"
) -> Iterable[dict[str, Any]]:
    """Flatten nested objects with dotted keys; explode one collection into rows, join the rest."""
    base: dict[str, Any] = {}
    exploded: list[dict[str, Any]] | None = None
    explode_key = explode.lstrip("/") if explode else None

    def walk(obj: Any, prefix: str) -> None:
        nonlocal exploded
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("source_meta",):
                    continue
                walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            if prefix == explode_key and obj and isinstance(obj[0], dict):
                exploded = obj
            elif obj and isinstance(obj[0], dict):
                base[prefix] = array_join.join(orjson.dumps(x).decode() for x in obj)
            else:
                base[prefix] = array_join.join("" if x is None else str(x) for x in obj)
        else:
            base[prefix] = obj

    walk(rec, "")
    if exploded:
        for item in exploded:
            row = dict(base)
            for k, v in item.items():
                row[f"{explode_key}.{k}"] = array_join.join(map(str, v)) if isinstance(v, list) else v
            yield row
    else:
        yield base


def _to_csv(records: list[dict[str, Any]], schema: TargetSchema) -> bytes:
    variant = schema.output_variants.get("csv", {})
    explode = variant.get("explode")
    join = variant.get("array_join", "|")
    rows = [r for rec in records for r in flatten_record(rec, explode=explode, array_join=join)]
    columns: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens Thai text correctly
