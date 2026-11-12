"""Repeatable quality gates. Free by default; isolated state, logs and exit codes for CI."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def clean_env(root: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(
            (
                "FORMA_",
                "COMPOSE_",
                "OPENAI_",
                "ANTHROPIC_",
                "DEEPSEEK_",
                "POSTGRES_",
                "MINIO_",
                "AWS_",
            )
        )
    }
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "FORMA_ENV": "dev",
            "FORMA_AUTH": "dev",
            "FORMA_LLM_PROVIDER": "mock",
            "FORMA_LLM_MOCK_ONLY": "true",
            "FORMA_LLM_API_KEY": "",
            "FORMA_LLM_BASE_URL": "http://127.0.0.1:1",
            "FORMA_JOB_BACKEND": "thread",
            "FORMA_STORAGE": "local",
            "FORMA_RETENTION_INTERVAL_H": "0",
            "FORMA_DATABASE_URL": "sqlite:///" + (root / "session.db").as_posix(),
            "FORMA_DATA_DIR": str(root / "data"),
            "FORMA_MAPPING_WORKFLOW": "legacy",
            "FORMA_TARGET_SCHEMA_DIR": str(ROOT / "target_schema"),
            "FORMA_POLICIES_DIR": str(ROOT / "policies"),
        }
    )
    # Optional test services only; never inherit application deployment URLs.
    for key in ("FORMA_TEST_PG_URL", "FORMA_TEST_S3_ENDPOINT"):
        if key in os.environ:
            if os.environ.get("FORMA_TEST_SERVICES_DISPOSABLE") != "1":
                raise RuntimeError(
                    "Test service URLs require FORMA_TEST_SERVICES_DISPOSABLE=1; never use dev/prod data"
                )
            env[key] = os.environ[key]
            env["FORMA_TEST_SERVICES_DISPOSABLE"] = "1"
    return env


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=10)


class Gates:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=False)
        self.temp = tempfile.TemporaryDirectory(prefix="forma-quality-")
        self.env = clean_env(Path(self.temp.name))
        self.results: list[dict] = []
        self.status = "running"
        self.error = None
        self.python = sys.executable

    def run(self, name, args, cwd=ROOT, timeout=3600, env=None):
        started = time.monotonic()
        log = self.out / f"{name}.log"
        print(f"RUN {name} -> {log}", flush=True)
        with log.open("w", encoding="utf-8") as stream:
            try:
                process = subprocess.Popen(
                    [str(x) for x in args],
                    cwd=cwd,
                    env=env or self.env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    stop(process)
                    code = 124
                except BaseException:
                    stop(process)
                    raise
            except OSError as exc:
                stream.write(str(exc))
                code = 127
        self.results.append(
            {
                "name": name,
                "exit_code": code,
                "seconds": round(time.monotonic() - started, 2),
                "log": log.name,
            }
        )
        self.save()
        if code:
            print(log.read_text(encoding="utf-8", errors="replace")[-6000:])
            raise RuntimeError(f"Gate {name} failed ({code}); see {log}")

    def save(self):
        (self.out / "summary.json").write_text(
            json.dumps(
                {
                    "status": self.status,
                    "error": self.error,
                    "model": "mock",
                    "paid_api_calls": 0,
                    "gates": self.results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def pytest(self, name, files=(), extra=()):
        self.run(
            name,
            [
                self.python,
                "-m",
                "pytest",
                *files,
                *extra,
                f"--junitxml={self.out / (name + '.xml')}",
            ],
            ROOT / "backend",
            env=self.env
            | {"FORMA_LLM_MOCK_ONLY": "false", "FORMA_TEST_BLOCK_EXTERNAL": "1"},
        )

    def backend(self):
        for name, args in (
            ("ruff", ["ruff", "check", "."]),
            ("format", ["ruff", "format", "--check", "."]),
            ("types", ["mypy"]),
        ):
            self.run(name, [self.python, "-m", *args], ROOT / "backend")
        self.pytest(
            "backend",
            extra=[
                "--cov=forma",
                "--cov-fail-under=85",
                "--maxfail=5",
                f"--cov-report=xml:{self.out / 'coverage.xml'}",
                "--cov-report=term",
            ],
        )

    def frontend(self):
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("Install Node.js/npm first")
        for task in ("lint", "test", "build"):
            self.run("frontend-" + task, [npm, "run", task], ROOT / "frontend")

    def renderer(self, image):
        self.run(
            "real-renderer",
            [
                self.python,
                "-m",
                "pytest",
                "tests/test_document_reports.py",
                "tests/test_assistant_authoring.py",
                "-k",
                "real",
                "--no-cov",
                f"--junitxml={self.out / 'renderer.xml'}",
            ],
            ROOT / "backend",
            env=self.env
            | {
                "FORMA_LLM_MOCK_ONLY": "false",
                "FORMA_TEST_BLOCK_EXTERNAL": "1",
                "FORMA_TEST_REPORT_DOCKER": "1",
                "FORMA_TEST_REPORT_IMAGE": image,
                "FORMA_TEST_REPORT_OUTPUT": str(self.out / "renderer"),
            },
            timeout=1200,
        )

    def evaluation(self):
        self.run(
            "eval-baseline",
            [
                self.python,
                "-m",
                "forma.cli",
                "eval",
                "--corpus",
                ROOT / "data/synthetic",
                "--baseline",
                ROOT / "data/eval_baseline.json",
                "--report",
                self.out / "eval.json",
            ],
            ROOT / "backend",
        )
        self.pytest(
            "mapping-contracts",
            [
                "tests/test_multisheet_contract.py",
                "tests/test_mapping_workflow.py",
                "tests/test_knowledge_revision.py",
                "tests/test_format_context.py",
                "tests/test_knowledge_cross_round.py",
                "tests/test_relationship_regressions.py",
                "tests/test_quiet_failures.py",
            ],
            ["--no-cov"],
        )

    def browser(self):
        processes, logs = [], []
        try:
            for port in (18171, 15174):
                with socket.socket() as sock:
                    sock.bind(
                        ("127.0.0.1", port)
                    )  # refuse to use someone else's server
            env = self.env | {
                "FORMA_DEV_API": "http://127.0.0.1:18171",
                "PYTHONPATH": str(ROOT / "backend"),
                "FORMA_BROWSER_FIXTURE_ROOT": str(Path(self.temp.name) / "browser"),
            }
            commands = [
                ([self.python, "tests/assistant_browser_server.py"], ROOT / "backend"),
                (
                    [
                        "node",
                        "node_modules/vite/bin/vite.js",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "15174",
                        "--strictPort",
                    ],
                    ROOT / "frontend",
                ),
            ]
            for i, (cmd, cwd) in enumerate(commands):
                stream = (self.out / f"browser-server-{i}.log").open(
                    "w", encoding="utf-8"
                )
                logs.append(stream)
                processes.append(
                    subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        env=env,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                        start_new_session=os.name != "nt",
                    )
                )
            for port, route in ((18171, "/ready"), (15174, "/")):
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if any(p.poll() is not None for p in processes):
                        raise RuntimeError(
                            "Browser fixture exited; inspect server logs"
                        )
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}{route}", timeout=2
                        ) as r:
                            if r.status == 200:
                                break
                    except OSError:
                        time.sleep(0.3)
                else:
                    raise RuntimeError("Browser server readiness timeout")
            self.run(
                "browser-chat",
                ["node", "tests/chat-continuity.browser.cjs", "playwright"],
                ROOT / "frontend",
                timeout=300,
                env=env | {"FORMA_QA_OUTPUT": str(self.out)},
            )
            self.run(
                "browser-review",
                ["node", "tests/review-journey.browser.cjs"],
                ROOT / "frontend",
                timeout=300,
                env=env | {"FORMA_QA_OUTPUT": str(self.out)},
            )
        finally:
            for p in reversed(processes):
                stop(p)
            for stream in logs:
                stream.close()

    def runtime(self, jobs):
        # Unique owned stack; no command can target the dev project or reuse its volumes.
        name = "qa-" + uuid.uuid4().hex[:10]
        repro = ROOT / "deploy/repro/repro.py"
        try:
            self.run(
                "stack-up",
                [self.python, repro, "up", "--name", name, "--capacity", "ci"],
                timeout=2400,
            )
            for file in ("smoke", "review_smoke"):
                self.run(
                    file,
                    [self.python, ROOT / f"deploy/repro/{file}.py", "--name", name],
                    timeout=900,
                )
            self.renderer(f"forma-repro-{name}-sandbox:local")
            self.run(
                "runtime-load",
                [
                    self.python,
                    ROOT / "tools/runtime_gate.py",
                    "--name",
                    name,
                    "--jobs",
                    str(jobs),
                    "--out",
                    self.out / "runtime.json",
                ],
                timeout=4500,
            )
        finally:
            # Diagnostics before destruction, even on a failed workload. Ownership is checked by repro.
            try:
                self.run(
                    "stack-logs",
                    [self.python, repro, "logs", "--name", name],
                    timeout=60,
                )
            finally:
                self.run(
                    "stack-cleanup",
                    [self.python, repro, "destroy", "--name", name, "--confirm", name],
                    timeout=180,
                )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "suite",
        choices=[
            "backend",
            "frontend",
            "eval",
            "browser",
            "renderer",
            "runtime",
            "release",
        ],
    )
    p.add_argument("--out", type=Path)
    p.add_argument("--jobs", type=int, default=300)
    p.add_argument(
        "--report-image",
        default="forma-sandbox:latest",
        help="Local sandbox image for renderer suite",
    )
    a = p.parse_args()
    if not 1 <= a.jobs <= 300:
        p.error("--jobs must be 1..300; use 300 for the release load gate")
    out = (
        a.out
        or ROOT / "test-results" / (time.strftime("%Y%m%d-%H%M%S") + "-" + a.suite)
    ).resolve()
    gates = Gates(out)
    try:
        suites = (
            ["backend", "frontend", "eval", "browser", "runtime"]
            if a.suite == "release"
            else [a.suite]
        )
        for suite in suites:
            if suite == "runtime":
                gates.runtime(a.jobs)
            elif suite == "renderer":
                gates.renderer(a.report_image)
            else:
                getattr(gates, "evaluation" if suite == "eval" else suite)()
        gates.status = "passed"
    except BaseException as exc:
        gates.status = "failed"
        gates.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        gates.save()
        gates.temp.cleanup()


if __name__ == "__main__":
    main()
