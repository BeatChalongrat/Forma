"""The isolated code runner (ADR-048): runs one piece of Python per request, inside its own container.

This file is the whole program in the sandbox image: standard library only, no ``forma`` import, no
credentials. The API is its only caller, over a Unix socket on a volume the two containers share. The
container has no network interface at all (``network_mode: none``), so the code it runs can reach neither
the API, the database, the object store nor the internet.

Every run starts empty. A request carries the code and its input files; the response carries what the code
printed and the files it left in ``outputs/``. Nothing survives a run, so one run cannot leave anything
for the next. The code runs as an unprivileged user with resource limits and is killed at its time limit.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import IO, Any

CODE_NAME = "code.py"
SPEC_NAME = "spec.json"
INPUTS = "inputs"
OUTPUTS = "outputs"
SHARED_TMP = Path("/tmp")
LOOK = "look"  # pictures the model checks its own work with: they reach its next step, never the chat
MAX_REQUEST_BYTES = 512 * 1024 * 1024
MAX_TIMEOUT_S = 300.0
BUSY_WAIT_S = 120.0

# Runs after the switch to the unprivileged user, then replaces itself with the code.
_BOOT = """
import os, resource, sys
memory, size, processes, cpu = map(int, sys.argv[1:5])
for limit, value in (
    (resource.RLIMIT_AS, memory),
    (resource.RLIMIT_FSIZE, size),
    (resource.RLIMIT_NPROC, processes),
    (resource.RLIMIT_CPU, cpu),
    (resource.RLIMIT_NOFILE, 512),
    (resource.RLIMIT_CORE, 0),
):
    resource.setrlimit(limit, (value, value))
os.execv(sys.executable, [sys.executable, "-I", "-X", "utf8", "code.py"])
"""


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 60.0
    memory_mb: int = 2560  # address space: below the container's limit, so code sees MemoryError first
    file_mb: int = 256
    processes: int = 128  # threads count here too
    max_files: int = 40
    max_output_mb: int = 40
    max_looks: int = 4
    max_look_mb: int = 12
    max_text: int = 20000


def _clip(text: str, limit: int) -> str:
    """The start and end of long output: errors tend to be at the end, the point at the start."""
    if len(text) <= limit:
        return text
    tail = limit // 4
    return f"{text[: limit - tail]}\n… {len(text) - limit} characters not shown …\n{text[-tail:]}"


def _chown(path: Path, uid: int, gid: int) -> None:
    if sys.platform != "win32":
        os.chown(path, uid, gid)


def _read(file: IO[bytes], limit: int) -> str:
    file.seek(0)
    return _clip(file.read(limit * 8).decode("utf-8", "replace"), limit)


def _environment(run: Path) -> dict[str, str]:
    tmp = run / "tmp"
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(tmp / "matplotlib"),
        "OMP_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2",
        "NUMEXPR_MAX_THREADS": "2",
        "POLARS_MAX_THREADS": "2",
        # Java reserves more address space by default than a run may have.
        "JAVA_TOOL_OPTIONS": (
            "-Xmx1g -XX:CompressedClassSpaceSize=128m -XX:ReservedCodeCacheSize=64m -XX:+UseSerialGC"
        ),
    }
    for name in ("MATPLOTLIBRC", "SYSTEMROOT"):  # the image's chart defaults; Windows needs SYSTEMROOT
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _prepare(run: Path, uid: int | None) -> None:
    """``inputs`` stays root's: the code can read it and cannot change it."""
    run.chmod(0o755)
    (run / INPUTS).mkdir(exist_ok=True)
    for name in (OUTPUTS, LOOK, "tmp"):
        (run / name).mkdir()
        if uid is not None:
            _chown(run / name, uid, uid)
    cache = Path(os.environ.get("MPL_CACHE", "/opt/matplotlib/cache"))
    if cache.is_dir():
        shutil.copytree(cache, run / "tmp" / "matplotlib")
        if uid is not None:
            for path in [run / "tmp" / "matplotlib", *(run / "tmp" / "matplotlib").iterdir()]:
                _chown(path, uid, uid)


def _spawn(
    run: Path, limits: Limits, uid: int | None, out: IO[bytes], err: IO[bytes]
) -> subprocess.Popen[bytes]:
    if sys.platform == "win32":  # developer machines only: the tests' path, never the sandbox image
        return subprocess.Popen(
            [sys.executable, "-I", "-X", "utf8", CODE_NAME],
            cwd=run,
            env=_environment(run),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        )
    numbers = (
        limits.memory_mb * 1024 * 1024,
        limits.file_mb * 1024 * 1024,
        limits.processes,
        int(limits.timeout_s) + 5,
    )
    identity: dict[str, Any] = {} if uid is None else {"user": uid, "group": uid, "extra_groups": []}
    return subprocess.Popen(
        [sys.executable, "-I", "-c", _BOOT, *map(str, numbers)],
        cwd=run,
        env=_environment(run),
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=err,
        process_group=0,
        umask=0o022,
        **identity,
    )


def _reap() -> None:
    """Orphans come to this process, the container's PID 1. Unreaped they use up the next run's limit."""
    if sys.platform != "win32":
        with contextlib.suppress(ChildProcessError):
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass


def _kill(process: subprocess.Popen[bytes], uid: int | None) -> None:
    """Stop the run and everything it started, including anything that left its process group."""
    if sys.platform == "win32":
        process.kill()
        process.wait()
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    if uid is None:
        return
    deadline = time.monotonic() + 2
    while True:
        alive = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid == uid:
                    alive = True
                    os.kill(int(entry.name), signal.SIGKILL)
            except (FileNotFoundError, ProcessLookupError):
                continue
        _reap()
        if not alive or time.monotonic() > deadline:
            return
        time.sleep(0.02)


