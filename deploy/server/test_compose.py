"""Validate real Compose merges with disposable credentials. Never starts containers."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import plan

ROOT = Path(__file__).resolve().parents[2]


class ComposeMergeTests(unittest.TestCase):
    def test_profiles_and_production_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            envfile = folder / "server.env"
            envfile.write_text(
                f"FORMA_ENV_FILE={envfile.as_posix()}\n"
                "FORMA_SERVER_PROJECT=forma-config-check\n"
                "POSTGRES_PASSWORD=disposable-db-check\n"
                "MINIO_ROOT_PASSWORD=disposable-storage-check\n"
                "FORMA_SECRET_KEY=disposable-check-only-not-deployed\n"
                "FORMA_AUTH_ISSUER=https://identity.invalid\n"
                "FORMA_AUTH_AUDIENCE=check\nFORMA_AUTH_CLIENT_ID=check\n",
                encoding="utf-8",
            )
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("FORMA_", "POSTGRES_", "MINIO_", "COMPOSE_"))
            }
            for jobs, cpu, ram in (
                (2, 8, 20),
                (4, 10, 24),
                (8, 16, 48),
                (16, 24, 64),
                (32, 48, 128),
            ):
                with self.subTest(jobs=jobs):
                    args = plan.parser().parse_args(
                        [
                            "--cpus",
                            str(cpu),
                            "--ram-gib",
                            str(ram),
                            "--max-jobs",
                            str(jobs),
                            "--disk-total-gib",
                            "1000",
                            "--disk-free-gib",
                            "900",
                            "--average-file-mib",
                            "5",
                        ]
                    )
                    report = plan.calculate(args)
                    override = folder / "capacity.json"
                    override.write_text(
                        json.dumps(plan.compose(report)), encoding="utf-8"
                    )
                    proc = subprocess.run(
                        [
                            "docker",
                            "compose",
                            "--project-directory",
                            str(ROOT),
                            "--env-file",
                            str(envfile),
                            "-f",
                            str(ROOT / "docker-compose.prod.yml"),
                            "-f",
                            str(override),
                            "-f",
                            str(ROOT / "deploy/server/compose.isolated.yml"),
                            "config",
                            "--format",
                            "json",
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    merged = json.loads(proc.stdout)
                    self.assertEqual(merged["name"], "forma-config-check")
                    self.assertTrue(
                        all(
                            v["name"].startswith("forma-config-check_")
                            for v in merged["volumes"].values()
                        )
                    )
                    services = merged["services"]
                    self.assertEqual(
                        services["worker"]["deploy"]["replicas"], max(1, jobs // 4)
                    )
                    for name in ("worker", "api", "migrate", "backup-nightly"):
                        self.assertEqual(
                            services[name]["environment"]["FORMA_KNOWLEDGE_POLICY"],
                            "strict",
                        )
                        self.assertEqual(
                            services[name]["environment"]["FORMA_AUTH"], "oidc"
                        )
                    sockets = json.loads(
                        services["worker"]["environment"]["FORMA_MAPPING_LAB_SOCKETS"]
                    )
                    self.assertEqual(len(set(sockets)), max(1, jobs // 4))
                    for name, service in services.items():
                        if not name.startswith("sandbox"):
                            continue
                        self.assertEqual(service["network_mode"], "none")
                        self.assertTrue(service["read_only"])
                        self.assertNotIn(
                            "FORMA_SECRET_KEY", service.get("environment", {})
                        )
                        socket = service["environment"]["SANDBOX_SOCKET"]
                        self.assertIn(socket, sockets)
                        self.assertIn(socket, service["healthcheck"]["test"][-1])


if __name__ == "__main__":
    unittest.main()
