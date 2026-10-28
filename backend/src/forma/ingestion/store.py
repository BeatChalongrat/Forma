"""Immutable source-file store (content-addressed) on top of the Storage protocol (ADR-005, ADR-012).

Key layout: ``files/<sha2>/<sha256>/<safe name>``. Uploaded files are never modified; the same bytes
uploaded twice map to the same key and are stored once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from forma.core.hashing import sha256_file
from forma.storage import LocalStorage, Storage, safe_name

PREFIX = "files"


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    key: str
    path: Path  # local file (the object itself on local storage, a cached copy on S3)
    original_name: str
    size: int
    duplicate: bool


def key_for(sha256: str, original_name: str) -> str:
    return f"{PREFIX}/{sha256[:2]}/{sha256}/{safe_name(original_name)}"


class FileStore:
    def __init__(self, storage: Storage | Path) -> None:
        self.storage: Storage = LocalStorage(storage) if isinstance(storage, Path) else storage

    def put(self, src: Path, original_name: str | None = None) -> StoredFile:
        name = original_name or src.name
        digest = sha256_file(src)
        key = key_for(digest, name)
        duplicate = self.storage.exists(key)
        if not duplicate:
            self.storage.put(key, src)
        return StoredFile(digest, key, self.storage.local_path(key), name, src.stat().st_size, duplicate)

    def put_bytes(self, data: bytes, original_name: str) -> StoredFile:
        digest = hashlib.sha256(data).hexdigest()
        key = key_for(digest, original_name)
        duplicate = self.storage.exists(key)
        if not duplicate:
            self.storage.put(key, data)
        return StoredFile(digest, key, self.storage.local_path(key), original_name, len(data), duplicate)

    def get(self, sha256: str) -> Path:
        objs = list(self.storage.list(f"{PREFIX}/{sha256[:2]}/{sha256}/"))
        if not objs:
            raise FileNotFoundError(sha256)
        return self.storage.local_path(objs[0].key)

    def local_path(self, key: str) -> Path:
        return self.storage.local_path(key)
