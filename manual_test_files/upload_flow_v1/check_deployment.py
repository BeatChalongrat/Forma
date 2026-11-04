"""Read-only localhost check. Never uploads, starts jobs or calls inference."""
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import httpx

c = httpx.Client(base_url="http://127.0.0.1", timeout=30)
ready = c.get("/ready").json()
assert ready["status"] == "ok", ready
ai = c.get("/api/settings/ai").json()
assert ai["model"] == "deepseek-flash"
stats = c.get("/api/jobs/stats").json()
assert stats["queued"] == stats["running"] == 0
assert stats["failed"] == 5  # unchanged, pre-existing jobs; do not retry them
checked = None
for b in c.get("/api/batches", params={"limit": 20}).json():
    if not b["counts"].get("ready"):
        continue
    r = c.get(f"/api/batches/{b['id']}/download")
    if r.status_code == 409:
        continue
    assert r.status_code == 200, r.status_code
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        m = json.loads(z.read("_forma/manifest.json"))
        for f in m["files"]:
            if f["included"]:
                assert hashlib.sha256(z.read(f["archive_name"])).hexdigest() == f["sha256"]
        checked = {"batch_id": b["id"], "json_files": m["exported_files"], "manifest": True, "hashes_match": True}
        break
assert checked, "No ready ZIP to verify"
result = {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ready": "ok", "model": ai["model"],
          "queued": stats["queued"], "running": stats["running"], "existing_failed": stats["failed"],
          "download": checked, "paid_requests": 0, "commit_created": False}
Path(__file__).with_name("deployment.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