def _collect(outputs: Path, limits: Limits) -> tuple[list[dict[str, Any]], list[str]]:
    """Regular files only: a link the code left behind must not make this process read something else."""
    kept: list[dict[str, Any]] = []
    omitted: list[str] = []
    room = limits.max_output_mb * 1024 * 1024
    for root, _dirs, names in os.walk(outputs, followlinks=False):
        for name in sorted(names):
            path = Path(root) / name
            relative = path.relative_to(outputs).as_posix()
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                omitted.append(relative)
                continue
            if len(kept) >= limits.max_files or info.st_size > room:
                omitted.append(relative)
                continue
            descriptor = os.open(
                path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            )
            with os.fdopen(descriptor, "rb") as file:
                data = file.read(room)
            room -= len(data)
            kept.append({"name": relative, "size": len(data), "data": base64.b64encode(data).decode("ascii")})
    return kept, omitted


def _sweep(shared: Path, uid: int) -> None:
    """LibreOffice insists on a pipe in /tmp. What a run left there must not reach the next run."""
    for entry in shared.iterdir():
        if entry.lstat().st_uid != uid:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def execute(archive: IO[bytes], workspace: Path, uid: int | None = None) -> dict[str, Any]:
    """One run: unpack, execute, collect, delete. The archive holds code.py, spec.json and inputs/."""
    run = Path(tempfile.mkdtemp(dir=workspace, prefix="run-"))
    try:
        with tarfile.open(fileobj=archive, mode="r|") as tar:
            for member in tar:
                if member.name in (CODE_NAME, SPEC_NAME) or member.name.startswith(f"{INPUTS}/"):
                    tar.extract(member, run, filter="data")
        spec = json.loads((run / SPEC_NAME).read_text(encoding="utf-8"))
        (run / SPEC_NAME).unlink()
        limits = Limits(timeout_s=min(float(spec.get("timeout_s", 60)), MAX_TIMEOUT_S))
        _prepare(run, uid)
        started = time.perf_counter()
        status = "ok"
        with tempfile.TemporaryFile(dir=workspace) as out, tempfile.TemporaryFile(dir=workspace) as err:
            process = _spawn(run, limits, uid, out, err)
            try:
                code = process.wait(timeout=limits.timeout_s)
            except subprocess.TimeoutExpired:
                status, code = "timeout", -1
            _kill(process, uid)
            if status != "timeout":
                status = "ok" if code == 0 else "killed" if code < 0 else "error"
            stdout, stderr = _read(out, limits.max_text), _read(err, limits.max_text)
        files, omitted = _collect(run / OUTPUTS, limits)
        looks, _ = _collect(
            run / LOOK, replace(limits, max_files=limits.max_looks, max_output_mb=limits.max_look_mb)
        )
        return {
            "status": status,
            "exit_code": code,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
            "files": files,
            "looks": looks,
            "omitted": omitted,
        }
    finally:
        shutil.rmtree(run, ignore_errors=True)
        if uid is not None:
            _sweep(SHARED_TMP, uid)


# --------------------------------------------------------------------------------------------
# the server: one Unix socket, one run at a time
# --------------------------------------------------------------------------------------------
_RUN_LOCK = threading.Lock()


def _packages() -> dict[str, str]:
    from importlib import metadata

    names = re.findall(r"[a-z0-9-]+", os.environ.get("SANDBOX_PACKAGES", ""))
    found: dict[str, str] = {}
    for name in names:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return found


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    workspace: Path
    uid: int | None

    def log_message(self, format: str, *args: Any) -> None:  # requests carry customer data: log nothing
        pass

    def _reply(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, {"ok": True, "python": sys.version.split()[0], "packages": _packages()})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if self.path != "/run" or not 0 < length <= MAX_REQUEST_BYTES:
            self.close_connection = True
            self._reply(400, {"error": "bad request"})
            return
        with tempfile.TemporaryFile(dir=self.workspace) as body:
            left = length
            while left:
                chunk = self.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    self.close_connection = True
                    return
                body.write(chunk)
                left -= len(chunk)
            body.seek(0)
            if not _RUN_LOCK.acquire(timeout=BUSY_WAIT_S):
                self._reply(503, {"error": "busy"})
                return
            try:
                result = execute(body, self.workspace, self.uid)
            except (tarfile.TarError, OSError, ValueError, KeyError) as error:
                self._reply(500, {"error": type(error).__name__})
                return
            finally:
                _RUN_LOCK.release()
        self._reply(200, result)


def serve(socket_path: Path, workspace: Path, uid: int | None, group: int | None) -> None:
    """Listen until stopped. Only ``group`` can reach the socket: the API can, the code being run cannot."""
    if sys.platform == "win32":
        raise SystemExit("the runner needs a Unix socket")
    else:
        from socketserver import UnixStreamServer

        class Server(ThreadingMixIn, UnixStreamServer):
            daemon_threads = True

        _Handler.workspace = workspace
        _Handler.uid = uid
        socket_path.unlink(missing_ok=True)
        os.umask(0o022)
        with Server(str(socket_path), _Handler) as server:
            if group is not None:
                _chown(socket_path.parent, 0, group)
                socket_path.parent.chmod(0o750)
                _chown(socket_path, 0, group)
            socket_path.chmod(0o660)
            server.serve_forever()


def main() -> None:
    serve(
        Path(os.environ.get("SANDBOX_SOCKET", "/run/sandbox/runner.sock")),
        Path(os.environ.get("SANDBOX_WORKSPACE", "/workspace")),
        int(os.environ.get("SANDBOX_RUN_UID", "20000")),
        int(os.environ.get("SANDBOX_API_GID", "10001")),
    )


if __name__ == "__main__":
    main()
