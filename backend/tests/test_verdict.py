"""The verdict layer (ADR-036): one conclusion per file, deterministic, acted on through the policy."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.ai.policy import DEFAULT_POLICY, validate_policy
from forma.schema_registry import TargetSchema
from forma.services import verdict

CFG = dict(DEFAULT_POLICY["verdict"])


def _check(pointer: str, v: str, pairs: int = 20, equal: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {
        "pointer": pointer,
        "risk": "critical",
        "verdict": v,
        "pairs": pairs,
        "equal_share": equal,
        "summary": v,
        **extra,
    }


def _mapping_verdict(schema: TargetSchema, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "policy": {
            "decision": "review",
            "reasons": ["first time this customer/format is seen"],
            "checks": {},
        },
        "open_questions": [],
        "questions": [],
        "summary": {"has_previous_approval": False},
        "round_id": "rnd_1",
        "mapping_id": "map_1",
        "customer_code": "C902",
    }
    return verdict.for_mapping(schema, CFG, **{**base, **kw})


# --------------------------------------------------------------------------------------------
# rules, from fabricated reports
# --------------------------------------------------------------------------------------------
def test_wrong_customer_from_identical_layout_and_different_values(schema: TargetSchema) -> None:
    price = next(iter(schema.semantic_pointers("price.amount")))
    name = next(iter(schema.semantic_pointers("product_name")))
    sku = next(iter(schema.semantic_pointers("sku")))
    v = _mapping_verdict(
        schema,
        policy={
            "decision": "review",
            "reasons": [
                "structure is identical to customer C901's approved format — confirm this file belongs here"
            ],
            "checks": {"drift": "columns_removed"},
            "field_review": {
                "checks": [
                    _check(price, "different", 69),
                    _check(name, "different", 69),
                    _check(sku, "same", 69, 1.0),
                ],
                "fields": [],
            },
        },
        summary={
            "has_previous_approval": True,
            "drift": "columns_removed",
            "matches_other_customer": {"customer_code": "C901", "similarity": 1.0},
        },
    )
    assert v["rules_fired"] == ["wrong_customer"]
    assert v["outcome"] == "assign_customer"
    assert "C901" in v["conclusion"]["en"] and "C901" in v["conclusion"]["th"]
    assert v["word"] == "sure" and v["confidence"] >= 0.85
    assert v["must_decide"] == []  # nothing to answer on a file that is not this customer's
    assert any(e["kind"] == "other_customer" for e in v["evidence"])
    # the same reports give the same digest; a changed report does not
    assert (
        _mapping_verdict(schema, policy={"decision": "review", "reasons": [], "checks": {}})["digest"]
        != v["digest"]
    )


def test_rename_only_is_accepted_with_one_click(schema: TargetSchema) -> None:
    price = next(iter(schema.semantic_pointers("price.amount")))
    v = _mapping_verdict(
        schema,
        policy={
            "decision": "review",
            "reasons": ["schema drift: columns_renamed"],
            "checks": {"drift": "columns_renamed"},
            "sandbox": {"delta": {"changed_records": 0, "lost": 0, "gained": 0, "changed_prices": 0}},
            "field_review": {"checks": [_check(price, "same", 40, 1.0)], "fields": []},
        },
        summary={"has_previous_approval": True, "drift": "columns_renamed"},
    )
    assert v["rules_fired"] == ["rename_only"] and v["outcome"] == "accept" and v["word"] == "sure"


def test_vat_shift_asks_about_the_vat_field_with_a_stated_default(schema: TargetSchema) -> None:
    price = next(iter(schema.semantic_pointers("price.amount")))
    flag = next(iter(schema.semantic_pointers("price.vat_included")))
    policy = {
        "decision": "review",
        "reasons": ["data check"],
        "checks": {"drift": "columns_renamed"},
        "field_review": {"checks": [_check(price, "vat_added", 30, 0.0, ratio=1.07)], "fields": []},
    }
    summary = {"has_previous_approval": True, "drift": "columns_renamed"}
    question = {
        "id": flag,
        "kind": "boolean",
        "field": {"name": "vat_included", "semantic": "price.vat_included"},
    }
    v = _mapping_verdict(schema, policy=policy, summary=summary, questions=[question])
    assert v["rules_fired"] == ["vat_shift"] and v["outcome"] == "answer"
    assert len(v["must_decide"]) == 1
    q = v["must_decide"][0]
    assert (
        q["id"] == flag and q["kind"] == "boolean" and q["default"] is True and q["options"] == [True, False]
    )
    assert q["semantic"] == "price.vat_included"
    # a contract with no VAT field to answer on: the person fixes the mapping, nothing is asked
    bare = _mapping_verdict(schema, policy=policy, summary=summary)
    assert (
        bare["rules_fired"] == ["vat_shift"]
        and bare["outcome"] == "fix_mapping"
        and bare["must_decide"] == []
    )


def test_a_choice_carries_its_options_and_the_self_check_is_evidence(schema: TargetSchema) -> None:
    q = {
        "id": "/product_name",
        "kind": "choice",
        "options": ["ชื่อ", "ยี่ห้อ"],
        "field": {"name": "product_name", "risk": "high", "description": "Product name"},
    }
    v = _mapping_verdict(
        schema,
        questions=[q],
        policy={
            "decision": "review",
            "reasons": ["first time"],
            "checks": {},
            "self_check": {"rows_checked": 50, "fix_rounds": 1, "inspections": 1, "problems_left": ["x"]},
        },
    )
    d = v["must_decide"][0]
    assert d["kind"] == "choice" and d["options"] == ["ชื่อ", "ยี่ห้อ"]
    assert d["question"]["en"] == "Which column holds Product name?"
    ev = next(e for e in v["evidence"] if e["kind"] == "self_check")
    assert ev["numbers"] == {"rows": 50, "fix_rounds": 1, "inspections": 1, "problems_left": 1}
    assert "1 problem(s) left" in ev["text"]["en"]


def test_first_time_and_clean(schema: TargetSchema) -> None:
    q = {"id": "/sku", "kind": "confirm", "field": {"name": "sku", "risk": "critical", "description": "SKU"}}
    first = _mapping_verdict(schema, questions=[q])
    assert first["rules_fired"] == ["first_time"] and first["outcome"] == "answer"
    assert first["must_decide"][0]["id"] == "/sku"
    clean = _mapping_verdict(
        schema,
        policy={"decision": "auto_approve", "reasons": ["all policy checks passed"], "checks": {}},
        summary={"has_previous_approval": True, "drift": "identical"},
    )
    assert clean["rules_fired"] == ["clean"] and clean["outcome"] == "accept" and clean["word"] == "sure"


def test_suppression_folds_data_changes_and_caps_decisions(schema: TargetSchema) -> None:
    price = next(iter(schema.semantic_pointers("price.amount")))
    same_rule = {"op": "col", "name": "Price"}
    fields = [
        {
            "pointer": f"/f{i}",
            "status": "changed",
            "before": {"expr": same_rule},
            "after": {"expr": same_rule},
        }
        for i in range(17)
    ]
    questions = [
        {
            "id": f"/q{i}",
            "kind": "confirm",
            "field": {"name": f"q{i}", "risk": "high", "description": f"Q{i}"},
        }
        for i in range(5)
    ]
    v = _mapping_verdict(
        schema,
        policy={
            "decision": "review",
            "reasons": ["x"],
            "checks": {"drift": "columns_removed"},
            "field_review": {"checks": [_check(price, "no_data", 1)], "fields": fields},
        },
        summary={"has_previous_approval": True, "drift": "columns_removed"},
        questions=questions,
    )
    reasons = {g["reason"]: g for g in v["suppressed"]["groups"]}
    assert len(reasons["data_not_mapping"]["pointers"]) == 17
    assert reasons["no_data"]["pointers"] == [price]
    assert len(v["must_decide"]) == CFG["max_must_decide"] and reasons["more_questions"]["pointers"] == [
        "/q3",
        "/q4",
    ]


def test_dataset_rules(schema: TargetSchema) -> None:
    common: dict[str, Any] = {
        "dataset_id": "dsv_1",
        "round_id": "rnd_1",
        "rows": 100,
        "error_findings": 0,
        "publish": None,
        "status": "draft",
    }
    broken = verdict.for_dataset(
        schema,
        CFG,
        invalid_records=3,
        anomalies={"items": [], "dataset": [], "high": 0, "by_kind": {}},
        **common,
    )
    assert broken["rules_fired"] == ["contract_broken"] and broken["outcome"] == "fix_data"
    jumps = [
        {"kind": "price_jump", "severity": "high", "value": 110, "expected": 100, "score": 10 + i}
        for i in range(6)
    ]
    move = verdict.for_dataset(
        schema,
        CFG,
        invalid_records=0,
        anomalies={"items": jumps, "dataset": [], "high": 6, "by_kind": {"price_jump": 6}, "normal_pct": 94},
        **common,
    )
    assert move["rules_fired"] == ["price_move"] and move["outcome"] == "check_data"
    assert move["must_decide"][0]["id"] == "acknowledge:price_jump" and "rose" in move["conclusion"]["en"]
    ready = verdict.for_dataset(
        schema,
        CFG,
        invalid_records=0,
        anomalies={"items": [], "dataset": [], "high": 0, "by_kind": {}, "normal_pct": 100},
        **common,
    )
    assert ready["rules_fired"] == ["publishable"] and ready["outcome"] == "accept"
    done = verdict.for_dataset(
        schema, CFG, invalid_records=0, anomalies={}, **{**common, "status": "published"}
    )
    assert done["rules_fired"] == ["published"]


def test_policy_section_is_validated() -> None:
    assert validate_policy({"verdict": {"min_pairs": 5, "narrative": "never"}}) == []
    problems = validate_policy(
        {"verdict": {"min_pairs": 0, "different_share": 2, "narrative": "always", "other": 1}}
    )
    assert len(problems) == 4


# --------------------------------------------------------------------------------------------
# through the API: stored on the round, acted on through the ordinary approval paths
# --------------------------------------------------------------------------------------------
@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _round(c: TestClient, code: str, path: Path, name: str = "file.xlsx") -> dict[str, Any]:
    cust = c.post("/api/customers", json={"code": code, "name": code}).json()
    r = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()
    _upload(c, r["id"], path, name)
    _run(c, r["id"])
    return c.get(f"/api/rounds/{r['id']}").json()  # type: ignore[no-any-return]


def test_verdict_is_stored_shown_and_accepted_through_the_policy(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    r = _round(c, "V1", make_workbook(isolated_env / "v1.xlsx"))
    got = c.get(f"/api/rounds/{r['id']}/verdict").json()
    v = got["verdict"]
    assert v["stage"] == "mapping" and v["rules_fired"] == ["first_time"]
    assert r["summary"]["verdict"]["digest"] == v["digest"]  # stored on the round at proposal time
    task = c.get("/api/review").json()[0]
    assert task["details"]["verdict"]["conclusion"] == v["conclusion"]  # and on the queue row
    # a stale digest is refused; a decision the verdict did not ask is refused
    assert c.post(f"/api/rounds/{r['id']}/verdict/accept", json={"digest": "0" * 32}).status_code == 409
    assert (
        c.post(
            f"/api/rounds/{r['id']}/verdict/accept",
            json={"digest": v["digest"], "decisions": {"/nope": True}},
        ).status_code
        == 422
    )
    if v["outcome"] == "answer":
        q = v["must_decide"][0]
        answer = True if q["kind"] == "boolean" else "confirm" if q["kind"] == "confirm" else 1
        res = c.post(
            f"/api/rounds/{r['id']}/verdict/accept",
            json={"digest": v["digest"], "decisions": {q["id"]: answer}},
        )
        assert res.status_code == 200, res.text
        assert res.json()["did"] == "apply_answers"
        v = c.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
        assert v["subject"]["id"] == res.json()["mapping_id"]  # the new version's verdict
    # approving the mapping through the verdict goes through approve_mapping (audit + review task)
    mid = v["subject"]["id"]
    assert c.post(f"/api/mappings/{mid}/approve", json={}).status_code == 200
    v2 = c.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
    assert v2["mapping_status"] == "approved"  # recomputed: the stored verdict was for a proposed version
    job = c.post(f"/api/rounds/{r['id']}/transform", params={"sync": "true"}).json()
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    v3 = c.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
    assert v3["stage"] == "data" and v3["outcome"] in ("accept", "check_data")
    decisions = {m["id"]: True for m in v3["must_decide"]}
    res = c.post(
        f"/api/rounds/{r['id']}/verdict/accept", json={"digest": v3["digest"], "decisions": decisions}
    )
    assert res.status_code == 200, res.text
    assert res.json()["did"] == "approve_dataset"
    assert c.get(f"/api/rounds/{r['id']}").json()["status"] == "approved_data"
    audit = [
        a
        for a in c.get("/api/audit", params={"entity_id": r["id"]}).json()
        if a["action"] == "verdict.accept"
    ]
    assert audit and audit[0]["details"]["rules_fired"] == v3["rules_fired"]


def test_blocked_mapping_cannot_be_accepted_with_one_click(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import ImportRound, MappingVersion
    from forma.db.session import session_scope

    c = client
    r = _round(c, "V2", make_workbook(isolated_env / "v2.xlsx"))
    mid = r["summary"]["mapping_version_id"]
    with session_scope() as s:
        row = s.get(MappingVersion, mid)
        rnd = s.get(ImportRound, r["id"])
        assert row and rnd
        row.policy = {
            **(row.policy or {}),
            "decision": "block",
            "reasons": ["transformation produced zero records"],
        }
        rnd.status = "blocked"  # a changed round status makes the stored verdict stale → recomputed on read
    v = c.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
    assert v["rules_fired"] == ["blocked"] and v["outcome"] == "fix_mapping"
    res = c.post(f"/api/rounds/{r['id']}/verdict/accept", json={"digest": v["digest"]})
    assert res.status_code == 409 and "own page" in res.json()["message"]


def test_wrong_file_is_named_through_the_api(client: TestClient, isolated_env: Path) -> None:
    """A's approved layout, uploaded under B whose own approved layout is a CSV: B's previous SKUs are
    there but every important value differs → 'this is A's file'."""
    c = client
    xlsx = make_workbook(isolated_env / "a.xlsx")
    a = _round(c, "A", xlsx)
    assert c.post(f"/api/mappings/{a['summary']['mapping_version_id']}/approve", json={}).status_code == 200
    rows = ["Item Code,Item Name,Brand,Selling Price,Status"]
    rows += [f"A{i:03d},Item {i},Acme,{i * 50}.00,active" for i in range(1, 13)]
    csv = isolated_env / "b.csv"
    csv.write_text("".join(f"{x}\n" for x in rows), encoding="utf-8")
    b = _round(c, "B", csv, "b.csv")
    assert c.post(f"/api/mappings/{b['summary']['mapping_version_id']}/approve", json={}).status_code == 200
    job = c.post(f"/api/rounds/{b['id']}/transform", params={"sync": "true"}).json()
    assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
    ds1 = c.get(f"/api/rounds/{b['id']}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds1}/approve", json={"acknowledge_anomalies": True}).status_code == 200
    r2 = c.post("/api/rounds", json={"customer_id": b["customer_id"]}).json()
    _upload(c, r2["id"], xlsx, "wrong.xlsx")
    _run(c, r2["id"])
    v = c.get(f"/api/rounds/{r2['id']}/verdict").json()["verdict"]
    assert v["rules_fired"][0] in ("wrong_customer", "blocked_unrelated"), v
    assert v["outcome"] == "assign_customer" and "A" in v["conclusion"]["en"]


