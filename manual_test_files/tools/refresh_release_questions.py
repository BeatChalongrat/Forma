"""Re-read question presentation after deduplication; preserve original predictions and scores."""
import httpx

from global_eval import ROOT, read, request, save

with httpx.Client(base_url="http://127.0.0.1/api", timeout=180) as client:
    for run in ("intl-release-a", "intl-release-b", "intl-release-check"):
        for path in sorted((ROOT / "runs" / run).glob("*/before.json")):
            mid = read(path)["mapping"]["id"]
            qs = request(client, "GET", f"/mappings/{mid}/questions")
            save(path.parent / "questions-current.json", qs)
            print(run, path.parent.name, len(qs["questions"]))
