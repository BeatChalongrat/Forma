"""Behavioral graph tests: independent review, bounded repair, durable resume and fail-closed gates."""

import json

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from tests.test_agents import _doc, _draft, _profile

from forma.ai.llm import LLMClient, LLMError, MockProvider
from forma.ai.mapping_workflow import build_workflow, run_workflow, snapshot_id
from forma.ai.workbench import RunReport
from forma.ai.workflow_store import workflow_storage
from forma.core.settings import Settings


class Bench:
    def __init__(self, *, complete=True, errors=0, decisions=None):
        self.complete, self.errors = complete, errors
        self.inspections = []
        self.fail = False
        self.decisions = decisions or []

    def ground(self, doc):
        return []

    def inspect(self, requests):
        self.inspections.extend(requests)
        return [{"request": r, "answer": {"values": ["001", "002"]}} for r in requests]

    def run(self, doc):
        if self.fail:
            raise RuntimeError("simulated worker crash")
        return RunReport(
            12, 12, errors=self.errors, complete=self.complete, decision_questions=self.decisions
        )

    verify = run


def profile():
    return _profile({"สินค้า": [("รหัสสินค้า", "string"), ("ชื่อสินค้า", "string"), ("ราคาขาย", "float")]})


def settings(path, **kwargs):
    return Settings(database_url="sqlite://", data_dir=path, mapping_workflow="graph", **kwargs)


def response(review=None):
    def responder(system, user, schema):
        if "Evidence Analyst" in system:
            return {
                "analysis": {"facts": [], "hypotheses": [], "mapping_guidance": [], "missing_information": []}
            }
        if "Semantic Reviewer" in system:
            return {
                "review": review
                or {"verdict": "pass", "checked": ["source and schema"], "concerns": [], "clarifications": []}
            }
        return {"mapping": _doc().model_dump(mode="json"), "open_questions": [], "clarifications": []}

    return responder


def run(tmp_path, schema, *, responder=None, bench=None, retries=0, **kwargs):
    client = LLMClient(MockProvider(responder or response()), "test", max_retries=retries)
    doc, res = run_workflow(
        client,
        profile(),
        schema,
        _draft(),
        bench or Bench(),
        settings(tmp_path, **kwargs),
        snapshot={"test": "a"},
    )
    return client, doc, res


def concern(kind="mapping_error"):
    return {
        "target": "/sku",
        "issue": "Check identifiers",
        "kind": kind,
        "sources": [{"table_index": 0, "column": "รหัสสินค้า", "location": "A2:A13"}],
    }


def test_three_roles_and_no_mapper_confidence_in_reviewer(tmp_path, schema):
    client, _, res = run(tmp_path, schema)
    assert res.data["workflow"]["status"] == "verified_proposal"
    assert [e["role"] for e in res.data["workflow"]["roles"]] == ["analyst", "mapper", "execute", "reviewer"]
    prompt = json.loads(client.provider.calls[-1]["user"])
    assert "analysis" not in prompt and "meta" not in prompt["mapping_to_check"]
    assert all("confidence" not in f and "evidence" not in f for f in prompt["mapping_to_check"]["fields"])
    assert res.usage.calls == 3


def test_completed_checkpoint_reused_without_provider_calls(tmp_path, schema):
    _, _, first = run(tmp_path, schema)
    client, _, second = run(tmp_path, schema)
    assert client.provider.calls == []
    assert second.usage == first.usage
    assert second.data["workflow"]["thread_id"] == first.data["workflow"]["thread_id"]


def test_reviewer_repair_is_bounded_and_unresolved_blocks(tmp_path, schema):
    review = {"verdict": "repair", "checked": ["identifiers"], "concerns": [concern()], "clarifications": []}
    client, _, res = run(tmp_path, schema, responder=response(review))
    assert len(client.provider.calls) == 5
    assert res.data["workflow"]["repairs"] == 1
    assert res.data["workflow"]["status"] == "failed"
    assert any("semantic review" in p for p in res.data["self_check"]["problems_left"])


