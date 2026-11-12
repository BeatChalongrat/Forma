"""Security/recovery edge cases discovered by branch coverage, with local keys and no network."""

import json
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from tests.test_auth import _token, _verifier
from tests.test_auth import rsa as rsa

from forma.auth import deps
from forma.auth.oidc import JWKSCache, discover_jwks_url
from forma.auth.principal import normalize_roles
from forma.core.errors import Forbidden, RateLimited, Unauthorized
from forma.core.settings import Settings


@pytest.mark.parametrize("customers,expected", [(" one, two, ", {"one", "two"}), (" ", set()), (None, None)])
def test_signed_claim_shapes_do_not_grant_unknown_roles(rsa, customers, expected):
    principal = _verifier(rsa).verify(_token(rsa, customers=customers, realm_access="malformed"))
    assert principal.customers == (frozenset(expected) if expected is not None else None)
    assert not principal.roles
    with pytest.raises(Forbidden):
        deps.current_principal(principal)
    assert not normalize_roles(42)


@pytest.mark.parametrize("customers", [7, True, {"customer": "one"}])
def test_malformed_customer_scope_cannot_become_unrestricted_access(rsa, customers):
    with pytest.raises(Unauthorized, match="customer"):
        _verifier(rsa).verify(_token(rsa, customers=customers))


def test_oidc_discovery_key_rotation_cache_and_unknown_key(rsa, monkeypatch):
    requests = []
    clock = [100.0]
    rotated = {**rsa["jwk"], "kid": "k2"}

    def handle(request):
        requests.append(str(request.url))
        data = (
            {"jwks_uri": "https://issuer.example/keys"}
            if "configuration" in request.url.path
            else {"keys": [rsa["jwk"], rotated, {"ignored": True}]}
        )
        return httpx.Response(200, json=data, request=request)

    real_client = httpx.Client
    monkeypatch.setattr(
        "forma.auth.oidc.httpx.Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)
    )
    monkeypatch.setattr("forma.auth.oidc.time.time", lambda: clock[0])
    url = discover_jwks_url("https://issuer.example/")
    cache = JWKSCache(url, ttl_s=10)
    assert cache.get("k1") == rsa["jwk"]
    assert cache.get("k1") == rsa["jwk"]
    assert len(requests) == 2  # discovery + initial keys, cache avoids repeat requests
    clock[0] += 11
    assert cache.get("k2") == rotated
    assert len(requests) == 3
    verifier = _verifier(rsa)
    verifier.jwks = cache
    with pytest.raises(Unauthorized, match="unknown signing key"):
        verifier.verify(_token(rsa, _kid="not-issued"))


def test_authenticated_bearer_ignores_forged_dev_identity(rsa, monkeypatch):
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(auth="oidc"))
    monkeypatch.setattr(deps, "_verifier", lambda: _verifier(rsa))
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"authorization", ("Bearer " + _token(rsa)).encode()),
                (b"x-forma-user", b"admin"),
                (b"x-forma-roles", b"admin"),
            ],
        }
    )
    principal = deps.authenticated_principal(request)
    assert principal.actor == "somchai" and not principal.has_role("admin")
    assert request.state.principal == principal
    monkeypatch.setattr(deps, "get_settings", lambda: Settings().model_copy(update={"auth": "unknown"}))
    with pytest.raises(Unauthorized, match="unknown"):
        deps.authenticated_principal(request)
    with pytest.raises(ValueError, match="unknown role"):
        deps.require_role("superuser")


def test_seeded_restricted_dev_identity_and_expired_rate_window(monkeypatch):
    request = Request({"type": "http", "headers": [(b"x-forma-user", b"tenant.c001")]})
    principal = deps._dev_principal(request, Settings())
    assert not principal.can_see("any-customer")
    limiter = deps.SlidingWindowLimiter()
    clock = [100.0]
    monkeypatch.setattr(deps.time, "monotonic", lambda: clock[0])
    limiter.check("alice", 1)
    with pytest.raises(RateLimited):
        limiter.check("alice", 1)
    limiter.check("bob", 1)
    clock[0] += 61
    limiter.check("alice", 1)  # recovery after the window expires, not a permanent denial


@pytest.mark.parametrize("job_status,expected", [("failed", "failed"), ("cancelled", "stopped")])
def test_reconnect_exposes_terminal_job_instead_of_spinning_forever(job_status, expected):
    from forma.services.conversations import _run_view

    view = {"status": "pending"}
    _run_view(
        view,
        SimpleNamespace(status="pending"),
        SimpleNamespace(revision=4),
        SimpleNamespace(status=job_status),
    )
    assert view == {"status": expected, "cursor": 4, "work_status": job_status}
    assert json.loads(json.dumps(view))["status"] != "pending"
