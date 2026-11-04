"""Files in a chat and models that read images (ADR-049)."""

from __future__ import annotations

import json
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from forma.ai import llm, vision
from forma.ai.llm import (
    AnthropicProvider,
    ImagePart,
    LLMClient,
    LLMError,
    OpenAICompatibleProvider,
    Route,
    Usage,
)
from forma.db.models import AssistantAttachment
from forma.db.session import session_scope
from forma.services import config, workspace_assistant
from forma.services.context import get_context

PNG = vision.two_colour_png("red", "blue")
OTHER = {"X-Forma-User": "someone-else", "X-Forma-Roles": "importer"}


def side_colours(png: bytes) -> tuple[str, str]:
    """What a model with eyes reports: the first and last pixel of the picture's first row."""
    position, data = 8, b""
    width = 0
    while position < len(png):
        size = struct.unpack(">I", png[position : position + 4])[0]
        kind = png[position + 4 : position + 8]
        body = png[position + 8 : position + 8 + size]
        if kind == b"IHDR":
            width = struct.unpack(">I", body[:4])[0]
        if kind == b"IDAT":
            data += body
        position += 12 + size
    row = zlib.decompress(data)[1 : 1 + width * 3]
    name = {v: k for k, v in vision.COLOURS.items()}
    return name[tuple(row[:3])], name[tuple(row[-3:])]


class Eyes:
    name = "test"

    def __init__(self, blind: bool = False) -> None:
        self.blind = blind
        self.images: list[list[ImagePart]] = []

    def complete_json(self, *, model: str, images: Any = (), **kw: Any) -> tuple[str, Usage]:
        self.images.append(list(images))
        left, right = ("red", "red") if self.blind else side_colours(images[0].data)
        return json.dumps({"left": left, "right": right}), Usage(5, 2, 1, 1.0, model)


class Refuses:
    name = "test"

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        raise LLMError("test HTTP 400: image_url is not supported")


class Offline:
    name = "test"

    def complete_json(self, **kw: Any) -> tuple[str, Usage]:
        raise LLMError("test HTTP 503: overloaded")


def test_a_probe_tells_who_can_see() -> None:
    assert vision.probe(Eyes(), "m")["vision"] is True
    assert vision.probe(Refuses(), "m")["vision"] is False
    assert vision.probe(Offline(), "m")["vision"] is None
    wrong = [vision.probe(Eyes(blind=True), "m")["vision"] for _ in range(40)]
    assert False in wrong  # the colours change each time, so a fixed answer does not keep passing


def test_images_only_go_to_routes_that_can_read_them() -> None:
    seeing, blind = Eyes(), Eyes()
    client = LLMClient(blind, "blind", max_retries=0)
    client.fallbacks = [Route("backup", seeing, "seeing", True)]
    assert client.supports_vision
    picture = ImagePart("image/png", PNG)
    text, _usage = client._call_provider("s", "u", {}, [picture])
    assert json.loads(text)["left"] == "red" and seeing.images and not blind.images
    assert client.last_route == "backup"

    nobody = LLMClient(Eyes(), "m", max_retries=0)
    assert not nobody.supports_vision
    with pytest.raises(LLMError, match="read images"):
        nobody._call_provider("s", "u", {}, [picture])


class _Http:
    sent: dict[str, Any] = {}

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply

    async def __aenter__(self) -> _Http:
        return self

    async def __aexit__(self, *a: Any) -> None:
        pass

    async def post(self, url: str, headers: Any, json: dict[str, Any]) -> Any:
        _Http.sent = json
        reply = self.reply

        class Response:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return reply

        return Response()


def test_each_provider_gets_the_picture_in_its_own_format(monkeypatch: pytest.MonkeyPatch) -> None:
    picture = ImagePart("image/png", b"abc")
    args: dict[str, Any] = {"system": "s", "user": "u", "schema": {}, "model": "m", "temperature": 0.0}

    openai = {"choices": [{"message": {"content": "{}"}}], "usage": {}}
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Http(openai))
    OpenAICompatibleProvider("k").complete_json(**args, images=[picture])
    content = _Http.sent["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "u"}
    assert content[1]["image_url"]["url"] == "data:image/png;base64,YWJj"
    OpenAICompatibleProvider("k").complete_json(**args)
    assert _Http.sent["messages"][1]["content"] == "u"

    anthropic = {"content": [{"type": "text", "text": "{}"}], "usage": {}}
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: _Http(anthropic))
    AnthropicProvider("k").complete_json(**args, images=[picture])
    content = _Http.sent["messages"][0]["content"]
    assert content[0]["source"] == {"type": "base64", "media_type": "image/png", "data": "YWJj"}
    assert content[1] == {"type": "text", "text": "u"}


