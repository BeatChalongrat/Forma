"""Immutable source-file store (content-addressed). Uploaded files are never modified."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from forma.core.hashing import sha256_file


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    path: Path
    original_name: str
    size: int
    duplicate: bool


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, src: Path, original_name: str | None = None) -> StoredFile:
        name = original_name or src.name
        digest = sha256_file(src)
        folder = self.root / digest[:2] / digest
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / _safe_name(name)
        duplicate = dest.exists()
        if not duplicate:
            shutil.copy2(src, dest)
        return StoredFile(digest, dest, name, dest.stat().st_size, duplicate)

    def put_bytes(self, data: bytes, original_name: str) -> StoredFile:
        tmp = self.root / "_incoming"
        tmp.mkdir(exist_ok=True)
        p = tmp / _safe_name(original_name)
        p.write_bytes(data)
        try:
            return self.put(p, original_name)
        finally:
            p.unlink(missing_ok=True)

    def get(self, sha256: str) -> Path:
        folder = self.root / sha256[:2] / sha256
        files = [p for p in folder.iterdir() if p.is_file()] if folder.exists() else []
        if not files:
            raise FileNotFoundError(sha256)
        return files[0]


def _safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._- ()[]" or ord(ch) > 127 else "_" for ch in name)
    return keep.strip() or "file"
