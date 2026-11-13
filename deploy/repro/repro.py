"""Owned, disposable production-topology stacks. stdlib only; never reads dev env/data."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATED = Path(__file__).resolve().parent / "generated"
OWNER = "io.forma.repro.owner"
PREFIX = "forma-repro-"
LEGACY_WORKER_CHECK = "import sys; from forma.jobs.runner import get_backend; sys.exit(1 if get_backend().stats().get('error') else 0)"
LIMITS = {
    "postgres": 512,
    "redis": 256,
    "minio": 512,
    "minio-init": 128,
    "api": 768,
    "worker": 2048,
    "sandbox": 3072,
    "web": 128,
    "migrate": 512,
    "report-worker": 512,
    "assistant-worker": 768,
    "report-sandbox": 3072,
}
CI_LIMITS = LIMITS | {
    "redis": 128,
    "minio": 384,
    "api": 512,
    "worker": 1024,
    "sandbox": 2048,
    "report-sandbox": 2048,
    "assistant-worker": 512,
    "migrate": 256,
}


def capacity_limits(capacity: str) -> dict[str, int]:
    if capacity not in ("standard", "ci"):
        fail("Unknown repro capacity")
    return CI_LIMITS if capacity == "ci" else LIMITS


def fail(message: str) -> None:
    raise RuntimeError(message)


def instance_dir(name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", name):
        fail(
            "Name must be 1-24 lowercase letters/digits/hyphens, starting with a letter."
        )
    path = GENERATED / name
    if GENERATED.is_symlink() or GENERATED.resolve() != GENERATED.absolute():
        fail("Generated directory must not be redirected outside the repository.")
    if path.resolve().parent != GENERATED.resolve() or path.is_symlink():
        fail("Instance path escapes generated directory.")
    return path


def clean_env() -> dict[str, str]:
    # Compose must not inherit dev interpolation, env-file, profile or project settings.
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(("FORMA_", "COMPOSE_", "POSTGRES_", "MINIO_"))
    } | {"COMPOSE_PARALLEL_LIMIT": "1"}


def docker(*args: str, capture: bool = True) -> str:
    p = subprocess.run(
        ["docker", *args],
        env=clean_env(),
        text=True,
        capture_output=capture,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if p.returncode:
        # Do not echo compose output containing generated credentials.
        fail(f"Docker command failed ({args[0]}, exit {p.returncode}).")
    return p.stdout.strip() if capture else ""


def local_daemon() -> None:
    host = os.environ.get("DOCKER_HOST")
    if not host:
        context = docker("context", "show")
        host = json.loads(docker("context", "inspect", context))[0]["Endpoints"][
            "docker"
        ]["Host"]
    if not host.startswith(("npipe://", "unix://")):
        fail(
            "Only a local Docker daemon is allowed; remote Docker contexts are refused."
        )


def build_config(
    name: str,
    token: str,
    ports: list[int],
    passwords: dict[str, str],
    *,
    reports: bool = True,
    capacity: str = "standard",
    pilot: bool = False,
) -> dict:
    limits = capacity_limits(capacity)
    project = PREFIX + name
    labels = {OWNER: token}
    env = {
        "FORMA_ENV": "prod",
        "FORMA_AUTH": "dev",
        "FORMA_AUTH_ALLOW_DEV_IN_PROD": "true",
        "FORMA_LLM_MOCK_ONLY": "true",
        "FORMA_LLM_PROVIDER": "mock",
        "FORMA_LLM_MODEL": "mock",
        "FORMA_LLM_API_KEY": "",
        "FORMA_LLM_BASE_URL": "",
        "FORMA_OTEL_ENDPOINT": "",
        "FORMA_SECRET_KEY": passwords["secret"],
        "FORMA_KNOWLEDGE_POLICY": "strict",
        "FORMA_MAPPING_WORKFLOW": "graph",
        "FORMA_WORKERS": "2",
        "FORMA_FILE_WORKERS": "1",
        "FORMA_LLM_SHARED_CONCURRENCY": "2",
        "FORMA_LLM_SHARED_RPM": "60",
        "FORMA_LLM_SHARED_LIMITER": "redis",
        "FORMA_DATA_DIR": "/data",
        "FORMA_TARGET_SCHEMA_DIR": "/app/target_schema",
        "FORMA_POLICIES_DIR": "/app/policies",
        "FORMA_DATABASE_URL": f"postgresql+psycopg://forma:{passwords['postgres']}@postgres:5432/forma",
        "FORMA_JOB_BACKEND": "arq",
        "FORMA_REDIS_URL": "redis://redis:6379/0",
        "FORMA_STORAGE": "s3",
        "FORMA_S3_ENDPOINT_URL": "http://minio:9000",
        "FORMA_S3_PUBLIC_ENDPOINT_URL": f"http://127.0.0.1:{ports[1]}",
        "FORMA_S3_BUCKET": "forma-repro",
        "FORMA_S3_ACCESS_KEY": "repro",
        "FORMA_S3_SECRET_KEY": passwords["minio"],
        "FORMA_LOG_FORMAT": "json",
        "FORMA_CORS_ORIGINS": json.dumps([f"http://127.0.0.1:{ports[0]}"]),
        "FORMA_CODE_SANDBOX_SOCKET": "/run/sandbox/runner.sock",
    }

    if pilot:
        env["FORMA_PILOT_MODE"] = "true"
        env["FORMA_RETENTION_INTERVAL_H"] = "0"

    def health(test: list[str]) -> dict:
        return {
            "test": test,
            "interval": "5s",
            "timeout": "5s",
            "retries": 30,
            "start_period": "10s",
        }

    deps = {
        "migrate": {"condition": "service_completed_successfully"},
        "redis": {"condition": "service_healthy"},
        "minio-init": {"condition": "service_completed_successfully"},
        "sandbox": {"condition": "service_healthy"},
    }
    app = {
        "image": project + "-api:local",
        "environment": env,
        "volumes": ["formadata:/data", "sandbox-socket:/run/sandbox"],
        "depends_on": deps,
    }
    services = {
        "postgres": {
            "image": "postgres:16-alpine",
            "environment": {
                "POSTGRES_USER": "forma",
                "POSTGRES_DB": "forma",
                "POSTGRES_PASSWORD": passwords["postgres"],
            },
            "volumes": ["pgdata:/var/lib/postgresql/data"],
            "healthcheck": health(["CMD-SHELL", "pg_isready -U forma -d forma"]),
        },
        "redis": {
            "image": "redis:7-alpine",
            "command": [
                "redis-server",
                "--appendonly",
                "yes",
                "--maxmemory",
                "128mb",
                "--maxmemory-policy",
                "noeviction",
            ],
            "volumes": ["redis:/data"],
            "healthcheck": health(["CMD", "redis-cli", "ping"]),
        },
        "minio": {
            "image": "minio/minio:latest",
            "command": ["server", "/data", "--console-address", ":9001"],
            "environment": {
                "MINIO_ROOT_USER": "repro",
                "MINIO_ROOT_PASSWORD": passwords["minio"],
            },
            "volumes": ["minio:/data"],
            "healthcheck": health(["CMD", "mc", "ready", "local"]),
        },
        "minio-init": {
            "image": "minio/mc:latest",
            "depends_on": {"minio": {"condition": "service_healthy"}},
            "environment": {"REPRO_PASSWORD": passwords["minio"]},
            "entrypoint": [
                "/bin/sh",
                "-ec",
                (
                    'mc alias set local http://minio:9000 repro "$$REPRO_PASSWORD"; '
                    "mc mb --ignore-existing local/forma-repro; mc version enable local/forma-repro"
                ),
            ],
            "restart": "no",
        },
        "migrate": {
            "image": project + "-api:local",
            "environment": env,
            "command": ["forma", "db", "upgrade"],
            "healthcheck": {"disable": True},
            "depends_on": {"postgres": {"condition": "service_healthy"}},
            "restart": "no",
        },
        "api": {
            **app,
            "build": {"context": str(ROOT), "dockerfile": "backend/Dockerfile"},
            "healthcheck": health(
                [
                    "CMD",
                    "python",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')",
                ]
            ),
        },
        "worker": {
            **app,
            "command": ["forma", "worker"],
            "healthcheck": health(
                [
                    "CMD",
                    "python",
                    "-c",
                    "import os; import redis; r=redis.Redis.from_url(os.environ['FORMA_REDIS_URL'], socket_timeout=2, socket_connect_timeout=2); assert r.get('arq:queue:health-check'), 'worker heartbeat missing'",
                ]
            ),
        },
        "sandbox": {
            "image": project + "-sandbox:local",
            "build": {"context": str(ROOT), "dockerfile": "deploy/sandbox/Dockerfile"},
            "network_mode": "none",
            "read_only": True,
            "environment": {"SANDBOX_QUEUE_SLOTS": "1"},
            "tmpfs": ["/workspace:size=1g,mode=0711", "/tmp:size=64m,mode=1777"],
            "volumes": ["sandbox-socket:/run/sandbox"],
            "cap_drop": ["ALL"],
            "cap_add": ["SETUID", "SETGID", "KILL", "CHOWN", "DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 256,
        },
        "web": {
            "image": project + "-web:local",
            "build": {"context": str(ROOT / "frontend"), "dockerfile": "Dockerfile"},
            "depends_on": {"api": {"condition": "service_healthy"}},
            "networks": ["default", "edge"],
            "ports": [
                f"127.0.0.1:{ports[0]}:80",
                f"127.0.0.1:{ports[1]}:9000",
                f"127.0.0.1:{ports[2]}:9001",
            ],
            # Only nginx joins the edge network. MinIO/API/worker have no outbound route.
            "command": [
                "/bin/sh",
                "-ec",
                (
                    "cat > /etc/nginx/conf.d/repro-storage.conf <<'NGINX'\n"
                    "server { listen 9000; client_max_body_size 256m; location / { proxy_pass http://minio:9000; "
                    "proxy_set_header Host $$http_host; proxy_request_buffering off; proxy_buffering off; } }\n"
                    "server { listen 9001; location / { proxy_pass http://minio:9001; proxy_set_header Host $$http_host; "
                    "proxy_http_version 1.1; proxy_set_header Upgrade $$http_upgrade; proxy_set_header Connection upgrade; } }\n"
                    "NGINX\nexec nginx -g 'daemon off;'"
                ),
            ],
        },
    }
    if reports:
        env["FORMA_REPORT_SANDBOX_SOCKET"] = "/run/report-sandbox/runner.sock"
        env["FORMA_REPORT_WORKERS"] = "1"
        services["report-sandbox"] = copy.deepcopy(services["sandbox"])
        services["report-sandbox"]["volumes"] = ["report-sandbox-socket:/run/sandbox"]
        services["report-worker"] = copy.deepcopy(services["worker"])
        services["report-worker"]["command"] = ["forma", "worker", "--queue", "reports"]
        services["report-worker"]["volumes"] = [
            "formadata:/data",
            "report-sandbox-socket:/run/report-sandbox",
        ]
        services["report-worker"]["depends_on"].pop("sandbox")
        services["report-worker"]["depends_on"]["report-sandbox"] = {
            "condition": "service_started"
        }
        services["report-worker"]["healthcheck"]["test"][-1] = services["worker"][
            "healthcheck"
        ]["test"][-1].replace("arq:queue", "arq:reports")
    services["assistant-worker"] = copy.deepcopy(services["worker"])
    services["assistant-worker"]["command"] = [
        "forma",
        "worker",
        "--queue",
        "assistant",
    ]
    services["assistant-worker"]["healthcheck"]["test"][-1] = services["worker"][
        "healthcheck"
    ]["test"][-1].replace("arq:queue", "arq:assistant")
    if reports:
        services["assistant-worker"]["volumes"].append(
            "report-sandbox-socket:/run/report-sandbox"
        )
    for key, service in services.items():
        service.update(
            {
                "labels": labels,
                "mem_limit": f"{limits[key]}m",
                "memswap_limit": f"{limits[key]}m",
                "cpus": 2 if key in ("worker", "sandbox") else 1,
            }
        )
        service.setdefault(
            "restart", "no"
        )  # local tests never auto-start on Docker restart
    return {
        "name": project,
        "services": services,
        "networks": {
            "default": {"internal": True, "labels": labels},
            "edge": {"labels": labels},
        },
        "volumes": {
            v: {"labels": labels}
            for v in (
                "pgdata",
                "redis",
                "minio",
                "formadata",
                "sandbox-socket",
                *(["report-sandbox-socket"] if reports else []),
            )
        },
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def init(
    name: str, ports: list[int], capacity: str = "standard", *, pilot: bool = False
) -> None:
    path = instance_dir(name)
    if (path / "owner.json").exists():
        _, marker, _ = load(name)
        if pilot and not marker.get("pilot"):
            fail("This existing instance is not a pilot. Choose a new name.")
        return
    if path.exists() and any(p.name != ".lifecycle.lock" for p in path.iterdir()):
        fail("Unrecognized nonempty instance directory; refusing to overwrite.")
    if len(set(ports)) != 3 or any(p < 1024 or p > 65535 for p in ports):
        fail("Choose three distinct ports between 1024 and 65535.")
    token = secrets.token_hex(24)
    passwords = {key: secrets.token_hex(24) for key in ("postgres", "minio", "secret")}
    config = build_config(
        name, token, ports, passwords, capacity=capacity, pilot=pilot, reports=not pilot
    )
    path.mkdir(parents=True, exist_ok=True)
    config_path = path / "compose.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    (path / "empty.env").write_text(
        "# No dev or production env is loaded.\n", encoding="utf-8"
    )
    (path / "owner.json").write_text(
        json.dumps(
            {
                "name": name,
                "project": PREFIX + name,
                "token": token,
                "ports": ports,
                "sha256": digest(config_path),
                "capacity": capacity,
                "pilot": pilot,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for file in path.iterdir():
        file.chmod(0o600)
    print(f"Created {PREFIX + name}: isolated config, fresh credentials; mock only.")


def load(name: str) -> tuple[Path, dict, dict]:
    path = instance_dir(name)
    for filename in ("owner.json", "compose.json", "empty.env"):
        if (path / filename).resolve().parent != path.resolve():
            fail("Instance file points outside its directory.")
    marker = json.loads((path / "owner.json").read_text(encoding="utf-8"))
    if marker["project"] != PREFIX + name or marker["name"] != name:
        fail("Project identity mismatch.")
    if digest(path / "compose.json") != marker["sha256"]:
        fail("Generated Compose file was edited; refusing unsafe lifecycle operation.")
    config = json.loads((path / "compose.json").read_text(encoding="utf-8"))
    # Regenerate shape from stored credentials: permits no external mounts, services, network, or image changes.
    env = config["services"]["api"]["environment"]
    pg = config["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"]
    expected = build_config(
        name,
        marker["token"],
        marker["ports"],
        {
            "postgres": pg,
            "minio": env["FORMA_S3_SECRET_KEY"],
            "secret": env["FORMA_SECRET_KEY"],
        },
        reports="report-worker" in config["services"],
        capacity=marker.get("capacity", "standard"),
        pilot=marker.get("pilot", False),
    )
    legacy = copy.deepcopy(expected)
    legacy["services"]["worker"]["healthcheck"]["test"][-1] = LEGACY_WORKER_CHECK
    if (
        config not in (expected, legacy)
        or (path / "empty.env").read_text(encoding="utf-8")
        != "# No dev or production env is loaded.\n"
    ):
        fail("Configuration does not match the isolated repro template.")
    return path, marker, config


def owned_resources(marker: dict, config: dict) -> None:
    project, token = marker["project"], marker["token"]
    volume_names = {project + "_" + v for v in config["volumes"]}
    network_names = {project + "_" + network for network in config["networks"]}
    for kind, names in [("volume", volume_names), ("network", network_names)]:
        existing = set(docker(kind, "ls", "--format", "{{.Name}}").splitlines())
        tagged = set(
            docker(
                kind,
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ).splitlines()
        )
        candidates = sorted((names & existing) | tagged)
        objects = json.loads(docker(kind, "inspect", *candidates)) if candidates else []
        for obj in objects:
            labels = obj.get("Labels") or {}
            if (
                obj["Name"] in names
                or labels.get("com.docker.compose.project") == project
            ) and (
                obj["Name"] not in names
                or labels.get(OWNER) != token
                or labels.get("com.docker.compose.project") != project
            ):
                fail(f"Unowned {kind} conflicts with this instance: {obj['Name']}")
    ids = docker("ps", "-aq").splitlines()
    for obj in json.loads(docker("inspect", *ids)) if ids else []:
        labels = obj["Config"].get("Labels") or {}
        ours = labels.get("com.docker.compose.project") == project
        if ours and (
            labels.get(OWNER) != token
            or labels.get("com.docker.compose.service") not in config["services"]
        ):
            fail("Unrecognized container shares this project; no changes made.")
        if not ours and any(
            m.get("Name") in volume_names for m in obj.get("Mounts", [])
        ):
            fail("Another project uses a repro volume; refusing lifecycle changes.")
        if not ours and network_names.intersection(
            obj.get("NetworkSettings", {}).get("Networks", {})
        ):
            fail("Another project uses the repro network; refusing lifecycle changes.")


def compose(path: Path, marker: dict, *args: str, capture: bool = False) -> str:
    return docker(
        "compose",
        "--project-directory",
        str(path),
        "--env-file",
        str(path / "empty.env"),
        "-p",
        marker["project"],
        "-f",
        str(path / "compose.json"),
        *args,
        capture=capture,
    )


def memory_bytes(value: str) -> float:
    match = re.fullmatch(r"([\d.]+)([KMGT]?i?B)", value.strip())
    if not match:
        fail("Cannot read Docker memory statistics safely.")
    return (
        float(match[1])
        * {
            "B": 1,
            "kB": 1000,
            "KB": 1000,
            "KiB": 1024,
            "MB": 1e6,
            "MiB": 1024**2,
            "GB": 1e9,
            "GiB": 1024**3,
            "TB": 1e12,
            "TiB": 1024**4,
        }[match[2]]
    )


def preflight(marker: dict) -> None:
    running = docker(
        "ps", "-q", "--filter", f"label=com.docker.compose.project={marker['project']}"
    ).splitlines()
    if not running:
        for port in marker["ports"]:
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    fail(
                        f"Port {port} is in use. Initialize another name with different ports."
                    )
    info = json.loads(docker("info", "--format", "{{json .}}"))
    stats = docker("stats", "--no-stream", "--format", "{{json .}}")
    used = sum(
        memory_bytes(row["MemUsage"].split("/")[0])
        for line in stats.splitlines()
        if (row := json.loads(line))
        and not row["Name"].startswith(marker["project"] + "-")
    )
    _, _, config = load(marker["name"])
    limit_mb = sum(
        int(s["mem_limit"].removesuffix("m")) for s in config["services"].values()
    )
    required = limit_mb * 1024**2 + used + 1536 * 1024**2
    if info["MemTotal"] < required:
        fail(
            f"Insufficient Docker RAM: need ~{required / 1024**3:.1f} GiB including current usage and reserve. "
            "Dev was not stopped or resized."
        )
    print(
        f"RAM check: Docker {info['MemTotal'] / 1024**3:.1f} GiB; other current usage "
        f"{used / 1024**3:.1f} GiB; repro cap {limit_mb / 1024:.2f} GiB + 1.5 GiB reserve."
    )


@contextmanager
def instance_lock(name: str):
    """OS releases the lock on crash; overlapping up/stop/destroy is refused."""
    path = instance_dir(name)
    path.mkdir(parents=True, exist_ok=True)
    lock_path = path / ".lifecycle.lock"
    if lock_path.resolve().parent != path.resolve():
        fail("Lock file escapes instance directory.")
    with lock_path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail(
                "Another lifecycle command is running for this instance. Wait for it to finish."
            )
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["init", "up", "status", "logs", "stop", "destroy"]
    )
    parser.add_argument("--name", default="local")
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Fresh mock-only pilot with manual export; report services omitted to reserve RAM",
    )
    parser.add_argument("--capacity", choices=["standard", "ci"], default="standard")
    parser.add_argument("--web-port", type=int, default=8081)
    parser.add_argument("--s3-port", type=int, default=19000)
    parser.add_argument("--console-port", type=int, default=19001)
    parser.add_argument("--confirm", help="Exact instance name required for destroy")
    args = parser.parse_args()
    with instance_lock(args.name):
        execute(args)


def execute(args: argparse.Namespace) -> None:
    if args.action in ("init", "up"):
        init(
            args.name,
            [args.web_port, args.s3_port, args.console_port],
            getattr(args, "capacity", "standard"),
            pilot=getattr(args, "pilot", False),
        )
    path, marker, config = load(args.name)
    local_daemon()
    owned_resources(marker, config)
    if args.action == "init":
        compose(path, marker, "config", "--quiet")
    elif args.action == "up":
        preflight(marker)
        # Upgrade the one known old template without replacing credentials or data volumes.
        worker_check = config["services"]["worker"]["healthcheck"]["test"]
        if worker_check[-1] == LEGACY_WORKER_CHECK:
            worker_check[-1] = build_config(
                args.name,
                marker["token"],
                marker["ports"],
                {"postgres": "", "minio": "", "secret": ""},
            )["services"]["worker"]["healthcheck"]["test"][-1]
            (path / "compose.json").write_text(
                json.dumps(config, indent=2), encoding="utf-8"
            )
            marker["sha256"] = digest(path / "compose.json")
            (path / "owner.json").write_text(
                json.dumps(marker, indent=2), encoding="utf-8"
            )
            load(args.name)
        compose(path, marker, "config", "--quiet")
        # Serial builds avoid resource spikes; image tags are unique to this instance.
        for service in ("api", "sandbox", "web"):
            compose(path, marker, "build", service)
        owned_resources(marker, config)
        compose(path, marker, "up", "-d", "--wait", "--wait-timeout", "240")
        # Resolve fresh upstream IPs after API/MinIO recreation on a code rebuild.
        compose(path, marker, "exec", "-T", "web", "nginx", "-s", "reload")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base_url = f"http://127.0.0.1:{marker['ports'][0]}"
        with opener.open(base_url + "/ready", timeout=20) as response:
            if json.load(response).get("status") != "ok":
                fail("Repro readiness failed.")
        with opener.open(base_url + "/api/settings/ai", timeout=20) as response:
            ai = json.load(response)
            if not (ai.get("mock_only") and ai.get("offline")):
                fail("Repro mock-only lock is not active.")
        print(
            f"Repro ready: http://127.0.0.1:{marker['ports'][0]} | MOCK ONLY | 2 jobs | no dev data"
        )
    elif args.action == "status":
        compose(path, marker, "ps", "-a")
        print(f"Repro URL: http://127.0.0.1:{marker['ports'][0]} (mock only)")
    elif args.action == "logs":
        compose(path, marker, "logs", "--tail", "100", "api", "worker")
    elif args.action == "stop":
        compose(path, marker, "down", "--timeout", "60")
        print("Stopped. Repro data retained; up resumes it.")
    elif args.action == "destroy":
        if args.confirm != args.name:
            fail(
                f"Destroy requires --confirm {args.name}; deletes ONLY this repro database/files/queue."
            )
        compose(
            path, marker, "down", "--volumes", "--remove-orphans", "--timeout", "60"
        )
        print(
            "Repro data removed. Generated config and isolated build images retained for a fresh up. Dev untouched."
        )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, KeyError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
