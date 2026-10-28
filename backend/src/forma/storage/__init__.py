"""Storage backends (ADR-012): ``FORMA_STORAGE=local`` (default) | ``s3`` (AWS S3 or MinIO)."""

from __future__ import annotations

from forma.core.settings import Settings
from forma.storage.base import ObjectInfo, Storage, key_from_legacy_path, safe_name, validate_key
from forma.storage.local import LocalStorage
from forma.storage.s3 import S3Storage


def make_storage(settings: Settings) -> Storage:
    if settings.storage == "local":
        return LocalStorage(settings.data_path)
    if settings.storage == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            cache_dir=settings.data_path / "cache",
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            presign_s=settings.s3_presign_s,
        )
    raise ValueError(f"unknown FORMA_STORAGE {settings.storage!r} (local | s3)")


__all__ = [
    "LocalStorage",
    "ObjectInfo",
    "S3Storage",
    "Storage",
    "key_from_legacy_path",
    "make_storage",
    "safe_name",
    "validate_key",
]
