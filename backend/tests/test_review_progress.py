from typing import Any

from forma.db.models import MappingVersion
from forma.services.review_progress import describe


def test_progress_counts_saved_answers_once_including_false_and_zero() -> None:
    m = MappingVersion(
        status="proposed",
        policy={
            "confirmed_decisions": [
                {"question_id": "vat", "answer": False},
                {"question_id": "stock", "answer": 0},
                {"question_id": "vat", "answer": True},
                {"question_id": "unknown", "answer": None},
            ]
        },
    )
    data: dict[str, Any] = {"questions": [], "open_questions": [], "blocked": False}
    assert describe(m, data) == {
        "state": "final_review",
        "saved_answers": 2,
        "remaining_questions": 0,
        "remaining_notes": 0,
        "topics": [],
    }
    assert m.status == "proposed"


def test_progress_preserves_notes_blockers_and_reviewed_status() -> None:
    m = MappingVersion(status="proposed", policy={})
    data: dict[str, Any] = {
        "questions": [{"question": "VAT?", "question_th": "รวม VAT ไหม?", "field": {"name": "vat"}}],
        "open_questions": ["VAT?", "Check sender", "Check sender"],
        "blocked": False,
    }
    progress = describe(m, data)
    assert progress["remaining_questions"] == 1
    assert progress["remaining_notes"] == 1
    assert progress["topics"][0]["th"] == "รวม VAT ไหม?"
    data["questions"] = []
    assert describe(m, data)["state"] == "notes"
    m.policy = {"verification": {"complete": True, "passed": False}}
    assert describe(m, data)["state"] == "blocked"
    m.status = "rejected"
    assert describe(m, data)["state"] == "rejected"
