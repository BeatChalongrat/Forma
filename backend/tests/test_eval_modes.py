"""Eval harness modes (ADR-041): AI-refined scoring, correction replay, an index for real files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import SCHEMA_DIR

from forma.ai.llm import LLMClient, Usage
from forma.eval.evaluate import build_index, evaluate_corpus, evaluate_variant
from forma.eval.replay import derive_corrections, replay_corpus
from forma.schema_registry import SchemaRegistry
from forma.services.learning import Learned
from forma.synth.generator import generate_corpus


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("corpus")
    generate_corpus(out, seed=1, n_variants=8)
    return out


class _EchoProvider:
    """A non-mock provider that returns the heuristic draft it was given, touched to prove it ran."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], model: str, temperature: float
    ) -> tuple[str, Usage]:
        self.calls += 1
        draft = json.loads(user)["draft_mapping"]
        return json.dumps({"mapping": draft, "confidence": 0.97, "open_questions": []}), Usage(
            50, 10, 1, 1.0, model
        )


def test_llm_mode_scores_the_refined_mapping_and_records_usage(corpus: Path) -> None:
    fake = _EchoProvider()
    rep = evaluate_corpus(corpus, llm=LLMClient(fake, model="fake-model"))  # type: ignore[arg-type]
    assert rep["summary"]["mode"] == "llm" and rep["summary"]["llm_fallbacks"] == 0
    assert fake.calls == rep["summary"]["variants"]
    v = rep["variants"][0]
    assert v["mode"] == "llm" and v["llm"]["usage"]["input_tokens"] == 50 and v["mapping_valid"]
    assert rep["summary"]["mean_field_accuracy"] >= 0.95  # the echoed draft scores like the heuristic


def test_llm_failure_falls_back_to_the_draft(corpus: Path) -> None:
    class _Broken:
        name = "fake"

        def complete_json(self, **kw: Any) -> tuple[str, Usage]:
            raise RuntimeError("provider down")

    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    schema = SchemaRegistry(SCHEMA_DIR).get("product")
    client = LLMClient(_Broken(), model="x", max_retries=0)  # type: ignore[arg-type]
    r = evaluate_variant(corpus / index["variants"][0]["variant_id"], schema, llm=client)
    assert r["mode"] == "heuristic" and any(n.startswith("llm_failed") for n in r["llm"]["notes"])
    assert r["field_accuracy"] >= 0.9


def test_replay_derives_corrections_and_scores_with_learning(corpus: Path) -> None:
    rep = replay_corpus(corpus)
    s = rep["summary"]
    assert s["sequences"] == 3 and s["rounds_scored"] >= 3  # wrong-file rounds are skipped
    assert s["mean_field_accuracy_with_learning"] >= s["mean_field_accuracy"]
    assert s["questions_with_learning"] <= s["questions"]
    # a fabricated mistake is corrected to the column whose values match the truth
    schema = SchemaRegistry(SCHEMA_DIR).get("product")
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    first = next(m for m in index["variants"] if m.get("sequence") and m["round_no"] == 0)
    vdir = corpus / first["variant_id"]
    fake = evaluate_variant(vdir, schema)
    fake["field_accuracy_by_pointer"]["/product_name"] = 0.1
    corrections = derive_corrections(vdir, fake, schema)
    assert "/product_name" in corrections
    learned = Learned(
        aliases={"/product_name": {corrections["/product_name"].strip().lower(): "learned: test"}}
    )
    taught = evaluate_variant(vdir, schema, learned=learned)
    assert taught["learned_used"].get("/product_name") is True
    assert taught["field_accuracy_by_pointer"]["/product_name"] >= 0.99


def test_index_for_a_folder_of_real_files(tmp_path: Path, corpus: Path) -> None:
    import shutil

    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    src = corpus / index["variants"][0]["variant_id"]
    real = tmp_path / "real"
    (real / "C777_2026-10" / "files").mkdir(parents=True)
    for f in (src / "files").iterdir():
        shutil.copy(f, real / "C777_2026-10" / "files" / f.name)
    shutil.copy(src / "expected_output.jsonl", real / "C777_2026-10" / "expected_output.jsonl")
    (real / "incomplete" / "files").mkdir(parents=True)
    built = build_index(real)
    assert [v["variant_id"] for v in built["variants"]] == ["C777_2026-10"] and built["skipped"] == [
        "incomplete"
    ]
    m = json.loads((real / "C777_2026-10" / "manifest.json").read_text(encoding="utf-8"))
    assert m["customer_code"] == "C777" and m["archetype"] == "real" and m["files"]
    shutil.copytree(SCHEMA_DIR, tmp_path / "target_schema")  # the harness finds the schema beside data/
    rep = evaluate_corpus(real)
    assert rep["summary"]["variants"] == 1 and rep["summary"]["failed_variants"] == []
