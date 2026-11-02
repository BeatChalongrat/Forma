"""Before and after, case by case: two `forma benchmark` reports (or an airun JSON) side by side.

    backend/.venv/Scripts/python manual_test_files/cases/tools/compare.py BEFORE.json AFTER.json

A benchmark report has ``cases[].metrics``; an airun JSON has ``[].score`` per round (r1 is used). Prints field
accuracy, exact-record accuracy and identity precision per case with the delta, then the means.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, dict[str, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    if isinstance(data, dict) and "cases" in data:  # forma benchmark
        for c in data["cases"]:
            m = c["metrics"]
            out[c["id"]] = {
                "acc": m["field_accuracy"],
                "exact": m["exact_record_accuracy"],
                "prec": m["identity_precision"],
            }
    else:  # airun
        for r in data:
            if r.get("round", "r1") != "r1" or not r.get("score"):
                continue
            s = r["score"]
            rows = max(1, s.get("rows_got") or 0)
            out[r["code"]] = {
                "acc": s["field_accuracy"] or 0.0,
                "exact": float("nan"),
                "prec": (rows - len(s.get("extra_rows") or [])) / rows,
            }
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    before, after = load(Path(sys.argv[1])), load(Path(sys.argv[2]))
    print(f"{'case':44} {'acc before':>10} {'after':>7} {'delta':>7} | {'exact':>5} {'after':>5} | {'prec':>5} {'after':>5}")
    deltas: list[float] = []
    for code in sorted(set(before) | set(after)):
        b, a = before.get(code, {}), after.get(code, {})
        d = (a.get("acc") or 0) - (b.get("acc") or 0) if a and b else float("nan")
        if a and b:
            deltas.append(d)

        def f(x: Any) -> str:
            return "   -" if x is None or x != x else f"{x:5.3f}"

        mark = "  " if d != d or abs(d) < 0.005 else ("▲ " if d > 0 else "▼ ")
        print(f"{code:44} {f(b.get('acc')):>10} {f(a.get('acc')):>7} {mark}{d:+.3f} | {f(b.get('exact'))} {f(a.get('exact'))} | {f(b.get('prec'))} {f(a.get('prec'))}")
    if deltas:
        mean_b = sum(v["acc"] for v in before.values()) / max(1, len(before))
        mean_a = sum(v["acc"] for v in after.values()) / max(1, len(after))
        print(f"\nmean field accuracy: before {mean_b:.4f} → after {mean_a:.4f} ({mean_a - mean_b:+.4f}); better {sum(d > 0.005 for d in deltas)}, worse {sum(d < -0.005 for d in deltas)}, same {sum(abs(d) <= 0.005 for d in deltas)}")


if __name__ == "__main__":
    main()