def _published_then_vat_round(c: TestClient, root: Path) -> str:
    """Round 1 published; round 2 renames the price column and takes the VAT out of every price."""
    import openpyxl
    from tests.test_lineage import _job

    cust = c.post("/api/customers", json={"code": "VAT", "name": "VAT"}).json()["id"]
    r1 = c.post("/api/rounds", json={"customer_id": cust, "label": "R1"}).json()["id"]
    _upload(c, r1, make_workbook(root / "vat1.xlsx"), "ERP.xlsx")
    mv = _run(c, r1)["result"]["mapping_version_id"]
    assert c.post(f"/api/mappings/{mv}/approve", json={}).status_code == 200
    _job(c, f"/api/rounds/{r1}/transform")
    ds = c.get(f"/api/rounds/{r1}").json()["datasets"][0]["id"]
    assert c.post(f"/api/datasets/{ds}/approve", json={}).status_code == 200
    path = make_workbook(root / "vat2.xlsx")
    wb = openpyxl.load_workbook(path)
    ws = wb["สินค้า"]
    ws["G3"] = "ราคาสุทธิ (ไม่รวม VAT)"
    for i in range(1, 13):
        ws.cell(row=3 + i, column=7, value=f"{i * 100:,}.00")
    wb.save(path)
    r2 = c.post("/api/rounds", json={"customer_id": cust, "label": "R2"}).json()["id"]
    _upload(c, r2, path, "ERP.xlsx")
    _run(c, r2)
    return str(r2)


