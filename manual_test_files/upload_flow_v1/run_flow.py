"""Actual HTTP/PG/Redis/worker/graph journey. Refuses non-local or non-fake providers."""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import openpyxl

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:18084"
c = httpx.Client(base_url=BASE, timeout=90)
events = []
started = time.monotonic()


def record(name, **values):
    event = {"step": name, "elapsed_s": round(time.monotonic() - started, 2), **values}
    events.append(event)
    (ROOT / "result.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(event, ensure_ascii=False), flush=True)


def request(method, path, **kwargs):
    r = c.request(method, path, **kwargs)
    assert r.is_success, (path, r.status_code, r.text[:2000])
    return r.json()


def wait_batch(bid):
    until = time.monotonic() + 180
    while time.monotonic() < until:
        b = request("GET", f"/api/batches/{bid}")
        if all(i["state"] != "processing" for i in b["items"]):
            return b
        time.sleep(1)
    raise AssertionError(("batch timeout", b))


def review(bid, iid):
    b = wait_batch(bid)
    item = next(i for i in b["items"] if i["id"] == iid)
    if item["state"] == "ready":
        record("already_ready", item=iid, rows=item["rows"], round=item["round_id"])
        return item
    r = request("GET", f"/api/rounds/{item['round_id']}")
    mid = r["mappings"][0]["id"]
    q = request("GET", f"/api/mappings/{mid}/questions")
    (ROOT / f"questions-{iid}.json").write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    answers = {}
    for question in q["questions"]:
        # These values are the fixture's declared meaning, not guesses from the model.
        if question["kind"] == "boolean" and "vat" in question["id"]:
            answers[question["id"]] = True
        elif question["kind"] == "confirm":
            answers[question["id"]] = True
        else:
            raise AssertionError(("Unexpected fixture question: inspect evidence", question))
    if answers:
        body = {"answers": answers}
        preview = request("POST", f"/api/mappings/{mid}/answers/preview", json=body)
        assert preview["preview"]["ok"], preview
        assert c.post(f"/api/mappings/{mid}/answers/apply", json={**body, "digest": "stale"}).status_code == 409
        saved = request("POST", f"/api/mappings/{mid}/answers/apply", json={**body, "digest": preview["digest"]})
        assert saved["parent_id"] == mid
        mid = saved["id"]
        record("answer_preview_and_save", item=iid, answers=answers, mapping=mid, stale_preview_rejected=True)
    request("POST", f"/api/mappings/{mid}/approve", json={})
    b = wait_batch(bid)
    item = next(i for i in b["items"] if i["id"] == iid)
    if item["state"] == "needs_data_check":
        r = request("GET", f"/api/rounds/{item['round_id']}")
        request("POST", f"/api/datasets/{r['datasets'][0]['id']}/approve", json={"acknowledge_anomalies": True})
        b = wait_batch(bid)
        item = next(i for i in b["items"] if i["id"] == iid)
    assert item["state"] == "ready", item
    record("approved_and_exported", item=iid, rows=item["rows"], round=item["round_id"])
    return item


def download(bid, filename):
    r = c.get(f"/api/batches/{bid}/download")
    assert r.status_code == 200, r.text
    (ROOT / filename).write_bytes(r.content)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        manifest = json.loads(z.read("_forma/manifest.json"))
        for entry in manifest["files"]:
            if entry["included"]:
                content = z.read(entry["archive_name"])
                assert hashlib.sha256(content).hexdigest() == entry["sha256"]
                assert len(json.loads(content)) == entry["rows"] == 12
                assert entry["mapping_version_id"] and entry["dataset_version_id"]
            else:
                assert entry["reason"]
        record("download_checked", archive=filename, exported=manifest["exported_files"], omitted=manifest["omitted_files"], complete=manifest["complete"])
        return manifest, {e["item_id"]: json.loads(z.read(e["archive_name"])) for e in manifest["files"] if e["included"]}


def main():
    assert request("GET", "/ready")["status"] == "ok"
    effective = request("GET", "/api/settings/ai")
    assert effective["model"] == "fake-stability" and effective["base_url"] == "http://fake:8001/v1", effective
    record("fake_guard", model=effective["model"], endpoint=effective["base_url"], cost_usd=0)
    requests_before = httpx.get("http://127.0.0.1:18085/stats").json()["requests"]
    prefix = str(int(time.time()))
    rows = ["Item Code,Item Name,Brand,Selling Price,Status"]
    rows += [f"P{i:03d},Product {i},Acme,{i * 10}.50,active" for i in range(1, 13)]
    source = ("\n".join(rows) + "\n").encode()
    changed = source.replace(b"10.50", b"99.50")
    assert len(source) == len(changed)
    files = [("files", ("same.csv", value, "text/csv")) for value in (source, changed, source)]
    form = {"label": f"Flow {prefix}", "expected_files": "4", "upload_key": prefix, "use_llm": "true"}
    b = request("POST", "/api/batches", files=files, data=form)
    bid = b["id"]
    assert b["added"] == 2 and b["skipped"] == 1
    replay = request("POST", "/api/batches", files=files, data=form)
    assert replay["id"] == bid and replay["replayed"] and replay["added"] == 0
    record("receipt_and_content_dedup", batch=bid, received=b["total"], skipped=b["skipped"], replay_same_batch=True)
    b = wait_batch(bid)
    assert all(i["state"] == "needs_customer" for i in b["items"])
    ids = [i["id"] for i in b["items"]]
    for index, iid in enumerate(ids):
        request("POST", f"/api/batches/{bid}/assign", json={"item_ids": [iid], "new_customer": {"code": f"FLOW{prefix}{index}", "name": f"Test {index}"}, "use_llm": True})
        review(bid, iid)
        if index == 0:
            m, _ = download(bid, "partial.zip")
            assert m["exported_files"] == m["omitted_files"] == 1 and m["not_received_files"] == 2 and not m["complete"]
    m, data = download(bid, "same-name.zip")
    assert m["exported_files"] == 2 and len({e["archive_name"] for e in m["files"]}) == 2
    for iid, price in zip(ids, (10.5, 99.5)):
        p = next(x for x in data[iid] if x["sku"] == "P001")
        assert p["prices"][0]["amount"] == price, p
    record("same_size_distinct_outputs", expected_prices=[10.5, 99.5])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    ws.append(["SKU", "Product Name", "Brand", "Sales Price incl VAT", "Status"])
    for i in range(1, 13):
        ws.append([f"X{i:03}", f"Excel {i}", "Acme", i * 20, "active"])
    stream = io.BytesIO()
    wb.save(stream)
    (ROOT / "products.xlsx").write_bytes(stream.getvalue())
    appended = request("POST", f"/api/batches/{bid}/files", files=[("files", ("products.xlsx", stream.getvalue())), ("files", ("broken.xlsx", b"not an xlsx file"))], data={"use_llm": "true"})
    assert appended["added"] == 2 and appended["total"] == 4
    b = wait_batch(bid)
    excel = next(i for i in b["items"] if i["file_name"] == "products.xlsx")
    assert next(i for i in b["items"] if i["file_name"] == "broken.xlsx")["state"] == "failed"
    if excel["state"] == "needs_customer":
        request("POST", f"/api/batches/{bid}/assign", json={"item_ids": [excel["id"]], "new_customer": {"code": f"EXCEL{prefix}", "name": "Excel"}, "use_llm": True})
    review(bid, excel["id"])
    m, data = download(bid, "with-failed-file.zip")
    assert m["exported_files"] == 3 and m["omitted_files"] == 1 and m["not_received_files"] == 0 and not m["complete"]
    assert next(x for x in data[excel["id"]] if x["sku"] == "X001")["prices"][0]["amount"] == 20
    # Replace an accepted CSV: old result must disappear until the successor is checked.
    old = next(i for i in request("GET", f"/api/batches/{bid}")["items"] if i["id"] == ids[0])
    fixed = source.replace(b"10.50", b"55.50")
    successor = request("POST", f"/api/rounds/{old['round_id']}/replace", data={"reason": "Correct fixture price"}, files=[("files", ("fixed.csv", fixed))])
    assert successor["predecessor_id"] == old["round_id"]
    review(bid, ids[0])
    m, data = download(bid, "replacement.zip")
    assert next(e for e in m["files"] if e["item_id"] == ids[0])["round_id"] == successor["id"]
    assert next(x for x in data[ids[0]] if x["sku"] == "P001")["prices"][0]["amount"] == 55.5
    assert request("GET", f"/api/rounds/{old['round_id']}")["status"] == "superseded"
    # Two concurrent retries of the first request must create just one work round.
    def complete_request(_):
        return request("POST", "/api/batches", data={"label": "Complete delivery", "expected_files": "1", "upload_key": prefix + "-complete", "use_llm": "true"},
                       files=[("files", ("products.xlsx", stream.getvalue()))])
    with ThreadPoolExecutor(max_workers=2) as pool:
        twins = list(pool.map(complete_request, range(2)))
    assert twins[0]["id"] == twins[1]["id"]
    assert sorted(x["replayed"] for x in twins) == [False, True]
    complete_bid = twins[0]["id"]
    complete_batch = wait_batch(complete_bid)
    assert complete_batch["total"] == 1
    review(complete_bid, complete_batch["items"][0]["id"])
    complete_manifest, _ = download(complete_bid, "complete.zip")
    assert complete_manifest["complete"] and complete_manifest["omitted_files"] == 0
    record("concurrent_receipt_and_complete_download", batch=complete_bid)
    stats = httpx.get("http://127.0.0.1:18085/stats").json()
    jobs = request("GET", "/api/jobs", params={"limit": 200})
    rounds = {old["round_id"]} | {i["round_id"] for i in request("GET", f"/api/batches/{bid}")["items"] if i["round_id"]}
    rounds.update(i["round_id"] for i in complete_batch["items"] if i["round_id"])
    jobs = [j for j in jobs if j["round_id"] in rounds or j["batch_id"] in (bid, complete_bid)]
    (ROOT / "jobs.json").write_text(json.dumps(jobs, indent=2), encoding="utf-8")
    assert all(j["status"] == "done" for j in jobs), [(j["id"], j["status"]) for j in jobs]
    record("completed", fake_requests=stats["requests"] - requests_before, cost_usd=0, job_count=len(jobs), failed_jobs=0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        record("FAILED", error=str(e))
        raise
