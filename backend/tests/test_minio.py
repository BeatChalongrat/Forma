"""Full API flow on S3-compatible storage (MinIO via docker compose), ADR-012. Skipped unless
``FORMA_TEST_S3_ENDPOINT`` is set, e.g.

    docker compose up -d minio
    FORMA_TEST_S3_ENDPOINT=http://localhost:9000 pytest tests/test_minio.py
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_api import FILE_NAME, _run, _upload, make_workbook

ENDPOINT = os.environ.get("FORMA_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="FORMA_TEST_S3_ENDPOINT not set (needs MinIO)")


@pytest.fixture
def s3_env(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    assert ENDPOINT
    monkeypatch.setenv("FORMA_STORAGE", "s3")
    monkeypatch.setenv("FORMA_S3_ENDPOINT_URL", ENDPOINT)
    monkeypatch.setenv("FORMA_S3_BUCKET", os.environ.get("FORMA_TEST_S3_BUCKET", "forma-test"))
    monkeypatch.setenv("FORMA_S3_PREFIX", f"t-{isolated_env.name}")
    monkeypatch.setenv("FORMA_S3_ACCESS_KEY", os.environ.get("FORMA_TEST_S3_ACCESS_KEY", "forma"))
    monkeypatch.setenv("FORMA_S3_SECRET_KEY", os.environ.get("FORMA_TEST_S3_SECRET_KEY", "forma12345"))
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    from forma.services.context import get_context

    ctx = get_context()
    assert ctx.storage.name == "s3"
    ctx.storage.ensure_bucket()  # type: ignore[attr-defined]
    yield isolated_env
    clear_forma_caches()


def test_full_flow_on_minio(s3_env: Path) -> None:
    from forma.api.app import create_app
    from forma.services.context import get_context

    ctx = get_context()
    with TestClient(create_app()) as c:
        cust = c.post("/api/customers", json={"code": "S3C", "name": "s3"}).json()
        rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
        files = _upload(c, rid, make_workbook(s3_env / FILE_NAME))
        job = _run(c, rid)
        mv = job["result"]["mapping_version_id"]
        c.post(f"/api/mappings/{mv}/approve", json={"actor": "s3.tester"})
        jid = c.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
        j = c.get(f"/api/jobs/{jid}").json()
        assert j["status"] == "done", j["error"]
        ds = j["result"]["dataset_version_id"]
        # objects live in the bucket under keys, rows are read back through the cache
        keys = {o.key for o in ctx.storage.list("")}
        assert any(k.startswith("files/") for k in keys) and any(k.startswith(f"datasets/{cust['id']}/") for k in keys)
        assert c.get(f"/api/datasets/{ds}/rows?limit=3").json()["total"] == 12
        grid = c.get(f"/api/rounds/{rid}/files/{files[0]['id']}/grid?rows=5").json()
        assert grid["rows"]
        # export → pre-signed redirect, and the object is downloadable from MinIO
        exp = c.post(f"/api/datasets/{ds}/export?fmt=jsonl").json()
        assert exp["key"].startswith(f"exports/{cust['id']}/")
        r = c.get(f"/api/exports/{exp['id']}/download", follow_redirects=False)
        assert r.status_code == 307 and ENDPOINT in r.headers["location"]
        import httpx

        body = httpx.get(r.headers["location"], timeout=10).content
        assert body.count(b"\n") == 12
        # edits create a new version object; rollback another; nothing is mutated in place
        nv = c.post(
            f"/api/datasets/{ds}/changes",
            json={"operations": [{"kind": "set_field", "target": "/status", "value": "inactive"}], "kind": "one_time"},
        ).json()["dataset"]
        assert nv["version"] == 2 and ctx.storage.exists(f"datasets/{cust['id']}/{nv['id']}.jsonl")
        assert ctx.storage.exists(f"datasets/{cust['id']}/{ds}.jsonl")
        assert c.get("/ready").json()["checks"]["storage"]["ok"] is True
