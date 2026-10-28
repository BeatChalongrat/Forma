"""Self-healing review (services/healing.py, ADR-021): per-field changes, checked on the data."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from tests.test_lineage import published_round

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
from forma.services.healing import data_check, field_changes


def recs(prices: list[Any]) -> list[dict[str, Any]]:
    return [{"sku": f"S{i}", "prices": [{"amount": p}]} for i, p in enumerate(prices)]


@pytest.mark.parametrize(
    ("before", "after", "verdict"),
    [
        ([100, 200, 300, 400], [100, 200, 300, 400], "same"),
        ([100, 200, 300, 400], [107, 214, 321, 428], "vat_added"),
        ([107, 214, 321, 428], [100, 200, 300, 400], "vat_removed"),
        ([100, 200, 300, 400], [1200, 2400, 3600, 4800], "scaled"),
        ([100, 200, 300, 400], [7, 950, 3, 120], "different"),
        ([100, 200], [100, 200], "no_data"),
    ],
)
def test_data_check(schema: TargetSchema, before: list[Any], after: list[Any], verdict: str) -> None:
    out = data_check(schema, "/prices/*/amount", recs(before), recs(after))
    assert out["verdict"] == verdict
    if verdict in ("vat_added", "vat_removed", "scaled"):
        assert out["ratio"] and out["examples"]


def _doc(price_col: str, name: str = "name") -> MappingDocument:
    return MappingDocument(
        meta=MappingMeta(target_schema="product@1"),
        sources=[SourceSpec(id="p")],
        record=RecordSpec(root_source="p"),
        fields=[
            FieldMapping(target="/sku", expr=col("code")),
            FieldMapping(target="/product_name", expr=col(name)),
            FieldMapping(target="/currency", expr=const("THB")),
        ]
        + ([FieldMapping(target="/brand", expr=col(price_col))] if price_col else []),
    )


def test_field_changes() -> None:
    old, new = _doc("b"), _doc("", name="title")
    by = {c["pointer"]: c for c in field_changes(old, new)}
    assert by["/sku"]["status"] == "unchanged" and by["/product_name"]["status"] == "changed"
    assert by["/product_name"]["before"]["reads"] == ["p.name"] and by["/product_name"]["after"]["reads"] == [
        "p.title"
    ]
    assert by["/brand"]["status"] == "removed"
    assert {c["status"] for c in field_changes(new, old)} >= {"added"}


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_renamed_price_without_vat_needs_a_decision(client: TestClient, isolated_env: Path) -> None:
    def net_prices(wb: openpyxl.Workbook) -> None:
        ws = wb["สินค้า"]
        ws["G3"] = "ราคาสุทธิ (ไม่รวม VAT)"
        for i in range(1, 13):
            ws.cell(row=3 + i, column=7, value=f"{i * 100:,}.00")  # 107 → 100: VAT taken out

    _, ds = published_round(client, isolated_env, "H1", net_prices)
    rnd = client.get(f"/api/datasets/{ds}").json()["round_id"]
    mv = client.get(f"/api/rounds/{rnd}").json()["mappings"][0]
    assert mv["status"] == "proposed" and mv["policy"]["risk_level"] == "high"
    assert any("VAT now excluded" in r for r in mv["policy"]["reasons"])
    review = client.get(f"/api/mappings/{mv['id']}/field-review").json()
    amount = next(f for f in review["fields"] if f["pointer"] == "/prices/*/amount")
    assert amount["status"] == "changed" and amount["check"]["verdict"] == "vat_removed"
    assert amount["after"]["reads"] == ["สินค้า.ราคาสุทธิ (ไม่รวม VAT)"]
    assert "/prices/*/amount" in review["needs_decision"] and review["counts"]["unchanged"] > 10
    assert review["baseline_version"] == 1 and review["compared_with"]
