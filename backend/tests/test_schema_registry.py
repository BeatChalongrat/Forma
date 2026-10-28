"""Registering target schemas (schema_registry/registry.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forma.core.errors import ValidationFailed
from forma.schema_registry import SchemaRegistry


def _doc(**extra: Any) -> dict[str, Any]:
    return {"type": "object", "properties": {"sku": {"type": "string"}}, **extra}


def test_register_keeps_schema_files_inside_the_directory(tmp_path: Path) -> None:
    reg = SchemaRegistry(tmp_path / "schemas")
    # the id becomes a file name, so anything that could leave the directory is refused
    for bad in ("../../evil", "a/b", "..", "UPPER CASE", "x" * 65):
        with pytest.raises(ValidationFailed):
            reg.register(_doc(**{"x-forma": {"schema_id": bad}}))
    with pytest.raises(ValidationFailed):
        reg.register(_doc(**{"x-forma": {"schema_id": "ok", "version": -1}}))
    assert not list(tmp_path.rglob("*.json"))
    # without an explicit id the title is slugified
    s = reg.register(_doc(title="Price List (TH)"))
    assert s.schema_id == "price_list_th" and s.path == tmp_path / "schemas" / "price_list_th.v1.json"
    assert reg.register(_doc(**{"x-forma": {"schema_id": "price_list_th"}})).version == 2
    with pytest.raises(ValidationFailed):
        reg.register(_doc(title="สินค้า"))  # nothing left to slugify → an explicit schema_id is needed
    assert sorted(p.name for p in (tmp_path / "schemas").iterdir()) == [
        "price_list_th.v1.json",
        "price_list_th.v2.json",
    ]