def test_reviewer_asks_actionable_question_without_approval(tmp_path, schema):
    question = {
        "target": "/currency",
        "question": "Which currency?",
        "why": "Source has no currency",
        "kind": "text",
        "action": "constant",
    }
    review = {
        "verdict": "ask",
        "checked": ["currency"],
        "concerns": [{**concern("missing_business_fact"), "target": "/currency"}],
        "clarifications": [question],
    }
    _, _, res = run(tmp_path, schema, responder=response(review))
    assert res.data["workflow"]["status"] == "awaiting_human"
    assert res.data["clarifications"] == [question]
    assert not res.data["self_check"]["problems_left"]


@pytest.mark.parametrize("complete,errors", [(False, 0), (True, 1)])
def test_reviewer_pass_never_overrides_execution(tmp_path, schema, complete, errors):
    _, _, res = run(tmp_path, schema, bench=Bench(complete=complete, errors=errors))
    assert "Full execution must pass before approval" in res.data["self_check"]["problems_left"]


def test_invalid_evidence_reference_fails_closed(tmp_path, schema):
    review = {
        "verdict": "repair",
        "checked": ["id"],
        "concerns": [{**concern(), "sources": [{"table_index": 999, "location": "A2"}]}],
    }
    _, _, res = run(tmp_path, schema, responder=response(review))
    assert res.notes and "llm_failed" in res.notes[0]
    assert res.data["workflow"]["status"] == "failed"


def test_budget_is_shared_across_roles_and_durable(tmp_path, schema):
    review = {"verdict": "repair", "checked": ["id"], "concerns": [concern()]}
    client, _, res = run(tmp_path, schema, responder=response(review), mapping_workflow_calls=3)
    assert len(client.provider.calls) == 3
    assert res.data["workflow"]["provider_attempts"] == 3
    assert res.data["workflow"]["status"] == "failed"
    again, _, _ = run(tmp_path, schema, mapping_workflow_calls=3)
    assert not again.provider.calls


def test_inspection_checkpoint_and_restart_does_not_repeat_analyst(tmp_path, schema):
    def respond(system, user, output):
        body = json.loads(user)
        if "Evidence Analyst" in system and not body.get("evidence"):
            return {"inspect": [{"table_index": 0, "what": "rows"}]}
        return response()(system, user, output)

    client = LLMClient(MockProvider(respond), "test", max_retries=0)
    bench = Bench()
    bench.fail = True
    path = str(tmp_path / "restart.sqlite")
    config = {"configurable": {"thread_id": "recover"}}
    with SqliteSaver.from_conn_string(path) as saver:
        graph = build_workflow(client, profile(), schema, _draft(), bench, settings(tmp_path), saver)
        with pytest.raises(RuntimeError, match="worker crash"):
            graph.invoke({"events": []}, config)
    analyst_calls = len([c for c in client.provider.calls if "Evidence Analyst" in c["system"]])
    bench.fail = False
    with SqliteSaver.from_conn_string(path) as saver:
        graph = build_workflow(client, profile(), schema, _draft(), bench, settings(tmp_path), saver)
        final = graph.invoke(None, config)
    assert final["review"]["verdict"] == "pass"
    assert len(bench.inspections) == 1
    assert len([c for c in client.provider.calls if "Evidence Analyst" in c["system"]]) == analyst_calls


def test_snapshot_invalidates_source_contract_requirement_and_memory():
    base = {"files": ["abc"], "schema": {"a": 1}, "instructions": "x", "learned": []}
    old = snapshot_id(base)
    for key in base:
        assert snapshot_id({**base, key: "changed"}) != old


