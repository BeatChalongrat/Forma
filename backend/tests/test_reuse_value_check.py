"""A reused mapping is approved by itself only when its prices were compared with an approved round (ADR-073)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook
from tests.test_batches import _known_customer

from forma.ai.policy import PolicyEngine, validate_policy
from forma.mapping_dsl.models import (
    FieldMapping,
    MappingDocument,
    MappingMeta,
    RecordSpec,
    SourceSpec,
    col,
    const,
)
from forma.schema_registry import TargetSchema
from forma.services.healing import review, typical_check
from forma.services.review_explanations import explain

PRICE = "/prices/*/amount"


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_typical_value_shows_a_unit_change_and_cannot_show_vat() -> None:
    last = [10.0, 20.0, 30.0, 40.0, 50.0]
    nothing = typical_check([], last, 0)
    assert nothing["verdict"] == "no_data" and nothing["basis"] == "none" and nothing["amount"] is True
    satang = typical_check(last, [v * 100 for v in last], 0)
    assert satang["verdict"] == "scaled" and satang["ratio"] == 100.0 and satang["basis"] == "typical_value"
    assert typical_check(last, [v / 12 for v in last], 0)["verdict"] == "scaled"
    net = typical_check(last, [v / 1.07 for v in last], 2)
    assert net["verdict"] == "no_data" and net["basis"] == "typical_value" and net["pairs"] == 2
    assert "cannot rule out a VAT change" in net["summary"]
    assert (
        typical_check(last, [v * 4 for v in last], 0)["verdict"] == "no_data"
    )  # a dearer range is not a unit


def _doc(price: Any) -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1"),
        sources=[SourceSpec(id="p")],
        record=RecordSpec(root_source="p"),
        fields=[
            FieldMapping(target="/sku", expr=col("code")),
            FieldMapping(target="/product_name", expr=col("name")),
            FieldMapping(target="/brand", expr=price),
        ],
    )


def _records(prefix: str, factor: float = 1.0) -> list[dict[str, Any]]:
    return [{"sku": f"{prefix}{i}", "brand": 10.0 * i * factor} for i in range(1, 9)]


def _review(old: list[dict[str, Any]], new: list[dict[str, Any]], schema: TargetSchema, **kw: Any) -> Any:
    doc = _doc(kw.pop("price", col("price")))
    return review(
        schema,
        doc,
        doc,
        [r for r in old if r["sku"] in {n["sku"] for n in new}],
        new,
        risk_of=lambda p: "critical" if p == "/brand" else "low",
        check_unchanged=True,
        is_amount=lambda p: p == "/brand",
        before_amounts={"/brand": [r["brand"] for r in old]},
        **kw,
    )


def test_an_unpaired_amount_always_has_a_check(schema: TargetSchema) -> None:
    same_skus = _review(_records("A"), _records("A"), schema)["checks"]
    assert [(c["pointer"], c["verdict"], c["amount"]) for c in same_skus] == [("/brand", "same", True)]
    other_skus = _review(_records("A"), _records("B"), schema)["checks"]
    assert [(c["verdict"], c["basis"], c["pairs"]) for c in other_skus] == [("no_data", "typical_value", 0)]
    in_satang = _review(_records("A"), _records("B", 100), schema)["checks"]
    assert [(c["verdict"], c["ratio"]) for c in in_satang] == [("scaled", 100.0)]
    first_round = _review([], _records("B"), schema)["checks"]
    assert [(c["verdict"], c["basis"]) for c in first_round] == [("no_data", "none")]
    # a fixed value is the same in every round and says nothing about the file
    assert _review(_records("A"), _records("B"), schema, price=const("x"))["checks"] == []


def _decide(schema: TargetSchema, checks: list[dict[str, Any]], *, reused: bool, policy: Any = None) -> Any:
    doc = _doc(col("price"))
    for field in doc.fields:
        field.confidence = 1.0
    doc.meta.confidence = 1.0
    return PolicyEngine(policy).evaluate(
        doc,
        schema,
        findings=[],
        rows_out=8,
        drift="identical",
        has_previous_approval=True,
        reused_approved=reused,
        field_checks=checks,
    )


def test_policy_holds_a_reused_mapping_whose_price_was_not_compared(schema: TargetSchema) -> None:
    unverified = [{"pointer": PRICE, "risk": "critical", **typical_check([], [1.0] * 5, 0)}]
    held = _decide(schema, unverified, reused=True)
    assert held.decision == "review" and held.checks["unverified_amounts"] == [PRICE]
    assert any("no published values from an earlier round" in r for r in held.reasons)
    compared = [{"pointer": PRICE, "risk": "critical", "verdict": "same", "pairs": 8, "amount": True}]
    assert _decide(schema, compared, reused=True).decision == "auto_approve"
    off = {"auto_approve": {"require_value_comparison_for_reuse": False}}
    relaxed = _decide(schema, unverified, reused=True, policy=off)
    assert relaxed.decision == "auto_approve" and relaxed.checks["unverified_amounts"] == [PRICE]
    # a new mapping is judged by the sandbox and its confidence, not by this rule
    assert "unverified" not in " ".join(_decide(schema, unverified, reused=False).reasons)
    # a sparse amount (a promotion price) carries no ``amount`` mark and is never required
    sparse = [{"pointer": PRICE, "risk": "critical", "verdict": "no_data", "pairs": 1}]
    assert _decide(schema, sparse, reused=True).decision == "auto_approve"


def test_the_switch_is_a_validated_policy_setting() -> None:
    assert validate_policy({"auto_approve": {"require_value_comparison_for_reuse": False}}) == []
    problems = validate_policy({"auto_approve": {"require_value_comparison_for_reuse": "no"}})
    assert problems == ["auto_approve.require_value_comparison_for_reuse must be true or false"]


def test_reasons_are_explained_in_thai() -> None:
    reasons = [
        f"data check: {PRICE}: no published values from an earlier round to compare these with; confirm",
        f"data check: {PRICE}: too few SKUs in common with last round to compare values; the typical value "
        "is ×1.02 of last round's, which cannot rule out a VAT change; confirm the reused mapping",
    ]
    first, second = explain(None, reasons, None)  # type: ignore[arg-type]
    assert "ยังไม่มีข้อมูลรอบก่อน" in first["th"]
    assert "×1.02" in second["th"] and "VAT" in second["th"]


def _other_catalogue(path: Path, *, factor: int = 1) -> Path:
    """The same layout with none of the first round's SKUs; prices times ``factor`` and a total that is true."""
    make_workbook(path)
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            cell = row[0]
            if isinstance(cell.value, str) and len(cell.value) == 4 and cell.value.startswith("A0"):
                cell.value = "B" + cell.value[1:]
    products = wb["สินค้า"]
    for i in range(1, 13):
        products.cell(row=3 + i, column=7, value=f"{i * 107 * factor:,}.00")
    products.cell(row=16, column=7, value=f"{107 * 78 * factor:,}.00")
    wb.save(path)
    wb.close()
    return path


