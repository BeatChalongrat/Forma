"""Approved ERP layouts survive A → B → A, without crossing customers or target contracts."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _upload, make_workbook
from tests.test_batches import _batch

from forma.core.hashing import stable_hash
from forma.db.models import MappingVersion
from forma.db.session import session_scope
from forma.fingerprint.fingerprint import fingerprint_profile
from forma.profiling.profile import profile_workbook
from forma.services import batches, format_memory
from forma.services.context import get_context


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def customer(c: TestClient, code: str = "MEM") -> str:
    return str(c.post("/api/customers", json={"code": code, "name": code}).json()["id"])


def run(
    c: TestClient, cid: str, path: Path, target: str = "product@1", *, use_llm: bool = False
) -> dict[str, Any]:
    r = c.post("/api/rounds", json={"customer_id": cid, "target_schema": target}).json()
    _upload(c, r["id"], path, name=path.name)
    response = c.post(f"/api/rounds/{r['id']}/run", json={"sync": True, "use_llm": use_llm})
    assert response.status_code == 200, response.text
    job = c.get(f"/api/jobs/{response.json()['job_id']}").json()
    assert job["status"] == "done", job.get("error")
    mid = job["result"]["mapping_version_id"]
    return c.get(f"/api/mappings/{mid}").json()  # type: ignore[no-any-return]


def approve(c: TestClient, m: dict[str, Any]) -> None:
    result = c.post(f"/api/mappings/{m['id']}/approve", json={})
    assert result.status_code == 200, result.text


def test_a_b_a_reuses_history_and_new_values_without_ai(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = client
    cid = customer(c)
    a = run(c, cid, make_workbook(isolated_env / "A.xlsx"))
    approve(c, a)
    b = run(c, cid, make_workbook(isolated_env / "B.xlsx", rename_qty=True))
    approve(c, b)
    assert c.get(f"/api/mappings/{a['id']}").json()["status"] == "superseded"
    with session_scope() as s:
        fp = fingerprint_profile(profile_workbook(isolated_env / "A.xlsx"))
        identified = batches.match_customer(s, fp)
        assert identified["customer_id"] == cid
        assert identified["mapping_version_id"] == a["id"]

    # Price changes without changing the layout. No model may be needed to remap it.
    path = make_workbook(isolated_env / "A_again.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["สินค้า"]["G4"] = "120.00"
    wb.save(path)

    def no_model(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("known format must not invoke an AI mapping agent")

    for name in ("schema_agent", "mapping_agent", "adaptation_agent"):
        monkeypatch.setattr(f"forma.services.mapping.{name}", no_model)
    again = run(c, cid, path, use_llm=True)
    assert again["policy"]["source"] == "memory"
    assert again["policy"]["reused_from"] == a["id"]
    assert again["version"] > b["version"]
    assert again["document"]["fields"] == a["document"]["fields"]
    assert again["sample"][0]["prices"][0]["amount"] == 120
    r = c.get(f"/api/rounds/{again['round_id']}").json()
    assert r["summary"]["format_memory"]["mapping_id"] == a["id"]
    assert r["summary"]["format_memory"]["status"] == "reused"
    # Bulk identification also remembers the older layout, not only the latest one.
    view = _batch(c, [("old_layout.xlsx", path.read_bytes())], auto_publish=False)
    assert view["items"][0]["customer"]["id"] == cid
    assert view["items"][0]["format_memory"]["status"] == "reused"


def test_schema_change_remaps_then_switching_back_reuses_history(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    cid = customer(c)
    path = make_workbook(isolated_env / "same.xlsx")
    old = run(c, cid, path)
    approve(c, old)
    document = c.get("/api/schemas/product@1").json()["document"]
    new = copy.deepcopy(document)
    new["properties"]["new_note"] = {"type": "string"}
    registered = c.post("/api/schemas", json={"document": new})
    assert registered.status_code == 201, registered.text
    ref = registered.json()["ref"]
    changed = run(c, cid, path, ref)
    assert changed["policy"]["source"] != "memory"
    assert changed["target_schema"] == ref
    assert changed["parent_id"] is None
    assert changed["policy"]["sandbox"] is None  # never compare different output contracts
    approve(c, changed)
    back = run(c, cid, path)
    assert back["policy"]["reused_from"] == old["id"]
    assert back["target_schema"] == "product@1"


def test_round_pins_target_before_latest_alias_changes(client: TestClient, isolated_env: Path) -> None:
    c = client
    cid = customer(c)
    original = run(c, cid, make_workbook(isolated_env / "same.xlsx"), target="product")
    approve(c, original)
    r = c.get(f"/api/rounds/{original['round_id']}").json()
    assert r["target_schema"] == "product@1"
    schema = c.get("/api/schemas/product@1").json()["document"]
    assert c.post("/api/schemas", json={"document": schema}).json()["ref"] == "product@2"
    jid = c.post(f"/api/rounds/{r['id']}/transform", params={"sync": True}).json()["job_id"]
    assert c.get(f"/api/jobs/{jid}").json()["status"] == "done"


def test_library_excludes_unapproved_rejected_and_other_customers(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    cid = customer(c)
    path = make_workbook(isolated_env / "same.xlsx")
    original = run(c, cid, path)
    approve(c, original)
    other = customer(c, "OTHER")
    ctx = get_context()
    with session_scope() as s:
        approved = s.get(MappingVersion, original["id"])
        assert approved is not None
        schema = ctx.registry.get_ref("product@1")
        fp = format_memory.mapping_fingerprint(approved)
        assert format_memory.select_baseline(s, other, schema, fp) is None
        approved.status = "superseded"
        approved.approved_at = None  # a superseded draft was never approved
        s.flush()
        assert format_memory.select_baseline(s, cid, schema, fp) is None
        approved.status = "rejected"
        s.flush()
        assert not format_memory.approved_history(s, cid)


def test_schema_hash_guard_and_latest_correction_win(client: TestClient, isolated_env: Path) -> None:
    c = client
    cid = customer(c)
    path = make_workbook(isolated_env / "same.xlsx")
    original = run(c, cid, path)
    approve(c, original)
    doc = copy.deepcopy(original["document"])
    price = next(coll for coll in doc["collections"] if coll["target"] == "/prices")
    next(f for f in price["fields"] if f["target"].endswith("/amount"))["expr"] = {"op": "const", "value": 99}
    edited = c.post(f"/api/mappings/{original['id']}/edit", json={"document": doc})
    assert edited.status_code == 201, edited.text
    correction = edited.json()
    approve(c, correction)
    again = run(c, cid, path)
    assert again["policy"]["reused_from"] == correction["id"]
    assert again["sample"][0]["prices"][0]["amount"] == 99
    schema = copy.deepcopy(get_context().registry.get_ref("product@1"))
    schema.document["description"] = "changed without a version bump"
    with session_scope() as s:
        m = s.get(MappingVersion, again["id"])
        assert m is not None
        assert not format_memory.compatible(m, schema)
        assert (m.policy or {})["target_schema_hash"] != stable_hash(schema.document)
        assert format_memory.select_baseline(s, cid, schema, format_memory.mapping_fingerprint(m)) is None


def test_same_layout_never_bypasses_invalid_new_data(client: TestClient, isolated_env: Path) -> None:
    c = client
    cid = customer(c)
    path = make_workbook(isolated_env / "same.xlsx")
    original = run(c, cid, path)
    approve(c, original)
    wb = openpyxl.load_workbook(path)
    wb["สินค้า"]["G4"] = "-100.00"
    wb.save(path)
    again = run(c, cid, path)
    assert again["policy"]["source"] == "memory"
    assert again["status"] != "approved"
    assert again["policy"]["decision"] != "auto_approve"


def test_two_similar_formats_of_one_customer_are_not_ambiguous(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = client
    cid = customer(c)
    a = run(c, cid, make_workbook(isolated_env / "A.xlsx"))
    approve(c, a)
    b = run(c, cid, make_workbook(isolated_env / "B.xlsx", rename_qty=True))
    approve(c, b)
    with session_scope() as s:
        formats = format_memory.catalog(s)
        assert len(formats) == 2
        fp = copy.deepcopy(formats[0][1])
        fp.strict = "unseen"
        fp.loose = "unseen"
        fp.tables[0].headers.append("delivery note")
        fp.tables[0].types.append("string")
        assert batches.match_customer(s, fp)["customer_id"] == cid
        schema = get_context().registry.get_ref("product@1")
        # Unseen but similar layout remains an adaptation, not exact memory reuse.
        candidate = format_memory.select_baseline(s, cid, schema, fp)
        assert candidate is not None
        assert batches.match_customer(s, fp)["how"] == "similar"


def test_changed_layout_calls_ai_adaptation(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.services import mapping

    c = client
    cid = customer(c)
    original = run(c, cid, make_workbook(isolated_env / "A.xlsx"))
    approve(c, original)
    calls: list[str] = []
    original_agent = mapping.adaptation_agent

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append("adaptation")
        return original_agent(*args, **kwargs)

    monkeypatch.setattr(mapping, "adaptation_agent", spy)
    changed = run(c, cid, make_workbook(isolated_env / "B.xlsx", rename_qty=True), use_llm=True)
    assert calls == ["adaptation"]
    assert changed["policy"]["source"] != "memory"
    assert changed["policy"]["reused_from"] is None
    job_id = c.post(f"/api/rounds/{changed['round_id']}/transform", params={"sync": True}).json()["job_id"]
    job = c.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and "approve a mapping" in job["error"]


def test_transform_rejects_cross_customer_and_cross_schema_mapping(
    client: TestClient, isolated_env: Path
) -> None:
    from forma.core.errors import ValidationFailed
    from forma.services import datasets, rounds

    c = client
    cid = customer(c)
    original = run(c, cid, make_workbook(isolated_env / "A.xlsx"))
    approve(c, original)
    other = customer(c, "OTHER")
    ctx = get_context()
    with session_scope() as s:
        r = rounds.create_round(s, other, target_schema="product@1")
        with pytest.raises(ValidationFailed, match="another customer or target schema"):
            datasets.transform_round(ctx, s, r.id, mapping_version_id=original["id"])
        schema = copy.deepcopy(ctx.registry.get_ref("product@1").document)
        next_schema = ctx.registry.register(schema, persist=False)
        another = rounds.create_round(s, cid, target_schema=next_schema.ref)
        with pytest.raises(ValidationFailed, match="another customer or target schema"):
            datasets.transform_round(ctx, s, another.id, mapping_version_id=original["id"])
