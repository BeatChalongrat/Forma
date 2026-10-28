"""FastAPI dependencies (ADR-013): ``current_principal`` resolves the caller, ``require_role`` is the one
place authorisation is enforced, ``ensure_customer`` applies tenant scoping.

Dev mode (``FORMA_AUTH=dev``) keeps today's behaviour: the caller is the ``X-Forma-User`` header (or the
``actor`` the request body carries, or ``operator``). Roles come from the seeded dev users or
``X-Forma-Roles``; unknown users get ``FORMA_AUTH_DEV_DEFAULT_ROLE`` so local workflows never need a login.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from fastapi import Depends, Request

from forma.auth.principal import ROLES, Principal, normalize_roles
from forma.core.errors import Forbidden, RateLimited, Unauthorized
from forma.core.settings import Settings, get_settings

# Seeded dev users (ADR-013). Anything else → FORMA_AUTH_DEV_DEFAULT_ROLE.
DEV_USERS: dict[str, dict[str, Any]] = {
    "operator": {"roles": ["admin"]},
    "demo.operator": {"roles": ["admin"]},
    "importer": {"roles": ["importer"]},
    "reviewer": {"roles": ["reviewer"]},
    "approver": {"roles": ["approver"]},
    "admin": {"roles": ["admin"]},
    "tenant.c001": {"roles": ["reviewer"], "customers": []},  # customers filled by tests
}


@lru_cache
def _verifier() -> Any:
    from forma.auth.oidc import OIDCVerifier

    return OIDCVerifier(get_settings())


def reset_auth_caches() -> None:
    _verifier.cache_clear()
    _LIMITER.reset()


def _dev_principal(request: Request, s: Settings) -> Principal:
    user = (request.headers.get("x-forma-user") or "").strip()
    if not user:
        user = getattr(request.state, "actor_hint", None) or "operator"
    seeded = DEV_USERS.get(user)
    roles_hdr = request.headers.get("x-forma-roles")
    if roles_hdr is not None:
        roles = normalize_roles(roles_hdr)
    elif seeded:
        roles = normalize_roles(seeded.get("roles"))
    else:
        roles = normalize_roles(s.auth_dev_default_role)
    customers: frozenset[str] | None = None
    cust_hdr = request.headers.get("x-forma-customers")
    if cust_hdr is not None:
        customers = frozenset(c.strip() for c in cust_hdr.split(",") if c.strip())
    elif seeded and seeded.get("customers") is not None:
        customers = frozenset(seeded["customers"])
    return Principal(
        subject=user,
        roles=roles,
        customers=customers,
        name=user,
        mode="dev",
        request_id=getattr(request.state, "request_id", None),
    )


def current_principal(request: Request) -> Principal:
    s = get_settings()
    if s.auth == "dev":
        p = _dev_principal(request, s)
    elif s.auth == "oidc":
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise Unauthorized("missing bearer token")
        p = _verifier().verify(auth[7:].strip(), request_id=getattr(request.state, "request_id", None))
    else:
        raise Unauthorized(f"unknown FORMA_AUTH mode {s.auth!r}")
    request.state.principal = p
    return p


def require_role(role: str) -> Callable[..., Principal]:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")

    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        if not p.has_role(role):
            raise Forbidden(f"role {role!r} required", details={"required": role, "roles": sorted(p.roles)})
        return p

    _dep.__name__ = f"require_{role}"
    return _dep


def ensure_customer(p: Principal, customer_id: str | None) -> None:
    """Tenant scoping: a principal with a ``customers`` claim only sees those customers."""
    if not p.can_see(customer_id):
        raise Forbidden("customer not visible to this user", details={"customer_id": customer_id})


class SlidingWindowLimiter:
    """Per-key request limiter (LLM-calling endpoints). In-memory: per API process, which is the unit
    that pays for the model calls; a shared limiter can replace it behind the same interface."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, key: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (now - q[0])) + 1
                raise RateLimited(
                    f"rate limit of {limit}/min exceeded for LLM endpoints", retry_after_s=retry
                )
            q.append(now)


_LIMITER = SlidingWindowLimiter()


def llm_rate_limited(p: Principal = Depends(current_principal)) -> Principal:
    _LIMITER.check(f"llm:{p.subject}", get_settings().llm_rate_limit_per_min)
    return p
