"""Schema-driven execution evidence; independent of model-written explanations."""

from __future__ import annotations

from typing import Any

from forma.ai.plausibility import values_at
from forma.schema_registry import TargetSchema


def coverage(records: list[dict[str, Any]], schema: TargetSchema, stats: dict[str, Any]) -> dict[str, Any]:
    fields = {}
    for f in schema.leaf_fields():
        present = sum(any(v not in (None, "", []) for v in values_at(r, f.pointer)) for r in records)
        fields[f.pointer] = {"records_filled": present, "records": len(records), "risk": f.risk}
    collections = {}
    for c in schema.collections():
        collections[c.pointer] = sum(
            len(v) for r in records for v in values_at(r, c.pointer) if isinstance(v, list)
        )
    return {
        "fields": fields,
        "collections": collections,
        "sources": stats.get("sources", {}),
        "invalid_records": stats.get("invalid_records", 0),
    }


def regressions(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """A valid earlier candidate must not lose data just to silence a diagnostic.

    Loss is a review/repair signal, not a claim that source total rows are products.
    The agent can explain legitimate exclusions to a reviewer, but cannot waive this check.
    """
    out = []
    for pointer, old in before.get("fields", {}).items():
        new = after.get("fields", {}).get(pointer, {})
        if old.get("risk") in ("critical", "high") and new.get("records_filled", 0) < old.get(
            "records_filled", 0
        ):
            out.append(
                f"coverage: {pointer} fell from {old['records_filled']} "
                f"to {new.get('records_filled', 0)} populated records"
            )
    for pointer, old in before.get("collections", {}).items():
        new = after.get("collections", {}).get(pointer, 0)
        if new < old:
            out.append(
                f"coverage: {pointer} lost {old - new} collection items; explain exclusions before approval"
            )
    return out


def verification(
    result: Any,
    schema: TargetSchema,
    *,
    complete: bool,
    binding_problems: list[str] | None = None,
    self_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evidence of execution, not proof of business meaning. Fail closed on unverified output."""
    from forma.ai import plausibility

    problems = list(binding_problems or [])
    if result is None:
        problems.append("No transformation result was verified")
        return {"version": 1, "complete": False, "passed": False, "problems": problems, "warnings": []}
    problems.extend(f.message for f in result.errors)
    # values that pass the contract and still look wrong: a warning class of its own, so the policy can
    # treat a mapping a person already approved differently from a new one
    warnings = plausibility.check(result.records, schema)
    problems.extend(f.message for f in result.findings if "unknown root records" in f.message)
    if not complete:
        problems.append("Only a sample was checked")
    if not result.records:
        problems.append("No output records")
    if self_check:
        problems.extend(self_check.get("problems_left") or [])
    evidence = coverage(result.records, schema, result.stats)
    return {
        "version": 1,
        "complete": complete,
        "passed": not problems,
        "problems": list(dict.fromkeys(problems)),
        "warnings": list(dict.fromkeys(warnings)),
        "rows_checked": result.stats.get("rows_in", 0),
        "rows_out": len(result.records),
        "coverage": evidence,
        "scope": "Execution and contract checks; business ambiguities require operator answers",
    }


def require_verification(decision: Any, report: dict[str, Any], *, reused: bool = False) -> None:
    """An incomplete or failed verification never approves automatically. Plausibility warnings do the
    same for a new or edited mapping; a mapping reused verbatim from an approval keeps the policy's
    warning budget (a person accepted those values once already) and the warnings stay on the card."""
    decision.checks["full_verification"] = bool(report.get("complete") and report.get("passed"))
    warnings = list(report.get("warnings") or [])
    decision.checks["plausibility_warnings"] = len(warnings)
    if decision.checks["full_verification"] and (reused or not warnings):
        return
    if decision.decision == "auto_approve":
        decision.decision = "review"
    if not decision.checks["full_verification"]:
        decision.reasons.extend(f"verification: {p}" for p in report.get("problems", [])[:8])
        decision.risk_level = "high"
    else:
        decision.reasons.extend(f"plausibility: {w}" for w in warnings[:8])
        decision.risk_level = "high" if decision.risk_level == "high" else "medium"
    decision.priority = max(decision.priority, 40)
