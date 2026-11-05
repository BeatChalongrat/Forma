"""Offline, conservative single-server capacity planner. Never deploys or calls an LLM.

Run from the repository root; uses only Python's standard library.
Generated JSON is a Docker Compose override (JSON is valid YAML).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

GIB = 1024**3
# Explicit limits for steady services. Include the scheduled backup's peak budget.
BASE = {
    "api": (1.5, 0.75),
    "postgres": (2.0, 0.75),
    "redis": (0.5, 0.25),
    "minio": (1.0, 0.5),
    "web": (0.25, 0.25),
    "backup-nightly": (0.75, 0.25),
    "migrate": (1.0, 0.5),
    "minio-init": (0.125, 0.25),
}


def limit(memory: float, cpu: float) -> dict:
    return {
        "mem_limit": f"{round(memory * 1024)}m",
        "memswap_limit": f"{round(memory * 1024)}m",
        "cpus": cpu,
        "logging": {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        },
    }


def calculate(a: argparse.Namespace) -> dict:
    memory_reserve = max(3.0, a.ram_gib * 0.20)
    cpu_reserve = max(1.0, a.cpus * 0.125)
    memory_budget = a.ram_gib - memory_reserve - a.other_ram_gib
    cpu_budget = a.cpus - cpu_reserve - a.other_cpus
    source_gib = a.files * a.average_file_mib / 1024
    retained = source_gib * a.retained_batches
    # Full backups contain outputs too, not just source XLSX. Allow an uncompressed
    # snapshot plus archive while backup runs; do not assume any compression savings.
    live_data = retained * (1 + a.output_multiplier) + a.database_gib
    persistent = live_data * (1 + a.backup_copies) + retained  # local source cache
    scratch = max(10.0, source_gib * 2) + 2 * live_data
    images_logs = 15.0
    disk_reserve = max(20.0, a.disk_total_gib * 0.20)
    disk_needed = persistent + scratch + images_logs
    disk_ok = a.disk_free_gib - disk_needed >= disk_reserve
    candidates = []
    for jobs in (2, 4, 8, 16, 32):
        slots = min(4, jobs)
        replicas = math.ceil(jobs / slots)
        sandboxes = max(1, jobs // 4)
        worker_ram = math.ceil((0.5 + slots * a.ram_per_job_gib) * 1024) / 1024
        worker_cpu = max(1.0, slots * 0.5)
        ram = sum(v[0] for v in BASE.values()) + replicas * worker_ram + 4 * sandboxes
        cpu = sum(v[1] for v in BASE.values()) + replicas * worker_cpu + 2 * sandboxes
        candidates.append(
            {
                "jobs": jobs,
                "slots_per_worker": slots,
                "worker_replicas": replicas,
                "sandboxes": sandboxes,
                "worker_ram_gib": worker_ram,
                "worker_cpus": worker_cpu,
                "ram_limits_gib": ram,
                "cpu_limits": cpu,
                "fits": jobs <= a.max_jobs
                and ram <= memory_budget
                and cpu <= cpu_budget,
            }
        )
    fitting = [c for c in candidates if c["fits"]]
    selected = fitting[-1] if fitting and disk_ok else None
    return {
        "schema_version": 1,
        "status": "candidate" if selected else "insufficient_capacity",
        "inputs": vars(a) | {"out": str(a.out) if a.out else None},
        "reserve": {
            "ram_gib": memory_reserve,
            "cpus": cpu_reserve,
            "disk_gib": disk_reserve,
        },
        "allocatable": {"ram_gib": memory_budget, "cpus": cpu_budget},
        "disk": {
            "source_per_batch_gib": source_gib,
            "live_data_gib": live_data,
            "persistent_growth_gib": persistent,
            "scratch_gib": scratch,
            "images_logs_gib": images_logs,
            "additional_required_gib": disk_needed,
            "fits": disk_ok,
            "free_after_growth_gib": a.disk_free_gib - disk_needed,
        },
        "candidates": candidates,
        "selected": selected,
        "warnings": [
            "Planning assumptions, not measured Excel RAM requirements or a readiness certificate.",
            "All CPU/memory hard limits fit together; swap is not counted as capacity.",
            "Includes local DB, Redis, MinIO, startup jobs and scheduled backups on ONE filesystem.",
            "Disk growth is additional to current used space; use separate estimates for separate disks.",
            "Retention/version cleanup is NOT enabled; storage grows until explicitly managed.",
            "RAM per job is an assumption: joins, formulas and expanded XML can exceed it.",
            "No runtime memory admission, autoscaling, HA or load balancer is installed by this tool.",
            "Build images before load; builds/optional ops and telemetry profiles need extra budget.",
            "Sandbox virtual-address limit stays 2560 MiB; container budget includes tmpfs overhead.",
            "Settings in the database may override environment settings; verify effective runtime values.",
        ],
    }


def compose(plan: dict) -> dict:
    p = plan["selected"]
    if not p:
        raise ValueError("No deployable candidate")
    sockets = ["/run/sandbox/runner.sock"]
    sockets.extend(
        f"/run/sandbox/runner-{i}.sock" for i in range(2, p["sandboxes"] + 1)
    )
    runtime = {
        "FORMA_WORKERS": str(p["slots_per_worker"]),
        "FORMA_FILE_WORKERS": "1",
        "FORMA_LLM_SHARED_CONCURRENCY": str(p["jobs"]),
        "FORMA_LLM_SHARED_RPM": "60",
        "FORMA_LLM_SHARED_LIMITER": "redis",
        "FORMA_KNOWLEDGE_POLICY": "strict",
        "FORMA_MAPPING_LAB_SOCKETS": json.dumps(sockets),
        "POLARS_MAX_THREADS": "2",
        "OMP_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2",
    }
    services = {name: limit(*budget) for name, budget in BASE.items()}
    services["api"]["environment"] = runtime.copy()
    services["worker"] = limit(p["worker_ram_gib"], p["worker_cpus"]) | {
        "deploy": {"replicas": p["worker_replicas"]},
        "environment": runtime.copy(),
    }
    # One admitted request per isolated runner: no queued input archives accumulating in tmpfs.
    services["sandbox"] = limit(4, 2) | {
        "environment": {"SANDBOX_QUEUE_SLOTS": "1", "SANDBOX_SOCKET": sockets[0]},
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                f"import socket; s=socket.socket(socket.AF_UNIX); s.connect('{sockets[0]}')",
            ],
            "interval": "30s",
            "timeout": "5s",
            "start_period": "10s",
        },
    }
    for i in range(2, p["sandboxes"] + 1):
        services[f"sandbox-{i}"] = limit(4, 2) | {
            "build": {"context": ".", "dockerfile": "deploy/sandbox/Dockerfile"},
            "network_mode": "none",
            "read_only": True,
            "tmpfs": ["/workspace:size=1g,mode=0711", "/tmp:size=64m,mode=1777"],
            "volumes": ["sandbox-socket:/run/sandbox"],
            "cap_drop": ["ALL"],
            "cap_add": ["SETUID", "SETGID", "KILL", "CHOWN", "DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 256,
            "environment": {
                "SANDBOX_SOCKET": sockets[i - 1],
                "SANDBOX_QUEUE_SLOTS": "1",
            },
            "healthcheck": {
                "test": [
                    "CMD",
                    "python",
                    "-c",
                    f"import socket; s=socket.socket(socket.AF_UNIX); s.connect('{sockets[i - 1]}')",
                ],
                "interval": "30s",
                "timeout": "5s",
                "start_period": "10s",
            },
            "restart": "unless-stopped",
        }
    for name in ("api", "worker"):
        services[name]["depends_on"] = {"sandbox": {"condition": "service_healthy"}}
        for i in range(2, p["sandboxes"] + 1):
            services[name]["depends_on"][f"sandbox-{i}"] = {
                "condition": "service_healthy"
            }
    # Do not cap Redis with an eviction policy: it contains durable queues, not disposable cache.
    services["redis"]["command"] = [
        "redis-server",
        "--save",
        "60",
        "1",
        "--appendonly",
        "yes",
        "--maxmemory",
        "256mb",
        "--maxmemory-policy",
        "noeviction",
    ]
    return {"services": services}


def probe(a: argparse.Namespace) -> None:
    if not sys.platform.startswith("linux"):
        raise ValueError(
            "--probe is for the Linux server itself. Use explicit capacity for planning on dev."
        )
    context = json.loads(
        subprocess.check_output(["docker", "context", "inspect"], text=True)
    )[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise ValueError(
            "Remote Docker cannot share the local disk probe; provide explicit capacity instead."
        )
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    disk = shutil.disk_usage(a.data_path)
    a.cpus, a.ram_gib = float(info["NCPU"]), info["MemTotal"] / GIB
    a.disk_total_gib, a.disk_free_gib = disk.total / GIB, disk.free / GIB


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--probe",
        action="store_true",
        help="Read local Linux Docker capacity; no changes",
    )
    p.add_argument(
        "--data-path",
        default="/var/lib/docker",
        help="Existing filesystem holding all volumes",
    )
    for name in ("cpus", "ram-gib", "disk-total-gib", "disk-free-gib"):
        p.add_argument("--" + name, type=float)
    p.add_argument(
        "--other-ram-gib",
        type=float,
        default=0,
        help="Peak budget for other server applications",
    )
    p.add_argument("--other-cpus", type=float, default=0)
    p.add_argument("--ram-per-job-gib", type=float, default=1.5)
    p.add_argument("--max-jobs", type=int, choices=(2, 4, 8, 16, 32), default=8)
    p.add_argument("--files", type=int, default=300)
    p.add_argument("--average-file-mib", type=float, default=20)
    p.add_argument("--retained-batches", type=int, default=7)
    p.add_argument(
        "--output-multiplier",
        type=float,
        default=1,
        help="JSON + versions relative to original bytes (can be much larger than compressed XLSX)",
    )
    p.add_argument(
        "--backup-copies",
        type=int,
        default=15,
        help="Full LOCAL copies; 15 allows the existing nightly 14-day cleanup boundary",
    )
    p.add_argument(
        "--database-gib",
        type=float,
        default=5,
        help="Additional DB growth incl. audit/mapping logs",
    )
    p.add_argument(
        "--out",
        type=Path,
        help="Create a NEW directory with report/override; never apply",
    )
    return p


def main() -> int:
    p = parser()
    a = p.parse_args()
    try:
        if a.probe:
            probe(a)
        for name in (
            "cpus",
            "ram_gib",
            "disk_total_gib",
            "disk_free_gib",
            "ram_per_job_gib",
            "average_file_mib",
            "files",
            "retained_batches",
        ):
            v = getattr(a, name)
            if v is None or not math.isfinite(v) or v <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        for name in (
            "other_ram_gib",
            "other_cpus",
            "output_multiplier",
            "backup_copies",
            "database_gib",
        ):
            if not math.isfinite(getattr(a, name)) or getattr(a, name) < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if a.disk_free_gib > a.disk_total_gib:
            raise ValueError("Disk free cannot exceed disk total")
        result = calculate(a)
        if a.out:
            a.out.mkdir(
                parents=True, exist_ok=False
            )  # never overwrite a previously reviewed plan
            (a.out / "capacity.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            if result["selected"]:
                (a.out / "compose.capacity.json").write_text(
                    json.dumps(compose(result), indent=2), encoding="utf-8"
                )
        print(json.dumps(result, indent=2))
        return 0 if result["selected"] else 2
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        p.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
