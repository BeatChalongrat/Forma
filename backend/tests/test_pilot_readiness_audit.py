"""Pre-pilot regressions: real HTTP boundaries, bounded resources and tenant permissions."""

from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as value:
        yield value


def test_cors_accepts_every_api_mutation_method(client):
    from forma.core.settings import get_settings

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        result = client.options(
            "/api/batches/batch-test",
            headers={
                "Origin": get_settings().cors_origins[0],
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert result.status_code == 200, (method, result.text)


def test_missing_routes_do_not_create_unbounded_metrics(client):
    for number in range(20):
        assert client.get(f"/api/audit-missing-{number}").status_code == 404
    metrics = client.get("/metrics").text
    assert "audit-missing-" not in metrics
    assert 'route="unmatched"' in metrics


def test_thai_batch_name_download_is_valid_http(client, monkeypatch):
    from forma.api import batch_routes
    from forma.db.models import Batch
    from forma.db.session import session_scope

    with session_scope() as session:
        batch = Batch(label="รอบส่งสินค้า ไทย 日本", auto_publish=False, created_by="tester")
        session.add(batch)
        session.flush()
        bid = batch.id

    def archive(*args, output):
        output.write(b"zip-test")
        return b"", 1

    monkeypatch.setattr(batch_routes.bs, "zip_outputs", archive)
    result = client.get(f"/api/batches/{bid}/download")
    assert result.status_code == 200
    assert "รอบส่งสินค้า" in unquote(result.headers["content-disposition"])
    assert result.content == b"zip-test"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/settings/ai", None),
        ("put", "/api/settings/ai", {"model": "forbidden-change"}),
        ("get", "/api/settings/policy", None),
        ("delete", "/api/settings/policy", None),
        ("put", "/api/settings/default-format", {"target_schema": "product@1"}),
        ("get", "/api/policy", None),
        ("post", "/api/customers", {"code": "outside-scope", "name": "outside-scope"}),
    ],
)
def test_customer_scoped_admin_cannot_access_global_settings(client, method, path, body):
    result = client.request(
        method,
        path,
        json=body,
        headers={"X-Forma-User": "tenant-admin", "X-Forma-Roles": "admin", "X-Forma-Customers": "c1"},
    )
    assert result.status_code == 403, result.text
    assert client.get("/api/settings/ai").status_code == 200


def test_upload_total_checked_before_storage(client, monkeypatch):
    from forma.services.context import get_context

    monkeypatch.setattr(get_context().settings, "max_upload_request_mb", 1)
    piece = b"sku,price\n" + b"a,1\n" * 160000
    result = client.post("/api/batches", files=[("files", ("a.csv", piece)), ("files", ("b.csv", piece))])
    assert result.status_code == 413, result.text
    assert client.get("/api/batches").json() == []
    assert not list(get_context().storage.list("files/"))


def test_upload_with_unknown_size_still_checks_total(isolated_env):
    import asyncio

    from fastapi import UploadFile

    from forma.api.uploads import read_uploads
    from forma.core.errors import PayloadTooLarge
    from forma.core.settings import Settings

    files = [UploadFile(io.BytesIO(b"x" * 600000), filename=f"{i}.csv") for i in range(2)]
    with pytest.raises(PayloadTooLarge):
        asyncio.run(read_uploads(files, Settings(max_upload_request_mb=1)))


def test_large_upload_does_not_keep_a_second_full_copy(isolated_env):
    import asyncio
    import tempfile
    import tracemalloc

    from fastapi import UploadFile

    from forma.api.uploads import read_upload

    size = 32 * 1024 * 1024
    with tempfile.TemporaryFile("w+b") as source:
        for _ in range(32):
            source.write(b"x" * 1024 * 1024)
        source.seek(0)
        upload = UploadFile(source, size=size, filename="large.csv")
        tracemalloc.start()
        try:
            result = asyncio.run(read_upload(upload, 32))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert len(result) == size and result[:4] == result[-4:] == b"xxxx"
        assert peak < size * 1.7, f"Upload allocated {peak / size:.2f} full copies"
        assert source.closed


def test_upload_releases_memory_spools_on_success_and_read_error(isolated_env):
    import asyncio

    from fastapi import UploadFile

    from forma.api.uploads import read_uploads
    from forma.core.errors import PayloadTooLarge
    from forma.core.settings import Settings

    files = [UploadFile(io.BytesIO(b"x" * 200000), filename=f"{i}.csv") for i in range(4)]
    result = asyncio.run(read_uploads(files, Settings(max_upload_request_mb=1)))
    assert len(result) == 4 and all(len(data) == 200000 for _, data in result)
    assert all(file.file.closed for file in files)
    large = UploadFile(io.BytesIO(b"x" * 1100000), filename="unknown-size.csv")
    with pytest.raises(PayloadTooLarge):
        asyncio.run(read_uploads([large], Settings(max_upload_request_mb=1)))
    assert large.file.closed


