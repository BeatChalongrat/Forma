"""Local-disk storage: the dev default and the single-node layout FORMA always had (ADR-012)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from forma.storage.base import ObjectInfo, validate_key


class LocalStorage:
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / validate_key(key)

    def put(self, key: str, src: Path | bytes) -> ObjectInfo:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        try:
            if isinstance(src, bytes):
                tmp.write_bytes(src)
            else:
                shutil.copy2(src, tmp)
            tmp.replace(dest)  # atomic publish
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return ObjectInfo(key, dest.stat().st_size)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def open(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        p.unlink(missing_ok=True)
        # prune empty parents (content-addressed folders), never above the root
        parent = p.parent
        while parent != self.root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        base = self.root / prefix if prefix else self.root
        if not base.exists():
            return
        for p in sorted(base.rglob("*")):
            if p.is_file() and not p.name.endswith(".tmp"):
                yield ObjectInfo(p.relative_to(self.root).as_posix(), p.stat().st_size)

    def url(self, key: str, *, expires_s: int = 900, filename: str | None = None) -> str | None:
        return None  # no pre-signed URLs: the API streams the object

    def local_path(self, key: str) -> Path:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p

    def healthy(self) -> bool:
        try:
            probe = self.root / ".forma-write-probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            return True
        except OSError:
            return False
