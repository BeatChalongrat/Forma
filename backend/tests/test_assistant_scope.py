from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient
from tests.test_workspace_tools import call


@pytest.fixture
def client(isolated_env):
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def seed(count=300):
    from forma.db.models import Customer, ImportRound, Job, ReviewTask
    from forma.db.session import session_scope

    with session_scope() as s:
        s.add_all(
            [
                Customer(id="c_scope", code="SCOPE", name="Visible"),
                Customer(id="c_other", code="OTHER", name="Private"),
            ]
        )
        s.flush()
        s.add(ImportRound(id="r_scope", customer_id="c_scope"))
        for i in range(count):
            # Rare issues exist beyond the first page. Each reason is counted once per task.
            reasons = (
                ["low confidence", "VAT unclear", "VAT unclear"]
                if i < count - 1
                else ["unit.pack_size", "unfamiliar ERP convention"]
            )
            s.add(
                ReviewTask(
                    id=f"rev_scope_{i:05}",
                    round_id="r_scope",
                    customer_id="c_scope",
                    kind="mapping",
                    reasons=reasons,
                    priority=count - i,
                    created_at=dt.datetime(2026, 11, 1, 18, tzinfo=dt.UTC),
                )
            )
        s.add(
            ReviewTask(
                id="rev_private",
                round_id="r_private",
                customer_id="c_other",
                kind="blocked",
                reasons=["VAT SECRET"],
            )
        )
        s.add(Job(id="job_public", kind="pipeline", round_id="r_scope", payload={}))
        s.add(Job(id="job_private", kind="assistant_message", payload={}))


@pytest.mark.parametrize("size", [21, 200, 300, 1001])
def test_full_summary_rare_issue_overlap_and_scope(client, size):
    seed(size)
    headers = {"X-Forma-Customers": "c_scope"}
    result = call(client, "review_summary", {}, headers=headers)
    assert result["total"] == size and result["coverage"]["scanned"] == size
    categories = {c["id"]: c["count"] for c in result["categories"]}
    assert categories == {"confidence": size - 1, "vat": size - 1, "unit": 1, "unclassified": 1}
    assert result["categories_overlap"] and result["coverage"]["tasks_with_unclassified_reasons"] == 1
    assert "SECRET" not in json.dumps(result)
    queue = call(client, "review_queue", {}, headers=headers)
    assert len(queue["items"]) == 20 and queue["overview"]["total"] == size
    assert not queue["coverage"]["complete"]
    chart = call(client, "build_report", {"report": "review_issues"}, headers=headers)["report"]
    assert chart["complete"] and chart["coverage"]["matched"] == size
    assert sum(r["value"] for r in chart["rows"]) == size * 2


def test_read_pages_follows_without_model_roundtrips_and_honors_cap(client):
    seed(1001)
    a = {"source": "review_queue", "arguments": {"customer_id": "c_scope"}}
    result = call(client, "read_pages", a)
    assert len(result["items"]) == 1000 and result["pages_read"] == 50
    assert result["next_offset"] == 1000 and not result["coverage"]["complete"]
    last = call(client, "read_pages", {**a, "arguments": {"customer_id": "c_scope", "offset": 1000}})
    assert len(last["items"]) == 1 and last["next_offset"] is None
    assert not last["coverage"]["complete"]  # a last page is not the entire population
    assert last["items"][0]["id"] not in {r["id"] for r in result["items"]}
    bounded = call(client, "read_pages", {**a, "max_items": 25})
    assert len(bounded["items"]) == 25 and bounded["next_offset"] == 25


