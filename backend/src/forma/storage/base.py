"""Object storage protocol (ADR-012). Keys are POSIX-style relative paths (``files/ab/<sha>/<name>``,
``datasets/<customer>/<version>.jsonl``, ``exports/<customer>/<file>``); never absolute paths."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

_UNSAFE = re.compile(r"[^A-Za-z0-9._\-() \[\]\u0080-￿]")


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int


class Storage(Protocol):
    name: str

    def put(self, key: str, src: Path | bytes) -> ObjectInfo: ...
    def get(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def size(self, key: str) -> int: ...
    def list(self, prefix: str) -> Iterator[ObjectInfo]: ...
    def url(self, key: str, *, expires_s: int = 900, filename: str | None = None) -> str | None: ...
    def local_path(self, key: str) -> Path: ...
    def healthy(self) -> bool: ...


def safe_name(name: str) -> str:
    """File name component safe for every backend: no separators, no traversal, no control chars."""
    base = PurePosixPath(name.replace("\\", "/")).name  # drop any directory part (path traversal)
    base = _UNSAFE.sub("_", base).strip(" .")
    return base or "file"


def validate_key(key: str) -> str:
    """Keys are relative, POSIX, without ``..`` segments."""
    if not key or key.startswith(("/", "\\")) or ":" in key.split("/", 1)[0]:
        raise ValueError(f"invalid storage key {key!r}")
    parts = key.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"invalid storage key {key!r}")
    return "/".join(parts)


def key_from_legacy_path(path: str, data_root: Path) -> str | None:
    """Absolute path from a pre-ADR-012 row → storage key, when it lives under the data root."""
    try:
        rel = Path(path).resolve().relative_to(data_root.resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()
