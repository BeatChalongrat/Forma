"""Works the assistant makes in a chat (ADR-050): flows, files, versions, previews and who can reach them."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forma.ai import vision
from forma.ai.llm import LLMClient, Usage
from forma.code_sandbox import runner
from forma.code_sandbox.client import Input, RunResult, build_archive, parse_result
from forma.db.models import AssistantArtifact, AssistantArtifactVersion, AssistantAttachment, AuditLog
from forma.db.session import session_scope
from forma.services import artifacts, code_sandbox, workspace_assistant
from forma.services.context import get_context

OTHER = {"X-Forma-User": "someone-else", "X-Forma-Roles": "importer"}
PNG = vision.two_colour_png("red", "blue")
FLOW = {
    "title": "How files move",
    "nodes": [{"id": "a", "label": "Upload"}, {"id": "b", "label": "Export", "tone": "done"}],
    "edges": [{"from": "a", "to": "b"}],
}


class Maker:
    """A model that makes ``steps`` calls in order, then answers from the last one."""

    name = "test"

    def __init__(self, *steps: tuple[str, dict[str, Any]]) -> None:
        self.steps = list(steps)
        self.payloads: list[dict[str, Any]] = []
        self.systems: list[str] = []

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        self.payloads.append(json.loads(kw["user"]))
        self.systems.append(kw["system"])
        if self.steps:
            name, arguments = self.steps.pop(0)
            reply = {"answer": "", "citations": [], "calls": [{"name": name, "arguments": arguments}]}
            return json.dumps(reply), Usage(model="test")
        last = self.payloads[-1]["evidence"][-1]
        done = {"answer": "Done.", "citations": [last["id"]], "calls": [], "actions": []}
        return json.dumps(done), Usage(model="test")


class LocalRunner:
    """The real runner in this process. ``previews`` stands in for LibreOffice, which tests do not have."""

    def __init__(self, workspace: Path, previews: dict[str, bytes] | None = None) -> None:
        self.workspace = workspace
        self.previews = previews
        self.inputs: list[list[str]] = []

    def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
        items = list(inputs)
        self.inputs.append([i.path for i in items])
        if self.previews is not None and items and items[0].path.startswith("preview/"):
            return RunResult("ok", 0, 1, "", "", files=list(self.previews.items()))
        with build_archive(code, items, timeout_s) as archive:
            return parse_result(json.loads(json.dumps(runner.execute(archive, self.workspace))))

    def healthy(self) -> bool:
        return True


@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def sandbox(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalRunner:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local = LocalRunner(workspace)
    monkeypatch.setattr(code_sandbox, "get_runner", lambda settings: local)
    return local


def model(*steps: tuple[str, dict[str, Any]]) -> Maker:
    maker = Maker(*steps)
    get_context().llm = LLMClient(maker, "test", max_retries=0)
    return maker


def new_chat(client: TestClient) -> str:
    return str(client.post("/api/assistant/conversations", json={}).json()["id"])


def say(client: TestClient, chat: str, request: str = "request-0001", **extra: Any) -> dict[str, Any]:
    body = {"question": "Make it", "request_id": request, **extra}
    response = client.post(f"/api/assistant/conversations/{chat}/messages", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def writes(name: str, text: str) -> tuple[str, dict[str, Any]]:
    return "run_python", {"code": f"open('outputs/{name}','w').write({text!r})"}


def test_a_flow_needs_no_sandbox(client: TestClient) -> None:
    maker = model(("save_flow", FLOW))
    turn = say(client, new_chat(client))
    made = turn["reply"]["artifacts"]
    assert [(a["kind"], a["title"], a["version"]) for a in made] == [("flow", "How files move", 1)]
    assert "save_flow" in {t["name"] for t in maker.payloads[0]["tools"]}
    assert "run_python" not in {t["name"] for t in maker.payloads[0]["tools"]}
    detail = client.get(f"/api/assistant/artifacts/{made[0]['id']}").json()
    assert detail["versions"][0]["content"]["nodes"][1]["tone"] == "done"
    assert detail["versions"][0]["content"]["direction"] == "down"
    assert detail["stale"] is False and detail["versions"][0]["uses_data"] is False


def test_mcp_never_gets_the_flow_tool() -> None:
    assert "save_flow" not in {t["name"] for t in workspace_assistant.TOOLS}


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"nodes": [{"id": "a", "label": "x"}, {"id": "a", "label": "y"}]}, "its own id"),
        ({"edges": [{"from": "a", "to": "gone"}]}, "gone"),
        ({"nodes": [{"id": "a", "label": "only one"}]}, "Invalid flow"),
    ],
)
def test_a_broken_flow_is_explained_and_nothing_is_saved(
    client: TestClient, change: dict[str, Any], says: str
) -> None:
    maker = model(("save_flow", {**FLOW, **change}))
    assert say(client, new_chat(client))["reply"]["artifacts"] == []
    assert says in maker.payloads[1]["evidence"][-1]["data"]["error"]
    with session_scope() as s:
        assert s.query(AssistantArtifact).count() == 0


def test_a_change_is_a_new_version_and_the_old_one_stays(client: TestClient) -> None:
    chat = new_chat(client)
    model(("save_flow", FLOW))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]
    changed = {
        **FLOW,
        "artifact_id": art,
        "note": "Added a step",
        "nodes": [*FLOW["nodes"], {"id": "c", "label": "New"}],
    }  # type: ignore[list-item]
    model(("save_flow", changed))
    say(client, chat, "request-0002")
    versions = client.get(f"/api/assistant/artifacts/{art}").json()["versions"]
    assert [(v["number"], len(v["content"]["nodes"]), v["note"]) for v in versions] == [
        (1, 2, ""),
        (2, 3, "Added a step"),
    ]
    with session_scope() as s:
        rows = s.query(AssistantArtifactVersion).order_by(AssistantArtifactVersion.number).all()
        assert rows[1].parent_id == rows[0].id


def test_one_message_makes_one_version(client: TestClient) -> None:
    second = {**FLOW, "title": "Better"}
    chat = new_chat(client)
    maker = Maker(("save_flow", FLOW))
    get_context().llm = LLMClient(maker, "test", max_retries=0)

    def again(**kw: Any) -> tuple[str, Usage]:
        saved = [e for e in json.loads(kw["user"])["evidence"] if e["tool"] == "save_flow"]
        if len(saved) == 1:
            second["artifact_id"] = saved[0]["data"]["artifact_id"]
            maker.steps.append(("save_flow", second))
        return Maker.complete_json(maker, **kw)

    maker.complete_json = again  # type: ignore[method-assign]
    made = say(client, chat)["reply"]["artifacts"]
    detail = client.get(f"/api/assistant/artifacts/{made[0]['id']}").json()
    assert detail["latest"] == 1 and detail["title"] == "Better"


def test_a_model_that_keeps_saving_is_stopped_and_the_work_still_arrives(client: TestClient) -> None:
    chat = new_chat(client)
    model(("save_flow", FLOW))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]

    class Repeater(Maker):
        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            payload = json.loads(kw["user"])
            self.payloads.append(payload)
            call = {"name": "save_flow", "arguments": {**FLOW, "artifact_id": art}}
            return json.dumps({"answer": "", "citations": [], "calls": [call]}), Usage(model="test")

    repeater = Repeater()
    get_context().llm = LLMClient(repeater, "test", max_retries=0)
    turn = say(client, chat, "request-0002", artifact_id=art)
    assert turn["status"] == "complete" and turn["reply"]["ai"] is True
    assert turn["reply"]["artifacts"][0]["version"] == 2
    assert repeater.payloads[1]["working_on"]["status"].startswith("already changed")
    assert "save_flow" not in {t["name"] for t in repeater.payloads[2]["tools"]}
    assert len([e for e in turn["reply"]["evidence"] if e["data"].get("saved")]) == 2


def test_a_flow_from_another_chat_cannot_be_changed(client: TestClient) -> None:
    model(("save_flow", FLOW))
    art = say(client, new_chat(client))["reply"]["artifacts"][0]["id"]
    maker = model(("save_flow", {**FLOW, "artifact_id": art}))
    say(client, new_chat(client))
    assert "not a flow in this chat" in maker.payloads[1]["evidence"][-1]["data"]["error"]


def test_the_same_file_name_is_the_next_version(client: TestClient, sandbox: LocalRunner) -> None:
    chat = new_chat(client)
    model(writes("report.txt", "one"))
    first = say(client, chat)["reply"]["artifacts"][0]
    assert first["kind"] == "text" and first["title"] == "report"
    model(writes("report.txt", "two"))
    second = say(client, chat, "request-0002")["reply"]["artifacts"][0]
    assert (second["id"], second["version"]) == (first["id"], 2)
    model(writes("report.txt", "two"))
    assert say(client, chat, "request-0003")["reply"]["artifacts"] == []  # the same bytes change nothing


def test_a_run_the_model_redid_leaves_no_version(client: TestClient, sandbox: LocalRunner) -> None:
    model(writes("draft.txt", "first try"), writes("draft.txt", "second try"))
    chat = new_chat(client)
    made = say(client, chat)["reply"]["artifacts"]
    detail = client.get(f"/api/assistant/artifacts/{made[0]['id']}").json()
    assert detail["latest"] == 1 and "second try" in detail["versions"][0]["code"]
    file = detail["versions"][0]["file"]
    assert client.get(f"/api/assistant/attachments/{file['id']}").content == b"second try"


def test_a_failed_run_makes_nothing(client: TestClient, sandbox: LocalRunner) -> None:
    model(("run_python", {"code": "open('outputs/x.txt','w').write('half')\nraise ValueError('no')"}))
    assert say(client, new_chat(client))["reply"]["artifacts"] == []


def test_a_csv_comes_with_its_cells(client: TestClient, sandbox: LocalRunner) -> None:
    model(writes("table.csv", "สินค้า,ราคา\nสบู่,10\n"))
    made = say(client, new_chat(client))["reply"]["artifacts"][0]
    assert made["kind"] == "sheet"
    preview = client.get(f"/api/assistant/artifacts/{made['id']}").json()["versions"][0]["preview"]
    assert preview["status"] == "ready"
    assert preview["sheets"][0]["rows"] == [["สินค้า", "ราคา"], ["สบู่", "10"]]


def test_pages_are_kept_apart_from_the_files_a_run_receives(client: TestClient, sandbox: LocalRunner) -> None:
    sandbox.previews = {"page-1.png": PNG, "page-2.png": PNG, "document.pdf": b"%PDF"}
    chat = new_chat(client)
    model(writes("deck.pptx", "not really a deck"))
    made = say(client, chat)["reply"]["artifacts"][0]
    assert made["kind"] == "deck"
    preview = client.get(f"/api/assistant/artifacts/{made['id']}").json()["versions"][0]["preview"]
    assert preview["status"] == "ready" and len(preview["pages"]) == 2
    picture = client.get(f"/api/assistant/attachments/{preview['pages'][0]}")
    assert picture.content == PNG and picture.headers["content-disposition"].startswith("inline")
    with session_scope() as s:
        assert s.query(AssistantAttachment).filter_by(kind="preview").count() == 3
    model(("run_python", {"code": "print('hello')"}))
    say(client, chat, "request-0002")
    assert sandbox.inputs[-1] == ["previous/deck.pptx"]


def test_a_file_that_will_not_preview_can_still_be_downloaded(
    client: TestClient, sandbox: LocalRunner
) -> None:
    def broken(code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
        if "preview/" in str([i.path for i in inputs]):
            return RunResult("error", 1, 1, "", "no")
        return LocalRunner.run(sandbox, code, inputs, timeout_s)

    sandbox.run = broken  # type: ignore[method-assign]
    model(("run_python", {"code": "open('outputs/deck.pptx','w').write('x')"}))
    made = say(client, new_chat(client))["reply"]["artifacts"][0]
    version = client.get(f"/api/assistant/artifacts/{made['id']}").json()["versions"][0]
    assert version["preview"]["status"] == "failed" and version["file"]["name"] == "deck.pptx"


def test_an_old_version_comes_back_as_the_next_number(client: TestClient, sandbox: LocalRunner) -> None:
    chat = new_chat(client)
    model(writes("note.txt", "one"))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]
    model(writes("note.txt", "two"))
    say(client, chat, "request-0002")
    restored = client.post(f"/api/assistant/artifacts/{art}/versions/1/restore").json()
    assert [v["number"] for v in restored["versions"]] == [1, 2, 3]
    assert restored["versions"][2]["restored_from"] == 1
    file = restored["versions"][2]["file"]
    assert client.get(f"/api/assistant/attachments/{file['id']}").content == b"one"
    assert client.post(f"/api/assistant/artifacts/{art}/versions/3/restore").status_code == 422
    assert client.post(f"/api/assistant/artifacts/{art}/versions/9/restore").status_code == 404
    with session_scope() as s:
        assert s.query(AuditLog).filter_by(action="assistant.artifact_restore").count() == 1
    model(("run_python", {"code": "print(open('inputs/previous/note.txt').read())"}))
    assert say(client, chat, "request-0003")["reply"]["code_runs"][0]["stdout"].strip() == "one"


def test_a_work_is_reached_only_through_its_chat(client: TestClient) -> None:
    model(("save_flow", FLOW))
    art = say(client, new_chat(client))["reply"]["artifacts"][0]["id"]
    assert [a["id"] for a in client.get("/api/assistant/artifacts").json()["items"]] == [art]
    assert client.get("/api/assistant/artifacts", headers=OTHER).json()["items"] == []
    assert client.get(f"/api/assistant/artifacts/{art}", headers=OTHER).status_code == 404
    url = f"/api/assistant/artifacts/{art}/versions/1/restore"
    assert client.post(url, headers=OTHER).status_code == 404


def test_the_model_is_told_what_is_open(client: TestClient) -> None:
    chat = new_chat(client)
    model(("save_flow", FLOW))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]
    maker = model(("save_flow", {**FLOW, "artifact_id": art}))
    say(client, chat, "request-0002", artifact_id=art, target="Upload")
    open_work = maker.payloads[0]["working_on"]
    assert (open_work["artifact_id"], open_work["target"], open_work["version"]) == (art, "Upload", 1)
    assert len(open_work["flow"]["nodes"]) == 2


def test_a_message_cannot_name_a_work_from_another_chat(client: TestClient) -> None:
    model(("save_flow", FLOW))
    art = say(client, new_chat(client))["reply"]["artifacts"][0]["id"]
    other = new_chat(client)
    body = {"question": "x", "request_id": "request-0009", "artifact_id": art}
    assert client.post(f"/api/assistant/conversations/{other}/messages", json=body).status_code == 404


def test_deleting_a_chat_deletes_its_works(client: TestClient, sandbox: LocalRunner) -> None:
    chat = new_chat(client)
    model(writes("note.txt", "one"))
    say(client, chat)
    assert client.delete(f"/api/assistant/conversations/{chat}").status_code == 204
    with session_scope() as s:
        assert s.query(AssistantArtifact).count() == 0
        assert s.query(AssistantArtifactVersion).count() == 0


def test_a_work_says_when_its_data_has_moved_on(client: TestClient, sandbox: LocalRunner) -> None:
    from tests.test_api import _upload, make_workbook

    customer = client.post("/api/customers", json={"code": "ST", "name": "ST"}).json()["id"]
    round_id = client.post("/api/rounds", json={"customer_id": customer}).json()["id"]
    chat = new_chat(client)
    model(("workspace_summary", {}), writes("numbers.txt", "1"))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]
    assert client.get(f"/api/assistant/artifacts/{art}").json()["stale"] is False
    _upload(client, round_id, make_workbook(get_context().settings.data_dir / "new.xlsx"))
    detail = client.get(f"/api/assistant/artifacts/{art}").json()
    assert detail["stale"] is True and detail["versions"][0]["uses_data"] is True


def test_a_change_that_read_no_data_keeps_the_numbers_it_rests_on(
    client: TestClient, sandbox: LocalRunner
) -> None:
    chat = new_chat(client)
    model(("workspace_summary", {}), writes("numbers.txt", "1"))
    art = say(client, chat)["reply"]["artifacts"][0]["id"]
    model(writes("numbers.txt", "1 with a new title"))
    say(client, chat, "request-0002", artifact_id=art)
    versions = client.get(f"/api/assistant/artifacts/{art}").json()["versions"]
    assert [v["uses_data"] for v in versions] == [True, True]
    assert versions[1]["generated_at"] == versions[0]["generated_at"]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.pptx", "deck"),
        ("a.docx", "doc"),
        ("a.pdf", "doc"),
        ("a.xlsx", "sheet"),
        ("a.html", "web"),
        ("a.png", "image"),
        ("a.md", "text"),
    ],
)
def test_a_file_name_says_what_kind_of_work_it_is(name: str, kind: str) -> None:
    assert artifacts.KIND_OF[Path(name).suffix] == kind
