"""Readiness checks (ADR-014): each dependency answers ok/detail; the whole thing never raises."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text


def _check(fn: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        detail = fn()
        return {
            "ok": True,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            **({"detail": detail} if detail else {}),
        }
    except Exception as e:
        return {
            "ok": False,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "detail": f"{type(e).__name__}: {e}"[:200],
        }


def readiness() -> dict[str, Any]:
    from forma.db.session import get_engine
    from forma.jobs import runner
    from forma.services.context import get_context

    def db() -> str | None:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        from forma.db import migrate

        st = migrate.status(get_engine())
        if st["pending"]:
            raise RuntimeError(f"migration pending (at {st['current']}, head {st['head']})")
        return None

    def storage() -> str | None:
        ctx = get_context()
        if not ctx.storage.healthy():
            raise RuntimeError(f"{ctx.storage.name} storage not writable/reachable")
        return ctx.storage.name

    def queue() -> str | None:
        stats = runner.get_backend().stats()
        if stats.get("error"):
            raise RuntimeError(str(stats["error"]))
        return str(stats.get("backend"))

    checks = {"db": _check(db), "storage": _check(storage), "queue": _check(queue)}
    return {"status": "ok" if all(c["ok"] for c in checks.values()) else "degraded", "checks": checks}