def _second_round(c: TestClient, customer: str, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rid = c.post("/api/rounds", json={"customer_id": customer}).json()["id"]
    _upload(c, rid, path)
    _run(c, rid)
    rnd = c.get(f"/api/rounds/{rid}").json()
    return rnd, rnd["mappings"][0]


def test_same_layout_with_new_skus_waits_for_a_person(client: TestClient, isolated_env: Path) -> None:
    customer = _known_customer(client, isolated_env, "NS1")
    rnd, mapping = _second_round(client, customer, _other_catalogue(isolated_env / "ns1_r2.xlsx"))
    assert mapping["policy"]["source"] == "memory" and rnd["summary"]["drift"] == "identical"
    assert rnd["summary"]["decision"] == "review", mapping["policy"]["reasons"]
    assert mapping["policy"]["checks"]["unverified_amounts"] == [PRICE]
    reason = next(r for r in mapping["policy"]["reasons"] if PRICE in r)
    assert "too few SKUs in common" in reason and "cannot rule out a VAT change" in reason


def test_same_layout_with_new_skus_in_another_unit_names_the_factor(
    client: TestClient, isolated_env: Path
) -> None:
    customer = _known_customer(client, isolated_env, "NS2")
    path = _other_catalogue(isolated_env / "ns2_r2.xlsx", factor=100)
    rnd, mapping = _second_round(client, customer, path)
    assert mapping["policy"]["source"] == "memory" and rnd["summary"]["decision"] == "review"
    assert any("typical value is ×100" in r for r in mapping["policy"]["reasons"]), mapping["policy"][
        "reasons"
    ]
    price = next(c for c in mapping["policy"]["field_review"]["checks"] if c["pointer"] == PRICE)
    assert price["verdict"] == "scaled" and price["basis"] == "typical_value"


def test_with_the_switch_off_only_the_unit_change_holds_the_file(
    client: TestClient, isolated_env: Path
) -> None:
    policy = client.get("/api/settings/policy").json()["effective"]
    policy["auto_approve"]["require_value_comparison_for_reuse"] = False
    assert client.put("/api/settings/policy", json={"policy": policy}).status_code == 200
    customer = _known_customer(client, isolated_env, "NS3")
    rnd, mapping = _second_round(client, customer, _other_catalogue(isolated_env / "ns3_r2.xlsx"))
    assert rnd["summary"]["decision"] == "auto_approve", mapping["policy"]["reasons"]
    assert mapping["policy"]["checks"]["unverified_amounts"] == [PRICE]  # still on record
    rnd, mapping = _second_round(client, customer, _other_catalogue(isolated_env / "ns3_r3.xlsx", factor=100))
    assert rnd["summary"]["decision"] == "review"
