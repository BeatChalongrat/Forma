"""The API's side of the code sandbox (ADR-048): sends a run over the Unix socket, returns what came back.

Everything the runner answers is treated as untrusted: the code that produced it may have been written by a
model reading a hostile spreadsheet cell.
"""

from __future__ import annotations

import base64
import io
import json
import tarfile
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Protocol

import httpx


class SandboxUnavailable(RuntimeError):
    pass


class SandboxBusy(SandboxUnavailable):
    """No code ran. Another isolated runner may safely accept the same request."""


@dataclass(frozen=True)
class Input:
    """One file for the run. ``open`` is called once, when the request is built."""

    path: str  # below ``inputs/``
    size: int
    open: Any


@dataclass
class RunResult:
    status: str
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str
    files: list[tuple[str, bytes]] = field(default_factory=list)
    looks: list[tuple[str, bytes]] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)


class CodeRunner(Protocol):
    def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult: ...

    def healthy(self) -> bool: ...


def build_archive(code: str, inputs: Iterable[Input], timeout_s: float) -> BinaryIO:
    """The request body, spooled to disk: inputs can be large."""
    archive: BinaryIO = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)  # type: ignore[assignment]  # noqa: SIM115
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in (
            ("spec.json", json.dumps({"timeout_s": timeout_s}).encode()),
            ("code.py", code.encode("utf-8")),
        ):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
        for item in inputs:
            info = tarfile.TarInfo(f"inputs/{item.path}")
            info.size, info.mode = item.size, 0o644
            with item.open() as source:
                tar.addfile(info, source)
    archive.seek(0)
    return archive


def parse_result(body: dict[str, Any]) -> RunResult:
    def decoded(key: str) -> list[tuple[str, bytes]]:
        return [(str(f["name"]), base64.b64decode(f["data"])) for f in body.get(key, [])]

    return RunResult(
        status=str(body["status"]),
        exit_code=int(body["exit_code"]),
        duration_ms=int(body["duration_ms"]),
        stdout=str(body.get("stdout", "")),
        stderr=str(body.get("stderr", "")),
        files=decoded("files"),
        looks=decoded("looks"),
        omitted=[str(name) for name in body.get("omitted", [])],
    )


class SocketRunner:
    """Talks to the runner container. The socket is a file on a shared volume, never a network address."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    def _client(self, timeout: float) -> httpx.Client:
        transport = httpx.HTTPTransport(uds=self.socket_path)
        return httpx.Client(transport=transport, base_url="http://sandbox", timeout=timeout)

    def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
        archive = build_archive(code, inputs, timeout_s)
        try:
            with self._client(timeout_s + 180) as client:
                response = client.post("/run", content=archive, headers={"Content-Type": "application/x-tar"})
            if response.status_code == 503:
                raise SandboxBusy("All runner slots are occupied")
            if response.status_code != 200:
                raise SandboxUnavailable(f"the runner answered HTTP {response.status_code}")
            return parse_result(response.json())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            raise SandboxUnavailable(type(error).__name__) from error
        finally:
            archive.close()

    def healthy(self) -> bool:
        try:
            with self._client(2) as client:
                return client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False


class RunnerPool:
    """Separate containers, each serial. Never run concurrent code under the same sandbox UID.

    Busy responses can fail over; uncertain failures cannot, to avoid duplicating a run.
    """

    def __init__(self, sockets: list[str]) -> None:
        import random

        self.runners = [SocketRunner(path) for path in dict.fromkeys(sockets)]
        random.SystemRandom().shuffle(self.runners)

    def run(self, code: str, inputs: Iterable[Input], timeout_s: float) -> RunResult:
        inputs = list(inputs)
        for runner in self.runners:
            try:
                return runner.run(code, inputs, timeout_s)
            except SandboxBusy:
                continue
        raise SandboxBusy("All isolated runners are busy")

    def healthy(self) -> bool:
        return any(runner.healthy() for runner in self.runners)