def test_full_read_and_filtered_date_counts(client):
    seed(45)
    result = call(client, "read_pages", {"source": "review_queue", "arguments": {"customer_id": "c_scope"}})
    assert result["coverage"]["complete"] and len(result["items"]) == 45
    assert len({x["id"] for x in result["items"]}) == 45
    filters = {
        "customer_id": "c_scope",
        "start": "2026-11-02",
        "end": "2026-11-02",
        "timezone": "Asia/Bangkok",
    }
    assert call(client, "review_summary", filters)["total"] == 45
    assert call(client, "review_summary", {**filters, "timezone": "UTC"})["total"] == 0
    for report in ("review_statuses", "import_statuses", "processing_statuses"):
        data = call(client, "build_report", {"report": report, "customer_id": "c_scope"})["report"]
        assert data["complete"]
    jobs = call(client, "operations_summary", {"resource": "jobs"})
    assert jobs["total"] == 1  # private chat jobs are never included


def test_compaction_retains_continuation_scope_and_full_summary():
    from forma.services.assistant_scope import compact

    raw = {
        "items": [{"id": i, "text": "x" * 2000} for i in range(20)],
        "total": 177,
        "offset": 0,
        "limit": 20,
        "next_offset": 20,
        "overview": {"total": 177, "coverage": {"scanned": 177, "complete": True}},
    }
    result = compact(raw, {"status": "open"}, limit=6000)
    assert result["total"] == 177 and result["overview"] == raw["overview"]
    assert result["next_offset"] == len(result["items"]) < 20
    assert result["source_next_offset"] == 20 and result["truncated"]
    assert result["query"] == {"status": "open"}
    assert len(json.dumps(result)) < 6000
    assert len(raw["items"]) == 20  # full persisted input is not mutated


def test_mock_assistant_builds_full_review_chart_and_retains_report_for_document(client):
    from forma.ai.llm import LLMClient, Usage
    from forma.services.context import get_context
    from forma.services.document_reports import freeze

    seed(300)

    class Provider:
        name = "test"

        def complete_json(self, **kw):
            evidence = json.loads(kw["user"])["evidence"]
            report = next((e for e in evidence if e["tool"] == "build_report"), None)
            if not report:
                return json.dumps(
                    {
                        "answer": "",
                        "citations": [],
                        "calls": [
                            {
                                "name": "build_report",
                                "arguments": {"report": "review_issues", "customer_id": "c_scope"},
                            }
                        ],
                    }
                ), Usage(model="test")
            return json.dumps(
                {
                    "answer": "ตรวจครบตามขอบเขตแล้ว",
                    "citations": [report["id"]],
                    "calls": [],
                    "visualizations": [{"evidence_id": report["id"], "view": "bar"}],
                }
            ), Usage(model="test")

    get_context().llm = LLMClient(Provider(), "test", max_retries=0)
    cid = client.post("/api/assistant/conversations", json={}).json()["id"]
    response = client.post(
        f"/api/assistant/conversations/{cid}/messages",
        json={"question": "กราฟปัญหาทุกงาน", "request_id": "full-review-scope"},
    )
    assert response.status_code == 200, response.text
    reply = response.json()["reply"]
    assert reply["ai"] and len(reply["visualizations"]) == 1
    chart = reply["visualizations"][0]
    assert chart["coverage"]["scanned"] == 300 and chart["complete"]
    eid = chart["evidence_id"]
    frozen = freeze(
        {"template": "executive", "evidence_ids": [eid], "formats": ["pdf"]},
        {eid: chart},
        reply["evidence"],
        "th",
        "Asia/Bangkok",
        {"formal-report"},
    )
    assert frozen["sources"][0]["report"]["coverage"]["scanned"] == 300
    saved = client.get(f"/api/assistant/conversations/{cid}").json()["turns"][0]["reply"]
    assert saved["visualizations"] == reply["visualizations"]

    from forma.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="only a preview"):
        freeze(
            {"template": "executive", "evidence_ids": [eid], "formats": ["pdf"]},
            {eid: {**chart, "complete": False}},
            reply["evidence"],
            "th",
            "Asia/Bangkok",
            {"formal-report"},
        )


