from __future__ import annotations

from typing import Any


class FormaError(Exception):
    """Base class. ``code`` is machine-readable; ``details`` is safe to return to clients."""

    code = "forma_error"
    http_status = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


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
