"""Describe the current review, without interpreting a historical verdict as approval."""

from typing import Any

from forma.db.models import MappingVersion


def describe(mapping: MappingVersion, data: dict[str, Any]) -> dict[str, Any]:
    questions = data["questions"]
    prompts = {q.get("question") for q in questions}
    notes = [note for note in dict.fromkeys(data["open_questions"]) if note not in prompts]
    policy = mapping.policy or {}
    saved = {
        (d.get("question_id") or d.get("target"), d.get("source_value"))
        for d in policy.get("confirmed_decisions", [])
        if d.get("answer") is not None
    }
    verification = policy.get("verification")
    blocked = data["blocked"] or bool(
        verification and not (verification.get("complete") and verification.get("passed"))
    )
    state = (
        mapping.status
        if mapping.status != "proposed"
        else "blocked"
        if blocked
        else "questions"
        if questions
        else "notes"
        if notes
        else "final_review"
    )
    return {
        "state": state,
        "saved_answers": len(saved),
        "remaining_questions": len(questions),
        "remaining_notes": len(notes),
        "topics": [
            {
                "en": q.get("question") or q["field"].get("description") or q["field"]["name"],
                "th": q.get("question_th")
                or q["field"].get("description_th")
                or q.get("question")
                or q["field"]["name"],
            }
            for q in questions[:3]
        ],
    }
