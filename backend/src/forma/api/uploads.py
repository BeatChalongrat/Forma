"""Bound file materialization independently of browser batching and reverse-proxy limits."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from io import BytesIO
from threading import BoundedSemaphore

from fastapi import UploadFile
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from forma.core.errors import FormaError, PayloadTooLarge, QueueFull, ValidationFailed
from forma.core.settings import Settings, get_settings


@lru_cache(maxsize=8)
def _slots(count: int) -> BoundedSemaphore:
    return BoundedSemaphore(count)


def upload_slot() -> Generator[None, None, None]:
    slot = _slots(get_settings().upload_concurrency)
    if not slot.acquire(blocking=False):
        raise QueueFull("กำลังรับไฟล์จากคำขออื่น กรุณารอสักครู่แล้วลองอัปโหลดอีกครั้ง")
    try:
        yield
    finally:
        slot.release()


class UploadAdmission:
    """Limit multipart requests before the parser allocates in-memory files or spools to disk."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = Headers(scope=scope) if scope["type"] == "http" else None
        if headers is None or not headers.get("content-type", "").lower().startswith("multipart/form-data"):
            await self.app(scope, receive, send)
            return
        # File payloads have a stricter limit below; reserve 1 MiB for multipart headers/boundaries.
        limit = (get_settings().max_upload_request_mb + 1) * 1024 * 1024
        slot = upload_slot()
        error: FormaError | None = None
        try:
            if int(headers.get("content-length", "0")) > limit:
                raise PayloadTooLarge("ชุดไฟล์ใหญ่เกินกำหนด กรุณาแบ่งอัปโหลดเป็นชุดเล็กลง")
            next(slot)
        except FormaError as exc:
            error = exc
        except ValueError:
            error = ValidationFailed("Invalid Content-Length")
        if error is not None:
            await JSONResponse(
                {"error": error.code, "message": error.message, "details": error.details},
                status_code=error.http_status,
                headers=error.headers,
            )(scope, receive, send)
            return
        size = 0
        exceeded = False

        async def bounded_receive() -> Message:
            nonlocal size, exceeded
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    exceeded = True
                    # Starlette closes every multipart spool on this exception.
                    raise MultiPartException("Upload request exceeds the configured limit")
            return message

        async def bounded_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
            if exceeded:
                await JSONResponse(
                    {
                        "error": "payload_too_large",
                        "message": "ชุดไฟล์ใหญ่เกินกำหนด กรุณาแบ่งอัปโหลดเป็นชุดเล็กลง",
                        "details": {},
                    },
                    status_code=413,
                )(scope, receive, send)
        finally:
            slot.close()


async def read_upload(up: UploadFile, limit_mb: int, *, remaining: int | None = None) -> bytes:
    limit = limit_mb * 1024 * 1024
    if remaining is not None:
        limit = min(limit, remaining)
    if up.size is not None and up.size > limit:
        raise PayloadTooLarge(
            f"ไฟล์ใหญ่เกินพื้นที่รับที่เหลือ {limit / 1024 / 1024:g} MiB กรุณาแบ่งอัปโหลดเป็นชุดเล็กลง",
            details={"file": up.filename, "size": up.size, "limit_bytes": limit},
        )
    size = 0
    # BytesIO.getvalue shares its immutable buffer on CPython. Joining retained chunks
    # instead temporarily needs two copies of the entire file, on top of multipart spools.
    try:
        with BytesIO() as buffer:
            while True:
                chunk = await up.read(min(1024 * 1024, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise PayloadTooLarge(
                        f"ไฟล์ใหญ่เกินพื้นที่รับที่เหลือ {limit / 1024 / 1024:g} MiB กรุณาแบ่งอัปโหลดเป็นชุดเล็กลง",
                        details={"file": up.filename, "limit_bytes": limit},
                    )
                buffer.write(chunk)
            if not size:
                raise ValidationFailed(f"{up.filename} is empty")
            return buffer.getvalue()
    finally:
        # A parsed small file still lives in RAM until closed; don't retain both copies
        # for every file in a batch. FastAPI's later close is idempotent.
        await up.close()


async def read_uploads(files: list[UploadFile], settings: Settings) -> list[tuple[str, bytes]]:
    remaining = settings.max_upload_request_mb * 1024 * 1024
    if len(files) > settings.max_upload_files or sum(f.size or 0 for f in files) > remaining:
        raise PayloadTooLarge(
            f"รับได้ไม่เกิน {settings.max_upload_files} ไฟล์ รวม {settings.max_upload_request_mb} MiB ต่อครั้ง "
            "กรุณาแบ่งอัปโหลดเข้ารอบเดิม",
            details={"limit_bytes": remaining, "limit_files": settings.max_upload_files},
        )
    result = []
    for file in files:
        data = await read_upload(file, settings.max_upload_mb, remaining=remaining)
        remaining -= len(data)
        result.append((file.filename or "upload.xlsx", data))
    return result
