from __future__ import annotations

from typing import Any


class FormaError(Exception):
    """Base class. ``code`` is machine-readable; ``details`` is safe to return to clients."""

    code = "forma_error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


class NotFound(FormaError):
    code = "not_found"
    http_status = 404


class ValidationFailed(FormaError):
    code = "validation_failed"
    http_status = 422


class Conflict(FormaError):
    code = "conflict"
    http_status = 409


class UnsupportedFile(FormaError):
    code = "unsupported_file"
    http_status = 415


class PolicyBlocked(FormaError):
    code = "policy_blocked"
    http_status = 403


class PayloadTooLarge(FormaError):
    code = "payload_too_large"
    http_status = 413


class QueueFull(FormaError):
    """Backpressure: the job queue is at capacity. Carries ``Retry-After``."""

    code = "queue_full"
    http_status = 429

    def __init__(
        self, message: str, *, retry_after_s: int = 5, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details, headers={"Retry-After": str(retry_after_s)})


class RateLimited(FormaError):
    code = "rate_limited"
    http_status = 429

    def __init__(self, message: str, *, retry_after_s: int = 60) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after_s)})


class Unauthorized(FormaError):
    code = "unauthorized"
    http_status = 401


class Forbidden(FormaError):
    code = "forbidden"
    http_status = 403


class ServiceUnavailable(FormaError):
    code = "service_unavailable"
    http_status = 503
