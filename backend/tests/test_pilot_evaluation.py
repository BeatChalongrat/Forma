"""Paired pilot evaluation integrity, independent gold and uncertainty accounting."""

from __future__ import annotations

import copy
import json
import shutil
import socket
from pathlib import Path

import pytest

from forma.eval.pilot import compare, echo_draft, freeze, question_score, read, run_pair, score_review

FIXTURE = Path(__file__).parent / "fixtures" / "pilot_pair" / "manifest.json"


def test_pair_runs_offline_and_never_puts_gold_in_blind_packet(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Paid API/network must not be used")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    out = tmp_path / "pair"
    result = run_pair(FIXTURE, out)
    assert len(result["cases"]) == 8
    assert result["cost_usd"] == 0 and result["mock_calls"] >= 16
    assert result["semantic_ai_evaluation"] is False
    assert all(c["field_delta"] == 0 for c in result["cases"])
    by_id = {c["id"]: c for c in result["cases"]}
    assert by_id["P04"]["rules"]["exact_record_accuracy"] == 1
    assert by_id["P03"]["candidate_agent_notes"]  # Failed echo repair remains visible, not a success.
    assert by_id["P05"]["decision_fields"] == ["/delivery_date"]
    assert by_id["P05"]["answerable_field_accuracy_rules"] == 1
    assert by_id["P05"]["rules"]["field_accuracy"] < 1
    assert len(list((out / "blind-inputs").glob("*/task.json"))) == 8
    assert not list((out / "blind-inputs").rglob("gold*"))
    assert not list((out / "blind-inputs").rglob("questions.json"))
    assert all(s["active_seconds"] is None for s in read(out / "reviewer-sessions.json"))
    observations = read(out / "question-review.json")
    review = tmp_path / "review.json"
    review.write_text(json.dumps(observations), encoding="utf-8")
    scored = score_review(FIXTURE, out, review)
    assert len(scored) == 16
    assert any(r["score"]["status"] == "pending_review" for r in scored)
    questions = next(r for r in observations if r["questions"])
    questions["questions"] = []
    review.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ValueError, match="removed"):
        score_review(FIXTURE, out, review)
    with pytest.raises(FileExistsError):
        run_pair(FIXTURE, out)


def test_echo_only_returns_draft_and_accepts_retry_feedback():
    draft = {"draft_mapping": {"fields": []}, "draft_open_questions": ["Which currency?"]}
    response = echo_draft("", json.dumps(draft) + "\n\nFix validation errors", {})
    assert response["mapping"] == draft["draft_mapping"]
    assert response["open_questions"] == draft["draft_open_questions"]


@pytest.mark.parametrize(
    "gold",
    [
        [],
        [{"sku": "001", "price": "broken"}],
        [{"sku": "001", "product_name": "Tea", "price": 10, "stock": 2}] * 2,
    ],
)
def test_bad_answer_keys_are_rejected_before_running(tmp_path, gold):
    root = tmp_path / "corpus"
    shutil.copytree(FIXTURE.parent, root)
    (root / "P01/gold.json").write_text(json.dumps(gold), encoding="utf-8")
    with pytest.raises(ValueError):
        freeze(root / "manifest.json")


def test_freeze_rejects_escape_non_synthetic_and_gold_mapping(tmp_path):
    root = tmp_path / "corpus"
    shutil.copytree(FIXTURE.parent, root)
    path = root / "manifest.json"
    original = read(path)
    for modification, message in [
        ({"synthetic": False}, "synthetic"),
        ({"cases": [{**original["cases"][0], "schema": "../secret.json"}]}, "escapes"),
        ({"cases": [{**original["cases"][0], "mapping": "answer.json"}]}, "own mappings"),
        ({"cases": [{**original["cases"][0], "id": "../../escape"}]}, "safe directory"),
    ]:
        path.write_text(json.dumps(original | modification), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            freeze(path)


def test_comparison_refuses_changed_gold_or_missing_case():
    one = {"frozen_sha256": {"gold.json": "a"}, "cases": [{"id": "x"}]}
    changed = copy.deepcopy(one)
    changed["frozen_sha256"]["gold.json"] = "b"
    with pytest.raises(ValueError, match="hashes"):
        compare(one, changed)
    changed = copy.deepcopy(one)
    changed["cases"] = []
    with pytest.raises(ValueError, match="Missing"):
        compare(one, changed)
    changed["cases"] = [{"id": "x"}, {"id": "x"}]
    with pytest.raises(ValueError, match="duplicate"):
        compare(one, changed)


def test_unassessed_questions_do_not_inflate_accuracy():
    gold = {"required": [{"target": "/currency", "category": "missing_information"}]}
    pending = {"category": "not_assessed", "target": "/currency"}
    result = question_score(gold, [pending])
    assert result["required_recall"] is None and result["necessary_precision"] is None
    assert result["unassessed"] == 1
    assert question_score(gold, [])["required_recall"] == 0
    labelled = dict(pending, category="missing_information", reviewer="owner", evidence_location="whole file")
    good = question_score(gold, [labelled])
    assert good["required_recall"] == 1 and good["necessary_precision"] == 1
    repeated = question_score(gold, [labelled, labelled])
    assert repeated["duplicate_topics"] == 1
    extra = dict(labelled, target="/stock", category="evidence_in_file")
    both = question_score(gold, [labelled, extra])
    assert both["necessary_precision"] == 0.5 and both["unnecessary_questions"] == 1


@pytest.mark.parametrize("field,value", [("reviewer", None), ("target", None), ("evidence_location", "")])
def test_question_labels_require_auditable_review(field, value):
    label = {
        "category": "evidence_in_file",
        "target": "/stock",
        "reviewer": "owner",
        "evidence_location": "D2",
    }
    label[field] = value
    with pytest.raises(ValueError, match="require"):
        question_score({"required": []}, [label])


def test_unknown_category_is_not_a_correct_question():
    with pytest.raises(ValueError, match="Unknown"):
        question_score({"required": []}, [{"category": "probably_right"}])
