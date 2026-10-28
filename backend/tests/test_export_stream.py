"""Streaming exports: identical bytes to the in-memory path, atomic publish, no second materialisation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import orjson
import pytest

from forma.export.exporter import export_bytes, export_file, iter_export, write_export


def _records(n: int = 2500) -> list[dict[str, Any]]:
    return [
        {
            "sku": f"A{i:05d}",
            "product_name": f"สินค้า {i}",
            "prices": [{"price_list": "retail", "amount": float(i), "vat_included": True}],
            "attributes": {"x": i} if i % 7 == 0 else {},
            "source_meta": {"file": "f.xlsx"},
        }
        for i in range(n)
    ]


@pytest.mark.parametrize("fmt", ["json", "jsonl", "csv"])
def test_stream_matches_in_memory_bytes(schema: Any, fmt: str) -> None:
    recs = _records()
    streamed = b"".join(iter_export(lambda: recs, fmt, schema))
    assert streamed == export_bytes(recs, fmt, schema)
    if fmt == "json":
        assert json.loads(streamed) == recs
    if fmt == "jsonl":
        assert [orjson.loads(line) for line in streamed.splitlines()] == recs
    if fmt == "csv":
        text = streamed.decode("utf-8")
        assert text.startswith("﻿")
        header = text.splitlines()[0]
        assert "sku" in header and "source_meta" not in header
        assert "attributes.x" in header  # columns discovered across ALL rows, not just the first


def test_write_export_is_atomic_and_hashes_while_writing(schema: Any, tmp_path: Path) -> None:
    recs = _records(300)
    dest = tmp_path / "out" / "x.jsonl"
    path, digest, size = write_export(lambda: iter(recs), "jsonl", schema, dest)
    assert path == dest and dest.exists() and not dest.with_suffix(".jsonl.tmp").exists()
    assert digest == hashlib.sha256(dest.read_bytes()).hexdigest() and size == dest.stat().st_size

    def boom() -> Any:
        yield recs[0]
        raise RuntimeError("disk on fire")

    with pytest.raises(RuntimeError):
        write_export(boom, "jsonl", schema, tmp_path / "out" / "y.jsonl")
    assert not (tmp_path / "out" / "y.jsonl").exists() and not (tmp_path / "out" / "y.jsonl.tmp").exists()


def test_csv_factory_is_called_twice_and_json_once(schema: Any) -> None:
    calls = {"n": 0}

    def factory() -> Any:
        calls["n"] += 1
        return iter(_records(10))

    b"".join(iter_export(factory, "csv", schema))
    assert calls["n"] == 2
    calls["n"] = 0
    b"".join(iter_export(factory, "json", schema))
    assert calls["n"] == 1
    with pytest.raises(ValueError):
        list(iter_export(factory, "parquet", schema))


def test_export_file_empty_json(schema: Any, tmp_path: Path) -> None:
    p = export_file([], "json", schema, tmp_path / "e.json")
    assert json.loads(p.read_text(encoding="utf-8")) == []
