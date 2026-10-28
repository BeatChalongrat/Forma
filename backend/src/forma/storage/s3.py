"""S3 / MinIO storage via boto3 (ADR-012).

Reads that need a real file (fastexcel, Polars) go through ``local_path`` which downloads into a
per-node cache directory; every FORMA key is immutable (content-addressed files, one key per dataset
version / export), so the cache never needs invalidation.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from forma.storage.base import ObjectInfo, validate_key


class S3Storage:
    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        cache_dir: Path,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        presign_s: int = 900,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.cache_dir = cache_dir
        self.presign_s = presign_s
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(
                    s3={"addressing_style": "path"} if endpoint_url else {},
                    retries={"max_attempts": 5},
                ),
            )
        self.client = client

    # ---- helpers --------------------------------------------------------------------------
    def _k(self, key: str) -> str:
        key = validate_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    # ---- protocol -------------------------------------------------------------------------
    def put(self, key: str, src: Path | bytes) -> ObjectInfo:
        k = self._k(key)
        if isinstance(src, bytes):
            self.client.put_object(Bucket=self.bucket, Key=k, Body=src)
            size = len(src)
        else:
            self.client.upload_file(str(src), self.bucket, k)
            size = src.stat().st_size
        cached = self.cache_dir / validate_key(key)
        if cached.exists():
            cached.unlink()
        return ObjectInfo(key, size)

    def get(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"]
        return bytes(body.read())

    def open(self, key: str) -> BinaryIO:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"]  # type: ignore[no-any-return]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._k(key))
        with contextlib.suppress(OSError):
            (self.cache_dir / validate_key(key)).unlink()

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=self._k(key))["ContentLength"])

    def list(self, prefix: str) -> Iterator[ObjectInfo]:
        paginator = self.client.get_paginator("list_objects_v2")
        clean = prefix.replace("\\", "/").lstrip("/")
        full = f"{self.prefix}/{clean}" if self.prefix else clean
        strip = len(self.prefix) + 1 if self.prefix else 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full):
            for obj in page.get("Contents", []):
                yield ObjectInfo(obj["Key"][strip:], int(obj["Size"]))

    def url(self, key: str, *, expires_s: int = 900, filename: str | None = None) -> str | None:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": self._k(key)}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        url = self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_s or self.presign_s
        )
        return str(url)

    def local_path(self, key: str) -> Path:
        dest = self.cache_dir / validate_key(key)
        if dest.is_file():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        try:
            self.client.download_file(self.bucket, self._k(key), str(tmp))
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return dest

    def healthy(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False
