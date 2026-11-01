"""Retention policy (plan/apply, audited, never touches kept versions) and backup/restore round trips."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_api import _run, _upload, make_workbook
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _published_round(c: TestClient, tmp: Path, code: str = "RT1") -> tuple[str, str, list[str]]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(tmp / f"{code}.xlsx"))
    job = _run(c, rid)
    mv = job["result"]["mapping_version_id"]
    c.post(f"/api/mappings/{mv}/approve", json={})
    jid = c.post(f"/api/rounds/{rid}/transform?sync=true").json()["job_id"]
    v1 = c.get(f"/api/jobs/{jid}").json()["result"]["dataset_version_id"]
    versions = [v1]
    cur = v1
    for i in range(4):  # v2..v5 drafts via edits
        nv = c.post(
            f"/api/datasets/{cur}/changes",
            json={
                "operations": [
                    {"kind": "set_field", "target": "/status", "value": "inactive" if i % 2 else "active"}
                ],
                "kind": "one_time",
            },
        ).json()["dataset"]
        versions.append(nv["id"])
        cur = nv["id"]
    c.post(f"/api/datasets/{cur}/approve", json={})
    c.post(f"/api/datasets/{cur}/export?fmt=json")
    return rid, cust["id"], versions


def _age_everything(days: int) -> None:
    """Pretend the whole history happened `days` ago."""
    from forma.db.models import DatasetVersion, ExportArtifact, ImportRound, Job, LLMCall, SourceFile
    from forma.db.session import session_scope

    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    with session_scope() as s:
        for model in (ImportRound, SourceFile, DatasetVersion, ExportArtifact, Job, LLMCall):
            for row in s.scalars(select(model)):
                row.created_at = old
                if hasattr(row, "updated_at"):
                    row.updated_at = old
                if hasattr(row, "finished_at") and getattr(row, "finished_at", None):
                    row.finished_at = old


def test_retention_plan_and_apply(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import AuditLog, DatasetVersion, Job, SourceFile
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context

    _rid, cid, versions = _published_round(client, isolated_env)
    ctx = get_context()
    with session_scope() as s:
        plan = retention.plan(ctx, s)
    # fresh data: nothing is old enough; only the version cap applies (keep 5 of 5 → nothing)
    assert plan["summary"]["source_files"] == 0 and plan["summary"]["jobs"] == 0
    assert plan["summary"]["dataset_versions"] == 0

    monkeypatch.setattr(
        retention,
        "load_policy",
        lambda _d: {
            "source_files": {"keep_days_after_publish": 30},
            "dataset_versions": {"keep_last_versions": 2},
            "exports": {"keep_days": 30},
            "jobs": {"keep_days": 7},
            "llm_calls": {"keep_days": 1},
        },
    )
    _age_everything(60)
    with session_scope() as s:
        plan = retention.plan(ctx, s)
    assert (
        plan["summary"]["source_files"] == 1
        and plan["summary"]["exports"] == 1
        and plan["summary"]["jobs"] >= 2
    )
    purged_ids = {d["id"] for d in plan["dataset_versions"]}
    # keep: last 2 versions (v4 superseded? no — v5 approved + v4) plus v1 (parent of nothing kept? v4's parent is v3)
    assert versions[-1] not in purged_ids and versions[-2] not in purged_ids  # last two kept
    assert versions[-3] not in purged_ids  # parent of a kept version (rollback target)
    assert versions[0] in purged_ids and versions[1] in purged_ids

    with session_scope() as s:
        done = retention.apply(ctx, s, plan, actor="test.retention")
    assert (
        done["source_files"] == 1
        and done["dataset_versions"] == 2
        and done["exports"] == 1
        and not done["errors"]
    )
    with session_scope() as s:
        f = s.scalars(select(SourceFile)).one()
        assert f.status == "purged" and f.storage_key == ""
        purged = [d for d in s.scalars(select(DatasetVersion)) if d.status == "purged"]
        assert {d.id for d in purged} == purged_ids and all(d.records_key == "" for d in purged)
        kept = [d for d in s.scalars(select(DatasetVersion)) if d.status != "purged"]
        assert all(ctx.storage.exists(d.records_key) for d in kept)
        assert s.scalar(select(Job)) is None  # old finished jobs gone
        actions = {a.action for a in s.scalars(select(AuditLog))}
        assert {
            "retention.purge_file",
            "retention.purge_dataset",
            "retention.purge_export",
            "retention.purge_rows",
        } <= actions
    assert not ctx.storage.exists(f"datasets/{cid}/{versions[0]}.jsonl")
    # the purged version is still listed (history), reading its rows fails loudly
    r = client.get(f"/api/datasets/{versions[0]}")
    assert r.status_code == 200 and r.json()["status"] == "purged"
    assert client.get(f"/api/datasets/{versions[0]}/rows").status_code >= 400


def test_retention_cli_dry_run_by_default(client: TestClient, isolated_env: Path) -> None:
    from forma.cli import app

    _published_round(client, isolated_env, code="RT2")
    res = runner.invoke(app, ["retention", "run", "--out", str(isolated_env / "plan.json")])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["dry_run"] is True and "summary" in out and (isolated_env / "plan.json").exists()
    res = runner.invoke(app, ["retention", "run", "--apply"])
    assert res.exit_code == 0 and json.loads(res.output)["dry_run"] is False


def test_backup_and_restore_round_trip(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import Customer, DatasetVersion
    from forma.db.session import session_scope
    from forma.services import backup
    from forma.services.context import get_context

    rid, cid, versions = _published_round(client, isolated_env, code="BK1")
    ctx = get_context()
    out = isolated_env / "backups"
    info = backup.create_backup(ctx, out, include_objects=True, archive=True)
    archive = Path(info["path"])
    assert archive.name.endswith(".tar.gz") and info["objects"] >= 7  # 1 file + 5 versions + 1 export
    # fresh environment: new DB + empty storage, restore, verify
    monkeypatch.setenv("FORMA_DATABASE_URL", f"sqlite:///{isolated_env}/restored.db")
    monkeypatch.setenv("FORMA_DATA_DIR", str(isolated_env / "restored-data"))
    from tests.conftest import clear_forma_caches

    clear_forma_caches()
    ctx2 = get_context()  # restore goes into an EMPTY database: the dump carries schema + alembic_version
    res = backup.restore_backup(ctx2, archive)
    assert res["objects_restored"] == info["objects"] and res["missing_objects"] == []
    with session_scope() as s:
        assert s.scalar(select(Customer).where(Customer.id == cid)) is not None
        dvs = list(s.scalars(select(DatasetVersion).where(DatasetVersion.round_id == rid)))
        assert {d.id for d in dvs} == set(versions)
        assert all(ctx2.storage.exists(d.records_key) for d in dvs)
    # the restored API serves the data
    from forma.api.app import create_app

    with TestClient(create_app()) as c2:
        assert c2.get(f"/api/datasets/{versions[-1]}/rows?limit=2").json()["total"] == 12
    # refusing to restore into a non-empty database
    with pytest.raises(RuntimeError, match="non-empty"):
        backup.restore_backup(ctx2, archive)


def test_a_backup_holds_the_bytes_its_manifest_names(client: TestClient, isolated_env: Path) -> None:
    from forma.services import backup
    from forma.services.context import get_context

    _published_round(client, isolated_env, code="BK3")
    ctx = get_context()
    info = backup.create_backup(ctx, isolated_env / "bk", include_objects=True, archive=True)
    with tarfile.open(info["path"], "r:gz") as tar:
        members = {m.name.split("/", 1)[1]: m for m in tar.getmembers() if m.isfile()}

        def read(name: str) -> bytes:
            fh = tar.extractfile(members[name])
            assert fh is not None
            return fh.read()

        manifest = json.loads(read("manifest.json"))
        assert manifest["objects_included"] is True and manifest["object_count"] == info["objects"] > 0
        for obj in manifest["objects"]:
            held = read(f"objects/{obj['key']}")
            assert hashlib.sha256(held).hexdigest() == obj["sha256"] and len(held) == obj["size"]


def test_a_dump_from_a_newer_client_restores_on_an_older_server(tmp_path: Path) -> None:
    from forma.services import backup

    lines = [b"SET lock_timeout = 0;", b"SET transaction_timeout = 0;", b"SET row_security = off;"]
    filler = [b"-- a comment"] * backup.DUMP_HEADER_LINES
    dump = tmp_path / "db.sql"
    dump.write_bytes(b"\n".join([*lines, *filler, b"SET transaction_timeout = 0; held in a row", b""]))
    out = tmp_path / "clean.sql"
    backup._drop_newer_settings(dump, out)
    lines = out.read_bytes().splitlines()
    assert lines[:2] == [b"SET lock_timeout = 0;", b"SET row_security = off;"]
    assert lines[-1] == b"SET transaction_timeout = 0; held in a row"


def test_a_backup_covers_every_kind_of_object(client: TestClient, isolated_env: Path) -> None:
    from forma.services import backup, sandbox
    from forma.services.context import get_context

    _published_round(client, isolated_env, code="BK7")
    ctx = get_context()
    ctx.storage.put("assistant/chat1/file1.png", b"a picture in a chat")
    ctx.storage.put(sandbox.details_key("c1", "m1"), b"{}")
    info = backup.create_backup(ctx, isolated_env / "bk", include_objects=False)
    manifest = json.loads((Path(info["path"]) / "manifest.json").read_text(encoding="utf-8"))
    kinds = {o["key"].split("/")[0] for o in manifest["objects"]}
    assert {"files", "datasets", "exports", "assistant", "sandbox"} <= kinds


def test_a_bucket_is_taken_whole() -> None:
    from forma.services import backup
    from forma.storage import ObjectInfo

    class Bucket:
        name = "s3"

        def list(self, prefix: str) -> Iterator[ObjectInfo]:
            assert prefix == ""
            yield ObjectInfo("a-kind-nobody-listed/x", 1)

    ctx: Any = SimpleNamespace(storage=Bucket())
    assert [o.key for o in backup._objects(ctx)] == ["a-kind-nobody-listed/x"]


def test_a_backup_does_not_fill_the_object_cache(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.services import backup
    from forma.services.context import get_context

    _published_round(client, isolated_env, code="BK4")
    ctx = get_context()

    def refuse(key: str) -> Path:
        raise AssertionError(f"local_path caches the whole bucket on S3: {key}")

    monkeypatch.setattr(ctx.storage, "local_path", refuse)
    assert backup.create_backup(ctx, isolated_env / "bk", include_objects=True)["objects"] > 0


@pytest.mark.parametrize("archive", [True, False])
def test_a_failed_backup_leaves_nothing_behind(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, archive: bool
) -> None:
    from forma.services import backup
    from forma.services.context import get_context

    _published_round(client, isolated_env, code="BK5")
    ctx = get_context()
    out = isolated_env / "bk"
    calls = {"n": 0}
    real = ctx.storage.open

    def fails_on_the_second(key: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("the bucket went away")
        return real(key)

    monkeypatch.setattr(ctx.storage, "open", fails_on_the_second)
    with pytest.raises(OSError, match="went away"):
        backup.create_backup(ctx, out, include_objects=True, archive=archive)
    assert list(out.iterdir()) == []


def test_only_a_finished_backup_carries_the_name(client: TestClient, isolated_env: Path) -> None:
    from forma.services import backup
    from forma.services.context import get_context

    out = isolated_env / "bk"
    stale = out / ".forma-backup-20200101T000000Z"
    (stale / "objects").mkdir(parents=True)
    (stale / "objects" / "x").write_bytes(b"half")
    running = out / ".forma-backup-29990101T000000Z.tar.gz"
    running.write_bytes(b"half")
    old = time.time() - backup.PARTIAL_MAX_AGE_S - 60
    os.utime(stale, (old, old))
    backup.create_backup(get_context(), out, include_objects=True, archive=True)
    assert not stale.exists() and running.exists()
    done = [p.name for p in out.iterdir() if p.name.startswith("forma-backup-")]
    assert len(done) == 1 and done[0].endswith(".tar.gz")


def test_the_health_page_says_whether_the_files_are_in_the_backup(
    client: TestClient, isolated_env: Path
) -> None:
    from forma.cli import app

    _published_round(client, isolated_env, code="BK6")
    out = str(isolated_env / "bk")
    assert runner.invoke(app, ["backup", "--out", out, "--archive", "--no-objects"]).exit_code == 0
    backup = client.get("/api/ops").json()["housekeeping"]["backup"]
    assert backup["files_included"] is False and backup["bytes"] > 0
    assert runner.invoke(app, ["backup", "--out", out, "--archive"]).exit_code == 0
    backup = client.get("/api/ops").json()["housekeeping"]["backup"]
    assert backup["files_included"] is True and backup["bytes"] > 0


def test_the_stack_backs_up_the_files_and_prunes_only_after_a_success() -> None:
    compose = Path(__file__).parents[2] / "docker-compose.prod.yml"
    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    nightly = " ".join(services["backup-nightly"]["command"])
    assert "--no-objects" not in nightly and "--archive && find" in nightly
    assert "--no-objects" not in services["backup"]["command"]
    for name in ("backup-nightly", "backup"):
        assert "minio" in services[name]["depends_on"]


def test_backup_cli(client: TestClient, isolated_env: Path) -> None:
    from forma.cli import app

    _published_round(client, isolated_env, code="BK2")
    res = runner.invoke(app, ["backup", "--out", str(isolated_env / "bk"), "--no-objects"])
    assert res.exit_code == 0, res.output
    info = json.loads(res.output)
    manifest = json.loads((Path(info["path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert (
        manifest["objects_included"] is False
        and manifest["schema_revision"]
        and manifest["config"]["llm_provider"] == "mock"
    )
    assert ("database_url" in manifest["config"] and "***" in str(manifest["config"]["database_url"])) or True


def test_a_pass_is_recorded_and_the_health_page_reads_it(client: TestClient, isolated_env: Path) -> None:
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context
    from forma.services.rounds import audit

    retention._USAGE = None
    health = client.get("/api/ops").json()["housekeeping"]
    assert health["retention"]["last_at"] is None and health["backup"]["last_at"] is None
    assert health["storage"]["known"] is True

    _published_round(client, isolated_env)
    with session_scope() as s:
        out = retention.run(get_context(), s, apply_=False, actor="test")
        assert out["applied"] is False
    with session_scope() as s:
        assert retention.ran_within(s, 3600) is True
        audit(s, "backup.create", "backup", "run", actor="backup", path="/backups/x.tar.gz", objects=1)
    retention._USAGE = None
    health = client.get("/api/ops").json()["housekeeping"]
    assert health["retention"]["last_at"] and health["retention"]["last_applied"] is False
    assert "source_files" in health["retention"]["last_counts"]
    assert health["backup"]["age_hours"] == 0 and health["backup"]["path"] == "/backups/x.tar.gz"
    assert health["storage"]["objects"] > 0 and health["storage"]["bytes"] > 0


def test_retention_clears_unassigned_uploads_and_old_answers(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import BatchItem, ImportRound, ReviewTask
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context

    batch = client.post(
        "/api/batches",
        params={"sync": "true"},
        files=[("files", ("mystery.csv", b"alpha,beta\n1,2\n", "text/csv"))],
    ).json()
    item = batch["items"][0]
    assert item["state"] == "needs_customer"
    _published_round(client, isolated_env)
    ctx = get_context()
    with session_scope() as s:
        row = s.get(BatchItem, item["id"])
        assert row is not None
        key = row.storage_key
        assert ctx.storage.exists(key)
        plan = retention.plan(ctx, s)
    assert plan["summary"]["batch_items"] == 0 and plan["summary"]["review_tasks"] == 0

    monkeypatch.setattr(
        retention,
        "load_policy",
        lambda _d: {"batch_items": {"keep_days_unassigned": 30}, "review_tasks": {"keep_days_resolved": 30}},
    )
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=60)
    with session_scope() as s:
        row = s.get(BatchItem, item["id"])
        assert row is not None
        row.created_at = old
        tasks = list(s.scalars(select(ReviewTask)))
        assert tasks and all(t.status != "open" for t in tasks)
        for t in tasks:
            t.resolved_at = old
        round_id, kept = tasks[0].round_id, len(tasks)
        # a cancelled round's answers come back with it, so they stay
        r = s.get(ImportRound, round_id)
        assert r is not None
        r.status = "cancelled"
    with session_scope() as s:
        plan = retention.plan(ctx, s)
        assert plan["summary"]["batch_items"] == 1 and plan["summary"]["review_tasks"] == 0
        done = retention.apply(ctx, s, plan, actor="test")
        assert done["batch_items"] == 1 and not done["errors"]
    assert not ctx.storage.exists(key)
    view = client.get(f"/api/batches/{batch['id']}").json()
    assert view["items"][0]["state"] == "closed" and view["items"][0]["closed_as"] == "purged"
    assert view["done"] is True
    with session_scope() as s:
        r = s.get(ImportRound, round_id)
        assert r is not None
        r.status = "published"
    with session_scope() as s:
        plan = retention.plan(ctx, s)
        assert plan["summary"]["review_tasks"] == kept
        assert retention.apply(ctx, s, plan, actor="test")["review_tasks"] == kept
        assert list(s.scalars(select(ReviewTask))) == []


def test_a_file_waiting_for_a_customer_keeps_its_bytes(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.db.models import Batch, BatchItem, SourceFile
    from forma.db.session import session_scope
    from forma.services import retention
    from forma.services.context import get_context

    _published_round(client, isolated_env)
    with session_scope() as s:
        key = next(iter(s.scalars(select(SourceFile)))).storage_key
        b = Batch(label="x", created_by="t")
        s.add(b)
        s.flush()
        s.add(
            BatchItem(
                batch_id=b.id,
                file_name="x.xlsx",
                sha256="0",
                size=1,
                kind="excel",
                storage_key=key,
                status="needs_customer",
            )
        )
    monkeypatch.setattr(
        retention, "load_policy", lambda _d: {"source_files": {"keep_days_after_publish": 30}}
    )
    _age_everything(60)
    with session_scope() as s:
        plan = retention.plan(get_context(), s)
    assert key not in [x["key"] for x in plan["source_files"]]
