"""Run INSIDE the isolated mock-only repro API. Reads an existing synthetic round; never approves it.

Exercises the real graph, file workbench and isolated Python runner using scripted model responses.
Usage: docker exec ... python /tmp/activity_probe.py ROUND_ID
"""

from __future__ import annotations

import json
import sys
import time
import uuid

from forma.ai.heuristics import propose_mapping
from forma.ai.llm import LLMClient, MockProvider
from forma.ai.mapping_workflow import run_workflow
from forma.ai.workbench import FileWorkbench
from forma.core.settings import get_settings
from forma.db.session import session_scope
from forma.jobs import activity, runner
from forma.services.mapping import bind_sources
from forma.services.mapping_lab import executor
from forma.services.rounds import get_round, round_profile


def check(ctx, payload, progress):
    assert ctx.settings.llm_mock_only and ctx.llm.is_mock
    with session_scope() as session:
        r = get_round(session, payload["round_id"])
        profile = round_profile(session, r.id)
        schema = ctx.registry.get_ref(r.target_schema)
        draft = propose_mapping(profile, schema, customer_id=r.customer_id)
        bench = FileWorkbench(
            schema,
            profile,
            r.files,
            bind=lambda doc: bind_sources(doc, r.files, resolve=ctx.storage.local_path),
            resolve=ctx.storage.local_path,
            execute=executor(ctx, r.files, schema),
        )
        assert bench.execute is not None, "The isolated runner must be configured"
        counters = {"analyst": 0, "mapper": 0, "reviewer": 0}

        def respond(system, user, contract):
            # Deliberate mock latency lets the UI test observe an actual outstanding request.
            time.sleep(2)
            if "Evidence Analyst" in system:
                counters["analyst"] += 1
                if counters["analyst"] == 1:
                    return {"inspect": [{"table_index": 0, "what": "rows"}]}
                return {
                    "analysis": {
                        "facts": [],
                        "hypotheses": [],
                        "mapping_guidance": [],
                        "missing_information": [],
                    }
                }
            if "Semantic Reviewer" in system:
                counters["reviewer"] += 1
                if counters["reviewer"] == 1:
                    return {"inspect": [{"table_index": 1, "what": "rows"}]}
                return {
                    "review": {
                        "verdict": "pass",
                        "checked": ["mock source/schema verification"],
                        "concerns": [],
                        "clarifications": [],
                    }
                }
            counters["mapper"] += 1
            if counters["mapper"] == 1:
                return {
                    "python": "import json\nfrom openpyxl import load_workbook\nm=json.load(open('inputs/manifest.json'))\nw=load_workbook(m[0]['path'],read_only=True,data_only=True)\nprint(json.dumps({s.title:s.max_row for s in w.worksheets},ensure_ascii=False))"
                }
            return {
                "mapping": draft.mapping.model_dump(mode="json"),
                "open_questions": [],
                "clarifications": [],
            }

        model = LLMClient(MockProvider(respond), "mock-activity", max_retries=0)
        _, result = run_workflow(
            model,
            profile,
            schema,
            draft,
            bench,
            ctx.settings,
            snapshot={"activity_probe": uuid.uuid4().hex},
        )
        assert result.data["workflow"]["status"] == "verified_proposal", result.data
        assert result.data["self_check"]["python_turns"] == 1, result.data
        return {
            "mock_only": True,
            "cost_usd": 0,
            "workflow": result.data["workflow"],
            "self_check": result.data["self_check"],
            "calls": len(model.provider.calls),
        }


if __name__ == "__main__":
    assert get_settings().llm_mock_only, (
        "Only an isolated mock repro may run this probe"
    )
    runner.handler("activity_mock_probe")(check)
    jid = runner.enqueue(
        "activity_mock_probe",
        {"round_id": sys.argv[1]},
        round_id=sys.argv[1],
        sync=True,
    )
    job = runner.job_status(jid)
    print(
        "ACTIVITY_PROBE="
        + json.dumps({"job_id": jid, "status": job["status"], "error": job["error"]})
    )
    assert job["status"] == "done", job["error"]
    receipts = activity.read(jid, job["attempts"], limit=500)
    sandbox = [
        r
        for r in receipts
        if r["action"] == "sandbox.python" and r["state"] == "completed"
    ]
    assert sandbox, "Missing actual sandbox receipt"
    assert sandbox[-1]["detail"]["output"]["status"] == "ok", sandbox[-1]
    assert sandbox[-1]["detail"]["output"]["exit_code"] == 0, sandbox[-1]
