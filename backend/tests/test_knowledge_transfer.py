from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from tests.test_api import make_workbook
from tests.test_format_memory import approve, customer, run

from forma.core.errors import ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import Base, Correction, Customer, KnowledgeLabel, KnowledgeRelease, MappingVersion
from forma.db.session import session_scope
from forma.ingestion.store import FileStore
from forma.services import format_memory, learning
from forma.services import knowledge_gate as gate
from forma.services import knowledge_transfer as transfer
from forma.services.context import ServiceContext, get_context
from forma.storage import LocalStorage


@pytest.fixture
def source(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as client:
        cid = customer(client, "REAL-ERP")
        m = run(client, cid, make_workbook(isolated_env / "real.xlsx"))
        approve(client, m)
        yield client, m["id"], isolated_env


def reviewed(source):
    client, mid, _ = source
    response = client.post(
        "/api/knowledge-transfer/classify",
        json={
            "subject": "mapping:" + mid,
            "origin": "real_trial",
            "state": "allowed",
            "reason": "Checked fixture output and evidence",
            "include_answers": True,
        },
    )
    assert response.status_code == 200, response.text
    with session_scope() as s:
        roots = ["mapping:" + mid] + [
            gate.subject(c)
            for c in s.scalars(select(Correction).where(Correction.mapping_version_id == mid))
            if gate.allowed(s, c, strict=True)
        ]
    response = client.post("/api/knowledge-transfer/export", json={"subjects": roots})
    assert response.status_code == 200, response.text
    return transfer.read_bundle(response.content)


def target(source):
    _, _, path = source
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    ctx = get_context()
    storage = LocalStorage(path / "target-storage")
    new = ServiceContext(settings=ctx.settings, storage=storage, files=FileStore(storage))
    new.registry = copy.deepcopy(ctx.registry)
    return Session(engine), new


def test_fresh_target_preview_import_replay_revoke_and_source_unchanged(source):
    package = reviewed(source)
    s, ctx = target(source)
    with s:
        before = transfer.preview(s, ctx, package)
        assert before["status"] == "ready", before
        assert s.scalar(select(func.count()).select_from(Customer)) == 0  # preview never writes
        result = transfer.apply(s, ctx, package, before["plan_digest"], "production-admin")
        s.commit()
        mid = next(key.split(":", 1)[1] for key in result["subjects"] if key.startswith("mapping:"))
        m = s.get(MappingVersion, mid)
        assert gate.allowed(s, m, strict=True)
        assert format_memory.approved_history(s)
        # The migrated DSL must still execute against the copied original evidence.
        from forma.db.models import ImportRound
        from forma.mapping_dsl.models import MappingDocument
        from forma.services.mapping import simulate

        evidence = s.get(ImportRound, m.round_id)
        actual, problems = simulate(
            ctx,
            MappingDocument.model_validate(m.document),
            ctx.registry.get_ref(m.target_schema),
            evidence.files,
            limit=None,
        )
        assert not problems and actual and not actual.errors and actual.records
        with session_scope() as original_db:
            original = original_db.get(MappingVersion, source[1])
            original_round = original_db.get(ImportRound, original.round_id)
            expected, errors = simulate(
                get_context(),
                MappingDocument.model_validate(original.document),
                ctx.registry.get_ref(original.target_schema),
                original_round.files,
                limit=None,
            )
            assert not errors and expected and not expected.errors

            def business(rows):
                return [{k: v for k, v in r.items() if k != "source_meta"} for r in rows]

            assert business(actual.records) == business(expected.records)
        replay = transfer.preview(s, ctx, package)
        assert replay["already_imported"]
        assert not transfer.apply(s, ctx, package, replay["plan_digest"], "admin")["created"]
        assert s.scalar(select(func.count()).select_from(MappingVersion)) == 1
        transfer.revoke(s, result["id"], "admin", "Source decision was withdrawn")
        s.commit()
        assert not gate.allowed(s, m, strict=True)
        assert format_memory.approved_history(s) == []
        revoked = transfer.preview(s, ctx, package)
        with pytest.raises(ValidationFailed):
            transfer.apply(s, ctx, package, revoked["plan_digest"], "admin")
    with session_scope() as source_db:
        original = source_db.get(MappingVersion, source[1])
        assert original is not None and gate.allowed(source_db, original, strict=True)
        assert source_db.scalar(select(func.count()).select_from(KnowledgeRelease)) == 0


def test_unknown_and_synthetic_do_not_export(source, monkeypatch):
    client, mid, _ = source
    with session_scope() as s:
        row = s.get(MappingVersion, mid)
        assert not gate.allowed(s, row, strict=True)
        with pytest.raises(ValidationFailed):
            transfer.export_bundle(s, get_context(), [gate.subject(row)], "admin")
        gate.classify(s, gate.subject(row), "synthetic", "pending", "admin", "Mock fixture")
        assert not gate.allowed(s, row, strict=False)
        with pytest.raises(ValidationFailed):
            gate.classify(s, gate.subject(row), "synthetic", "allowed", "admin", "Cannot trust synthetic")
        assert not learning.anchors_for(s, None, get_context().registry.get("product")).pairs
    assert client.get("/api/knowledge-transfer").status_code == 200


def test_changed_target_rejects_stale_plan_and_schema_conflicts(source):
    package = reviewed(source)
    s, ctx = target(source)
    with s:
        plan = transfer.preview(s, ctx, package)
        c = package.manifest["customers"][0]
        s.add(Customer(code=c["code"], name="Different customer", erp_type=c["erp_type"]))
        s.commit()
        with pytest.raises(ValidationFailed):
            transfer.apply(s, ctx, package, plan["plan_digest"], "admin")
        assert s.scalar(select(func.count()).select_from(KnowledgeRelease)) == 0
        assert transfer.preview(s, ctx, package)["status"] == "blocked"
    s, ctx = target(source)
    with s:
        schema = ctx.registry.get("product")
        schema.document["title"] = "Another contract"
        assert transfer.preview(s, ctx, package)["status"] == "blocked"


def test_tamper_extra_archive_entries_and_missing_dependencies(source):
    package = reviewed(source)
    manifest = copy.deepcopy(package.manifest)
    manifest["entries"][0]["reviewed_by"] = "changed"
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ValidationFailed):
        transfer.read_bundle(raw.getvalue())
    manifest = copy.deepcopy(package.manifest)
    manifest.pop("digest")
    manifest["digest"] = stable_hash(manifest)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for key, data in package.files.items():
            z.writestr("files/" + key, data)
        z.writestr("../outside", "not extracted")
    with pytest.raises(ValidationFailed):
        transfer.read_bundle(raw.getvalue())


