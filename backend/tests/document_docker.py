"""Opt-in real renderer without network, credentials or any model API."""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from forma.code_sandbox.client import RunResult, build_archive, parse_result


class DockerDocumentSandbox:
    calls = 0
    broken = False

    def run(self, code: str, inputs: Any, timeout_s: float) -> RunResult:
        self.calls += 1
        if self.broken:
            return RunResult("error", 1, 0, "", "scripted renderer failure")
        items = list(inputs)
        with build_archive(code, items, timeout_s) as archive:
            content = archive.read()
        program = "import sys,json; from pathlib import Path; sys.path.insert(0,'/srv'); import runner; print(json.dumps(runner.execute(sys.stdin.buffer,Path('/workspace'),20000)))"
        proc = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--memory",
                "3g",
                "--memory-swap",
                "3g",
                "--cpus",
                "2",
                "--tmpfs",
                "/workspace:size=1g,mode=0711",
                "--tmpfs",
                "/tmp:size=64m,mode=1777",
                "--entrypoint",
                "python",
                "forma-sandbox:latest",
                "-c",
                program,
            ],
            input=content,
            capture_output=True,
            timeout=timeout_s + 40,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr.decode(errors="replace")[-1000:])
        result = parse_result(json.loads(proc.stdout))
        out = os.environ.get("FORMA_TEST_REPORT_OUTPUT")
        if out and result.status == "ok":
            destination = Path(out) / f"pack-{self.calls}"
            destination.mkdir(parents=True, exist_ok=True)
            for name, data in result.files:
                (destination / Path(name).name).write_bytes(data)
            preview = destination / "previews.zip"
            if preview.exists():
                with zipfile.ZipFile(preview) as z:
                    for name in z.namelist():
                        path = (destination / "previews" / name).resolve()
                        assert path.is_relative_to(destination.resolve())
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(z.read(name))
        return result

    def healthy(self) -> bool:
        return True
