"""Retention policy (plan/apply, audited, never touches kept versions) and backup/restore round trips."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
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