def test_the_vat_decision_on_the_card_saves_and_the_round_can_finish(
    client: TestClient, isolated_env: Path
) -> None:
    c = client
    rid = _published_then_vat_round(c, isolated_env)
    v = c.get(f"/api/rounds/{rid}/verdict").json()["verdict"]
    assert v["rules_fired"] == ["vat_shift"] and v["outcome"] == "answer"
    decision = next(d for d in v["must_decide"] if d["semantic"] == "price.vat_included")
    assert decision["default"] is False  # the prices fell by exactly the VAT rate
    res = c.post(
        f"/api/rounds/{rid}/verdict/accept",
        json={"digest": v["digest"], "decisions": {decision["id"]: False}},
    )
    assert res.status_code == 200, res.text
    assert res.json()["did"] == "apply_answers"
    doc = c.get(f"/api/mappings/{res.json()['mapping_id']}").json()["document"]
    fields = [*doc["fields"], *(f for coll in doc["collections"] for f in coll["fields"])]
    flag = next(f for f in fields if f["target"] == decision["id"])
    assert flag["expr"] == {"op": "const", "value": False} and flag["confidence"] == 1.0
    # answered: the next verdict lets the reviewer finish, with no dead end
    after = c.get(f"/api/rounds/{rid}/verdict").json()["verdict"]
    assert after["subject"]["id"] == res.json()["mapping_id"]
    assert (
        after["outcome"] == "accept" and after["must_decide"] == [] and after["rules_fired"] == ["answered"]
    )
    done = c.post(f"/api/rounds/{rid}/verdict/accept", json={"digest": after["digest"]})
    assert done.status_code == 200, done.text
    assert c.get(f"/api/rounds/{rid}").json()["status"] == "approved"


