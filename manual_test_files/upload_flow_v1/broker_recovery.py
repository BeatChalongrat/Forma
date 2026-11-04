"""Stop ONLY the isolated test Redis, accept an upload, and prove outbox recovery."""
import json
import subprocess
import time
from pathlib import Path

import httpx

root = Path(__file__).resolve().parent
c = httpx.Client(base_url="http://127.0.0.1:18084", timeout=90)
assert c.get("/api/settings/ai").json()["base_url"] == "http://fake:8001/v1"
container = "forma-flow-redis-1"
project = subprocess.check_output(["docker", "inspect", container, "--format", '{{index .Config.Labels "com.docker.compose.project"}}'], text=True).strip()
assert project == "forma-flow"
subprocess.run(["docker", "stop", container], check=True, capture_output=True)
started = time.monotonic()
try:
    r = c.post("/api/batches", data={"label": "Broker recovery", "upload_key": str(time.time())},
               files=[("files", ("warehouse-recovery.csv", b"warehouse_id,temperature\nW1,4.5\nW2,9.5\n"))])
    assert r.status_code == 201, (r.status_code, r.text)
    bid = r.json()["id"]
    jobs = c.get(f"/api/jobs/batch/{bid}").json()
    assert jobs["total"] == 1 and jobs["by_status"] == {"queued": 1}, jobs
    original_job = jobs["jobs"][0]["id"]
    (root / "broker-before.json").write_text(json.dumps(jobs, indent=2), encoding="utf-8")
finally:
    subprocess.run(["docker", "start", container], check=True, capture_output=True)
until = time.monotonic() + 120
while time.monotonic() < until:
    b = c.get(f"/api/batches/{bid}").json()
    if b["items"][0]["state"] == "needs_customer":
        break
    time.sleep(1)
else:
    raise AssertionError(b)
jobs = c.get(f"/api/jobs/batch/{bid}").json()
assert jobs["total"] == 1 and jobs["by_status"] == {"done": 1}, jobs
assert jobs["jobs"][0]["id"] == original_job
(root / "broker-after.json").write_text(json.dumps(jobs, indent=2), encoding="utf-8")
result = {"accepted_while_redis_down": True, "batch_id": bid, "state_after_recovery": b["items"][0]["state"],
          "files": b["total"], "elapsed_s": round(time.monotonic()-started,2), "cost_usd":0}
(root / "broker-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
