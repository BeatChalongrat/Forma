from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace import uncertain_mapping


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def test_real_cells_constants_and_scoped_evidence(client: TestClient, isolated_env: Path) -> None:
    r, mid, vat = uncertain_mapping(client, isolated_env)
    q = client.get(f"/api/mappings/{mid}/questions").json()
    pack = next(f["pointer"] for f in q["fields"] if f["semantic"] == "unit.pack_size")
    url = f"/api/mappings/{mid}/questions/evidence"
    response = client.get(url, params={"field": pack})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["sample_limit"] == 50 and len(data["examples"]) == 5
    first, second = data["examples"][:2]
    assert first["identity"] == "A001" and first["value"] is None
    assert second["identity"] == "A002" and second["value"] == 12
    assert second["source_complete"]
    cell = second["cells"][0]
    assert cell["raw"] == "ลัง(12)" and cell["row"] == 5 and cell["column"] == "F"
    assert cell["sheet"] == "สินค้า" and cell["verified"] is True
    assert q["context"]["files"][0]["name"] == r["files"][0]["name"]
    fixed = client.get(url, params={"field": vat}).json()
    assert fixed["constant"] and fixed["context_only"]
    assert fixed["examples"][0]["value"] is True
    assert "107" in fixed["examples"][0]["cells"][0]["raw"]
    assert client.get(url, params={"field": pack}, headers={"X-Forma-Customers": ""}).status_code == 403
    assert client.get(url, params={"field": "/not-a-field"}).status_code == 422


def test_preview_compares_actual_versions_without_mutation(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import MappingVersion
    from forma.db.session import session_scope

    _, mid, vat = uncertain_mapping(client, isolated_env)
    before = client.get(f"/api/mappings/{mid}").json()["document"]
    # The old cached sample is not a reliable basis for before/after counts.
    with session_scope() as session:
        m = session.get(MappingVersion, mid)
        assert m
        m.policy = {**m.policy, "sample": []}
    result = client.post(f"/api/mappings/{mid}/answers/preview", json={"answers": {vat: False}})
    assert result.status_code == 200, result.text
    comparison = result.json()["comparison"]
    assert comparison["reliable"]
    assert comparison["before_records"] == comparison["after_records"] == 12
    assert comparison["counts"]["changed_records"] == 12
    assert comparison["counts"]["lost"] == comparison["counts"]["gained"] == 0
    assert all(row["changes"][0]["before"] is True for row in comparison["rows"])
    assert all(row["changes"][0]["after"] is False for row in comparison["rows"])
    assert client.get(f"/api/mappings/{mid}").json()["document"] == before


def test_collection_evidence_follows_item_rows(client: TestClient, isolated_env: Path) -> None:
    _, mid, _ = uncertain_mapping(client, isolated_env)
    q = client.get(f"/api/mappings/{mid}/questions").json()
    pointer = next(f["pointer"] for f in q["fields"] if f["semantic"] == "stock.qty")
    data = client.get(f"/api/mappings/{mid}/questions/evidence", params={"field": pointer}).json()
    rows = data["examples"]
    assert rows[0]["identity"] == rows[1]["identity"] == "A001"
    assert [r["value"] for r in rows[:2]] == [3, 1]
    assert [r["cells"][0]["row"] for r in rows[:2]] == [2, 3]
    assert all(r["cells"][0]["sheet"] == "Stock" for r in rows)


def test_missing_sources_never_fabricate_examples(client: TestClient, isolated_env: Path) -> None:
    from forma.db.models import SourceFile
    from forma.db.session import session_scope

    r, mid, vat = uncertain_mapping(client, isolated_env)
    with session_scope() as s:
        file = s.get(SourceFile, r["files"][0]["id"])
        assert file
        file.status = "ignored"
    data = client.get(f"/api/mappings/{mid}/questions/evidence", params={"field": vat}).json()
    assert data["unavailable"] and data["examples"] == []
