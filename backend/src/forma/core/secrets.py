"""Encryption for secrets saved from the web app (ADR-017), e.g. an LLM API key.

Values are Fernet tokens (AES-128-CBC + HMAC) keyed from ``FORMA_SECRET_KEY``. In dev, when the variable
is unset, a random key is generated once into ``<data>/.secret_key`` so the zero-config loop keeps working;
in prod the variable is required to store a secret. The plaintext never leaves the API: reads return a
masked hint only.
"""

from __future__ import annotations

import base64
import hashlib
import os

import structlog
from cryptography.fernet import Fernet, InvalidToken

from forma.core.errors import ValidationFailed
from forma.core.settings import Settings

log = structlog.get_logger(__name__)
_KEY_FILE = ".secret_key"


def _fernet(s: Settings) -> Fernet:
    if s.secret_key:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()))
    if s.is_prod:
        raise ValidationFailed(
            "FORMA_SECRET_KEY is not set: secrets cannot be stored in production without it",
            details={"setting": "FORMA_SECRET_KEY"},
        )
    path = s.data_path / _KEY_FILE
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        os.chmod(path, 0o600)
    return Fernet(path.read_bytes().strip())


def encrypt(s: Settings, plaintext: str) -> str:
    return _fernet(s).encrypt(plaintext.encode()).decode()


def decrypt(s: Settings, token: str) -> str | None:
    """``None`` when the token was made with another key (FORMA_SECRET_KEY changed): the secret must be
    entered again, it is never guessed."""
    try:
        return _fernet(s).decrypt(token.encode()).decode()
    except (InvalidToken, ValidationFailed, ValueError):
        log.warning("secrets.unreadable", hint="re-enter the secret in Settings")
        return None


def hint(plaintext: str | None) -> str | None:
    """What the UI may show of a secret: its last four characters."""
    if not plaintext:
        return None
    return f"…{plaintext[-4:]}" if len(plaintext) > 8 else "…"