def test_scoped_pagination_cannot_bypass_roles_or_select_mutating_tools(client):
    from tests.test_workspace import rpc

    seed(21)
    for name, args in [
        ("read_pages", {"source": "audit_history", "arguments": {}}),
        ("review_summary", {"customer_id": "c_other"}),
        ("read_pages", {"source": "review_queue", "arguments": {"customer_id": "c_other"}}),
        ("read_pages", {"source": "run_python", "arguments": {}}),
        ("review_summary", {"start": "2026-11-01"}),
    ]:
        response = rpc(
            client,
            "tools/call",
            {"name": name, "arguments": args},
            headers={"X-Forma-Customers": "c_scope", "X-Forma-Roles": "importer"},
        )
        assert response.json()["result"]["isError"]


def test_search_record_after_100000_is_found(client):
    from tests.test_assistant_reports import seed_records

    seed_records([{"id": "ordinary"}] * 100000 + [{"id": "last-unique"}])
    found = call(client, "search_records", {"dataset_id": "dsv_report", "search": "last-unique"})
    assert found["total"] == 1 and found["records"][0]["id"] == "last-unique"
    assert found["search"]["scanned"] == 100001 and not found["search"]["truncated"]


def test_multi_page_read_stays_on_snapshot_during_concurrent_insert(client, monkeypatch):
    from forma.db.models import ReviewTask
    from forma.db.session import get_engine, session_scope
    from forma.services import workspace_assistant as wa

    seed(21)
    with get_engine().connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    original = wa.run_tool
    pages = 0

    def run(ctx, session, principal, name, arguments):
        nonlocal pages
        result = original(ctx, session, principal, name, arguments)
        if name == "review_queue":
            pages += 1
            if pages == 1:
                with session_scope() as writer:
                    writer.add(
                        ReviewTask(
                            id="rev_new",
                            round_id="r_scope",
                            customer_id="c_scope",
                            kind="mapping",
                            priority=999,
                            reasons=["new VAT"],
                        )
                    )
        return result

    monkeypatch.setattr(wa, "run_tool", run)
    result = call(client, "read_pages", {"source": "review_queue", "arguments": {"customer_id": "c_scope"}})
    assert result["coverage"]["complete"] and result["total"] == 21
    assert len({r["id"] for r in result["items"]}) == 21
    assert "rev_new" not in {r["id"] for r in result["items"]}
    assert call(client, "review_summary", {"customer_id": "c_scope"})["total"] == 22


def test_import_pages_continue_and_filters_match_overview(client):
    from forma.db.models import ImportRound, SourceFile
    from forma.db.session import session_scope

    seed(21)
    with session_scope() as session:
        session.add_all([ImportRound(id=f"import_{i}", customer_id="c_scope") for i in range(25)])
        session.flush()
        session.add_all(
            [
                SourceFile(
                    round_id=rid,
                    customer_id="c_scope",
                    sha256="a" * 64,
                    original_name=rid + ".csv",
                    size=10,
                    kind="csv",
                    storage_key="mock/" + rid,
                )
                for rid in ["r_scope", *(f"import_{i}" for i in range(25))]
            ]
        )
    result = call(client, "read_pages", {"source": "search_imports", "arguments": {"customer_id": "c_scope"}})
    assert result["coverage"]["complete"] and result["total"] == 26
    assert len(result["items"]) == 26
    queue = call(client, "review_queue", {"status": "all", "round_id": "r_scope"})
    assert queue["total"] == queue["overview"]["total"] == 21
    filtered = call(client, "review_queue", {"start": "2026-11-02", "end": "2026-11-02", "timezone": "UTC"})
    assert filtered["total"] == filtered["overview"]["total"] == 0


def test_large_report_compaction_preserves_coverage_and_scope():
    from forma.services.assistant_scope import compact

    report = {
        "rows": [{"label": "long" * 5000}],
        "coverage": {"matched": 1001, "scanned": 1001, "complete": True},
        "complete": True,
        "scope": {"th": "ครบทั้งหมด"},
        "filters": {"customer_id": "c_scope"},
        "categories_overlap": True,
    }
    result = compact({"report": report}, {"report": "review_issues"})
    assert result["report_available"] and result["truncated"]
    assert result["coverage"] == report["coverage"] and result["filters"] == report["filters"]
    assert result["categories_overlap"]
