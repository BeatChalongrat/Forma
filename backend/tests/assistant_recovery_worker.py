"""Subprocess fixture: crash after a committed tool, resume against the same private DB."""

import os
import sys

from forma.db.models import AssistantRun
from forma.jobs import runner
from forma.services import attachments, workspace_assistant
from forma.services.assistant_runtime import active, step
from forma.services.context import get_context

assert get_context().llm.is_mock, "This fixture must never call a paid model"


def fake(ctx, session, principal, *args, **kwargs):
    journal = active.get()
    run = session.get(AssistantRun, journal.turn_id)

    def create_file():
        return attachments.add_output(
            ctx, session, principal, run.conversation_id, run.turn_id, "report.csv", b"sku,total\nA,12"
        )

    step("make_report", {}, create_file)
    if sys.argv[2] == "crash":
        os._exit(86)
    kwargs["stream"].delta("Recovered report")
    return {"answer": "Recovered report", "ai": True, "evidence": [], "actions": []}


workspace_assistant.ask = fake
assert runner.run_job(sys.argv[1])