def test_revocation_blocks_descendant_and_changed_content_requires_review(source):
    reviewed(source)
    with session_scope() as s:
        original = s.get(MappingVersion, source[1])
        child = MappingVersion(
            customer_id=original.customer_id,
            round_id=original.round_id,
            version=2,
            status="approved",
            target_schema=original.target_schema,
            document=original.document,
            policy={**original.policy, "reused_from": original.id},
            approved_at=original.approved_at,
        )
        s.add(child)
        s.flush()
        gate.classify(s, gate.subject(child), "production", "allowed", "admin", "Reviewed next version")
        assert gate.allowed(s, child, strict=True)
        gate.classify(s, gate.subject(original), "real_trial", "revoked", "admin", "Withdraw original")
        assert not gate.allowed(s, child, strict=True)
        label = s.get(KnowledgeLabel, gate.subject(child))
        assert label.state == "allowed"  # dependency, not silent mutation/deletion
        original.policy = {**original.policy, "changed": True}
        assert not gate.allowed(s, original, strict=True)


def test_admin_and_customer_scope_required(source):
    from forma.auth import Principal, current_principal

    client = source[0]
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        subject="limited", roles=frozenset({"admin"}), customers=frozenset({"other"})
    )
    try:
        assert client.get("/api/knowledge-transfer").status_code == 403
    finally:
        client.app.dependency_overrides.pop(current_principal, None)


def pack(package):
    manifest = copy.deepcopy(package.manifest)
    manifest.pop("digest", None)
    manifest["digest"] = stable_hash(manifest)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        for key, data in package.files.items():
            z.writestr("files/" + key, data)
    return raw.getvalue()


def test_reference_rewrite_preserves_business_json_literals():
    record = {
        "customer_id": "old",
        "round_id": "round-old",
        "document": {
            "meta": {"customer_id": "old"},
            "fields": [
                {"expr": {"op": "const", "value": {"customer_id": "old", "dependencies": ["mapping:old"]}}}
            ],
        },
        "policy": {"knowledge_dependencies": ["mapping:old"], "simulation": {"customer_id": "old"}},
    }
    got = transfer._rewrite(record, {"old": "new", "round-old": "round-new"})
    assert got["customer_id"] == "new" and got["round_id"] == "round-new"
    assert got["document"]["meta"]["customer_id"] == "new"
    assert got["document"]["fields"] == record["document"]["fields"]
    assert got["policy"]["simulation"] == record["policy"]["simulation"]
    assert got["policy"]["knowledge_dependencies"] == ["mapping:new"]


