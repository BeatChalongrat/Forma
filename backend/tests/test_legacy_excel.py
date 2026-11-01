"""Old and binary Excel files: real ones, saved by LibreOffice and by Excel, read through the whole pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _send(c: TestClient, customer: str, name: str) -> dict[str, Any]:
    rid = c.post("/api/rounds", json={"customer_id": customer}).json()["id"]
    data = (FIXTURES / name).read_bytes()
    up = c.post(f"/api/rounds/{rid}/files", files=[("files", (name, data, "application/octet-stream"))])
    assert up.status_code == 201, up.text
    assert up.json()[0]["kind"] == "excel"
    c.post(f"/api/rounds/{rid}/run", json={"sync": True})
    return c.get(f"/api/rounds/{rid}").json()  # type: ignore[no-any-return]


@pytest.mark.parametrize("name", ["legacy.xls", "binary.xlsb"])
def test_an_old_or_binary_workbook_is_read_and_always_held_for_a_person(
    client: TestClient, name: str
) -> None:
    c = client
    customer = c.post("/api/customers", json={"code": "OLD", "name": "old file"}).json()["id"]
    first = _send(c, customer, name)
    assert first["status"] == "review"
    mapping = first["mappings"][0]["id"]
    assert c.post(f"/api/mappings/{mapping}/approve", json={}).status_code == 200
    c.post(f"/api/rounds/{first['id']}/transform", params={"sync": True})
    dataset = c.get(f"/api/rounds/{first['id']}").json()["datasets"][-1]["id"]
    d = c.get(f"/api/datasets/{dataset}").json()
    assert d["row_count"] == 12 and d["invalid_records"] == 0
    assert any("legacy/binary Excel" in f["message"] for f in d["findings"])

    # an approved mapping does not lift the need to look: formulas were not seen
    second = _send(c, customer, name)
    assert second["status"] == "review"
    assert second["summary"]["drift"] == "identical" and second["summary"]["decision"] == "review"