def test_open_decisions_turn_accept_into_answer(schema: TargetSchema) -> None:
    price = next(iter(schema.semantic_pointers("price.amount")))
    q = {"id": "/unit/pack_size", "kind": "confirm", "field": {"name": "pack_size", "risk": "high"}}
    v = _mapping_verdict(
        schema,
        policy={
            "decision": "review",
            "reasons": ["schema drift: columns_renamed"],
            "checks": {"drift": "columns_renamed"},
            "field_review": {"checks": [_check(price, "same", 40, 1.0)], "fields": []},
        },
        summary={"has_previous_approval": True, "drift": "columns_renamed"},
        questions=[q],
    )
    assert v["rules_fired"] == ["rename_only"]
    assert v["outcome"] == "answer" and [m["id"] for m in v["must_decide"]] == ["/unit/pack_size"]


def test_accept_is_refused_while_decisions_remain(
    client: TestClient, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored verdict from before the rule (or any bug) still cannot approve past open decisions."""
    from forma.services import verdict_flow

    c = client
    r = _round(c, "OPEN", make_workbook(isolated_env / "open.xlsx"))
    stored = c.get(f"/api/rounds/{r['id']}/verdict").json()["verdict"]
    stale = {**stored, "outcome": "accept", "must_decide": [{"id": "/x", "default": None}]}
    monkeypatch.setattr(verdict_flow, "current", lambda *a: stale)
    res = c.post(f"/api/rounds/{r['id']}/verdict/accept", json={"digest": stale["digest"]})
    assert res.status_code == 409 and "open" in res.json()["message"]
    assert c.get(f"/api/rounds/{r['id']}").json()["status"] == "review"
