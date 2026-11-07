"""Run inside a mock-only API: exercise Redis delivery, document worker and sandbox, then HTTP downloads.

No model is called. The fixture is explicitly synthetic. Never run against a non-mock environment.
"""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
from forma.auth import Principal
from forma.db.models import AssistantTurn
from forma.db.session import session_scope
from forma.services import conversations, document_reports
from forma.services.assistant_reports import column, report, text
from forma.services.context import get_context


def main() -> None:
    ctx = get_context()
    assert ctx.settings.llm_mock_only, "Only the isolated mock-only stack is allowed"
    assert ctx.settings.job_backend == "arq"
    assert ctx.settings.report_sandbox_socket
    for attempt in range(30):
        try:
            httpx.get("http://127.0.0.1:8000/ready", timeout=2).raise_for_status()
            break
        except httpx.HTTPError:
            if attempt == 29:
                raise
            time.sleep(1)
    # Fresh schema, concurrent setup: reproduce the PostgreSQL startup lock regression.
    if ctx.settings.database_url.startswith("postgresql"):
        import psycopg
        from forma.ai.workflow_store import workflow_storage
        from psycopg import sql
        from sqlalchemy.engine import make_url

        url = make_url(ctx.settings.database_url).set(drivername="postgresql")
        schema = "report_probe_" + uuid.uuid4().hex
        with psycopg.connect(
            url.render_as_string(hide_password=False), autocommit=True
        ) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            settings = ctx.settings.model_copy(
                update={
                    "database_url": url.update_query_dict(
                        {"options": f"-csearch_path={schema}"}
                    ).render_as_string(hide_password=False)
                }
            )

            def setup(i: int) -> None:
                with workflow_storage(settings, f"probe-{i}"):
                    pass

            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(setup, range(8)))
            finally:
                conn.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                )
    p = Principal("report-probe", frozenset({"admin"}), name="report-probe")
    value = str(Decimal("0.10") + Decimal("0.20"))
    source = report(
        text("Synthetic measured results", "ผลการวัดจากข้อมูลจำลอง"),
        [column("label", "Market", "ตลาด", "text"), column("value", "THB", "บาท")],
        [{"label": "ประเทศไทย", "value": value}],
        ["value"],
        text("Isolated test only", "ข้อมูลทดสอบในระบบแยกเท่านั้น"),
        [],
        [],
    )["report"]
    with session_scope() as s:
        chat = conversations.create(s, p, "Formal report integration probe", None, None)
        turn = AssistantTurn(
            conversation_id=chat.id,
            request_id="probe",
            sequence=1,
            question="Synthetic formal report",
            status="complete",
        )
        s.add(turn)
        s.flush()
        job = document_reports.create(
            ctx,
            s,
            p,
            chat.id,
            turn.id,
            {
                "template": "executive",
                "formats": ["docx", "pptx", "pdf"],
                "evidence_ids": [1],
            },
            {1: source},
            [
                {
                    "id": 1,
                    "tool": "build_report",
                    "arguments": {"report": "synthetic_probe"},
                }
            ],
            {"formal-report"},
            "th",
            "Asia/Bangkok",
        )
    with httpx.Client(
        base_url="http://127.0.0.1:8000",
        timeout=20,
        headers={"X-Forma-User": p.subject, "X-Forma-Roles": "admin"},
    ) as client:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            response = client.get(f"/api/assistant/reports/{job['id']}")
            response.raise_for_status()
            result = response.json()
            if result["status"] in {"done", "failed", "cancelled"}:
                break
            time.sleep(1)
        assert result["status"] == "done", result
        assert result["snapshot"]["sources"][0]["report"]["rows"][0]["value"] == "0.30"
        for artifact in result["artifacts"]:
            response = client.get(f"/api/assistant/artifacts/{artifact['id']}")
            response.raise_for_status()
            version = response.json()["versions"][0]
            response = client.get(f"/api/assistant/attachments/{version['file']['id']}")
            response.raise_for_status()
            assert len(response.content) > 1000
        print(
            json.dumps(
                {
                    "job_id": job["id"],
                    "status": "done",
                    "formats": result["formats"],
                    "files": result["quality"]["files"],
                    "cost_usd": 0,
                }
            )
        )


if __name__ == "__main__":
    main()