def test_anchor_dependencies_only_include_headers_offered_to_mapper():
    anchors = learning.Anchors()
    anchors.add("sku", "/sku", "c1", "mapping:used")
    anchors.add("irrelevant", "/sku", "c2", "mapping:unused")
    learned = learning.Learned()
    learned.absorb(anchors, {"sku"}, 2)
    assert learned.dependencies == {"mapping:used"}


def test_few_shot_examples_respect_production_permission_and_contract(source, monkeypatch):
    from forma.core.settings import get_settings
    from forma.profiling.profile import profile_workbook
    from forma.services.mapping import _examples

    monkeypatch.setattr(get_settings(), "knowledge_policy", "strict")
    profile = profile_workbook(source[2] / "real.xlsx")
    schema = get_context().registry.get_ref("product@1")
    with session_scope() as s:
        assert _examples(s, "another-customer", profile, schema) == []
    reviewed(source)
    with session_scope() as s:
        examples = _examples(s, "another-customer", profile, schema)
        assert examples and examples[0]["mapping_id"] == source[1]
        changed = copy.deepcopy(schema)
        changed.document["title"] = "changed contract"
        assert _examples(s, "another-customer", profile, changed) == []
        gate.classify(s, "mapping:" + source[1], "real_trial", "revoked", "admin", "withdraw example")
        assert _examples(s, "another-customer", profile, schema) == []


def test_malformed_manifest_and_dependency_graph_rejected_in_preview(source):
    package = reviewed(source)
    bad = copy.deepcopy(package)
    del bad.manifest["customers"][0]["name"]
    with pytest.raises(ValidationFailed):
        transfer.read_bundle(pack(bad))
    bad = copy.deepcopy(package)
    mapping = next(e for e in bad.manifest["entries"] if e["subject"].startswith("mapping:"))
    mapping["record"]["policy"]["knowledge_dependencies"] = ["mapping:missing"]
    s, ctx = target(source)
    with s:
        assert transfer.preview(s, ctx, transfer.read_bundle(pack(bad)))["status"] == "blocked"
        answer = next((e for e in bad.manifest["entries"] if e["subject"].startswith("correction:")), None)
        if answer is None:
            answer = {
                "subject": "correction:cycle",
                "origin": "real_trial",
                "reviewed_by": "admin",
                "review_reason": "fixture",
                "record": {
                    "id": "cycle",
                    "customer_id": mapping["record"]["customer_id"],
                    "kind": "alias",
                    "target": "/sku",
                    "header": "sku",
                    "before": None,
                    "after": {},
                    "actor": "admin",
                    "mapping_version_id": mapping["record"]["id"],
                    "round_id": mapping["record"]["round_id"],
                    "status": "confirmed",
                },
            }
            bad.manifest["entries"].append(answer)
        mapping["record"]["policy"]["knowledge_dependencies"] = [answer["subject"]]
        plan = transfer.preview(s, ctx, transfer.read_bundle(pack(bad)))
        assert plan["status"] == "blocked" and any("Cyclic" in e for e in plan["errors"])
        assert s.scalar(select(func.count()).select_from(KnowledgeRelease)) == 0


def test_overlapping_release_reuses_evidence_and_cannot_revive_revoked_source(source):
    package = reviewed(source)
    s, ctx = target(source)
    with s:
        plan = transfer.preview(s, ctx, package)
        transfer.apply(s, ctx, package, plan["plan_digest"], "admin")
        s.commit()
        other = copy.deepcopy(package)
        # New packaging of the same reviewed records, without changing their contents.
        other.manifest["roots"] = [e["subject"] for e in other.manifest["entries"]][::-1]
        other.manifest["export_note"] = "second release"
        other = transfer.read_bundle(pack(other))
        plan2 = transfer.preview(s, ctx, other)
        assert plan2["status"] == "ready", plan2
        transfer.apply(s, ctx, other, plan2["plan_digest"], "admin")
        s.commit()
        assert s.scalar(select(func.count()).select_from(MappingVersion)) == 1
        mapping = s.scalar(select(MappingVersion))
        schema = ctx.registry.get_ref(mapping.target_schema)
        fp = format_memory.mapping_fingerprint(mapping)
        assert format_memory.select_baseline(s, mapping.customer_id, schema, fp).id == mapping.id
        result = transfer.revoke(s, package.manifest["digest"], "admin", "withdraw upstream")
        s.commit()
        assert mapping.id in result["review_mapping_ids"]
        assert format_memory.select_baseline(s, mapping.customer_id, schema, fp) is None