def test_ledger_records_failed_attempt_without_inventing_usage(tmp_path):
    with workflow_storage(settings(tmp_path, mapping_workflow_calls=3), "one") as (_, ledger):
        ledger.reserve()
        assert ledger.used() == 1 and ledger.usage().calls == 0
    with workflow_storage(settings(tmp_path, mapping_workflow_calls=3), "one") as (_, ledger):
        assert ledger.used() == 1
        ledger.reserve()
        ledger.reserve()
        with pytest.raises(LLMError, match="budget"):
            ledger.reserve()


def test_analyst_cannot_change_mapping_or_approve(tmp_path, schema):
    def respond(system, user, output):
        return {"mapping": _doc().model_dump(mode="json"), "approved": True}

    _, _, res = run(tmp_path, schema, responder=respond)
    assert res.data["workflow"]["status"] == "failed"
    assert res.notes


def test_durable_deadline_not_reset_on_restart(tmp_path, monkeypatch):
    from forma.ai import workflow_store

    monkeypatch.setattr(workflow_store.time, "time", lambda: 100.0)
    with workflow_storage(settings(tmp_path, llm_task_budget_s=10), "deadline") as (_, ledger):
        ledger.reserve()
    monkeypatch.setattr(workflow_store.time, "time", lambda: 111.0)
    with workflow_storage(settings(tmp_path, llm_task_budget_s=10), "deadline") as (_, ledger):
        with pytest.raises(LLMError, match="time budget"):
            ledger.reserve()
        assert ledger.used() == 1


def test_review_ask_cannot_hide_a_mapping_error(tmp_path, schema):
    review = {
        "verdict": "ask",
        "checked": ["sku"],
        "concerns": [concern()],
        "clarifications": [
            {"target": "/currency", "question": "Currency?", "why": "Missing", "kind": "text"}
        ],
    }
    _, _, res = run(tmp_path, schema, responder=response(review))
    assert res.data["workflow"]["status"] == "failed"
    assert "Repair source-backed" in res.notes[0]


def test_role_output_schemas_have_resolvable_references(tmp_path, schema):
    from jsonschema import Draft202012Validator

    def respond(system, user, output_schema):
        answer = response()(system, user, output_schema)
        if "Evidence Analyst" in system or "Semantic Reviewer" in system:
            Draft202012Validator(output_schema).validate(answer)
        return answer

    _, _, res = run(tmp_path, schema, responder=respond)
    assert res.data["workflow"]["status"] == "verified_proposal"


