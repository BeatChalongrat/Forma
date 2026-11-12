"""Storage protocol (ADR-012): local disk and S3 (moto) behave identically; keys are safe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from forma.storage import LocalStorage, S3Storage, key_from_legacy_path, make_storage, safe_name, validate_key
from forma.storage.base import Storage


@pytest.fixture(params=["local", "s3"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "local":
        yield LocalStorage(tmp_path / "root")
        return
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        import boto3

        client = boto3.client("s3", region_name="us-east-1")
        s3 = S3Storage(bucket="forma-test", prefix="env/test", cache_dir=tmp_path / "cache", client=client)
        s3.ensure_bucket()
        yield s3


def test_put_get_open_exists_delete(storage: Storage, tmp_path: Path) -> None:
    info = storage.put("files/ab/abc/ชื่อไฟล์.xlsx", b"hello")
    assert info.key == "files/ab/abc/ชื่อไฟล์.xlsx" and info.size == 5
    assert storage.exists(info.key) and storage.get(info.key) == b"hello" and storage.size(info.key) == 5
    with storage.open(info.key) as fh:
        assert fh.read() == b"hello"
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 1000)
    info2 = storage.put("datasets/c1/dsv_1.jsonl", src)
    assert info2.size == 1000
    p = storage.local_path(info2.key)
    assert p.is_file() and p.read_bytes() == b"x" * 1000
    assert storage.local_path(info2.key) == p  # cached / same path
    keys = [o.key for o in storage.list("datasets/")]
    assert keys == ["datasets/c1/dsv_1.jsonl"]
    assert {o.key for o in storage.list("")} == {info.key, info2.key}
    storage.delete(info2.key)
    assert not storage.exists(info2.key)
    with pytest.raises(FileNotFoundError if storage.name == "local" else Exception):
        storage.local_path(info2.key)
    assert storage.healthy()


def test_presigned_url_only_on_s3(storage: Storage) -> None:
    storage.put("exports/c1/x.csv", b"a,b\n")
    url = storage.url("exports/c1/x.csv", expires_s=60, filename="x.csv")
    if storage.name == "local":
        assert url is None
    else:
        assert url and "exports/c1/x.csv" in url and "Signature" in url and "attachment" in url


def test_concurrent_cold_cache_downloads_are_independent(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    gate = Barrier(2)

    class Client:
        def download_file(self, bucket: str, key: str, filename: str) -> None:
            Path(filename).write_bytes(b"complete object")
            gate.wait(timeout=10)

    storage = S3Storage(bucket="test", cache_dir=tmp_path, client=Client())
    with ThreadPoolExecutor(2) as pool:
        for attempt in range(20):
            paths = list(pool.map(storage.local_path, [f"files/cold-{attempt}.xlsx"] * 2))
            assert paths[0] == paths[1] and paths[0].read_bytes() == b"complete object"
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("another_download_finished", [True, False])
def test_cache_publish_permission_error_requires_a_completed_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, another_download_finished: bool
) -> None:
    class Client:
        def download_file(self, bucket: str, key: str, filename: str) -> None:
            Path(filename).write_bytes(b"complete object")

    def denied_replace(source: Path, target: Path) -> Path:
        if another_download_finished:
            target.write_bytes(b"complete object")
        raise PermissionError("cache publish denied")

    monkeypatch.setattr(Path, "replace", denied_replace)
    storage = S3Storage(bucket="test", cache_dir=tmp_path, client=Client())
    if another_download_finished:
        assert storage.local_path("files/cold.xlsx").read_bytes() == b"complete object"
    else:
        with pytest.raises(PermissionError, match="cache publish denied"):
            storage.local_path("files/cold.xlsx")
    assert not list(tmp_path.rglob("*.part"))


def test_local_put_is_atomic_and_prunes_empty_dirs(tmp_path: Path) -> None:
    st = LocalStorage(tmp_path / "root")
    st.put("files/aa/sha/one.bin", b"1")
    assert not (tmp_path / "root/files/aa/sha/one.bin.tmp").exists()
    st.delete("files/aa/sha/one.bin")
    assert not (tmp_path / "root/files").exists() and (tmp_path / "root").exists()


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\report.xlsx", "report.xlsx"),
        ("ราคา (ต.ค.69) [v2].xlsx", "ราคา (ต.ค.69) [v2].xlsx"),
        ("weird/na:me?.csv", "na_me_.csv"),
        ("   ", "file"),
        (".hidden", "hidden"),
    ],
)
def test_safe_name(name: str, want: str) -> None:
    assert safe_name(name) == want


@pytest.mark.parametrize("bad", ["", "/abs/key", "a/../b", "a//b", "C:/x", "..", "a/./b"])
def test_validate_key_rejects_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_key(bad)
    assert validate_key("files\\ab\\c.bin") == "files/ab/c.bin"


def test_key_from_legacy_path(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "files" / "ab").mkdir(parents=True)
    assert key_from_legacy_path(str(root / "files" / "ab" / "x.xlsx"), root) == "files/ab/x.xlsx"
    assert key_from_legacy_path(str(tmp_path / "elsewhere.xlsx"), root) is None


def test_make_storage_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.core.settings import Settings

    monkeypatch.setenv("FORMA_DATA_DIR", str(tmp_path))
    assert make_storage(Settings(storage="local")).name == "local"
    # Constructing a client must not query cloud instance metadata for real credentials.
    s3 = make_storage(
        Settings(
            storage="s3",
            s3_bucket="b",
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
        )
    )
    assert s3.name == "s3"
    with pytest.raises(ValueError):
        make_storage(Settings(storage="gcs"))
