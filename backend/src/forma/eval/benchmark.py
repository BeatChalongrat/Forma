"""Frozen, schema-driven benchmark; gold labels never enter the agent context.

The manifest explicitly separates cold, warm and drift cases. External baseline outputs
are scored with the very same contract, identities and per-field comparison. This measures
the submitted outputs, not a vendor's general capability.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from forma.ai.agents import PROMPT_VERSION, adaptation_agent, mapping_agent, schema_agent
from forma.ai.heuristics import propose_mapping
from forma.ai.plausibility import values_at
from forma.ai.reconciliation import verification
from forma.ai.workbench import FileWorkbench
from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import MappingDocument
from forma.mapping_dsl.validate import validate_mapping
from forma.profiling.profile import merge_profiles, profile_workbook
from forma.schema_registry import INTERNAL_KEYS, TargetSchema
from forma.services.mapping import bind_sources


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical(value: Any) -> str:
    from decimal import Decimal

    def normalize(v: Any) -> Any:
        if isinstance(v, (float, int)) and not isinstance(v, bool):
            return {"$number": str(Decimal(str(v)).normalize())}
        if isinstance(v, list):
            return [normalize(x) for x in v]
        if isinstance(v, dict):
            return {k: normalize(x) for k, x in v.items()}
        return v

    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _strip(value: Any) -> Any:
    """Drop nulls and empty lists at every depth, so an absent key and an explicit null compare equal."""
    if isinstance(value, dict):
        out = {k: _strip(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None and v != []}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def score(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]], schema: TargetSchema
) -> dict[str, Any]:
    if not schema.identity_keys:
        raise ValueError("Benchmark requires identity_keys in the supplied contract")

    def key(record: dict[str, Any]) -> str:
        return _canonical([list(values_at(record, p)) for p in schema.identity_keys])

    def present(record: dict[str, Any], pointer: str) -> list[Any]:
        # a null or an empty list says the same as a missing key: the contract allows both spellings
        return [v for v in values_at(record, pointer) if v is not None and v != []]

    def business_record(record: dict[str, Any]) -> str:
        return _canonical(
            _strip({k: v for k, v in record.items() if k not in INTERNAL_KEYS or schema.declares(k)})
        )

    a, e = {key(r): r for r in actual}, {key(r): r for r in expected}
    if len(e) != len(expected):
        raise ValueError("Golden labels contain duplicate identities")
    common = a.keys() & e.keys()
    accuracy = {}
    for field in schema.leaf_fields():
        # Collection order is immaterial; duplicates are material. Never hide missing identities.
        accuracy[field.pointer] = sum(
            Counter(_canonical(v) for v in present(a[k], field.pointer))
            == Counter(_canonical(v) for v in present(e[k], field.pointer))
            for k in common
        ) / max(1, len(e))
    critical = [accuracy[f.pointer] for f in schema.leaf_fields() if f.risk in ("critical", "high")]
    collections = {
        c.pointer: sum(
            Counter(_canonical(_strip(v)) for v in values_at(a[k], c.pointer + "/*"))
            == Counter(_canonical(_strip(v)) for v in values_at(e[k], c.pointer + "/*"))
            for k in common
        )
        / max(1, len(e))
        for c in schema.collections()
    }
    invalid = sum(bool(schema.validate_record(r)) for r in actual)
    return {
        "expected_records": len(expected),
        "actual_records": len(actual),
        "identity_recall": len(common) / max(1, len(e)),
        "identity_precision": len(common) / max(1, len(actual)),
        "duplicate_identities": len(actual) - len(a),
        "invalid_records": invalid,
        "field_accuracy": sum(accuracy.values()) / max(1, len(accuracy)),
        "critical_accuracy": sum(critical) / len(critical) if critical else None,
        "per_field": accuracy,
        # Field marginals alone miss swapped prices between two items. Compare whole items too.
        "per_collection": collections,
        "exact_record_accuracy": sum(business_record(a[k]) == business_record(e[k]) for k in common)
        / max(1, len(e)),
    }


def run(
    manifest_path: Path, *, client: Any = None, execute: Any = None, settings: Any = None
) -> dict[str, Any]:
    manifest = _json(manifest_path)
    root = manifest_path.resolve().parent
    cases = manifest["cases"]
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("Benchmark needs nonempty cases with unique IDs")
    train = set(manifest.get("training_families", []))
    results = []
    for case in cases:
        split = case["split"]
        if split not in ("cold", "warm", "drift"):
            raise ValueError("split must be cold, warm or drift")
        if split == "cold" and (case["family"] in train or case.get("approved_mapping")):
            raise ValueError(f"Cold case {case['id']} leaks approved family knowledge")
        if split != "cold" and not case.get("approved_mapping"):
            raise ValueError(
                "Warm/drift cases need an explicit approved_mapping, never a hidden database lookup"
            )
        schema_path = root / case["schema"]
        schema = TargetSchema(
            case.get("schema_id", "benchmark"), case.get("schema_version", 1), _json(schema_path), schema_path
        )
        files, profiles, hashes = [], [], {}
        for i, source in enumerate(case["files"]):
            path = root / source
            prof = profile_workbook(path)
            f = SimpleNamespace(
                id=f"f{i}",
                original_name=path.name,
                storage_key=str(path),
                profile=prof.to_dict(),
                status="profiled",
                kind=prof.meta.kind,
                fingerprint=None,
            )
            files.append(f)
            profiles.append((f.id, path.name, prof))
            hashes[source] = hashlib.sha256(path.read_bytes()).hexdigest()
        profile = merge_profiles(profiles)

        def bind(doc: MappingDocument, files: list[Any] = files) -> tuple[dict[str, BoundSource], list[str]]:
            return bind_sources(doc, files, resolve=Path)

        case_executor = execute
        if case_executor is None and settings is not None:
            from forma.services.mapping_lab import executor

            allowed = {f.storage_key: Path(f.storage_key) for f in files}
            storage = SimpleNamespace(
                size=lambda key, allowed=allowed: allowed[key].stat().st_size,
                open=lambda key, allowed=allowed: allowed[key].open("rb"),
            )
            case_executor = executor(SimpleNamespace(settings=settings, storage=storage), files, schema)
        bench = FileWorkbench(schema, profile, files, bind=bind, resolve=Path, execute=case_executor)
        started = time.perf_counter()
        log: dict[str, Any] = {}
        if case.get("mapping"):
            doc = MappingDocument.model_validate(_json(root / case["mapping"]))
        else:
            draft = propose_mapping(profile, schema, customer_id="benchmark")
            if split == "warm":
                doc = MappingDocument.model_validate(_json(root / case["approved_mapping"]))
                log["route"] = "explicit_approved_adapter_reuse"
            elif client is None:
                doc = draft.mapping
                log["open_questions"] = draft.open_questions
            elif split == "cold":
                analysis = schema_agent(client, profile, schema)
                doc, result = mapping_agent(
                    client, profile, schema, draft, schema_analysis=analysis.data, workbench=bench
                )
                log = {"schema": analysis.to_dict(), "mapping": result.to_dict()}
            else:
                approved = MappingDocument.model_validate(_json(root / case["approved_mapping"]))
                doc, result = adaptation_agent(
                    client,
                    approved,
                    schema,
                    profile,
                    {"classification": case.get("drift", "columns_renamed")},
                    draft,
                    workbench=bench,
                )
                log = {"mapping": result.to_dict()}
        valid = validate_mapping(doc, schema)
        bindings, problems = bind(doc)
        output = TransformEngine(schema).run(doc, bindings, limit=None) if valid.ok and not problems else None
        # Read gold after generation. No production memory or previous test outputs enter the prompt.
        expected = _json(root / case["expected"])
        metrics = score(output.records if output else [], expected, schema)
        report = {
            "id": case["id"],
            "split": split,
            "family": case["family"],
            "metrics": metrics,
            "input_sha256": hashes,
            "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            "gold_sha256": hashlib.sha256((root / case["expected"]).read_bytes()).hexdigest(),
            "seconds": round(time.perf_counter() - started, 3),
            "trace": log,
            "verification": verification(output, schema, complete=True, binding_problems=problems),
            "mapping": doc.model_dump(mode="json", exclude_none=True),
        }
        if case.get("baseline_output"):
            report["baseline"] = score(_json(root / case["baseline_output"]), expected, schema)
            report["field_accuracy_delta"] = metrics["field_accuracy"] - report["baseline"]["field_accuracy"]
        results.append(report)
    return {
        "version": 1,
        "prompt_version": PROMPT_VERSION,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "baseline_label": manifest.get("baseline_label"),
        "cases": results,
        "summary": {
            split: {
                "cases": sum(r["split"] == split for r in results),
                "mean_field_accuracy": sum(
                    r["metrics"]["field_accuracy"] for r in results if r["split"] == split
                )
                / max(1, sum(r["split"] == split for r in results)),
            }
            for split in ("cold", "warm", "drift")
        },
        "limitation": "Scores apply only to these cases and supplied baselines, not vendor superiority.",
    }