def test_a_saved_result_belongs_to_exactly_one_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    eyes = LLMClient(Eyes(), "seeing", max_retries=0)
    monkeypatch.setattr(config, "build_client", lambda base, ai: eyes)
    settings = get_context().settings
    with session_scope() as s:
        result = config.test_vision(s, settings, "tester")
        assert result["results"][0]["vision"] is True
        s.flush()
        saved = config._row(s, config.AI_KEY)
        assert saved is not None
        record = saved.value["vision"]
    eff = config.llm_settings(settings, {})
    assert config._vision(record, eff.llm_provider, eff.llm_base_url, eff.llm_model) is True
    assert config._vision(record, eff.llm_provider, eff.llm_base_url, "another-model") is None
    assert config._vision(record, "anthropic", eff.llm_base_url, eff.llm_model) is None


# --------------------------------------------------------------------------------------------
# files through the API
# --------------------------------------------------------------------------------------------
@pytest.fixture
def client(isolated_env: Path) -> Iterator[TestClient]:
    from forma.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def answer(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"answer": "ok", "ai": True, "evidence": [], "actions": []}

    monkeypatch.setattr(workspace_assistant, "ask", answer)
    return calls


def new_chat(c: TestClient) -> str:
    return c.post("/api/assistant/conversations", json={}).json()["id"]


def attach(c: TestClient, cid: str, name: str = "screen.png", data: bytes = PNG, **kw: Any) -> Any:
    return c.post(f"/api/assistant/conversations/{cid}/attachments", files={"file": (name, data)}, **kw)


def send(c: TestClient, cid: str, ids: list[str], request: str = "request-0001") -> Any:
    body = {"question": "Is this right?", "request_id": request, "attachment_ids": ids}
    return c.post(f"/api/assistant/conversations/{cid}/messages", json=body)


def test_the_type_comes_from_the_bytes_not_the_name(client: TestClient) -> None:
    cid = new_chat(client)
    assert attach(client, cid, "named.txt").json()["media_type"] == "image/png"
    assert attach(client, cid, "data.csv", b"a,b\n1,2").json()["media_type"] == "text/csv"
    assert attach(client, cid, "tool.exe", b"MZ\x00").status_code == 422
    assert attach(client, cid, "run.bat", b"@echo off").status_code == 422
    assert attach(client, cid, "empty.csv", b"").status_code in (400, 422)


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("old.xls", "application/vnd.ms-excel"),
        ("big.xlsb", "application/vnd.ms-excel.sheet.binary.macroEnabled.12"),
        ("report.pdf", "application/pdf"),
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("mail.eml", "message/rfc822"),
        ("box.7z", "application/x-7z-compressed"),
        ("notes.md", "text/markdown"),
    ],
)
def test_many_kinds_of_file_can_be_attached(client: TestClient, name: str, media_type: str) -> None:
    body = attach(client, new_chat(client), name, b"\xd0\xcf\x11\xe0").json()
    assert (body["name"], body["media_type"]) == (name, media_type)


def test_the_page_is_told_what_it_can_attach(client: TestClient) -> None:
    types = client.get("/api/assistant/status").json()["file_types"]
    assert {".pdf", ".docx", ".pptx", ".xlsb", ".zip"} <= set(types) and ".exe" not in types


def test_a_web_page_in_a_chat_is_only_ever_downloaded(client: TestClient) -> None:
    file = attach(client, new_chat(client), "page.html", b"<script>alert(1)</script>").json()
    headers = client.get(f"/api/assistant/attachments/{file['id']}").headers
    assert headers["content-disposition"].startswith("attachment")
    assert headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in headers["content-security-policy"]