def test_upload_backpressure_is_released_after_failure(isolated_env):
    from forma.api.uploads import upload_slot
    from forma.core.errors import QueueFull

    slot = upload_slot()
    next(slot)
    try:
        with pytest.raises(QueueFull):
            next(upload_slot())
    finally:
        slot.close()
    with_slot = upload_slot()
    next(with_slot)
    with_slot.close()


def test_postgres_pool_checks_stale_connections(isolated_env, monkeypatch):
    from forma.core.settings import get_settings
    from forma.db import session

    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://test:test@127.0.0.1/test")
    # Construction only: no network connection is opened.
    session.get_engine.cache_clear()
    engine = session.get_engine()
    assert engine.pool._pre_ping
    engine.dispose()
    session.get_engine.cache_clear()


def test_compose_storage_initialization_does_not_mask_failure():
    import yaml

    compose = yaml.safe_load(
        (Path(__file__).parents[2] / "docker-compose.prod.yml").read_text(encoding="utf-8")
    )
    assert "|| true" not in str(compose["services"]["minio-init"]["entrypoint"])
    assert all(
        service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
        for service in compose["services"].values()
    )


@pytest.mark.parametrize("path", ["/api/rounds", "/api/review", "/api/jobs", "/api/audit"])
@pytest.mark.parametrize("limit", [0, -1, 1000000])
def test_list_limits_reject_unbounded_requests(client, path, limit):
    result = client.get(path, params={"limit": limit})
    assert result.status_code == 422, result.text


@pytest.mark.parametrize("query", [{"rows": -1}, {"rows": 0}, {"rows": 501}, {"sheet": -1}])
def test_grid_rejects_invalid_window_before_reading_file(client, query):
    assert client.get("/api/rounds/missing/files/missing/grid", params=query).status_code == 422


@pytest.mark.parametrize("sheet", [-1, 500, "renamed-sheet"])
def test_unknown_sheet_is_a_validation_error_not_a_crash(tmp_path, sheet):
    from openpyxl import Workbook

    from forma.core.errors import ValidationFailed
    from forma.ingestion.readers import read_excel_sheet_raw

    path = tmp_path / "one.xlsx"
    book = Workbook()
    book.active.append(["sku", "price"])
    book.save(path)
    with pytest.raises(ValidationFailed, match="ไม่พบชีต"):
        read_excel_sheet_raw(path, sheet, n_rows=10)


def test_export_proxy_preserves_permissions_and_closes_storage(client, monkeypatch):
    from forma.db.models import ExportArtifact
    from forma.db.session import session_scope
    from forma.services.context import get_context

    with session_scope() as session:
        session.add(
            ExportArtifact(
                id="exp-audit",
                dataset_version_id="ds-audit",
                customer_id="c1",
                fmt="json",
                storage_key="exports/ผลลัพธ์.json",
                row_count=1,
                sha256="test",
            )
        )
    storage = get_context().storage
    stream = io.BytesIO(b'[{"sku":"one"}]')
    monkeypatch.setattr(storage, "open", lambda key: stream)

    def no_presign(*args, **kwargs):
        pytest.fail("Same-origin downloads must not require S3 presigning")

    monkeypatch.setattr(storage, "url", no_presign)
    denied = client.get("/api/exports/exp-audit/download?proxy=true", headers={"X-Forma-Customers": "c2"})
    assert denied.status_code == 403 and stream.tell() == 0
    result = client.get("/api/exports/exp-audit/download?proxy=true")
    assert result.status_code == 200 and result.json() == [{"sku": "one"}]
    assert "ผลลัพธ์.json" in unquote(result.headers["content-disposition"])
    assert stream.closed


def test_multipart_admission_rejects_oversize_and_busy_before_read(client, monkeypatch):
    from forma.api.uploads import upload_slot
    from forma.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_request_mb", 1)
    result = client.post(
        "/api/batches",
        content=b"unread",
        headers={
            "Content-Type": "multipart/form-data; boundary=test",
            "Content-Length": str(3 * 1024 * 1024),
        },
    )
    assert result.status_code == 413
    slot = upload_slot()
    next(slot)
    try:
        result = client.post("/api/batches", files={"files": ("x.csv", b"sku\nx")})
        assert result.status_code == 429 and result.headers["Retry-After"] == "5"
    finally:
        slot.close()


def test_chunked_multipart_is_bounded_and_next_upload_can_proceed(client, monkeypatch):
    from forma.core.settings import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_request_mb", 1)
    chunks = iter(
        [
            b'--test\r\nContent-Disposition: form-data; name="files"; filename="x.csv"\r\n\r\n',
            b"x" * (3 * 1024 * 1024),
            b"\r\n--test--\r\n",
        ]
    )
    result = client.post(
        "/api/batches", content=chunks, headers={"Content-Type": "multipart/form-data; boundary=test"}
    )
    assert result.status_code == 413 and result.json()["error"] == "payload_too_large"
    retry = client.post("/api/batches?sync=true", files={"files": ("x.csv", b"sku\nx")})
    assert retry.status_code == 201
