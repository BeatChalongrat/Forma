"""OIDC bearer-token verification (ADR-013): JWKS fetched from the issuer (or ``FORMA_AUTH_JWKS_URL``),
cached, rotated on unknown ``kid``; signature + issuer + audience + expiry validated with PyJWT."""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from forma.auth.principal import Principal, normalize_roles
from forma.core.errors import Unauthorized
from forma.core.settings import Settings


class JWKSCache:
    def __init__(self, url: str, *, ttl_s: float = 3600.0, timeout_s: float = 5.0) -> None:
        self.url = url
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self._keys: dict[str, Any] = {}
        self._fetched = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        with httpx.Client(timeout=self.timeout_s) as c:
            data = c.get(self.url).raise_for_status().json()
        self._keys = {k["kid"]: k for k in data.get("keys", []) if "kid" in k}
        self._fetched = time.time()

    def get(self, kid: str) -> dict[str, Any] | None:
        with self._lock:
            stale = time.time() - self._fetched > self.ttl_s
            if kid not in self._keys or stale:
                self._refresh()
            return self._keys.get(kid)


def discover_jwks_url(issuer: str, *, timeout_s: float = 5.0) -> str:
    with httpx.Client(timeout=timeout_s) as c:
        doc = c.get(issuer.rstrip("/") + "/.well-known/openid-configuration").raise_for_status().json()
    return str(doc["jwks_uri"])


class OIDCVerifier:
    def __init__(self, settings: Settings, *, jwks: JWKSCache | None = None) -> None:
        if not settings.auth_issuer:
            raise ValueError("FORMA_AUTH=oidc requires FORMA_AUTH_ISSUER")
        self.issuer = settings.auth_issuer.rstrip("/")
        self.audience = settings.auth_audience
        self.role_claim = settings.auth_role_claim
        self.customers_claim = settings.auth_customers_claim
        self.jwks = jwks or JWKSCache(settings.auth_jwks_url or discover_jwks_url(self.issuer))

    def verify(self, token: str, *, request_id: str | None = None) -> Principal:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if not kid:
                raise Unauthorized("token has no kid")
            key = self.jwks.get(kid)
            if key is None:
                raise Unauthorized("unknown signing key")
            public = jwt.PyJWK(key).key
            claims = jwt.decode(
                token,
                public,
                algorithms=[header.get("alg", "RS256")],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "sub"], "verify_aud": self.audience is not None},
                leeway=30,
            )
        except Unauthorized:
            raise
        except jwt.ExpiredSignatureError as e:
            raise Unauthorized("token expired") from e
        except Exception as e:  # invalid signature, issuer, audience, format…
            raise Unauthorized(f"invalid token: {type(e).__name__}") from e
        roles = normalize_roles(_claim(claims, self.role_claim))
        cust = _claim(claims, self.customers_claim)
        customers: frozenset[str] | None = None
        if isinstance(cust, list | tuple):
            customers = frozenset(str(c) for c in cust)
        elif isinstance(cust, str):
            customers = frozenset(c.strip() for c in cust.split(",") if c.strip())
        elif cust is not None:
            # A malformed restriction must never turn into access to every customer.
            raise Unauthorized("invalid customer scope claim")
        return Principal(
            subject=str(claims["sub"]),
            roles=roles,
            customers=customers,
            name=claims.get("preferred_username") or claims.get("email") or claims.get("name"),
            mode="oidc",
            request_id=request_id,
        )


def _claim(claims: dict[str, Any], path: str) -> Any:
    """Dotted claim paths (``realm_access.roles``) for Keycloak-style tokens."""
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur
