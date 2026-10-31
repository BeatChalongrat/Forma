"""The assistant's code sandbox (ADR-048): the runner itself, then a run from a chat message to its files."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import tarfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.test_api import _run, _upload, make_workbook

from forma.ai import vision
from forma.ai.llm import LLMClient, Usage
from forma.code_sandbox import runner
from forma.code_sandbox.client import Input, RunResult, SandboxUnavailable, build_archive, parse_result
from forma.db.models import AssistantAttachment, AuditLog
from forma.db.session import session_scope
from forma.services import code_sandbox, workspace_assistant
from forma.services.context import get_context

IMPORTER = {"X-Forma-User": "someone-else", "X-Forma-Roles": "importer"}
PNG = vision.two_colour_png("red", "blue")


def run_code(tmp_path: Path, code: str, inputs: dict[str, bytes] | None = None, timeout: float = 20) -> Any:
    files = [
        Input(path, len(data), lambda data=data: io.BytesIO(data)) for path, data in (inputs or {}).items()
    ]
    with build_archive(code, files, timeout) as archive:
        return runner.execute(archive, tmp_path)


def test_a_run_prints_reads_its_inputs_and_leaves_files(tmp_path: Path) -> None:
    body = run_code(
        tmp_path,
        "from pathlib import Path\n"
        "print('สวัสดี', Path('inputs/data/a.txt').read_text())\n"
        "Path('outputs/sub').mkdir()\n"
        "Path('outputs/sub/r.csv').write_text('a,b')\n",
        {"data/a.txt": b"hello"},
    )
    assert body["status"] == "ok" and body["exit_code"] == 0
    assert body["stdout"].strip() == "สวัสดี hello"
    assert [(f["name"], base64.b64decode(f["data"])) for f in body["files"]] == [("sub/r.csv", b"a,b")]


def test_nothing_survives_a_run(tmp_path: Path) -> None:
    run_code(tmp_path, "open('outputs/x.txt','w').write('x')\nopen('tmp/y.txt','w').write('y')")
    assert list(tmp_path.iterdir()) == []
    assert run_code(tmp_path, "import os\nprint(sorted(os.listdir('outputs')))")["stdout"].strip() == "[]"


def test_pictures_for_the_model_stay_apart_from_the_chat(tmp_path: Path) -> None:
    body = run_code(tmp_path, "open('look/a.png','wb').write(b'x')\nopen('outputs/b.txt','w').write('y')")
    assert [f["name"] for f in body["looks"]] == ["a.png"]
    assert [f["name"] for f in body["files"]] == ["b.txt"]


@pytest.mark.skipif(sys.platform == "win32", reason="file owners need POSIX")
def test_what_a_run_leaves_in_the_shared_tmp_is_swept(tmp_path: Path) -> None:
    (tmp_path / "left").write_text("x")
    (tmp_path / "folder").mkdir()
    runner._sweep(tmp_path, os.getuid() + 1)
    assert len(list(tmp_path.iterdir())) == 2  # another owner's files stay
    runner._sweep(tmp_path, os.getuid())
    assert list(tmp_path.iterdir()) == []


def test_a_failing_run_reports_its_error(tmp_path: Path) -> None:
    body = run_code(tmp_path, "print('before')\nraise ValueError('nope')")
    assert body["status"] == "error" and body["exit_code"] == 1
    assert "before" in body["stdout"] and "ValueError: nope" in body["stderr"]


def test_a_run_is_stopped_at_its_time_limit(tmp_path: Path) -> None:
    body = run_code(tmp_path, "while True:\n    pass", timeout=1)
    assert body["status"] == "timeout" and body["exit_code"] == -1
    assert 900 <= body["duration_ms"] < 10000


def test_only_part_of_a_long_output_comes_back(tmp_path: Path) -> None:
    body = run_code(tmp_path, "print('x' * 500000)")
    assert len(body["stdout"]) < 21000 and "characters not shown" in body["stdout"]


def test_an_archive_cannot_write_outside_its_run(tmp_path: Path) -> None:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for name, content in (
            ("spec.json", b"{}"),
            ("code.py", b"print('ran')"),
            ("elsewhere.txt", b"x"),
            ("inputs/../../escaped.txt", b"x"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    data.seek(0)
    with pytest.raises(tarfile.TarError):
        runner.execute(data, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need POSIX")
def test_a_link_left_in_outputs_is_not_followed(tmp_path: Path) -> None:
    body = run_code(tmp_path, "import os\nos.symlink('/etc/passwd','outputs/p.txt')\nprint('ok')")
    assert body["files"] == [] and body["omitted"] == ["p.txt"]


def test_a_result_is_read_strictly() -> None:
    with pytest.raises(KeyError):
        parse_result({"exit_code": 0})
    result = parse_result(
        {
            "status": "ok",
            "exit_code": 0,
            "duration_ms": 1,
            "files": [{"name": "a.txt", "data": base64.b64encode(b"x").decode()}],
        }
    )
    assert result.files == [("a.txt", b"x")]


# --------------------------------------------------------------------------------------------
# from a chat message to its files
# --------------------------------------------------------------------------------------------
class LocalRunner:
    """The real runner, in this process: everything but the container around it."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.inputs: list[list[str]] = []

    def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
        items = list(inputs)
        self.inputs.append([i.path for i in items])
        with build_archive(code, items, timeout_s) as archive:
            return parse_result(json.loads(json.dumps(runner.execute(archive, self.workspace))))

    def healthy(self) -> bool:
        return True


class Coder:
    """A model that runs ``steps`` in order, then answers from the last run."""

    name = "test"

    def __init__(self, *steps: dict[str, Any]) -> None:
        self.steps = list(steps)
        self.payloads: list[dict[str, Any]] = []
        self.systems: list[str] = []

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        payload = json.loads(kw["user"])
        self.payloads.append(payload)
        self.systems.append(kw["system"])
        if self.steps:
            call = {"name": "run_python", "arguments": self.steps.pop(0)}
            return json.dumps({"answer": "", "citations": [], "calls": [call]}), Usage(model="test")
        last = next(e for e in reversed(payload["evidence"]) if e["tool"] == "run_python")
        reply = {"answer": "Here is the result.", "citations": [last["id"]], "calls": [], "actions": []}
        return json.dumps(reply), Usage(model="test")


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


def ask(client: TestClient, question: str = "How many records?", **chat: Any) -> dict[str, Any]:
    cid = client.post("/api/assistant/conversations", json=chat).json()["id"]
    body = {"question": question, "request_id": "request-0001"}
    response = client.post(f"/api/assistant/conversations/{cid}/messages", json=body)
    assert response.status_code == 200, response.text
    return {"chat": cid, **response.json()}


class Seer(Coder):
    """A Coder that also keeps the pictures each step was shown."""

    def __init__(self, *steps: dict[str, Any]) -> None:
        super().__init__(*steps)
        self.seen: list[list[bytes]] = []

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        self.seen.append([image.data for image in kw.get("images", [])])
        return super().complete_json(**kw)


LOOK_CODE = (
    "import base64\n"
    f"open('look/page.png','wb').write(base64.b64decode('{base64.b64encode(PNG).decode()}'))\n"
    "open('look/notes.txt','w').write('not a picture')\n"
)


def test_a_model_sees_its_own_work_once_and_the_chat_does_not(
    client: TestClient, sandbox: LocalRunner
) -> None:
    seer = Seer({"code": LOOK_CODE}, {"code": "print('fixed')"})
    get_context().llm = LLMClient(seer, "test", max_retries=0, primary_vision=True)
    run = ask(client)["reply"]["code_runs"][0]
    assert seer.seen == [[], [PNG], []]
    assert run["files"] == [] and run["looked_at"] == 1
    assert "look/" in seer.systems[0]


def test_a_model_that_cannot_see_is_not_told_about_look(client: TestClient, sandbox: LocalRunner) -> None:
    seer = Seer({"code": LOOK_CODE})
    get_context().llm = LLMClient(seer, "test", max_retries=0)
    run = ask(client)["reply"]["code_runs"][0]
    assert seer.seen == [[], []] and run["looked_at"] == 0
    assert "look/" not in seer.systems[0]


def test_the_tool_is_offered_only_when_the_sandbox_is_on(client: TestClient, tmp_path: Path) -> None:
    coder = Coder()
    coder.steps = []
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    ask(client)
    assert "run_python" not in {t["name"] for t in coder.payloads[0]["tools"]}
    assert "run_python" not in coder.systems[0]


def test_mcp_never_gets_the_code_tool() -> None:
    assert "run_python" not in {t["name"] for t in workspace_assistant.TOOLS}


def test_a_run_answers_a_question_and_keeps_its_files(
    client: TestClient, isolated_env: Path, sandbox: LocalRunner
) -> None:
    code = (
        "import json\n"
        "from pathlib import Path\n"
        "rows = [json.loads(line) for line in next(Path('inputs/datasets').iterdir()).open(encoding='utf-8')]\n"
        "print('records', len(rows))\n"
        "Path('outputs/summary.csv').write_text('records,%d' % len(rows))\n"
    )
    customer = client.post("/api/customers", json={"code": "SA", "name": "SA"}).json()["id"]
    round_id = client.post("/api/rounds", json={"customer_id": customer}).json()["id"]
    _upload(client, round_id, make_workbook(isolated_env / "a.xlsx"))
    _run(client, round_id)
    mapping = client.get(f"/api/rounds/{round_id}").json()["mappings"][0]["id"]
    client.post(f"/api/mappings/{mapping}/approve", json={})
    client.post(f"/api/rounds/{round_id}/transform?sync=true")
    dataset = client.get(f"/api/rounds/{round_id}").json()["datasets"][0]["id"]

    coder = Coder({"code": code, "datasets": [dataset]})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    turn = ask(client)
    assert turn["status"] == "complete", turn
    assert "run_python" in {t["name"] for t in coder.payloads[0]["tools"]}
    assert "run_python" in coder.systems[0]
    run = turn["reply"]["code_runs"][0]
    assert run["status"] == "ok" and run["stdout"].startswith("records ") and run["code"] == code
    assert sandbox.inputs[0] == [f"datasets/{dataset}.jsonl"]
    assert coder.payloads[1]["evidence"][-1]["data"]["files"] == ["summary.csv"]

    file = run["files"][0]
    download = client.get(f"/api/assistant/attachments/{file['id']}")
    assert download.status_code == 200 and download.content.startswith(b"records,")
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/assistant/attachments/{file['id']}", headers=IMPORTER).status_code == 404
    with session_scope() as s:
        log = s.query(AuditLog).filter_by(action="assistant.code_run").one()
        assert log.details["status"] == "ok" and log.details["outputs"] == ["summary.csv"]
        assert log.actor and len(log.details["code_sha256"]) == 64


