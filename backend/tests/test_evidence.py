"""Confidence from evidence (ADR-059): the column card, FORMA looking before the model maps, anchors from other
customers, the matcher-vs-model agreement, history-relative plausibility, spot checks and question groups."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_agents import _doc, _draft, _profile
from tests.test_api import _run, _upload, make_workbook
from tests.test_self_check import FakeBench, _bench, _responder

from forma.ai import agents, plausibility
from forma.ai.agents import _column_card
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.policy import DEFAULT_POLICY, PolicyEngine, validate_policy
from forma.ai.workbench import RunReport
from forma.mapping_dsl.models import FieldMapping, MappingDocument, col
from forma.profiling.profile import ColumnProfile, WorkbookProfile, profile_workbook
from forma.schema_registry import TargetSchema
from forma.services import calibration
from forma.services.learning import Anchors, Learned
from forma.services.mapping import _agreement, _ground_confidence, _sampled_for_audit


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


# --------------------------------------------------------------------------------------------
# 1. the column card: what the model sees before it maps
# --------------------------------------------------------------------------------------------
def test_profile_keeps_top_values_and_the_card_shows_them(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["code", "status", "price"])
    for i in range(1, 13):
        ws.append([f"A{i:03d}", "sell" if i % 4 else "stop", i * 10.5])
    wb.save(tmp_path / "p.xlsx")
    prof = profile_workbook(tmp_path / "p.xlsx")
    status = next(c for c in prof.tables[0].columns if c.name == "status")
    assert status.top_values == [["sell", 9], ["stop", 3]]  # by frequency, with counts
    card = _column_card(status, rows=5, chars=200)
    assert (
        card["top_values"] == [["sell", 9], ["stop", 3]] and card["distinct"] == 2 and "samples" not in card
    )
    price = next(c for c in prof.tables[0].columns if c.name == "price")
    pc = _column_card(price, rows=5, chars=200)
    assert pc["min"] == "10.5" and pc["max"] == "126.0" and len(pc["top_values"]) == 10
    # a profile stored before top values existed still shows its samples
    old = WorkbookProfile.from_dict(json.loads(json.dumps(prof.to_dict())))
    old.tables[0].columns[1].top_values = []
    assert _column_card(old.tables[0].columns[1], rows=5, chars=200)["samples"] == ["sell", "stop"]
    assert ColumnProfile("x", 0, "string", 0.0, 1.0, 1, ["a"]).top_values == []  # the default for stored rows


# --------------------------------------------------------------------------------------------
# 2. FORMA looks at the uncertain columns before the model's first turn
# --------------------------------------------------------------------------------------------
def test_ground_looks_at_uncertain_columns_and_their_alternatives(
    tmp_path: Path, schema: TargetSchema
) -> None:
    bench, _ = _bench(tmp_path, schema)
    doc = _doc()
    for f in [*doc.fields, *doc.collections[0].fields]:
        f.confidence = 0.95
    doc.fields[1].confidence = 0.5  # /product_name from ชื่อสินค้า, unsure
    looked = bench.ground(doc)
    columns = [x["request"]["column"] for x in looked]
    assert columns[0] == "ชื่อสินค้า"  # the column read comes first
    assert "สถานะ" in columns  # a free text column that could hold the name instead
    assert "รหัสสินค้า" not in columns  # taken by /sku
    assert looked[0]["answer"]["values"][0]["value"] == "สินค้า 1"
    doc.fields[1].confidence = 0.95
    assert bench.ground(doc) == []  # nothing is uncertain: nothing to look at


def test_the_agent_sees_what_forma_looked_at(profile: WorkbookProfile, schema: TargetSchema) -> None:
    class Bench(FakeBench):
        def ground(self, doc: MappingDocument) -> list[dict[str, Any]]:
            return [
                {"request": {"what": "distinct", "column": "สถานะ"}, "answer": {"values": [{"value": "ขาย"}]}}
            ]

    prompts: list[str] = []
    good = _doc().model_dump(mode="json", exclude_none=True)
    client = LLMClient(MockProvider(_responder([{"mapping": good, "confidence": 0.9}], prompts)), model="m")
    _, res = agents.mapping_agent(client, profile, schema, _draft(), workbench=Bench([RunReport(50, 50)]))
    first = json.loads(prompts[0])
    assert first["forma_looked_at_the_file_for_you"][0]["request"]["column"] == "สถานะ"
    sc = res.data["self_check"]
    assert sc["grounded"] == 1 and sc["history"][0] == {"turn": "ground", "columns": 1}


@pytest.fixture
def profile() -> WorkbookProfile:
    return _profile(
        {"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float"), ("สถานะ", "string")]}
    )


# --------------------------------------------------------------------------------------------
# 3. anchors: what other customers' approved mappings say a header means
# --------------------------------------------------------------------------------------------
def test_anchors_become_aliases_only_when_enough_customers_approved_them() -> None:
    anchors = Anchors()
    anchors.add("รหัสสินค้า", "/sku", "c1")
    anchors.add("รหัสสินค้า", "/sku", "c2")
    anchors.add("ชื่อสินค้า", "/product_name", "c1")
    anchors.add("ไม่มีในไฟล์", "/brand", "c1")
    learned = Learned()
    learned.absorb(anchors, {"รหัสสินค้า", "ชื่อสินค้า"}, share_after=2)
    assert (
        learned.alias_for("/sku", "รหัสสินค้า")
        == "learned: 2 other customers map this header here (anchor, ADR-059)"
    )
    assert learned.alias_for("/product_name", "ชื่อสินค้า") is None  # one customer: an anchor, not an alias
    assert learned.anchors == [{"header": "ชื่อสินค้า", "target": "/product_name", "customers": 1}]
    assert learned.summary() == {"aliases": 1, "constants": 0, "anchored": 1, "anchors": 1}
    assert learned.for_prompt()["anchors_from_other_customers"] == learned.anchors and not learned.empty


def test_a_new_customer_inherits_what_two_others_approved(client: TestClient, isolated_env: Path) -> None:
    c = client

    def approved(code: str) -> dict[str, Any]:
        cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
        r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
        _upload(c, r, make_workbook(isolated_env / f"{code}.xlsx"))
        mv = _run(c, r)["result"]["mapping_version_id"]
        assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
        return c.get(f"/api/mappings/{mv}").json()  # type: ignore[no-any-return]

    approved("N1")
    second = approved("N2")  # one customer before it: anchors reach the prompt only
    assert second["policy"]["learning"]["anchored"] == 0 and second["policy"]["learning"]["anchors"] > 0
    assert not any("anchor" in e for f in second["document"]["fields"] for e in f["evidence"])
    third = approved("N3")  # two customers before it: their shared pairs are aliases now
    note = third["policy"]["learning"]
    assert note["anchored"] > 0
    sku = next(f for f in third["document"]["fields"] if f["target"] == "/sku")
    assert sku["confidence"] == 1.0 and any("2 other customers" in e for e in sku["evidence"])
    assert any(e["agent"] == "learning" and e["anchored"] == note["anchored"] for e in third["agent_log"])


# --------------------------------------------------------------------------------------------
# 4. the matcher and the model as two readers; confidence bounded by evidence
# --------------------------------------------------------------------------------------------
def test_agreement_and_grounded_confidence() -> None:
    draft, doc = _doc(), _doc()
    doc.fields[1].expr = col("สถานะ")  # the model read another column for /product_name
    doc.fields[1].confidence = 0.95
    doc.fields.append(FieldMapping(target="/brand", expr=col("ยี่ห้อ"), confidence=0.6))  # model only
    del draft.collections[0].fields[0]  # the matcher never mapped /prices/*/price_list: model only too
    draft.fields.append(
        FieldMapping(target="/category", expr=col("หมวด"), confidence=0.8)
    )  # model dropped it
    ag = _agreement(draft, doc)
    assert set(ag["agree"]) >= {"/sku", "/currency", "/prices/*/amount"}
    assert ag["disagree"]["/product_name"] == {"matcher": ["สินค้า.ชื่อสินค้า"], "model": ["สินค้า.สถานะ"]}
    assert set(ag["model_only"]) == {"/brand", "/prices/*/price_list"} and ag["dropped"] == ["/category"]
    doc.fields[0].confidence = 0.6  # /sku: the model was unsure, the matcher read it the same way
    basis = _ground_confidence(
        doc,
        ag,
        {"problems_left": ["/prices/*/amount: 3 of 12 values are zero or negative"]},
        ["/prices/*/amount: 3 of 12 values are zero or negative"],
        DEFAULT_POLICY["confidence"],
    )
    by = {f.target: f for f in [*doc.fields, *doc.collections[0].fields]}
    assert by["/sku"].confidence == 0.9 and "matcher reads this field the same way" in by["/sku"].evidence[-1]
    assert (
        by["/product_name"].confidence == 0.7
        and "read สินค้า.ชื่อสินค้า instead" in by["/product_name"].evidence[-1]
    )
    assert by["/prices/*/amount"].confidence == 0.75 and "plausibility" in by["/prices/*/amount"].evidence[-1]
    assert by["/brand"].confidence == 0.6  # the model alone: its word stands
    assert doc.meta.confidence == 0.8 and basis["mapping_capped"] is True
    assert basis["raised"] == ["/sku"] and set(basis["capped"]) == {"/product_name", "/prices/*/amount"}


def test_policy_sends_dropped_high_risk_fields_to_review_and_knows_the_new_sections(
    schema: TargetSchema,
) -> None:
    engine = PolicyEngine()
    doc = _doc()
    for f in [*doc.fields, *doc.collections[0].fields]:
        f.confidence = 1.0
    doc.meta.confidence = 1.0
    ok = engine.evaluate(doc, schema, findings=[], rows_out=12, drift="identical", has_previous_approval=True)
    assert ok.decision == "auto_approve"
    d = engine.evaluate(
        doc,
        schema,
        findings=[],
        rows_out=12,
        drift="identical",
        has_previous_approval=True,
        agreement={
            "agree": ["/sku"],
            "disagree": {},
            "model_only": [],
            "dropped": ["/prices/*/amount", "/brand"],
        },
    )
    assert d.decision == "review" and d.checks["dropped_high_risk"] == ["/prices/*/amount"]
    assert d.reasons == ["the model dropped fields FORMA's matcher had mapped: /prices/*/amount"]
    assert d.checks["agreement"] == {"agree": 1, "disagree": 0, "model_only": 0, "dropped": 2}
    assert (
        validate_policy({"confidence": {"agree_floor": 0.95}, "learning": {"share_after_customers": 3}}) == []
    )
    assert validate_policy({"audit": {"sample_share": 0.1}}) == []
    assert validate_policy({"confidence": {"agree_floor": 1.5}})
    assert validate_policy({"confidence": {"nope": 0.5}})
    assert validate_policy({"learning": {"share_after_customers": 0}})
    assert validate_policy({"audit": {"sample_share": 2}})


# --------------------------------------------------------------------------------------------
# 5. plausibility against the customer's own history
# --------------------------------------------------------------------------------------------
def test_history_plausibility_names_a_field_that_went_empty(schema: TargetSchema) -> None:
    before = [{"sku": f"A{i}", "product_name": f"n{i}", "prices": [{"amount": 1.0}]} for i in range(10)]
    fill = plausibility.fill_shares(before, schema)
    assert fill["/sku"] == 1.0 and fill["/product_name"] == 1.0 and fill["/prices/*/amount"] == 1.0
    assert fill["/brand"] == 0.0
    now = [{"sku": f"A{i}", "product_name": None, "prices": [{"amount": 1.0}]} for i in range(10)]
    problems = plausibility.check_history(now, schema, {"fill": fill, "rows_in": 100, "rows_out": 95})
    assert problems == [
        "/product_name: filled in 100% of records in the approved round, now 0%"
        " (the column may have moved or the wrong one is read)"
    ]
    assert (
        plausibility.check_history(now, schema, None) == []
        and plausibility.check_history([], schema, {}) == []
    )
    assert plausibility.keep_ratio_problem(50, 10, {"rows_in": 100, "rows_out": 95}) is not None
    assert plausibility.keep_ratio_problem(50, 45, {"rows_in": 100, "rows_out": 95}) is None
    assert plausibility.keep_ratio_problem(50, 10, None) is None


# --------------------------------------------------------------------------------------------
# 6. spot checks of automation and the same question across files
# --------------------------------------------------------------------------------------------
def test_audit_sampling_is_deterministic() -> None:
    assert _sampled_for_audit("rnd_abc", 0) is False and _sampled_for_audit("rnd_abc", 1.0) is True
    picks = {rid for rid in (f"rnd_{i}" for i in range(400)) if _sampled_for_audit(rid, 0.05)}
    assert 5 <= len(picks) <= 45 and picks == {
        rid for rid in (f"rnd_{i}" for i in range(400)) if _sampled_for_audit(rid, 0.05)
    }


def test_an_automatic_approval_can_be_spot_checked(client: TestClient, isolated_env: Path) -> None:
    c = client
    policy = c.get("/api/settings/policy").json()["effective"]
    policy["audit"]["sample_share"] = 1.0
    assert c.put("/api/settings/policy", json={"policy": policy}).status_code == 200
    cust = c.post("/api/customers", json={"code": "AU1", "name": "AU1"}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r1, make_workbook(isolated_env / "au1.xlsx"))
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    r2 = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r2, make_workbook(isolated_env / "au1_r2.xlsx"))
    _run(c, r2)
    round2 = c.get(f"/api/rounds/{r2}").json()
    assert round2["summary"]["decision"] == "auto_approve", round2["summary"]
    tasks = [t for t in c.get("/api/review").json() if t["round_id"] == r2]
    assert len(tasks) == 1 and tasks[0]["kind"] == "audit" and tasks[0]["details"]["audit"] is True
    # the approval stood: the round went on to transform without waiting for the spot check
    assert c.get(f"/api/rounds/{r2}").json()["status"] in (
        "auto_approved",
        "transformed",
        "approved_data",
        "published",
    )
    other = next(t for t in c.get("/api/review", params={"status": "approved"}).json() if t["round_id"] == r1)
    assert c.post(f"/api/review/{other['id']}/confirm", json={}).status_code == 422
    done = c.post(f"/api/review/{tasks[0]['id']}/confirm", json={"comment": "sample looks right"})
    assert done.status_code == 200 and done.json()["status"] == "approved"
    assert c.post(f"/api/review/{tasks[0]['id']}/confirm", json={}).status_code == 409
    rep = c.get("/api/calibration").json()["report"]
    assert rep["audit"] == {"sampled": 1, "confirmed": 1, "corrected": 0, "open": 0}
    assert set(rep["per_signal"]) == {"agree", "disagree", "model_only"}


def _unsure_name(c: TestClient, root: Path, code: str) -> str:
    """A proposed mapping whose /product_name is read from one column with low confidence (a choice)."""
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()["id"]
    r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r, make_workbook(root / f"{code}.xlsx"))
    mv = _run(c, r)["result"]["mapping_version_id"]
    doc = copy.deepcopy(c.get(f"/api/mappings/{mv}").json()["document"])
    for f in doc["fields"] + [f for coll in doc["collections"] for f in coll["fields"]]:
        f["confidence"] = 1.0
    doc["fields"] = [f for f in doc["fields"] if f["target"] != "/brand"]
    name = next(f for f in doc["fields"] if f["target"] == "/product_name")
    name["expr"], name["confidence"] = {"op": "col", "name": "ชื่อสินค้า"}, 0.5
    return str(c.post(f"/api/mappings/{mv}/edit", json={"document": doc}).json()["id"])


def test_the_same_question_across_files_is_answered_once(client: TestClient, isolated_env: Path) -> None:
    c = client
    a, b = _unsure_name(c, isolated_env, "G1"), _unsure_name(c, isolated_env, "G2")
    groups = c.get("/api/review/question-groups").json()
    g = next(x for x in groups if x["field"]["pointer"] == "/product_name")
    assert g["kind"] == "choice" and {m["mapping_id"] for m in g["members"]} == {a, b}
    assert g["options"][0] == "ชื่อสินค้า" and "ยี่ห้อ" in g["options"]
    assert (
        c.post("/api/review/question-groups/answer", json={"key": "0" * 16, "answer": "ยี่ห้อ"}).status_code
        == 409
    )
    bad = c.post("/api/review/question-groups/answer", json={"key": g["key"], "answer": "nope"})
    assert bad.status_code == 422
    res = c.post("/api/review/question-groups/answer", json={"key": g["key"], "answer": "ยี่ห้อ"})
    assert res.status_code == 200 and res.json()["answered"] == 2
    for new_id in res.json()["mapping_version_ids"]:
        m = c.get(f"/api/mappings/{new_id}").json()
        name = next(f for f in m["document"]["fields"] if f["target"] == "/product_name")
        assert name["expr"] == {"op": "col", "name": "ยี่ห้อ"} and name["confidence"] == 1.0
    assert not any(
        x["field"]["pointer"] == "/product_name" for x in c.get("/api/review/question-groups").json()
    )


def test_a_contested_field_is_asked_with_the_matcher_column_first(
    client: TestClient, isolated_env: Path
) -> None:
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    c = client
    cust = c.post("/api/customers", json={"code": "CT1", "name": "CT1"}).json()["id"]
    r = c.post("/api/rounds", json={"customer_id": cust}).json()["id"]
    _upload(c, r, make_workbook(isolated_env / "ct1.xlsx"))
    mv = _run(c, r)["result"]["mapping_version_id"]
    with session_scope() as s:
        m = s.get(MappingVersion, mv)
        assert m is not None
        doc = copy.deepcopy(m.document)
        for f in doc["fields"] + [f for coll in doc["collections"] for f in coll["fields"]]:
            f["confidence"] = 0.97  # sure of everything ...
        m.document = doc
        m.policy = {  # ... but the matcher read /product_name from ยี่ห้อ
            **(m.policy or {}),
            "checks": {},
            "agreement": {"disagree": {"/product_name": {"matcher": ["สินค้า.ยี่ห้อ"], "model": ["สินค้า.ชื่อสินค้า"]}}},
        }
    qs = c.get(f"/api/mappings/{mv}/questions").json()["questions"]
    q = next(x for x in qs if x["id"] == "/product_name")
    assert q["kind"] == "choice" and q["contested"] is True and q["options"][:2] == ["ชื่อสินค้า", "ยี่ห้อ"]
    assert q["evidence"][-1] == "FORMA's matcher read ยี่ห้อ for this field" and qs[0]["id"] == "/product_name"
    # a contested constant (the VAT flag the matcher assumed otherwise) is a yes/no, not a choice
    with session_scope() as s:
        m = s.get(MappingVersion, mv)
        assert m is not None
        vat = next(
            f
            for coll in m.document["collections"]
            for f in coll["fields"]
            if f["target"].endswith("/vat_included")
        )
        m.policy = {
            **(m.policy or {}),
            "agreement": {
                "disagree": {vat["target"]: {"matcher": ['{"op": "const", "value": false}'], "model": []}}
            },
        }
    qs = c.get(f"/api/mappings/{mv}/questions").json()["questions"]
    q = next(x for x in qs if x["id"].endswith("/vat_included"))
    assert q["kind"] == "boolean" and q["contested"] is True and "matcher read" in q["evidence"][-1]


# --------------------------------------------------------------------------------------------
# 7. calibration reads the signals and proposes the floor
# --------------------------------------------------------------------------------------------
def test_calibration_counts_corrections_per_signal_and_moves_the_floor() -> None:
    rows = [
        {
            "mapping_id": f"m{i}",
            "agreement": {"agree": ["/sku", "/product_name"], "disagree": {"/brand": {}}},
            "corrected_targets": ["/brand"],
        }
        for i in range(12)
    ]
    sig = calibration.per_signal(rows)
    assert sig["agree"] == {"fields": 24, "corrected": 0, "correction_rate": 0.0}
    assert sig["disagree"] == {"fields": 12, "corrected": 12, "correction_rate": 1.0}
    assert sig["model_only"]["fields"] == 0 and sig["model_only"]["correction_rate"] is None
    props = calibration.confidence_proposals(rows, {"confidence": {"agree_floor": 0.9}})
    assert len(props) == 1 and props[0]["path"] == "confidence.agree_floor" and props[0]["value"] == 0.95
    assert calibration.confidence_proposals(rows[:5], {"confidence": {"agree_floor": 0.9}}) == []  # too few
    loose = [{**r, "corrected_targets": ["/sku", "/brand"]} for r in rows]
    down = calibration.confidence_proposals(loose, {"confidence": {"agree_floor": 0.9}})
    assert down and down[0]["value"] == 0.85
    assert calibration.confidence_proposals(rows, {"confidence": {"agree_floor": 0.95}}) == []  # at the top