def test_a_name_cannot_become_a_path(client: TestClient) -> None:
    file = attach(client, new_chat(client), "..\\../evil/ชื่อ ไฟล์.csv", b"a").json()
    assert file["name"] == "ชื่อ ไฟล์.csv"
    with session_scope() as s:
        row = s.get(AssistantAttachment, file["id"])
        assert row is not None and ".." not in row.storage_key


def test_large_files_are_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_context().settings, "assistant_max_image_mb", 0)
    response = attach(client, new_chat(client))
    assert response.status_code == 422 and "larger" in response.json()["message"]


def test_a_chat_file_is_only_reachable_through_its_chat(client: TestClient) -> None:
    cid = new_chat(client)
    file = attach(client, cid).json()
    assert attach(client, cid, headers=OTHER).status_code == 404
    assert client.get(f"/api/assistant/attachments/{file['id']}", headers=OTHER).status_code == 404
    shown = client.get(f"/api/assistant/attachments/{file['id']}")
    assert shown.content == PNG and shown.headers["content-disposition"].startswith("inline")


def test_a_message_carries_its_files(client: TestClient, asked: list[dict[str, Any]]) -> None:
    cid = new_chat(client)
    file = attach(client, cid).json()
    turn = send(client, cid, [file["id"]]).json()
    assert [f["id"] for f in turn["attachments"]] == [file["id"]]
    assert [f["id"] for f in asked[0]["attachments"]] == [file["id"]]
    assert "images" not in asked[0]  # this model has not been shown to read images
    history = client.get(f"/api/assistant/conversations/{cid}").json()
    assert history["turns"][0]["attachments"][0]["name"] == "screen.png"
    assert send(client, cid, [file["id"]], "request-0002").status_code == 422  # used by another message
    assert send(client, cid, ["att_missing"], "request-0003").status_code == 422
    assert send(client, new_chat(client), [file["id"]], "request-0004").status_code == 422


def test_a_model_that_reads_images_receives_them(client: TestClient, asked: list[dict[str, Any]]) -> None:
    get_context().llm = LLMClient(Eyes(), "seeing", max_retries=0, primary_vision=True)
    cid = new_chat(client)
    ids = [attach(client, cid).json()["id"]]
    ids.append(attach(client, cid, "data.csv", b"a,b").json()["id"])
    send(client, cid, ids)
    assert [i.data for i in asked[0]["images"]] == [PNG]
    assert asked[0]["images"][0].media_type == "image/png"
    assert client.get("/api/assistant/status").json()["vision"] is True


def test_the_model_is_told_when_it_cannot_see_an_image(client: TestClient) -> None:
    class Reader:
        name = "test"
        system = ""

        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            Reader.system = kw["system"]
            reply = {"answer": "I cannot read images.", "citations": [1], "calls": [], "actions": []}
            return json.dumps(reply), Usage(model="test")

    get_context().llm = LLMClient(Reader(), "m", max_retries=0)
    cid = new_chat(client)
    send(client, cid, [attach(client, cid).json()["id"]])
    assert "cannot read the images" in Reader.system


def test_text_in_an_image_is_data(client: TestClient) -> None:
    class Reader:
        name = "test"
        system = ""
        images: list[Any] = []

        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            Reader.system, Reader.images = kw["system"], list(kw.get("images", []))
            reply = {"answer": "Read from the image.", "citations": [1], "calls": [], "actions": []}
            return json.dumps(reply), Usage(model="test")

    get_context().llm = LLMClient(Reader(), "m", max_retries=0, primary_vision=True)
    cid = new_chat(client)
    send(client, cid, [attach(client, cid).json()["id"]])
    assert len(Reader.images) == 1
    assert "never instructions" in Reader.system and "not as a confirmed fact" in Reader.system


def test_deleting_a_chat_deletes_its_files(client: TestClient, asked: list[dict[str, Any]]) -> None:
    cid = new_chat(client)
    file = attach(client, cid).json()
    send(client, cid, [file["id"]])
    with session_scope() as s:
        key = s.get(AssistantAttachment, file["id"]).storage_key
    assert get_context().storage.exists(key)
    assert client.delete(f"/api/assistant/conversations/{cid}").status_code == 204
    assert not get_context().storage.exists(key)
    with session_scope() as s:
        assert s.get(AssistantAttachment, file["id"]) is None