def test_a_failed_run_is_read_and_tried_again(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder({"code": "print(undefined)"}, {"code": "print('fixed')"})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    turn = ask(client)
    runs = turn["reply"]["code_runs"]
    assert [r["status"] for r in runs] == ["error", "ok"]
    assert "NameError" in coder.payloads[1]["evidence"][-1]["data"]["stderr"]


def test_code_is_not_scanned_as_prose(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder({"code": "# drop the rows with no price\nprint('kept')"})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    assert ask(client)["reply"]["code_runs"][0]["stdout"].strip() == "kept"


def test_a_later_message_knows_the_files_of_earlier_runs(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder({"code": "open('outputs/first.txt','w').write('kept')"})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    cid = ask(client)["chat"]
    coder.steps = [{"code": "print(1)"}]
    body = {"question": "And now?", "request_id": "request-0002"}
    client.post(f"/api/assistant/conversations/{cid}/messages", json=body)
    assert [a["name"] for a in coder.payloads[-1]["attachments"]] == ["first.txt"]


def test_files_from_an_earlier_run_come_back_as_inputs(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder(
        {"code": "open('outputs/first.txt','w').write('kept')"},
        {"code": "print(open('inputs/previous/first.txt').read())"},
    )
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    turn = ask(client)
    assert turn["reply"]["code_runs"][1]["stdout"].strip() == "kept"


def test_a_run_cannot_read_another_customers_data(client: TestClient, sandbox: LocalRunner) -> None:
    from tests.test_tenant_scope import _round

    a, b = (
        _round(client, get_context().settings.data_path, "SA"),
        _round(client, get_context().settings.data_path, "SB"),
    )
    source = client.get(f"/api/rounds/{b['round']}").json()["files"][0]["id"]
    coder = Coder({"code": "print('never runs')", "files": [source]})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    cid = client.post("/api/assistant/conversations", json={}, headers=_scoped(a)).json()["id"]
    body = {"question": "Read it", "request_id": "request-0002"}
    turn = client.post(f"/api/assistant/conversations/{cid}/messages", json=body, headers=_scoped(a)).json()
    assert sandbox.inputs == []
    assert not turn["reply"].get("code_runs")


def _scoped(a: dict[str, Any]) -> dict[str, str]:
    return {"X-Forma-User": "tenant", "X-Forma-Roles": "importer", "X-Forma-Customers": a["customer"]}


def test_a_chat_file_passed_as_an_id_is_explained(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder({"code": "print('never runs')", "files": ["first.csv"]}, {"code": "print('fixed')"})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    turn = ask(client)
    assert [r["stdout"].strip() for r in turn["reply"]["code_runs"]] == ["fixed"]
    assert "read them from inputs/" in coder.payloads[1]["evidence"][-1]["data"]["error"]


def test_inputs_above_the_limit_are_refused(
    client: TestClient, sandbox: LocalRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_tenant_scope import _round

    a = _round(client, get_context().settings.data_path, "SA")
    source = client.get(f"/api/rounds/{a['round']}").json()["files"][0]["id"]
    monkeypatch.setattr(get_context().settings, "code_sandbox_max_input_mb", 0)
    coder = Coder({"code": "print('never runs')", "files": [source]})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    ask(client)
    assert sandbox.inputs == []
    errors = [e["data"]["error"] for e in coder.payloads[-1]["evidence"] if "error" in e["data"]]
    assert errors and "limit" in errors[0]


def test_an_unreachable_sandbox_is_told_to_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Down(LocalRunner):
        def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
            raise SandboxUnavailable("gone")

    monkeypatch.setattr(code_sandbox, "get_runner", lambda settings: Down(tmp_path))
    coder = Coder({"code": "print(1)"})
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    ask(client)
    assert any(
        e["data"].get("error") == "The code sandbox could not be reached"
        for e in coder.payloads[-1]["evidence"]
    )


def test_status_reports_the_sandbox(client: TestClient, sandbox: LocalRunner) -> None:
    assert client.get("/api/assistant/status").json()["code_sandbox"] is True


def test_output_files_stay_in_the_chat(client: TestClient, sandbox: LocalRunner) -> None:
    coder = Coder(
        {"code": "open('outputs/../../x.txt','w')"}, {"code": "open('outputs/a b.csv','w').write('x')"}
    )
    get_context().llm = LLMClient(coder, "test", max_retries=0)
    turn = ask(client)
    assert turn["reply"]["code_runs"][0]["status"] in ("ok", "error")
    file = turn["reply"]["code_runs"][-1]["files"][0]
    assert file["name"] == "a b.csv" and file["media_type"] == "text/csv" and file["kind"] == "output"
    with session_scope() as s:
        assert s.get(AssistantAttachment, file["id"]).conversation_id == turn["chat"]
