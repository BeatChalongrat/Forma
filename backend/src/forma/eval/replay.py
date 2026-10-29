"""Correction replay (ADR-041): does FORMA get better after a person corrects it?

For every returning-customer sequence of a corpus, round 0 is proposed and scored; each root-level field
the proposal got wrong is "corrected" the way an operator would — pointed at the source column whose
values match the ground truth — and the correction becomes a learned alias (ADR-038). The later rounds of
the sequence are then scored twice, without and with what was learned, and the difference is reported.
On a corpus the heuristic already maps well the difference is small; on real files it is the number
that says the learning loop works.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forma.engine.transform import _get_pointer
from forma.eval.evaluate import COMPARE_POINTERS, _norm, _schema_dir, evaluate_variant
from forma.fingerprint.fingerprint import norm_header
from forma.profiling.profile import merge_profiles, profile_workbook
from forma.schema_registry import SchemaRegistry, TargetSchema
from forma.services.learning import Learned

WRONG_BELOW = 0.9  # a pointer this inaccurate is one an operator would correct
MIN_OVERLAP = 0.6  # share of expected values a column must contain to be "the right column"


def _expected_values(vdir: Path, pointer: str, limit: int = 60) -> set[str]:
    out: set[str] = set()
    for line in (vdir / "expected_output.jsonl").read_text(encoding="utf-8").splitlines()[:limit]:
        if not line.strip():
            continue
        v = _get_pointer(json.loads(line), pointer)
        if v is not None and not isinstance(v, list | dict):
            out.add(str(_norm(v)).strip().lower())
    return out


def derive_corrections(vdir: Path, result: dict[str, Any], schema: TargetSchema) -> dict[str, str]:
    """pointer → source header: the column an operator would have pointed each wrong field at."""
    manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
    files = [vdir / "files" / f for f in manifest["files"]]
    profile = merge_profiles([(f"f{i}", f.name, profile_workbook(f)) for i, f in enumerate(files)])
    acc = result.get("field_accuracy_by_pointer") or {}
    known = {f.pointer for f in schema.fields()}
    out: dict[str, str] = {}
    for pointer in COMPARE_POINTERS:
        if "/0/" in pointer or pointer not in known or acc.get(pointer, 1.0) >= WRONG_BELOW:
            continue
        expected = _expected_values(vdir, pointer)
        if not expected:
            continue
        best: tuple[float, str] | None = None
        for t in profile.tables:
            for c in t.columns:
                samples = {str(s).strip().lower() for s in c.samples if s is not None}
                if not samples:
                    continue
                overlap = len(samples & expected) / len(samples)
                if overlap >= MIN_OVERLAP and (best is None or overlap > best[0]):
                    best = (overlap, c.name)
        if best is not None:
            out[pointer] = best[1]
    return out


def replay_corpus(corpus: Path) -> dict[str, Any]:
    reg = SchemaRegistry(_schema_dir(corpus))
    schema = reg.get("product")
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    seqs: dict[str, list[dict[str, Any]]] = {}
    for m in index["variants"]:
        if m.get("sequence"):
            seqs.setdefault(m["sequence"], []).append(m)
    sequences: list[dict[str, Any]] = []
    for seq, items in sorted(seqs.items()):
        items.sort(key=lambda x: x["round_no"])
        first = corpus / items[0]["variant_id"]
        r0 = evaluate_variant(first, schema)
        corrections = derive_corrections(first, r0, schema)
        learned = Learned(
            aliases={
                p: {norm_header(h): f"learned: replayed correction from {items[0]['variant_id']}"}
                for p, h in corrections.items()
            }
        )
        rounds: list[dict[str, Any]] = []
        for it in items[1:]:
            vdir = corpus / it["variant_id"]
            if it.get("expected_relation") == "wrong_file":
                continue  # another customer's file: nothing to learn across
            plain = evaluate_variant(vdir, schema)
            taught = evaluate_variant(vdir, schema, learned=learned if not learned.empty else None)
            rounds.append(
                {
                    "variant": it["variant_id"],
                    "round": it["round_no"],
                    "field_accuracy": plain["field_accuracy"],
                    "field_accuracy_with_learning": taught["field_accuracy"],
                    "questions": len(plain.get("open_questions") or []),
                    "questions_with_learning": len(taught.get("open_questions") or []),
                    "learned_fields_used": sorted(
                        p for p, v in (taught.get("learned_used") or {}).items() if v
                    ),
                }
            )
        sequences.append(
            {
                "sequence": seq,
                "round0_field_accuracy": r0["field_accuracy"],
                "corrections": corrections,
                "rounds": rounds,
            }
        )
    scored = [r for s in sequences for r in s["rounds"]]

    def mean(key: str) -> float | None:
        return round(sum(float(r[key]) for r in scored) / len(scored), 4) if scored else None

    return {
        "summary": {
            "sequences": len(sequences),
            "rounds_scored": len(scored),
            "corrections_replayed": sum(len(s["corrections"]) for s in sequences),
            "mean_field_accuracy": mean("field_accuracy"),
            "mean_field_accuracy_with_learning": mean("field_accuracy_with_learning"),
            "questions": sum(r["questions"] for r in scored),
            "questions_with_learning": sum(r["questions_with_learning"] for r in scored),
        },
        "sequences": sequences,
    }