@pytest.mark.parametrize("malformed_review_first", [False, True])
def test_graph_is_used_by_real_upload_profile_propose_api(isolated_env, monkeypatch, malformed_review_first):
    from fastapi.testclient import TestClient
    from tests.conftest import clear_forma_caches
    from tests.test_api import _upload, make_workbook

    from forma.api.app import create_app
    from forma.services.context import get_context

    monkeypatch.setenv("FORMA_MAPPING_WORKFLOW", "graph")
    clear_forma_caches()

    reviews = 0

    def responder(system, user, output):
        nonlocal reviews
        if "Semantic Reviewer" in system:
            reviews += 1
            if malformed_review_first and reviews == 1:
                return {
                    "review": {
                        "verdict": "repair",
                        "checked": ["identity"],
                        "concerns": [{**concern(), "target": "/sku (record.dedupe)"}],
                    }
                }
        if "Evidence Analyst" in system or "Semantic Reviewer" in system:
            return response()(system, user, output)
        return {"mapping": json.loads(user)["draft_mapping"], "clarifications": [], "open_questions": []}

    with TestClient(create_app()) as c:
        get_context().llm = LLMClient(MockProvider(responder), "test")
        cust = c.post("/api/customers", json={"code": "GRAPH", "name": "Graph integration"}).json()
        rnd = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()
        _upload(c, rnd["id"], make_workbook(isolated_env / "input.xlsx"))
        job = c.post(f"/api/rounds/{rnd['id']}/profile", params={"sync": True}).json()
        assert c.get(f"/api/jobs/{job['job_id']}").json()["status"] == "done"
        job = c.post(f"/api/rounds/{rnd['id']}/propose", json={"sync": True, "use_llm": True}).json()
        result = c.get(f"/api/jobs/{job['job_id']}").json()
        assert result["status"] == "done", result.get("error")
        mapping = c.get(f"/api/mappings/{result['result']['mapping_version_id']}").json()
        workflow = mapping["policy"]["workflow"]
        assert {e["role"] for e in workflow["roles"]} >= {"analyst", "mapper", "execute", "reviewer"}
        assert mapping["policy"]["verification"]["complete"]
        assert mapping["policy"]["simulation"]["rows_out"] == 12
        assert any(e["agent"] == "mapping_workflow" for e in mapping["agent_log"])
        assert not mapping["policy"]["checks"]["llm_fallback"]
        assert reviews == (2 if malformed_review_first else 1)
        # An already approved layout must retain the no-LLM fast path under graph mode.
        assert c.post(f"/api/mappings/{mapping['id']}/approve", json={}).status_code == 200
        calls = len(get_context().llm.provider.calls)
        second = c.post("/api/rounds", json={"customer_id": cust["id"]}).json()
        _upload(c, second["id"], make_workbook(isolated_env / "next.xlsx"))
        c.post(f"/api/rounds/{second['id']}/profile", params={"sync": True})
        job = c.post(f"/api/rounds/{second['id']}/propose", json={"sync": True, "use_llm": True}).json()
        result = c.get(f"/api/jobs/{job['job_id']}").json()
        assert result["status"] == "done", result.get("error")
        reused = c.get(f"/api/mappings/{result['result']['mapping_version_id']}").json()
        assert any(e["agent"] == "memory" and e["result"] == "reuse" for e in reused["agent_log"])
        assert len(get_context().llm.provider.calls) == calls


def test_reviewer_receives_tool_receipts_and_scoped_decisions_not_mapper_opinions(
    tmp_path, schema, monkeypatch
):
    from forma.ai import agents
    from forma.ai.llm import Usage

    receipts = {
        "history": [
            {
                "turn": "python",
                "code": "print(workbook['C2'].value)",
                "result": {
                    "status": "ok",
                    "stdout": "=Counts!B2*Pack!B2",
                    "inputs": [{"sha256": "source-hash"}],
                },
            },
            {"turn": "inspect", "evidence": [{"answer": {"values": [12]}}]},
            {"turn": "mapping", "confidence": 1.0, "claim": "Trust this answer"},
        ]
    }
    monkeypatch.setattr(
        agents,
        "mapping_agent",
        lambda *a, **kw: (
            _doc(),
            agents.AgentResult(
                "mapping", {"self_check": receipts, "private_opinion": "Trust me"}, Usage(), 0
            ),
        ),
    )
    client = LLMClient(MockProvider(response()), "test", max_retries=0)
    memory = {"approved_interpretation_rules": [{"target": "/unit/sell_unit", "answer": "BOTTLE"}]}
    run_workflow(
        client,
        profile(),
        schema,
        _draft(),
        Bench(),
        settings(tmp_path),
        snapshot={"test": "receipts"},
        learned=memory,
    )
    payload = json.loads(client.provider.calls[-1]["user"])
    assert payload["mapper_tool_receipts"][0]["result"]["stdout"] == "=Counts!B2*Pack!B2"
    assert payload["mapper_tool_receipts"][0]["result"]["inputs"][0]["sha256"] == "source-hash"
    assert payload["mapper_tool_receipts"][1]["evidence"][0]["answer"]["values"] == [12]
    assert payload["recalled_operator_decisions"] == memory
    assert "Trust me" not in json.dumps(payload) and "Trust this answer" not in json.dumps(payload)


