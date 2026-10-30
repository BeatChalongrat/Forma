"""Auth, RBAC and tenancy (ADR-013): principals, OIDC verification, role enforcement, dev mode."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _upload, make_workbook

from forma.auth.principal import Principal, normalize_roles


def test_roles_are_hierarchical() -> None:
    p = Principal(subject="u", roles=frozenset({"approver"}))
    assert p.has_role("importer") and p.has_role("reviewer") and p.has_role("approver")
    assert not p.has_role("admin")
    assert normalize_roles("Reviewer, admin bogus") == frozenset({"reviewer", "admin"})
    assert normalize_roles(["approver"]) == frozenset({"approver"}) and normalize_roles(None) == frozenset()
    assert Principal(subject="x").can_see("c1")
    assert Principal(subject="x", customers=frozenset({"c1"})).can_see("c1")
    assert not Principal(subject="x", customers=frozenset({"c1"})).can_see("c2")
    assert Principal(subject="x", roles=frozenset({"admin"})).to_dict()["effective_roles"] == [
        "importer",
        "reviewer",
        "approver",
        "admin",
    ]


# --------------------------------------------------------------------------------------------
# OIDC
# --------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rsa() -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from jwt.algorithms import RSAAlgorithm

    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "k1"
    return {"pem": pem, "jwk": jwk}


def _token(rsa: dict[str, Any], **claims: Any) -> str:
    import jwt

    now = dt.datetime.now(dt.UTC)
    payload = {
        "iss": "https://idp.example.com/realms/forma",
        "aud": "forma-api",
        "sub": "user-123",
        "iat": now,
        "exp": now + dt.timedelta(minutes=5),
        "preferred_username": "somchai",
        "realm_access": {"roles": ["reviewer", "offline_access"]},
        **claims,
    }
    return jwt.encode(payload, rsa["pem"], algorithm="RS256", headers={"kid": claims.pop("_kid", "k1")})


def _verifier(rsa: dict[str, Any], **overrides: Any) -> Any:
    from forma.auth.oidc import JWKSCache, OIDCVerifier
    from forma.core.settings import Settings

    s = Settings(
        auth="oidc",
        auth_issuer="https://idp.example.com/realms/forma",
        auth_audience="forma-api",
        auth_jwks_url="https://idp.example.com/jwks",
        auth_role_claim="realm_access.roles",
        **overrides,
    )
    cache = JWKSCache(s.auth_jwks_url or "")
    cache._keys = {"k1": rsa["jwk"]}
    cache._fetched = 10**12
    return OIDCVerifier(s, jwks=cache)


def test_oidc_verify_good_token(rsa: dict[str, Any]) -> None:
    v = _verifier(rsa)
    p = v.verify(_token(rsa, customers=["cus_1", "cus_2"]))
    assert p.subject == "user-123" and p.actor == "somchai" and p.mode == "oidc"
    assert p.roles == frozenset({"reviewer"}) and p.customers == frozenset({"cus_1", "cus_2"})


@pytest.mark.parametrize(
    "bad",
    [
        {"exp": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)},
        {"iss": "https://evil.example.com"},
        {"aud": "other-api"},
        {"_kid": "unknown"},
    ],
)
def test_oidc_rejects_bad_tokens(rsa: dict[str, Any], bad: dict[str, Any]) -> None:
    from forma.core.errors import Unauthorized

    v = _verifier(rsa)
    with pytest.raises(Unauthorized):
        v.verify(_token(rsa, **bad))
    with pytest.raises(Unauthorized):
        v.verify("not.a.jwt")


def test_oidc_requires_issuer() -> None:
    from forma.auth.oidc import OIDCVerifier
    from forma.core.settings import Settings

    with pytest.raises(ValueError):
        OIDCVerifier(Settings(auth="oidc"))


# --------------------------------------------------------------------------------------------
# API enforcement
# --------------------------------------------------------------------------------------------
@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_dev_mode_roles_and_actor_recording(client: TestClient, isolated_env: Path) -> None:
    c = client
    me = c.get("/api/me").json()
    assert me["name"] == "operator" and "admin" in me["effective_roles"] and me["mode"] == "dev"
    # importer can upload and run but cannot approve
    cust = c.post("/api/customers", json={"code": "A1", "name": "a"}).json()
    rid = c.post(
        "/api/rounds", json={"customer_id": cust["id"]}, headers={"X-Forma-User": "importer"}
    ).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    job = c.post(f"/api/rounds/{rid}/run", json={"sync": True}, headers={"X-Forma-User": "importer"}).json()
    j = c.get(f"/api/jobs/{job['job_id']}").json()
    assert j["status"] == "done", j["error"]
    mv = j["result"]["mapping_version_id"]
    r = c.post(f"/api/mappings/{mv}/approve", json={"actor": "spoofed"}, headers={"X-Forma-User": "importer"})
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    r = c.post(f"/api/mappings/{mv}/approve", json={"actor": "spoofed"}, headers={"X-Forma-User": "reviewer"})
    assert r.status_code == 200, r.text
    # the authenticated user is recorded, not the body's free-text actor
    assert r.json()["approved_by"] == "reviewer"
    audit = c.get(f"/api/audit?entity_id={mv}").json()
    assert any(a["action"] == "mapping.approve" and a["actor"] == "reviewer" for a in audit)
    # reviewer cannot export; approver can
    job = c.post(f"/api/rounds/{rid}/transform?sync=true", headers={"X-Forma-User": "reviewer"}).json()
    ds = c.get(f"/api/jobs/{job['job_id']}").json()["result"]["dataset_version_id"]
    assert (
        c.post(f"/api/datasets/{ds}/approve", json={}, headers={"X-Forma-User": "reviewer"}).status_code
        == 200
    )
    assert (
        c.post(f"/api/datasets/{ds}/export?fmt=json", headers={"X-Forma-User": "reviewer"}).status_code == 403
    )
    assert (
        c.post(f"/api/datasets/{ds}/export?fmt=json", headers={"X-Forma-User": "approver"}).status_code == 201
    )
    # only admin manages customers / policy
    assert (
        c.post(
            "/api/customers", json={"code": "B", "name": "b"}, headers={"X-Forma-User": "approver"}
        ).status_code
        == 403
    )
    assert c.get("/api/policy", headers={"X-Forma-User": "importer"}).status_code == 403
    # explicit role header wins over the seeded table
    assert (
        c.get("/api/policy", headers={"X-Forma-User": "importer", "X-Forma-Roles": "admin"}).status_code
        == 200
    )


def test_tenant_scoping(client: TestClient, isolated_env: Path) -> None:
    c = client
    a = c.post("/api/customers", json={"code": "T1", "name": "t1"}).json()
    b = c.post("/api/customers", json={"code": "T2", "name": "t2"}).json()
    ra = c.post("/api/rounds", json={"customer_id": a["id"]}).json()["id"]
    rb = c.post("/api/rounds", json={"customer_id": b["id"]}).json()["id"]
    h = {"X-Forma-User": "tenant", "X-Forma-Customers": a["id"]}
    assert [x["id"] for x in c.get("/api/customers", headers=h).json()] == [a["id"]]
    assert {r["id"] for r in c.get("/api/rounds", headers=h).json()} == {ra}
    assert c.get(f"/api/rounds/{ra}", headers=h).status_code == 200
    assert c.get(f"/api/rounds/{rb}", headers=h).status_code == 403
    assert c.get(f"/api/customers/{b['id']}", headers=h).status_code == 403
    assert c.post("/api/rounds", json={"customer_id": b["id"]}, headers=h).status_code == 403
    assert c.get("/api/dashboard", headers=h).json()["customers"] == 1


def test_an_identity_with_no_forma_role_can_only_ask_who_it_is(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    c.post("/api/customers", json={"code": "NR", "name": "nr"})
    h = {"X-Forma-User": "nobody", "X-Forma-Roles": "contractor"}  # authenticated, no FORMA role
    me = c.get("/api/me", headers=h)
    assert me.status_code == 200 and me.json()["roles"] == []
    for path in (
        "/api/customers",
        "/api/rounds",
        "/api/dashboard",
        "/api/jobs",
        "/api/review",
        "/api/schemas",
    ):
        r = c.get(path, headers=h)
        assert r.status_code == 403 and r.json()["error"] == "forbidden", path
    assert c.get("/api/auth/config", headers=h).status_code == 200  # public, the sign-in page needs it
    assert c.get("/api/customers", headers={"X-Forma-User": "importer"}).status_code == 200


def test_oidc_mode_requires_bearer(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_AUTH", "oidc")
    monkeypatch.setenv("FORMA_AUTH_ISSUER", "https://idp.example.com/realms/forma")
    monkeypatch.setenv("FORMA_AUTH_JWKS_URL", "https://idp.example.com/jwks")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        assert c.get("/health").status_code == 200  # liveness is public
        r = c.get("/api/customers")
        assert r.status_code == 401 and r.json()["error"] == "unauthorized"
        cfg = c.get("/api/auth/config").json()
        assert cfg["mode"] == "oidc" and cfg["issuer"].startswith("https://idp")


def test_prod_refuses_dev_auth(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import clear_forma_caches

    from forma.api.app import create_app
    from forma.auth import ensure_auth_is_safe
    from forma.core.settings import Settings

    # dev mode answers every header-less request as admin: never acceptable in production
    with pytest.raises(RuntimeError, match="FORMA_AUTH=dev with FORMA_ENV=prod"):
        ensure_auth_is_safe(Settings(env="prod", auth="dev"))
    ensure_auth_is_safe(Settings(env="production", auth="oidc"))
    ensure_auth_is_safe(Settings(env="dev", auth="dev"))
    ensure_auth_is_safe(Settings(env="prod", auth="dev", auth_allow_dev_in_prod=True))  # local smoke test
    # the app factory enforces it, so neither uvicorn nor a TestClient gets a listening app
    monkeypatch.setenv("FORMA_ENV", "prod")
    clear_forma_caches()
    with pytest.raises(RuntimeError, match="FORMA_AUTH_ALLOW_DEV_IN_PROD"):
        create_app()
    monkeypatch.setenv("FORMA_AUTH_ALLOW_DEV_IN_PROD", "true")
    clear_forma_caches()
    assert create_app().title == "FORMA API"


def test_llm_endpoint_rate_limit(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORMA_LLM_RATE_LIMIT_PER_MIN", "2")
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        cust = c.post("/api/customers", json={"code": "R1", "name": "r"}).json()
        rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
        _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
        job = c.post(f"/api/rounds/{rid}/run", json={"sync": True}).json()
        mv = c.get(f"/api/jobs/{job['job_id']}").json()["result"]["mapping_version_id"]
        codes = [c.post(f"/api/mappings/{mv}/diagnose").status_code for _ in range(3)]
        assert codes[:2] == [200, 200] and codes[2] == 429
        # another user has their own budget
        assert c.post(f"/api/mappings/{mv}/diagnose", headers={"X-Forma-User": "reviewer"}).status_code == 200
