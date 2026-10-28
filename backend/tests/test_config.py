"""Settings edited in the web app (ADR-017): AI provider + encrypted key, approval policy, target formats,
customer master data — validated, audited, and applied without a restart."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import FILE_NAME, _run, _upload, make_workbook
from tests.test_custom_schema import CATALOG

REVIEWER = {"X-Forma-User": "reviewer"}


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_ai_settings_round_trip_key_is_encrypted_and_never_returned(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.ai.llm import AnthropicProvider, Usage
    from forma.db.models import AppSetting, AuditLog
    from forma.db.session import session_scope
    from forma.services.context import get_context

    c = client
    first = c.get("/api/settings/ai").json()
    assert first["offline"] is True and first["api_key_set"] is False and "anthropic" in first["providers"]
    assert c.get("/api/settings/ai", headers=REVIEWER).status_code == 403
    assert c.put("/api/settings/ai", json={"provider": "nope"}).status_code == 422
    assert c.put("/api/settings/ai", json={"timeout_s": 0}).status_code == 422
    assert c.post("/api/settings/ai/test").json()["offline"] is True

    key = "sk-ant-test-0123456789abcd"
    saved = c.put(
        "/api/settings/ai", json={"provider": "anthropic", "model": "claude-x", "api_key": key}
    ).json()
    assert saved["api_key_set"] and saved["api_key_hint"] == "…abcd" and saved["api_key_source"] == "ui"
    assert saved["active_provider"] == "anthropic" and saved["offline"] is False and saved["warning"] is None
    assert saved["sources"]["provider"] == "ui" and key not in json.dumps(saved)
    # this process uses it at once; the key is stored encrypted and the audit log names the field only
    assert not get_context().llm.is_mock and get_context().llm.model == "claude-x"
    with session_scope() as s:
        stored = json.dumps(s.get(AppSetting, "ai").value)  # type: ignore[union-attr]
        audits = [a.details for a in s.query(AuditLog).filter(AuditLog.action == "settings.ai.update")]
    assert key not in stored and "api_key_enc" in stored
    assert audits and all(key not in json.dumps(d) for d in audits)

    # connectivity check goes through the provider and lands in the LLM ledger
    def fake(self: Any, **kw: Any) -> tuple[str, Usage]:
        return '{"ok": true}', Usage(12, 3, 1, 5.0, kw["model"])

    monkeypatch.setattr(AnthropicProvider, "complete_json", fake)
    probe = c.post("/api/settings/ai/test").json()
    assert probe["ok"] is True and probe["provider"] == "anthropic"
    assert c.get("/api/dashboard").json()["llm"]["calls"] == 1

    # removing the key puts the server value back: here none → offline, with the reason shown
    cleared = c.put("/api/settings/ai", json={"api_key": ""}).json()
    assert (
        cleared["api_key_set"] is False and cleared["offline"] is True and "no API key" in cleared["warning"]
    )
    back = c.put("/api/settings/ai", json={"provider": None, "model": None}).json()
    assert back["provider"] == "mock" and back["sources"]["provider"] == "server"


def test_other_processes_follow_within_the_refresh_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.core.settings import get_settings
    from forma.ingestion.store import FileStore
    from forma.services.context import ServiceContext
    from forma.storage import make_storage

    s = get_settings().model_copy(update={"config_refresh_s": 0.0})
    worker = ServiceContext(settings=s, storage=make_storage(s), files=FileStore(make_storage(s)))
    assert worker.llm.is_mock and worker.policy.policy["auto_approve"]["max_warning_findings"] == 5
    client.put("/api/settings/ai", json={"provider": "openai", "api_key": "sk-test-0000000000000"})
    policy = copy.deepcopy(client.get("/api/settings/policy").json()["effective"])
    policy["auto_approve"]["max_warning_findings"] = 9
    assert client.put("/api/settings/policy", json={"policy": policy}).status_code == 200
    assert not worker.llm.is_mock and worker.policy.policy["auto_approve"]["max_warning_findings"] == 9


def test_secrets_need_a_key_in_production(tmp_path: Path) -> None:
    from forma.core import secrets
    from forma.core.errors import ValidationFailed
    from forma.core.settings import Settings

    with pytest.raises(ValidationFailed, match="FORMA_SECRET_KEY"):
        secrets.encrypt(Settings(env="prod", data_dir=tmp_path), "sk-1")
    token = secrets.encrypt(Settings(env="prod", secret_key="k1", data_dir=tmp_path), "sk-123456789")
    assert secrets.decrypt(Settings(env="prod", secret_key="k1", data_dir=tmp_path), token) == "sk-123456789"
    assert secrets.decrypt(Settings(env="prod", secret_key="k2", data_dir=tmp_path), token) is None
    dev = Settings(env="dev", data_dir=tmp_path)
    assert (
        secrets.decrypt(dev, secrets.encrypt(dev, "sk-dev")) == "sk-dev"
        and (tmp_path / ".secret_key").exists()
    )


def test_policy_edit_validates_applies_and_resets(client: TestClient, isolated_env: Path) -> None:
    c = client
    view = c.get("/api/settings/policy").json()
    assert view["source"] == "file" and view["effective"]["auto_approve"]["min_mapping_confidence"] == 0.95
    bad = copy.deepcopy(view["effective"])
    bad["auto_approve"]["min_mapping_confidence"] = 2
    bad["auto_approve"]["allowed_drift"] = ["unrelated_file"]
    bad["review"]["priority_weights"]["nonsense"] = 1
    r = c.put("/api/settings/policy", json={"policy": bad})
    assert r.status_code == 422 and len(r.json()["details"]["problems"]) == 3
    assert (
        c.put("/api/settings/policy", json={"policy": view["effective"]}, headers=REVIEWER).status_code == 403
    )

    # trust first-time customers and medium-confidence fields: the next upload is auto-approved
    lenient = copy.deepcopy(view["effective"])
    lenient["auto_approve"].update(require_previous_approval_for_auto=False, min_field_confidence_high=0.5)
    saved = c.put("/api/settings/policy", json={"policy": lenient}).json()
    assert saved["source"] == "ui" and saved["updated_by"] == "operator"
    assert c.get("/api/policy").json()["auto_approve"]["require_previous_approval_for_auto"] is False
    cust = c.post("/api/customers", json={"code": "P1", "name": "p"}).json()
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    assert _run(c, rid)["result"]["mapping_status"] == "approved"
    audit = c.get("/api/audit?entity_id=policy").json()
    assert audit[0]["action"] == "settings.policy.update" and audit[0]["details"]["before"]["auto_approve"]

    assert c.delete("/api/settings/policy").json()["source"] == "file"
    assert c.get("/api/policy").json()["auto_approve"]["require_previous_approval_for_auto"] is True


def test_new_format_from_the_ui_end_to_end(client: TestClient, isolated_env: Path) -> None:
    from forma.core.settings import get_settings

    c = client
    preview = c.post("/api/schemas/preview", json={"document": CATALOG}).json()
    assert preview["ref"] == "catalog@1" and preview["previous"] is None
    assert {f["pointer"] for f in preview["fields"]} >= {"/item_code", "/stock/*/on_hand"}
    assert c.post("/api/schemas/preview", json={"document": {"type": "object"}}).status_code == 422
    assert c.post("/api/schemas", json={"document": CATALOG}, headers=REVIEWER).status_code == 403
    assert c.post("/api/schemas", json={"document": CATALOG}).json()["ref"] == "catalog@1"
    listed = {s["ref"]: s for s in c.get("/api/schemas").json()}
    assert listed["catalog@1"]["source"] == "ui" and listed["product@1"]["source"] == "file"
    assert listed["product@1"]["is_default"] and not listed["catalog@1"]["is_default"]
    s = get_settings()
    assert not list(s.resolve(s.target_schema_dir).glob("catalog*"))  # stored in the DB, not in the image

    # a second version shows its diff before it is registered
    v2 = copy.deepcopy(CATALOG)
    v2["properties"]["barcode"] = {"type": "string", "x-forma": {"semantic": "barcode"}}
    v2["x-forma"].pop("version")
    p2 = c.post("/api/schemas/preview", json={"document": v2}).json()
    assert p2["ref"] == "catalog@2" and p2["previous"] == "catalog@1" and p2["diff"]["added"] == ["/barcode"]

    # the customer is switched to the new format; their next round uses it without naming it
    cust = c.post("/api/customers", json={"code": "K1", "name": "k"}).json()
    assert c.patch(f"/api/customers/{cust['id']}", json={"default_target_schema": "nope"}).status_code == 422
    patched = c.patch(f"/api/customers/{cust['id']}", json={"default_target_schema": "catalog"}).json()
    assert patched["default_target_schema"] == "catalog"
    rid = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()["id"]
    assert c.get(f"/api/rounds/{rid}").json()["target_schema"] == "catalog"
    _upload(c, rid, make_workbook(isolated_env / FILE_NAME))
    mv = c.get(f"/api/mappings/{_run(c, rid)['result']['mapping_version_id']}").json()
    assert mv["validation"]["ok"] and mv["policy"]["decision"] == "review"
    assert c.post(f"/api/mappings/{mv['id']}/approve", json={}).status_code == 200
    job = c.post(f"/api/rounds/{rid}/transform", params={"sync": "true"}).json()
    ds = c.get(f"/api/jobs/{job['job_id']}").json()["result"]["dataset_version_id"]
    assert not [f for f in c.get(f"/api/datasets/{ds}").json()["findings"] if f["severity"] == "error"]
    c.post(f"/api/datasets/{ds}/approve", json={})
    exp = c.post(f"/api/datasets/{ds}/export", params={"fmt": "json"}).json()
    out = json.loads(c.get(f"/api/exports/{exp['id']}/download").content)
    assert len(out) == 12 and all(set(r) <= set(CATALOG["properties"]) for r in out)
    assert {"item_code", "item_name", "price", "state"} <= set(out[0])

    # a global default applies to customers without their own
    assert c.put("/api/settings/default-format", json={"target_schema": "nope"}).status_code == 422
    assert c.put("/api/settings/default-format", json={"target_schema": "catalog@1"}).json() == {
        "target_schema": "catalog@1"
    }
    other = c.post("/api/customers", json={"code": "K2", "name": "k2"}).json()
    rid2 = c.post("/api/rounds", json={"customer_id": other["id"]}).json()["id"]
    assert c.get(f"/api/rounds/{rid2}").json()["target_schema"] == "catalog@1"
    assert c.get("/api/settings/semantics").json()["price.amount"]


def test_customer_master_data_is_edited_in_the_app(client: TestClient) -> None:
    c = client
    a = c.post("/api/customers", json={"code": "A1", "name": "Alpha"}).json()
    c.post("/api/customers", json={"code": "B1", "name": "Beta"})
    assert c.patch(f"/api/customers/{a['id']}", json={"code": "B1"}).status_code == 422
    assert c.patch(f"/api/customers/{a['id']}", json={"name": " "}).status_code == 422
    assert c.patch(f"/api/customers/{a['id']}", json={"notes": "x"}, headers=REVIEWER).status_code == 403
    up = c.patch(f"/api/customers/{a['id']}", json={"code": "A2", "erp_type": "SAP", "notes": "moved"}).json()
    assert (up["code"], up["erp_type"], up["notes"], up["name"]) == ("A2", "SAP", "moved", "Alpha")
    assert (
        c.patch(f"/api/customers/{a['id']}", json={"erp_type": None, "active": False}).json()["erp_type"]
        is None
    )
    audit = [x for x in c.get(f"/api/audit?entity_id={a['id']}").json() if x["action"] == "customer.update"]
    assert audit[-1]["details"]["before"]["code"] == "A1" and audit[-1]["details"]["after"]["code"] == "A2"


def test_paste_a_key_and_connect(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from forma.ai.llm import AnthropicProvider, LLMError, OpenAICompatibleProvider, Usage

    def works(self: Any, **kw: Any) -> tuple[str, Any]:
        return '{"ok": true}', Usage(9, 2, 1, 3.0, kw["model"])

    def rejects(self: Any, **kw: Any) -> tuple[str, Any]:
        raise LLMError("anthropic HTTP 401: invalid x-api-key")

    c = client
    url = "/api/settings/ai/connect"
    assert c.post(url, json={"api_key": "sk-ant-x"}, headers=REVIEWER).status_code == 403
    unknown = c.post(url, json={"api_key": "gw-123456789"})
    assert unknown.status_code == 422 and unknown.json()["details"]["needs_provider"] is True
    # a key that does not work is refused and never stored
    monkeypatch.setattr(AnthropicProvider, "complete_json", rejects)
    bad = c.post(url, json={"api_key": "sk-ant-wrong-0000000000"})
    assert (
        bad.status_code == 422 and "did not work" in bad.json()["message"] and "401" in bad.json()["message"]
    )
    assert c.get("/api/settings/ai").json()["api_key_set"] is False

    # paste an Anthropic key (stray spaces and newline included): provider and model are chosen for you
    monkeypatch.setattr(AnthropicProvider, "complete_json", works)
    ok = c.post(url, json={"api_key": "  sk-ant-good-123456789abc \n"}).json()
    assert ok["test"]["ok"] and ok["ai"]["provider"] == "anthropic" and ok["ai"]["model"] == "claude-opus-5-5"
    assert ok["ai"]["api_key_hint"] == "…9abc" and ok["ai"]["offline"] is False
    # an OpenAI key switches the provider and moves the model to that provider's default
    monkeypatch.setattr(OpenAICompatibleProvider, "complete_json", works)
    oa = c.post(url, json={"api_key": "sk-proj-openai-00000000000"}).json()
    assert (oa["ai"]["provider"], oa["ai"]["model"]) == ("openai", "gpt-4.1-mini")
    llm = c.get("/api/dashboard").json()["llm"]
    assert llm["calls"] == 2 and llm["cost_usd"] > 0  # both checks are in the ledger
