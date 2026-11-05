"""Offline capacity arithmetic and generated deployment invariants; no containers started."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import plan


def arguments(**overrides):
    a = plan.parser().parse_args([])
    for key, value in dict(
        cpus=16,
        ram_gib=48,
        disk_total_gib=1000,
        disk_free_gib=900,
        average_file_mib=5,
        **overrides,
    ).items():
        setattr(a, key, value)
    return a


class CapacityTests(unittest.TestCase):
    def test_large_profiles_require_opt_in_and_account_every_replica(self):
        for cpus, ram, jobs in ((24, 64, 16), (48, 128, 32)):
            a = arguments()
            a.cpus, a.ram_gib = cpus, ram
            self.assertEqual(plan.calculate(a)["selected"]["jobs"], 8)
            a.max_jobs = jobs
            result = plan.calculate(a)
            self.assertEqual(result["selected"]["jobs"], jobs)
            services = plan.compose(result)["services"]
            self.assertEqual(services["worker"]["deploy"]["replicas"], jobs // 4)
            self.assertEqual(
                len([s for s in services if s.startswith("sandbox")]), jobs // 4
            )
            sockets = json.loads(
                services["worker"]["environment"]["FORMA_MAPPING_LAB_SOCKETS"]
            )
            self.assertEqual(len(set(sockets)), jobs // 4)
            total = sum(
                float(s["mem_limit"][:-1])
                / 1024
                * s.get("deploy", {}).get("replicas", 1)
                for s in services.values()
            )
            self.assertAlmostEqual(total, result["selected"]["ram_limits_gib"])

    def test_all_limits_fit_and_no_unrequested_scale_up(self):
        for cpus in (4, 8, 12, 16, 32):
            for ram in (12, 16, 24, 32, 48, 64, 128):
                a = arguments()
                a.cpus, a.ram_gib = cpus, ram
                result = plan.calculate(a)
                if p := result["selected"]:
                    self.assertLessEqual(p["jobs"], 8)
                    self.assertLessEqual(
                        p["ram_limits_gib"], result["allocatable"]["ram_gib"]
                    )
                    self.assertLessEqual(p["cpu_limits"], result["allocatable"]["cpus"])
                    services = plan.compose(result)["services"]
                    total = sum(
                        float(s["mem_limit"][:-1])
                        / 1024
                        * s.get("deploy", {}).get("replicas", 1)
                        for s in services.values()
                    )
                    self.assertAlmostEqual(total, p["ram_limits_gib"])

    def test_disk_reserve_blocks_otherwise_large_server(self):
        a = arguments()
        a.disk_free_gib = 30
        result = plan.calculate(a)
        self.assertIsNone(result["selected"])
        self.assertFalse(result["disk"]["fits"])
        with self.assertRaises(ValueError):
            plan.compose(result)

    def test_backup_storage_includes_outputs_and_staging(self):
        a = arguments()
        first = plan.calculate(a)
        a.backup_copies += 1
        second = plan.calculate(a)
        self.assertAlmostEqual(
            second["disk"]["additional_required_gib"]
            - first["disk"]["additional_required_gib"],
            first["disk"]["live_data_gib"],
        )
        self.assertGreaterEqual(
            first["disk"]["scratch_gib"], 2 * first["disk"]["live_data_gib"]
        )

    def test_external_workloads_reduce_capacity(self):
        a = arguments()
        self.assertEqual(plan.calculate(a)["selected"]["jobs"], 8)
        a.other_ram_gib = 30
        self.assertIsNone(plan.calculate(a)["selected"])

    def test_pool_isolated_and_key_untouched(self):
        c = plan.compose(plan.calculate(arguments()))["services"]
        self.assertEqual(c["worker"]["deploy"]["replicas"], 2)
        self.assertEqual(c["worker"]["environment"]["FORMA_FILE_WORKERS"], "1")
        self.assertNotIn("FORMA_LLM_API_KEY", json.dumps(c))
        self.assertEqual(c["sandbox-2"]["network_mode"], "none")
        self.assertNotIn("env_file", c["sandbox-2"])
        self.assertIn("runner-2.sock", c["sandbox-2"]["healthcheck"]["test"][-1])
        self.assertEqual(c["sandbox"]["environment"]["SANDBOX_QUEUE_SLOTS"], "1")

    def test_cli_blocks_invalid_and_writes_no_config_on_insufficient(self):
        script = str(Path(__file__).with_name("plan.py"))
        for value in ("nan", "inf", "-1"):
            proc = subprocess.run(
                [sys.executable, script, "--cpus", value],
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "new"
            cmd = [
                sys.executable,
                script,
                "--cpus",
                "4",
                "--ram-gib",
                "8",
                "--disk-total-gib",
                "1000",
                "--disk-free-gib",
                "900",
                "--out",
                str(out),
            ]
            self.assertEqual(
                subprocess.run(cmd, capture_output=True, check=False).returncode, 2
            )
            self.assertTrue((out / "capacity.json").exists())
            self.assertFalse((out / "compose.capacity.json").exists())
            self.assertEqual(
                subprocess.run(cmd, capture_output=True, check=False).returncode, 2
            )


if __name__ == "__main__":
    unittest.main()
