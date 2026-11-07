"""Only bundled, reviewed resources can be loaded; the model cannot supply paths or scripts."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any, cast

from forma.core.errors import ValidationFailed


def catalog() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(files("forma.reporting").joinpath("catalog.json").read_text(encoding="utf-8")),
    )


def skill(name: str) -> dict[str, Any]:
    item = next((s for s in catalog()["skills"] if s["id"] == name), None)
    if not item:
        raise ValidationFailed("Unknown report skill")
    content = files("forma.reporting").joinpath(item["file"]).read_text(encoding="utf-8")
    return {
        "id": name,
        "version": catalog()["version"],
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "instructions": content,
    }


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
