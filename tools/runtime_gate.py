"""Queue load, interruption and report rendering on a unique owned mock repro only."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/repro"))
import repro


def workbook(i):
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(["sku", "product_name", "currency", "price", "sell_unit", "pack_size"])
    stock = wb.create_sheet("Stock")
    stock.append(["sku", "warehouse", "stock"])
    for n in range(1, 13):
        ws.append([f"A{n:03}", f"Product {n}", "THB", n * 107, "PCS", 1])
        stock.append([f"A{n:03}", "BKK", n * 3])
        stock.append([f"A{n:03}", "CNX", n])
    wb.create_sheet("Notes").append(
        [f"Synthetic workload {i}; not production knowledge"]
    )
    content = io.BytesIO()
    wb.save(content)
    return content.getvalue()


def run(name, count, out):
    path, marker, config = repro.load(name)
    assert name.startswith("qa-"), "Only a disposable quality-gate instance is allowed"
    repro.local_daemon()
    repro.owned_resources(marker, config)
    base = f"http://127.0.0.1:{marker['ports'][0]}"
    with httpx.Client(base_url=base, timeout=120, trust_env=False) as c:

        def call(method, url, **kwargs):
            r = c.request(method, url, **kwargs)
            r.raise_for_status()
            return r.json()

        ai = call("GET", "/api/settings/ai")
        assert ai["mock_only"] and ai["active_provider"] == "mock"

        def upload(i):
            customer = call(
                "POST",
                "/api/customers",
                json={"code": f"LOAD{i:04}", "name": "Synthetic load"},
            )
            rnd = call(
                "POST",
                "/api/rounds",
                json={"customer_id": customer["id"], "label": f"load-{i}"},
            )
            call(
                "POST",
                f"/api/rounds/{rnd['id']}/files",
                files=[
                    (
                        "files",
                        (
                            f"load-{i}.xlsx",
                            workbook(i),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        ),
                    )
                ],
            )
            return rnd["id"]

        with ThreadPoolExecutor(4) as pool:
            rounds = list(pool.map(upload, range(count)))
        # Hold the worker so every request enters the broker before the interruption test.
        repro.compose(path, marker, "stop", "worker", capture=True)
        started = time.monotonic()
        try:
            jobs = [
                call(
                    "POST",
                    f"/api/rounds/{rid}/run",
                    json={"sync": False, "use_llm": False},
                )["job_id"]
                for rid in rounds
            ]
        finally:
            repro.compose(path, marker, "start", "worker", capture=True)
        assert len(set(jobs)) == count
        deadline = time.monotonic() + 3600
        pending = set(jobs)
        completed = {}
        killed = False
        interrupted_job = None
        observed_running = False
        while pending and time.monotonic() < deadline:
            for jid in list(pending):
                j = call("GET", f"/api/jobs/{jid}")
                if j["status"] == "running":
                    observed_running = True
                    if not killed:
                        repro.compose(
                            path,
                            marker,
                            "kill",
                            "-s",
                            "SIGKILL",
                            "worker",
                            capture=True,
                        )
                        repro.compose(path, marker, "start", "worker", capture=True)
                        killed = True
                        interrupted_job = jid
                assert j["status"] not in ("failed", "cancelled"), j
                if j["status"] == "done":
                    completed[jid] = j
                    pending.remove(jid)
            print(f"Completed {len(completed)}/{count}", flush=True)
            if pending:
                time.sleep(1)
        assert not pending, f"Unfinished jobs: {len(pending)}"
        # Even a fast machine must actually exercise a kill during active work.
        assert observed_running and killed, (
            "No active interruption observed; recovery gate was not exercised"
        )
        assert completed[interrupted_job]["attempts"] >= 2, (
            "Killed job was not actually resumed"
        )
        mids = [j["result"]["mapping_version_id"] for j in completed.values()]
        assert len(set(mids)) == count
        for rid, jid in zip(rounds, jobs, strict=True):
            m = call(
                "GET", f"/api/mappings/{completed[jid]['result']['mapping_version_id']}"
            )
            assert m["policy"]["simulation"]["rows_out"] == 12
            p = call(
                "POST",
                f"/api/rounds/{rid}/mapping-preview",
                json={"document": m["document"], "limit": 50},
            )
            assert p["ok"] and len(p["records"]) == 12
            assert {r["sku"] for r in p["records"]} == {
                f"A{n:03}" for n in range(1, 13)
            }
            for row in p["records"]:
                n = int(row["sku"][1:])
                assert row["prices"][0]["amount"] == n * 107
                assert {s["warehouse_code"]: s["qty"] for s in row["inventory"]} == {
                    "BKK": n * 3,
                    "CNX": n,
                }
                assert len(row["inventory"]) == 2
            history = call("GET", f"/api/rounds/{rid}")
            assert len(history["mappings"]) == 1, (
                "Recovery published duplicate mapping versions"
            )
        for filename, args in (
            ("report_probe.py", []),
            ("activity_probe.py", [rounds[0]]),
        ):
            source = (ROOT / "deploy/repro" / filename).read_text(encoding="utf-8")
            result = repro.compose(
                path,
                marker,
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                source,
                *args,
                capture=True,
            )
            (out.parent / (filename + ".log")).write_text(result, encoding="utf-8")
        out.write_text(
            json.dumps(
                {
                    "jobs": count,
                    "completed": len(completed),
                    "active_worker_killed": killed,
                    "interrupted_job": interrupted_job,
                    "recovered_attempts": completed[interrupted_job]["attempts"],
                    "seconds": round(time.monotonic() - started, 2),
                    "rows_per_file": 12,
                    "model": "mock",
                    "api_cost_usd": 0,
                    "report_renderer": "passed",
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--jobs", type=int, default=300)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not 1 <= a.jobs <= 300:
        p.error("jobs must be 1..300")
    with repro.instance_lock(a.name):
        run(a.name, a.jobs, a.out)
