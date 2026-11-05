import csv

import pytest
from fastapi.testclient import TestClient
from tests.test_learning import _round


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as client:
        yield client


def book(tmp, name, token="BT", currency="THB", extra=False):
    path = tmp / name
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "SKU",
                "Product name",
                "Unit",
                "Price incl VAT",
                "Currency",
                *(["New ERP column"] if extra else []),
            ]
        )
        writer.writerows(
            [
                [
                    f"A{i:03d}",
                    f"Item {i}",
                    token if i < 3 else "PCS",
                    i * 10,
                    currency,
                    *(["new"] if extra else []),
                ]
                for i in range(1, 7)
            ]
        )
    return path


def setup_rule(client, tmp):
    from sqlalchemy import select

    from forma.db.models import Correction
    from forma.db.session import session_scope

    customer = client.post("/api/customers", json={"code": "REVISE-UNIT", "name": "Unit correction"}).json()[
        "id"
    ]
    r = _round(client, customer, book(tmp, "first.csv"))
    mid = r["summary"]["mapping_version_id"]
    q = next(
        q
        for q in client.get(f"/api/mappings/{mid}/questions").json()["questions"]
        if q.get("source_value") == "BT"
    )
    assert q["source_examples"][0]["file"] == "first.csv"
    assert q["source_examples"][0]["column"] == "Unit"
    assert "BT" in q["question_th"]
    body = {"answers": {q["id"]: "BOTTLE"}}
    preview = client.post(f"/api/mappings/{mid}/answers/preview", json=body).json()
    saved = client.post(f"/api/mappings/{mid}/answers/apply", json={**body, "digest": preview["digest"]})
    assert saved.status_code == 201, saved.text
    mid = saved.json()["id"]
    approved = client.post(f"/api/mappings/{mid}/approve", json={})
    assert approved.status_code == 200, approved.text
    with session_scope() as session:
        cid = session.scalar(
            select(Correction.id).where(Correction.mapping_version_id == mid, Correction.kind == "decision")
        )
    return customer, mid, cid


def test_correct_unit_memory_then_reuse_and_ask_about_new_unknown_code(client, isolated_env):
    customer, _mid, cid = setup_rule(client, isolated_env)
    r2 = _round(client, customer, book(isolated_env, "second.csv"))
    assert (
        client.get(f"/api/mappings/{r2['summary']['mapping_version_id']}").json()["policy"]["source"]
        == "memory"
    )
    body = {"answer": "BOX", "reason": "เจ้าของไฟล์ยืนยันว่า BT หมายถึงกล่อง"}
    preview = client.post(f"/api/learning/{cid}/revision/preview", json=body)
    assert preview.status_code == 200, preview.text
    assert preview.json()["comparison"]["counts"]["changed_records"] == 2
    revised = client.post(
        f"/api/learning/{cid}/revision/apply", json={**body, "digest": preview.json()["digest"]}
    )
    assert revised.status_code == 201, revised.text
    approved = client.post(f"/api/mappings/{revised.json()['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    r3 = _round(client, customer, book(isolated_env, "third.csv"))
    new = client.get(f"/api/mappings/{r3['summary']['mapping_version_id']}").json()
    assert new["policy"]["source"] == "memory"
    assert {
        row["unit"]["sell_unit"]
        for row in client.get(f"/api/mappings/{new['id']}/questions").json()["sample"]
    } == {"BOX", "PCS"}
    # A structural change plus a new code must ask about that code, not blindly reuse a label.
    r4 = _round(client, customer, book(isolated_env, "new-erp.csv", token="XYZ", extra=True))
    current = r4["summary"]["mapping_version_id"]
    qs = client.get(f"/api/mappings/{current}/questions").json()["questions"]
    assert any(q.get("source_value") == "XYZ" and q.get("action") == "value_map" for q in qs)
    assert not any(q.get("source_value") == "BT" for q in qs)


def test_same_headers_but_changed_currency_do_not_recall_old_convention(client, isolated_env):
    customer, _mid, _cid = setup_rule(client, isolated_env)
    r = _round(client, customer, book(isolated_env, "new-country.csv", currency="USD"))
    m = client.get(f"/api/mappings/{r['summary']['mapping_version_id']}").json()
    assert m["policy"]["source"] != "memory"
    assert any(c["kind"] == "currency" for c in m["policy"]["format_context_changes"])
    assert not m["policy"]["confirmed_decisions"]
    assert any(
        q.get("source_value") == "BT"
        for q in client.get(f"/api/mappings/{m['id']}/questions").json()["questions"]
    )
