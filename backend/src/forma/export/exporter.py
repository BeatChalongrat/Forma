"""Export validated records as JSON / JSONL / CSV (flattened per schema output variant).

Streaming by design: ``iter_export`` yields bytes from any iterable of records, and ``write_export``
takes a *factory* of iterables so CSV can do its two passes (columns, then rows) without ever holding
the dataset in memory. ``export_file`` keeps the old list-based signature.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import orjson

from forma.schema_registry import TargetSchema

FORMATS = ("json", "jsonl", "csv")
MEDIA_TYPES = {"json": "application/json", "jsonl": "application/x-ndjson", "csv": "text/csv"}
BOM = "﻿"  # Excel opens Thai text correctly only with a BOM


def export_bytes(records: list[dict[str, Any]], fmt: str, schema: TargetSchema) -> bytes:
    return b"".join(iter_export(lambda: records, fmt, schema))


def iter_export(
    records: Callable[[], Iterable[dict[str, Any]]], fmt: str, schema: TargetSchema
) -> Iterator[bytes]:
    """Stream an export. ``records`` is called once (json/jsonl) or twice (csv: header pass + row pass)."""
    if fmt == "json":
        yield b"[\n"
        first = True
        for r in records():
            if not first:
                yield b",\n"
            first = False
            yield orjson.dumps(r, option=orjson.OPT_INDENT_2)
        yield b"\n]" if not first else b"]"
        return
    if fmt == "jsonl":
        for r in records():
            yield orjson.dumps(r) + b"\n"
        return
    if fmt == "csv":
        yield from _iter_csv(records, schema)
        return
    raise ValueError(f"unsupported export format {fmt!r}")


def write_export(
    records: Callable[[], Iterable[dict[str, Any]]], fmt: str, schema: TargetSchema, dest: Path
) -> tuple[Path, str, int]:
    """Atomic publish: stream to ``dest.tmp``, rename on success. Returns (path, sha256, bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as fh:
            for chunk in iter_export(records, fmt, schema):
                fh.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest, digest.hexdigest(), size


def export_file(records: list[dict[str, Any]], fmt: str, schema: TargetSchema, dest: Path) -> Path:
    """Atomic publish from an in-memory list (CLI and tests)."""
    return write_export(lambda: records, fmt, schema, dest)[0]


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


def _iter_csv(records: Callable[[], Iterable[dict[str, Any]]], schema: TargetSchema) -> Iterator[bytes]:
    variant = schema.output_variants.get("csv", {})
    explode = variant.get("explode")
    join = variant.get("array_join", "|")
    # pass 1: column order = first-seen order across all rows (identical to the old in-memory output)
    columns: list[str] = []
    seen: set[str] = set()
    for rec in records():
        for row in flatten_record(rec, explode=explode, array_join=join):
            for k in row:
                if k not in seen:
                    seen.add(k)
                    columns.append(k)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    yield (BOM + buf.getvalue()).encode("utf-8")
    # pass 2: rows, flushed every few thousand lines
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    n = 0
    for rec in records():
        for row in flatten_record(rec, explode=explode, array_join=join):
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
            n += 1
            if n % 2000 == 0:
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate(0)
    if buf.tell():
        yield buf.getvalue().encode("utf-8")
