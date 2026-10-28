from __future__ import annotations

from ulid import ULID


def new_id(prefix: str) -> str:
    """Prefixed, time-sortable identifier, e.g. ``imp_01J...``."""
    return f"{prefix}_{ULID()!s}"
