"""Read-only-to-dev audit probes. Uses private SQLite/storage and a mock model only.

Run from the repository root with backend\.venv\Scripts\python.
Writes probe-results.json beside this script; it does not modify application code.
The observations describe defects, not assertions of desired behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def run(root: Path) -> dict:
    # Do not inherit a live database, provider, credentials or remote storage.
    for key in list(os.environ):
        if key.startswith(("FORMA_", "OPENAI_", "ANTHROPIC_", "DEEPSEEK_")):
            os.environ.pop(key)
    os.environ.update(
        FORMA_DATABASE_URL=f"sqlite:///{root}/audit.db",
        FORMA_DATA_DIR=str(root / "data"),
        FORMA_LLM_PROVIDER="mock",
        FORMA_LLM_MOCK_ONLY="true",
        FORMA_MAPPING_WORKFLOW="legacy",
        FORMA_AUTH_MODE="dev",
        FORMA_STORAGE_BACKEND="local",
    )
    os.chdir(ROOT)

    from fastapi.testclient import TestClient
    from sqlalchemy import event, select
    from sqlalchemy.orm import Session

    from tests.conftest import clear_forma_caches
    from tests.test_api import make_workbook
    from forma.api.app import create_app
    from forma.db.models import Customer
    from forma.db.session import session_scope
    from forma.services import backup, datasets
    from forma.services.context import get_context

    clear_forma_caches()
    findings: dict = {}

    def checked(response):
        assert response.is_success, (response.status_code, response.text)
        return response.json()

    try:
        with TestClient(create_app()) as c:
            original_commit = Session.commit
            fault = {"injected": False}

            def mark(session, *_args):
                if any(isinstance(row, Customer) and row.code == "AUDIT-COMMIT" for row in session.new):
                    session.info["audit_fault"] = True

            def failing_commit(session):
                if session.info.pop("audit_fault", False) and not fault["injected"]:
                    fault["injected"] = True
                    raise RuntimeError("audit: injected commit failure")
                return original_commit(session)

            event.listen(Session, "after_flush", mark)
            try:
                with patch.object(Session, "commit", failing_commit):
                    response = c.post("/api/customers", json={"code": "AUDIT-COMMIT", "name": "Commit probe"})
            finally:
                event.remove(Session, "after_flush", mark)
            with session_scope() as session:
                persisted = session.scalar(select(Customer).where(Customer.code == "AUDIT-COMMIT")) is not None
            findings["commit_failure"] = {
                "injected": fault["injected"], "http_status": response.status_code,
                "record_persisted": persisted,
            }

            customer = checked(c.post("/api/customers", json={"code": "AUDIT-DATA", "name": "Private mock audit"}))
            rnd = checked(c.post("/api/rounds", json={"customer_id": customer["id"], "label": "Audit"}))
            rid = rnd["id"]
            workbook = make_workbook(root / "audit.xlsx")
            checked(c.post(f"/api/rounds/{rid}/files", files=[("files", ("audit.xlsx", workbook.read_bytes()))]))
            job = checked(c.post(f"/api/rounds/{rid}/run", json={"sync": True}))
            assert checked(c.get(f"/api/jobs/{job['job_id']}"))["status"] == "done"
            mapping = checked(c.get(f"/api/rounds/{rid}"))["mappings"][0]
            checked(c.post(f"/api/mappings/{mapping['id']}/approve", json={"comment": "Private test"}))
            job = checked(c.post(f"/api/rounds/{rid}/transform", params={"sync": "true"}))
            done = checked(c.get(f"/api/jobs/{job['job_id']}"))
            assert done["status"] == "done", done
            dsid = done["result"]["dataset_version_id"]

            def edit(identity, value):
                return checked(c.post(f"/api/datasets/{dsid}/changes", json={
                    "operations": [{"kind": "set_cell", "identity": identity,
                                    "target": "/product_name", "value": value}],
                }))["dataset"]

            first = edit("A001", "Tab A")
            second = edit("A002", "Tab B")
            rows = checked(c.get(f"/api/datasets/{second['id']}/rows"))["rows"]
            findings["stale_edit"] = {
                "first_version": first["version"], "second_version": second["version"],
                "same_parent": first["parent_id"] == second["parent_id"],
                "first_edit_retained_in_second": next(r for r in rows if r["sku"] == "A001")["product_name"] == "Tab A",
            }

            op = {"kind": "set_field", "target": "/product_name", "value": "Persistent edit",
                  "identities": ["A001", "A005"],
                  "where": {"op": "eq", "args": [{"op": "col", "name": "/status"}, {"op": "const", "value": "active"}]}}
            preview = checked(c.post(f"/api/datasets/{dsid}/preview-changes", json={"operations": [op]}))
            checked(c.post(f"/api/datasets/{dsid}/changes", json={"operations": [op], "kind": "persistent"}))
            ctx = get_context()
            with session_scope() as session:
                dv = datasets.get_dataset(session, dsid)
                original = datasets.records_of(ctx, dv)
                schema = ctx.registry.get_ref(mapping["target_schema"])
                changed, impact = datasets.apply_persistent_overrides(session, customer["id"], schema, original)
            findings["persistent_selection"] = {
                "preview": preview["impact"], "next_run": impact,
                "inactive_A005_changed": next(r for r in changed if r["sku"] == "A005")["product_name"] == "Persistent edit",
            }

            checked(c.post(f"/api/rounds/{rid}/cancel", json={"reason": "Audit cancellation"}))
            approved = c.post(f"/api/datasets/{second['id']}/approve", json={"acknowledge_anomalies": True})
            exported = c.post(f"/api/datasets/{second['id']}/export", params={"fmt": "jsonl"})
            findings["cancelled_round"] = {
                "approve_http": approved.status_code, "export_http": exported.status_code,
                "final_status": checked(c.get(f"/api/rounds/{rid}"))["status"],
            }
            saved = backup.create_backup(ctx, root / "backups", include_objects=True, archive=False)

        # Restore to a SECOND empty private database, with one damaged backup object.
        source = Path(saved["path"])
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        entry = manifest["objects"][0]
        corrupt = source / "objects" / entry["key"]
        corrupt.write_bytes(corrupt.read_bytes() + b"\nAUDIT-CORRUPTION\n")
        clear_forma_caches()
        os.environ["FORMA_DATABASE_URL"] = f"sqlite:///{root}/restore.db"
        os.environ["FORMA_DATA_DIR"] = str(root / "restored-data")
        restored_ctx = get_context()
        restored = backup.restore_backup(restored_ctx, source, verify=True)
        with restored_ctx.storage.open(entry["key"]) as file:
            actual = hashlib.sha256(file.read()).hexdigest()
        findings["restore_checksum"] = {
            "verify": True, "missing_objects": restored["missing_objects"],
            "restored_hash_matches_manifest": actual == entry["sha256"],
            "corrupted_object_was_restored": actual != entry["sha256"],
        }
        return findings
    finally:
        clear_forma_caches()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="forma-audit-") as folder:
        results = run(Path(folder))
    output = Path(__file__).with_name("probe-results.json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
