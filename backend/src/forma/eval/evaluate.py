"""Evaluation harness: heuristic mapping + engine vs. corpus ground truth.

Metrics
-------
* identity_recall / identity_precision — SKUs found vs expected
* field_value_accuracy — per target pointer, exact (numeric tolerance 0.01) match over matched SKUs
* critical_accuracy — same, restricted to critical-risk fields
* mapping_coverage — expected pointers that the proposed mapping populates at all
* drift_classification — for returning-customer sequences: identical / renamed / unrelated detected correctly
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forma.ai.heuristics import propose_mapping
from forma.engine.transform import BoundSource, TransformEngine, _get_pointer
from forma.fingerprint.fingerprint import compare, fingerprint_profile
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import WorkbookProfile, merge_profiles, profile_workbook
from forma.schema_registry import SchemaRegistry, TargetSchema

COMPARE_POINTERS = [
    "/sku",
    "/product_name",
    "/brand",
    "/barcode",
    "/category",
    "/unit/sell_unit",
    "/unit/pack_size",
    "/status",
    "/currency",
    "/prices/0/amount",
    "/prices/0/vat_included",
    "/inventory",
    "/promotions/0/type",
    "/promotions/0/discount_percent",
    "/promotions/0/start_date",
    "/promotions/0/end_date",
]
CRITICAL = {
    "/sku",
    "/prices/0/amount",
    "/prices/0/vat_included",
    "/promotions/0/type",
    "/promotions/0/discount_percent",
    "/promotions/0/start_date",
    "/promotions/0/end_date",
    "/currency",
}


def _norm(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return sorted(
                json.dumps(
                    {k: _norm(x) for k, x in d.items() if k in ("warehouse_code", "qty")}, sort_keys=True
                )
                for d in v
            )
        return [_norm(x) for x in v]
    if isinstance(v, str):
        return v.strip()
    return v


def _eq(a: Any, b: Any) -> bool:
    a, b = _norm(a), _norm(b)
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) <= 0.011
    return bool(a == b)


def evaluate_variant(vdir: Path, schema: TargetSchema) -> dict[str, Any]:
    manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
    expected = [
        json.loads(line)
        for line in (vdir / "expected_output.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    files = [vdir / "files" / f for f in manifest["files"]]
    parts: list[tuple[str, str, WorkbookProfile]] = []
    for i, f in enumerate(files):
        parts.append((f"f{i}", f.name, profile_workbook(f)))
    profile = merge_profiles(parts)
    result: dict[str, Any] = {
        "variant": manifest["variant_id"],
        "archetype": manifest["archetype"],
        "perturbations": manifest["perturbations"],
        "files": len(files),
        "tables": len(profile.tables),
    }
    try:
        prop = propose_mapping(profile, schema, customer_id=manifest["customer_code"])
    except Exception as e:
        result.update(
            {
                "error": f"propose: {e}",
                "identity_recall": 0.0,
                "field_accuracy": 0.0,
                "critical_accuracy": 0.0,
            }
        )
        return result
    rep = validate_mapping(prop.mapping, schema)
    result["mapping_valid"] = rep.ok
    result["mapping_confidence"] = prop.confidence
    result["open_questions"] = prop.open_questions
    if not rep.ok:
        result.update(
            {
                "error": "invalid mapping: " + "; ".join(i.message for i in rep.issues if i.level == "error"),
                "identity_recall": 0.0,
                "field_accuracy": 0.0,
                "critical_accuracy": 0.0,
            }
        )
        return result
    bindings: dict[str, BoundSource] = {}
    by_name = {f.name: f for f in files}
    import re

    for s in prop.mapping.sources:
        target = None
        if s.file.name_pattern:
            target = next((f for n, f in by_name.items() if re.search(s.file.name_pattern, n)), None)
        bindings[s.id] = BoundSource(target or files[0])
    res = TransformEngine(schema).run(prop.mapping, bindings)
    result["engine"] = res.summary()
    result["errors"] = [f.message for f in res.findings if f.severity == "error"][:10]

    exp_by = {r["sku"]: r for r in expected}
    got_by = {
        str(r.get("sku", "")).lstrip("0") if str(r.get("sku", ""))[:1].isdigit() else str(r.get("sku", "")): r
        for r in res.records
    }
    exp_keys = set(exp_by)
    got_keys = {k for k in got_by}
    matched = exp_keys & got_keys
    result["identity_recall"] = round(len(matched) / len(exp_keys), 4) if exp_keys else 0.0
    result["identity_precision"] = round(len(matched) / len(got_keys), 4) if got_keys else 0.0

    per_field: dict[str, dict[str, int]] = {}
    for k in matched:
        exp_rec, got_rec = exp_by[k], got_by[k]
        for ptr in COMPARE_POINTERS:
            ev = _get_pointer(exp_rec, ptr) if "/0/" not in ptr else _idx(exp_rec, ptr)
            if ev is None:
                continue
            gv = _get_pointer(got_rec, ptr) if "/0/" not in ptr else _idx(got_rec, ptr)
            d = per_field.setdefault(ptr, {"ok": 0, "n": 0})
            d["n"] += 1
            if _eq(ev, gv):
                d["ok"] += 1
    acc = {p: round(d["ok"] / d["n"], 4) for p, d in per_field.items() if d["n"]}
    result["field_accuracy_by_pointer"] = acc
    tot_ok = sum(d["ok"] for d in per_field.values())
    tot_n = sum(d["n"] for d in per_field.values())
    result["field_accuracy"] = round(tot_ok / tot_n, 4) if tot_n else 0.0
    c_ok = sum(d["ok"] for p, d in per_field.items() if p in CRITICAL)
    c_n = sum(d["n"] for p, d in per_field.items() if p in CRITICAL)
    result["critical_accuracy"] = round(c_ok / c_n, 4) if c_n else 0.0
    result["coverage"] = round(
        len(acc)
        / len(
            [
                p
                for p in COMPARE_POINTERS
                if any(
                    _idx(e, p) is not None if "/0/" in p else _get_pointer(e, p) is not None
                    for e in expected[:50]
                )
            ]
        ),
        4,
    )
    result["fingerprint"] = fingerprint_profile(parts[0][2]).to_dict() if parts else None
    return result


def _idx(rec: dict[str, Any], ptr: str) -> Any:
    # "/prices/0/amount" → first item of the collection; for sortable determinism pick the 'standard' list if present
    head, _, tail = ptr.partition("/0/")
    arr = _get_pointer(rec, head)
    if not isinstance(arr, list) or not arr:
        return None
    item = arr[0]
    if head == "/prices":
        item = next((x for x in arr if x.get("price_list") in ("standard", "ปลีก")), arr[0])
    return item.get(tail) if isinstance(item, dict) else None


def evaluate_corpus(corpus: Path) -> dict[str, Any]:
    reg = SchemaRegistry(_schema_dir(corpus))
    schema = reg.get("product")
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    results = [evaluate_variant(corpus / m["variant_id"], schema) for m in index["variants"]]

    # drift classification on sequences
    drift_results = []
    seqs: dict[str, list[dict[str, Any]]] = {}
    for m, r in zip(index["variants"], results, strict=True):
        if m.get("sequence"):
            seqs.setdefault(m["sequence"], []).append({**m, "fp": r.get("fingerprint")})
    for seq, items in seqs.items():
        items.sort(key=lambda x: x["round_no"])
        base = items[0]["fp"]
        for it in items[1:]:
            if not base or not it["fp"]:
                continue
            from forma.fingerprint.fingerprint import Fingerprint

            rep = compare(Fingerprint.from_dict(base), Fingerprint.from_dict(it["fp"]))
            want = {
                "same_customer_unchanged": {"identical", "header_moved"},
                "renamed": {"columns_renamed", "columns_added", "columns_removed", "types_changed"},
                "wrong_file": {"unrelated_file"},
            }[it["expected_relation"]]
            drift_results.append(
                {
                    "sequence": seq,
                    "round": it["round_no"],
                    "expected": it["expected_relation"],
                    "got": rep.classification,
                    "similarity": round(rep.similarity, 3),
                    "ok": rep.classification in want,
                }
            )

    ok = [r for r in results if "error" not in r]
    summary = {
        "variants": len(results),
        "failed_variants": [r["variant"] for r in results if "error" in r],
        "mean_identity_recall": round(sum(r["identity_recall"] for r in results) / len(results), 4),
        "mean_field_accuracy": round(sum(r["field_accuracy"] for r in results) / len(results), 4),
        "mean_critical_accuracy": round(sum(r["critical_accuracy"] for r in results) / len(results), 4),
        "variants_at_99": sum(1 for r in ok if r["field_accuracy"] >= 0.99),
        "drift_checks": len(drift_results),
        "drift_correct": sum(1 for d in drift_results if d["ok"]),
        "by_archetype": _by_arch(results),
    }
    return {"summary": summary, "variants": results, "drift": drift_results}


def _by_arch(results: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, list[float]] = {}
    for r in results:
        out.setdefault(r["archetype"], []).append(r["field_accuracy"])
    return {k: round(sum(v) / len(v), 4) for k, v in out.items()}


def _schema_dir(corpus: Path) -> Path:
    for p in [corpus.parent.parent / "target_schema", Path("../target_schema"), Path("target_schema")]:
        if p.exists():
            return p
    from forma.core.settings import get_settings

    s = get_settings()
    return s.resolve(s.target_schema_dir)