def test_confirm_cannot_offer_alternatives_it_cannot_apply(tmp_path, schema):
    review = {
        "verdict": "ask",
        "checked": ["sku"],
        "concerns": [concern("missing_business_fact")],
        "clarifications": [
            {
                "target": "/sku",
                "question": "Which source?",
                "why": "Ambiguous",
                "kind": "confirm",
                "options": ["Source A", "Source B"],
            }
        ],
    }
    _, _, res = run(tmp_path, schema, responder=response(review))
    assert res.data["workflow"]["status"] == "failed"
    assert "Confirm only endorses" in res.notes[0]


def test_graph_nodes_honor_the_callers_expired_task_deadline(tmp_path, schema):
    from forma.ai.budget import task_budget

    with task_budget(0):
        client, _, res = run(tmp_path, schema)
    assert client.provider.calls == []
    assert res.data["workflow"]["status"] == "failed"
    assert "deadline" in res.notes[0]


def test_reviewer_contract_uses_customer_schema_and_separates_dsl_annotations():
    from jsonschema import Draft202012Validator, ValidationError

    from forma.ai.mapping_workflow import _review_response_schema
    from forma.schema_registry import TargetSchema

    contract = TargetSchema(
        "customer-contract",
        1,
        {
            "type": "object",
            "properties": {
                "item_code": {"type": "string"},
                "balances": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"available": {"type": "number"}}},
                },
            },
        },
    )
    output = _review_response_schema(contract)
    validator = Draft202012Validator(output)
    allowed = output["$defs"]["Concern"]["properties"]["target"]["enum"]
    assert set(allowed) == {None, *(f.pointer for f in contract.fields(include_system=True))}
    for pointer in ("/item_code", "/balances/*/available", None):
        validator.validate(
            {
                "review": {
                    "verdict": "repair",
                    "checked": ["identity"],
                    "concerns": [{**concern(), "target": pointer, "issue": "Check record.dedupe"}],
                }
            }
        )
    for pointer in ("/item_code (record.dedupe)", "record.dedupe", "/sku", "", "/balances/*/unknown"):
        with pytest.raises(ValidationError):
            validator.validate(
                {
                    "review": {
                        "verdict": "repair",
                        "checked": ["identity"],
                        "concerns": [{**concern(), "target": pointer}],
                    }
                }
            )


def test_malformed_reviewer_pointer_repaired_without_rerunning_mapper(tmp_path, schema):
    reviews = 0
    question = {
        "target": "/currency",
        "question": "Which currency?",
        "why": "Not supplied",
        "kind": "text",
        "action": "constant",
    }

    def responder(system, user, output):
        nonlocal reviews
        if "Semantic Reviewer" not in system:
            return response()(system, user, output)
        reviews += 1
        if reviews == 2:
            assert "without annotations" in user and "concern" in user.lower()
        return {
            "review": {
                "verdict": "ask",
                "checked": ["currency"],
                "concerns": [
                    {
                        **concern("missing_business_fact"),
                        "target": "/currency (record.dedupe)" if reviews == 1 else "/currency",
                    }
                ],
                "clarifications": [question],
            }
        }

    client, doc, result = run(tmp_path, schema, responder=responder, retries=1)
    assert len(client.provider.calls) == 4  # analyst, mapper, bad review, corrected review
    assert result.usage.calls == result.data["workflow"]["provider_attempts"] == 4
    assert doc.model_dump(exclude={"meta"}) == _doc().model_dump(exclude={"meta"})
    assert doc.meta.created_by == "ai"
    assert not result.notes
    assert result.data["workflow"]["status"] == "awaiting_human"
    assert result.data["clarifications"] == [question]
    assert not result.data["self_check"]["problems_left"]


