"""Finished files must not be processed again to repair a lost queue entry."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from forma.db.models import (
    AuditLog,
    Batch,
    BatchItem,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    MappingVersion,
)
from forma.db.session import init_db, session_scope
from forma.jobs import runner
from forma.jobs.recovery import reconcile_completed_jobs


@pytest.fixture
def completed(isolated_env: Path) -> str:
    init_db()
    with session_scope() as s:
        s.add(Customer(id="cus_recovery", code="REC", name="Recovery"))
        s.add(Batch(id="bch_upload", label="Upload", created_by="test"))
        s.flush()
        s.add(
            ImportRound(
                id="rnd_recovery",
                customer_id="cus_recovery",
                status="published",
                summary={"dataset_version_id": "dsv_recovery"},
            )
        )
        s.add(
            MappingVersion(
                id="map_recovery",
                customer_id="cus_recovery",
                round_id="rnd_recovery",
                version=1,
                status="approved",
                target_schema="product@1",
                document={},
            )
        )
        s.add(
            DatasetVersion(
                id="dsv_recovery",
                customer_id="cus_recovery",
                round_id="rnd_recovery",
                mapping_version_id="map_recovery",
                status="published",
                records_key="records.jsonl",
                row_count=172,
            )
        )
        s.add(
            ExportArtifact(
                id="exp_recovery",
                dataset_version_id="dsv_recovery",
                customer_id="cus_recovery",
                fmt="json",
                storage_key="output.json",
                row_count=172,
                sha256="a" * 64,
            )
        )
        s.add(
            BatchItem(
                id="bit_recovery",
                batch_id="bch_upload",
                file_name="test.xlsx",
                sha256="b" * 64,
                size=100,
                kind="excel",
                storage_key="source.xlsx",
                status="attached",
                round_id="rnd_recovery",
                customer_id="cus_recovery",
            )
        )
        s.add(
            Job(
                id="job_recovery",
                kind="process_batch_item",
                payload={"item_id": "bit_recovery"},
                batch_id="bat_job_group",
                result={"stages": {"identify": {"at": "earlier"}}},
            )
        )
    return "job_recovery"


def test_reconciliation_closes_queue_without_new_output(completed: str) -> None:
    assert runner.queue_depth() == 1
    assert reconcile_completed_jobs() == [completed]
    assert reconcile_completed_jobs() == []
    assert runner.run_job(completed) is False  # an old Redis delivery cannot run it again
    job = runner.job_status(completed)
    assert job["status"] == "done" and job["progress"] == 100 and job["attempts"] == 0
    assert job["round_id"] == "rnd_recovery"
    assert job["result"]["publish"]["export_id"] == "exp_recovery"
    assert job["result"]["stages"]["identify"]["at"] == "earlier"
    assert runner.queue_depth() == 0
    with session_scope() as s:
        for model in (MappingVersion, DatasetVersion, ExportArtifact):
            assert s.scalar(select(func.count()).select_from(model)) == 1
        logs = list(s.scalars(select(AuditLog).where(AuditLog.action == "job.reconciled")))
        assert len(logs) == 1 and logs[0].details["dataset_version_id"] == "dsv_recovery"


@pytest.mark.parametrize("incomplete", ["no_export", "draft", "different_summary", "running", "force"])
def test_reconciliation_requires_evidence_and_leaves_active_work_alone(
    completed: str, incomplete: str
) -> None:
    with session_scope() as s:
        if incomplete == "no_export":
            s.delete(s.get(ExportArtifact, "exp_recovery"))
        elif incomplete == "draft":
            s.get(DatasetVersion, "dsv_recovery").status = "draft"
        elif incomplete == "different_summary":
            s.get(ImportRound, "rnd_recovery").summary = {"dataset_version_id": "some_other_output"}
        elif incomplete == "running":
            s.get(Job, completed).status = "running"
        else:
            s.get(Job, completed).payload = {"item_id": "bit_recovery", "force": True}
    assert reconcile_completed_jobs() == []
    assert runner.job_status(completed)["status"] != "done"


def test_worker_delivery_also_skips_completed_pipeline(
    completed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.jobs import handlers  # noqa: F401

    handler = Mock(side_effect=AssertionError("must not run the completed file again"))
    monkeypatch.setitem(runner._HANDLERS, "process_batch_item", handler)
    assert runner.run_job(completed)
    handler.assert_not_called()
    assert runner.job_status(completed)["result"]["reconciled"]
    assert runner.run_job(completed) is False


def test_pipeline_direct_resume_preserves_published_output(
    completed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from forma.jobs.handlers import run_pipeline
    from forma.services import publishing
    from forma.services.context import get_context

    publish = Mock(side_effect=AssertionError("must not publish twice"))
    monkeypatch.setattr(publishing, "auto_publish", publish)
    result = run_pipeline(
        get_context(), {"round_id": "rnd_recovery", "auto_publish": True}, runner.JobProgress(completed)
    )
    assert result["dataset_version_id"] == "dsv_recovery"
    publish.assert_not_called()


def test_arq_start_restores_unfinished_jobs_only(completed: str, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Mock(name="backend")
    backend.name = "arq"
    monkeypatch.setattr(runner, "get_backend", lambda: backend)
    with session_scope() as s:
        s.add(Job(id="job_pending", kind="profile_round", payload={"round_id": "rnd_pending"}))
    result = runner.start()
    assert result["reconciled"] == [completed]
    assert result["resubmitted"] == ["job_pending"]
    backend.submit.assert_called_once_with("job_pending")


def test_arq_startup_does_not_nest_event_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    async def probe() -> str:
        return "arq"

    def start() -> dict:
        return {"backend": asyncio.run(probe())}

    monkeypatch.setattr(runner, "start", start)
    asyncio.run(runner._arq_startup({}))
