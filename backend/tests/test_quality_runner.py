"""The gate wrapper must isolate state, propagate failure and always leave diagnostics."""

import importlib.util
import os
import socket
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "quality", Path(__file__).resolve().parents[2] / "tools/quality.py"
)
assert spec and spec.loader
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


def test_gate_strips_application_environment(tmp_path, monkeypatch):
    for key in (
        "FORMA_DATABASE_URL",
        "FORMA_LLM_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "COMPOSE_PROJECT_NAME",
        "AWS_ACCESS_KEY_ID",
    ):
        monkeypatch.setenv(key, "must-not-inherit")
    env = quality.clean_env(tmp_path)
    assert env["FORMA_LLM_MOCK_ONLY"] == "true"
    assert env["FORMA_LLM_API_KEY"] == ""
    assert env["FORMA_STORAGE"] == "local"
    assert "must-not-inherit" not in env.values()
    assert str(tmp_path).replace("\\", "/") in env["FORMA_DATABASE_URL"]


def test_failed_gate_is_not_reported_as_success(tmp_path):
    gates = quality.Gates(tmp_path / "results")
    try:
        with pytest.raises(RuntimeError, match="failed"):
            gates.run("failure", [sys.executable, "-c", "raise SystemExit(7)"])
        assert gates.results[0]["exit_code"] == 7
        assert (gates.out / "summary.json").exists()
    finally:
        gates.temp.cleanup()


def test_timed_out_gate_is_failure(tmp_path):
    gates = quality.Gates(tmp_path / "results")
    try:
        with pytest.raises(RuntimeError, match="124"):
            gates.run("timeout", [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.1)
        assert gates.results[0]["exit_code"] == 124
    finally:
        gates.temp.cleanup()


def test_external_service_requires_disposable_ack(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMA_TEST_PG_URL", "postgresql://not-a-test/database")
    monkeypatch.delenv("FORMA_TEST_SERVICES_DISPOSABLE", raising=False)
    with pytest.raises(RuntimeError, match="DISPOSABLE"):
        quality.clean_env(tmp_path)


def test_disposable_ack_survives_nested_test_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMA_TEST_PG_URL", "postgresql://forma@localhost/test")
    monkeypatch.setenv("FORMA_TEST_SERVICES_DISPOSABLE", "1")
    env = quality.clean_env(tmp_path)
    assert env["FORMA_TEST_SERVICES_DISPOSABLE"] == "1"
    assert env["FORMA_TEST_PG_URL"].endswith("/test")


def test_missing_executable_is_a_failed_gate(tmp_path):
    gates = quality.Gates(tmp_path / "results")
    try:
        with pytest.raises(RuntimeError, match="127"):
            gates.run("missing", [str(tmp_path / "nonexistent-executable")])
        assert gates.results[0]["exit_code"] == 127
    finally:
        gates.temp.cleanup()


@pytest.mark.skipif(os.environ.get("FORMA_TEST_BLOCK_EXTERNAL") != "1", reason="Runner network guard only")
def test_free_gate_blocks_external_tcp_before_connect():
    with socket.socket() as sock, pytest.raises(AssertionError, match="blocked external"):
        sock.connect(("192.0.2.1", 443))