@pytest.mark.parametrize("pointer", ["/sku (record.dedupe)", "/unknown", "", "/prices/*/invented"])
def test_reviewer_invalid_pointer_is_not_silently_coerced_or_approved(tmp_path, schema, pointer):
    review = {"verdict": "repair", "checked": ["id"], "concerns": [{**concern(), "target": pointer}]}
    client, _, result = run(tmp_path, schema, responder=response(review), retries=1)
    assert len(client.provider.calls) == result.data["workflow"]["provider_attempts"] == 4
    assert "llm_failed" in result.notes[0]
    assert result.data["workflow"]["status"] == "failed"
    assert "Unknown target" in result.notes[0]
    assert not result.data["workflow"]["review"]


def test_whole_mapping_concern_uses_null_and_still_blocks_approval(tmp_path, schema):
    review = {
        "verdict": "repair",
        "checked": ["dedupe"],
        "concerns": [{**concern(), "target": None, "issue": "record.dedupe loses conflicting rows"}],
    }
    _, _, result = run(tmp_path, schema, responder=response(review), mapping_workflow_repairs=0)
    assert not result.notes
    assert result.data["workflow"]["status"] == "failed"
    assert (
        "semantic review: record.dedupe loses conflicting rows" in result.data["self_check"]["problems_left"]
    )


def row_decision():
    return {
        "id": "rows:abc",
        "target": "/prices/*/amount",
        "affected_fields": ["/prices/*/amount"],
        "action": "select_rows",
        "kind": "enum",
        "options": ["A", "B"],
    }


def covered_review():
    return {
        "verdict": "ask",
        "checked": ["Prices"],
        "concerns": [{**concern("missing_business_fact"), "target": "/prices/*/amount"}],
        "clarifications": [],
        "covered_questions": ["rows:abc"],
    }


@pytest.mark.parametrize("execution_errors", [0, 1])
def test_existing_row_decision_does_not_need_invented_numeric_question(tmp_path, schema, execution_errors):
    client, _, result = run(
        tmp_path,
        schema,
        responder=response(covered_review()),
        bench=Bench(errors=execution_errors, decisions=[row_decision()]),
    )
    assert not result.notes
    assert result.data["clarifications"] == []
    assert result.data["workflow"]["review"]["covered_questions"] == ["rows:abc"]
    prompt = json.loads(client.provider.calls[-1]["user"])
    assert prompt["execution"]["decision_questions"] == [row_decision()]
    if execution_errors:
        assert "Full execution must pass before approval" in result.data["self_check"]["problems_left"]
    else:
        assert result.data["workflow"]["status"] == "awaiting_human"


@pytest.mark.parametrize("bad", ["id", "target", "sample", "mapping_error"])
def test_existing_decisions_cannot_waive_uncovered_concerns(tmp_path, schema, bad):
    review = covered_review()
    if bad == "id":
        review["covered_questions"] = ["rows:invented"]
    if bad == "target":
        review["concerns"][0]["target"] = "/currency"
    if bad == "mapping_error":
        review["concerns"][0]["kind"] = "mapping_error"
    _, _, result = run(
        tmp_path,
        schema,
        responder=response(review),
        bench=Bench(complete=bad != "sample", decisions=[row_decision()]),
    )
    assert result.notes and result.data["workflow"]["status"] == "failed"


def test_numeric_proxy_for_row_choice_rejected_then_existing_question_reused(tmp_path, schema):
    reviews = 0

    def responder(system, user, output):
        nonlocal reviews
        if "Semantic Reviewer" not in system:
            return response()(system, user, output)
        reviews += 1
        review = covered_review()
        if reviews == 1:
            review["clarifications"] = [
                {
                    "target": "/prices/*/amount",
                    "question": "1 for A, 2 for B?",
                    "why": "Conflicting prices",
                    "kind": "number",
                    "action": "value_map",
                    "source_value": "1=A; 2=B",
                }
            ]
        else:
            assert "numeric proxy" in user
        return {"review": review}

    _, _, result = run(
        tmp_path, schema, responder=responder, retries=1, bench=Bench(errors=1, decisions=[row_decision()])
    )
    assert reviews == 2 and not result.notes
    assert not result.data["clarifications"]
