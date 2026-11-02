"""Field-level comparison of FORMA's records with an expected.json.

A record matches on its identity (sku). Collections are matched on their own identity: inventory by warehouse_code,
promotions by promo_code, prices by price_list. `price_list` itself is not scored (the contract leaves the name to
the operator) but each expected price entry must exist. Output keys that the expectation does not mention are
listed as extras, not counted as wrong.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from catalogue import canonical  # noqa: E402

KEYS = {"inventory": "warehouse_code", "promotions": "promo_code", "prices": "price_list"}
UNSCORED = {"/prices/*/price_list", "/source_meta"}
CASELESS = {"/inventory/*/warehouse_code", "/promotions/*/promo_code"}  # a code's case comes from the header


def _leaves(obj: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _leaves(v, f"{path}/{k}", out)
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        name = path.rsplit("/", 1)[-1]
        key = KEYS.get(name)
        for n, item in enumerate(obj):
            tag = str(item.get(key, n)).lower() if key else str(n)  # MAIN / Main: a code's case is not scored
            _leaves(item, f"{path}[{tag}]", out)
    else:
        out[path] = obj


def _generic(path: str) -> str:
    import re

    return re.sub(r"\[[^\]]*\]", "/*", path)


def _price_fallback(got: dict[str, Any], want_path: str) -> str | None:
    """A single expected price may carry another price_list name; match it to the only price present."""
    if "/prices[" not in want_path:
        return None
    tail = want_path.split("]", 1)[1]
    cands = [p for p in got if p.startswith("/prices[") and p.endswith(tail)]
    return cands[0] if len(cands) == 1 else None


def score(got_rows: list[dict[str, Any]], want_rows: list[dict[str, Any]]) -> dict[str, Any]:
    got = {str(r.get("sku")): canonical({k: v for k, v in r.items() if k != "source_meta"}) for r in got_rows}
    want = {str(r.get("sku")): canonical(r) for r in want_rows}
    total = ok = 0
    wrong: dict[str, list[str]] = {}
    extra: set[str] = set()
    missing_rows = sorted(set(want) - set(got))
    extra_rows = sorted(set(got) - set(want))
    for sku, w in want.items():
        g = got.get(sku)
        wl: dict[str, Any] = {}
        _leaves(w, "", wl)
        gl: dict[str, Any] = {}
        if g is not None:
            _leaves(g, "", gl)
        single_price = sum(1 for p in wl if p.startswith("/prices[")) and len({p.split("]")[0] for p in wl if p.startswith("/prices[")}) == 1
        for path, value in wl.items():
            gen = _generic(path)
            if gen in UNSCORED:
                continue
            total += 1
            have = gl.get(path, "__missing__")
            if have == "__missing__" and single_price:
                alt = _price_fallback(gl, path)
                if alt is not None:
                    have = gl[alt]
            caseless = gen in CASELESS and isinstance(have, str) and isinstance(value, str) and have.lower() == value.lower()
            if have == value or caseless or (isinstance(have, (int, float)) and isinstance(value, (int, float)) and abs(have - value) < 0.006):
                ok += 1
            else:
                wrong.setdefault(gen, []).append(f"{sku}: want {value!r} got {have!r}")
        for path in gl:
            if path not in wl and _generic(path) not in UNSCORED:
                extra.add(_generic(path))
    return {
        "rows_expected": len(want),
        "rows_got": len(got),
        "missing_rows": missing_rows,
        "extra_rows": extra_rows,
        "fields": total,
        "fields_ok": ok,
        "field_accuracy": round(ok / total, 4) if total else None,
        "wrong": {k: v[:3] + ([f"... {len(v) - 3} more"] if len(v) > 3 else []) for k, v in sorted(wrong.items(), key=lambda kv: -len(kv[1]))},
        "wrong_counts": {k: len(v) for k, v in wrong.items()},
        "extra_fields": sorted(extra),
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    got = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    want = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(score(got if isinstance(got, list) else got["rows"], want), ensure_ascii=False, indent=2))
