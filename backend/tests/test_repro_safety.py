"""Repro must not use restored keys or target another Compose project's data."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_saved_primary_and_backup_never_decrypted(isolated_env, monkeypatch):
    from forma.ai.llm import LLMClient
    from forma.core.settings import Settings
    from forma.services import config

    def forbidden(*args, **kwargs):
        pytest.fail("A saved credential was decrypted in mock-only mode")

    monkeypatch.setattr(config.secrets, "decrypt", forbidden)
    settings = Settings(llm_mock_only=True, llm_provider="openai", llm_api_key="secret-real-key")
    restored = {
        "provider": "openai",
        "model": "paid-model",
        "api_key_enc": "encrypted",
        "backups": [{"id": "backup", "provider": "anthropic", "api_key_enc": "encrypted"}],
    }
    effective = config.llm_settings(settings, restored)
    assert effective.llm_provider == "mock" and effective.llm_api_key is None
    assert config.build_client(settings, restored).is_mock
    assert LLMClient.from_settings(settings).is_mock


def test_mock_lock_blocks_ui_network_and_discovery(isolated_env, monkeypatch):
    monkeypatch.setenv("FORMA_LLM_MOCK_ONLY", "true")
    from forma.ai.llm import LLMError, _post_json_admitted
    from forma.api.app import create_app
    from forma.core.settings import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        view = client.get("/api/settings/ai").json()
        assert view["mock_only"] and view["offline"] and view["active_provider"] == "mock"
        for method, endpoint, body in [
            ("post", "connect", {"api_key": "not-a-key"}),
            ("post", "models", {"provider": "openai", "api_key": "not-a-key"}),
            ("post", "test", {}),
            ("post", "backups", {}),
            ("put", "", {"provider": "openai"}),
        ]:
            response = getattr(client, method)(
                "/api/settings/ai" + ("/" + endpoint if endpoint else ""), json=body
            )
            assert response.status_code == 422, response.text
            assert "ล็อกโมเดลจำลอง" in response.text
        # Unrelated settings/routes are still usable.
        assert client.post("/api/customers", json={"code": "REPRO", "name": "Repro"}).status_code == 201
    with pytest.raises(LLMError, match="external AI requests"):
        # Check the network boundary before it can create an HTTP client.
        _post_json_admitted("https://example.invalid", {}, {}, 1)


@pytest.fixture
def repro(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "deploy/repro/repro.py"
    spec = importlib.util.spec_from_file_location("repro_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "GENERATED", tmp_path / "generated")
    return module


def test_config_isolated_and_edits_refused(repro):
    repro.init("issue-123", [8081, 19000, 19001])
    path, marker, config = repro.load("issue-123")
    assert config["name"] == "forma-repro-issue-123"
    assert config["networks"]["default"]["internal"]
    assert config["services"]["sandbox"]["network_mode"] == "none"
    for service in config["services"].values():
        assert "env_file" not in service
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
        for volume in service.get("volumes", []):
            assert volume.split(":")[0] in config["volumes"]
    for service in ("api", "worker", "migrate"):
        assert config["services"][service]["environment"]["FORMA_LLM_MOCK_ONLY"] == "true"
    config["volumes"]["pgdata"] = {"external": True, "name": "forma_pgdata"}
    (path / "compose.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RuntimeError, match="edited"):
        repro.load("issue-123")
    # Even updating the hash cannot opt this CLI into an external volume.
    marker["sha256"] = repro.digest(path / "compose.json")
    (path / "owner.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(RuntimeError, match="template"):
        repro.load("issue-123")


def test_known_old_worker_health_template_can_be_upgraded(repro):
    repro.init("old", [8081, 19000, 19001])
    path, marker, config = repro.load("old")
    config["services"]["worker"]["healthcheck"]["test"][-1] = repro.LEGACY_WORKER_CHECK
    (path / "compose.json").write_text(json.dumps(config), encoding="utf-8")
    marker["sha256"] = repro.digest(path / "compose.json")
    (path / "owner.json").write_text(json.dumps(marker), encoding="utf-8")
    assert repro.load("old")[2] == config
    config["services"]["worker"]["healthcheck"]["test"][-1] = "anything else"
    (path / "compose.json").write_text(json.dumps(config), encoding="utf-8")
    marker["sha256"] = repro.digest(path / "compose.json")
    (path / "owner.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(RuntimeError, match="template"):
        repro.load("old")


@pytest.mark.parametrize("name", ["../forma", "..", "", "a/b", "A", "a;docker", "a" * 25])
def test_path_traversal_rejected(repro, name):
    with pytest.raises(RuntimeError):
        repro.instance_dir(name)


def test_foreign_container_mount_blocks_destroy(repro, monkeypatch):
    repro.init("local", [8081, 19000, 19001])
    _, marker, config = repro.load("local")

    def fake_docker(*args, **kwargs):
        if args[:2] in [("volume", "ls"), ("network", "ls")]:
            return ""
        if args[:2] == ("ps", "-aq"):
            return "foreign"
        if args == ("inspect", "foreign"):
            return json.dumps(
                [
                    {
                        "Config": {"Labels": {"com.docker.compose.project": "forma"}},
                        "Mounts": [{"Name": "forma-repro-local_pgdata"}],
                    }
                ]
            )
        pytest.fail(str(args))

    monkeypatch.setattr(repro, "docker", fake_docker)
    with pytest.raises(RuntimeError, match="Another project uses"):
        repro.owned_resources(marker, config)


def test_inherited_dev_environment_removed(repro, monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.prod.yml")
    monkeypatch.setenv("FORMA_LLM_API_KEY", "secret")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "forma")
    env = repro.clean_env()
    assert "COMPOSE_FILE" not in env and "FORMA_LLM_API_KEY" not in env
    assert "COMPOSE_PROJECT_NAME" not in env


def test_lifecycle_commands_cannot_overlap(repro):
    with (
        repro.instance_lock("local"),
        pytest.raises(RuntimeError, match="Another lifecycle"),
        repro.instance_lock("local"),
    ):
        pytest.fail("Second lifecycle lock was acquired")
    with repro.instance_lock("local"):
        repro.init("local", [8081, 19000, 19001])
        repro.load("local")


def test_wrong_destroy_confirmation_never_calls_compose(repro, monkeypatch):
    import argparse

    repro.init("local", [8081, 19000, 19001])
    monkeypatch.setattr(repro, "local_daemon", lambda: None)
    monkeypatch.setattr(repro, "owned_resources", lambda *a: None)
    monkeypatch.setattr(repro, "compose", lambda *a, **k: pytest.fail("Wrong confirmation reached Compose"))
    with pytest.raises(RuntimeError, match="requires --confirm local"):
        repro.execute(argparse.Namespace(action="destroy", name="local", confirm="forma"))


def test_foreign_project_volume_is_never_adopted(repro, monkeypatch):
    repro.init("local", [8081, 19000, 19001])
    _, marker, config = repro.load("local")

    def fake(*args, **kwargs):
        if args == ("volume", "ls", "--format", "{{.Name}}"):
            return "forma-repro-local_pgdata"
        if args[:3] == ("volume", "ls", "-q"):
            return ""
        if args[:2] == ("volume", "inspect"):
            return json.dumps(
                [{"Name": "forma-repro-local_pgdata", "Labels": {"com.docker.compose.project": "forma"}}]
            )
        pytest.fail(str(args))

    monkeypatch.setattr(repro, "docker", fake)
    with pytest.raises(RuntimeError, match="Unowned volume"):
        repro.owned_resources(marker, config)
