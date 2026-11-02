"""Optional business invariants owned by the versioned target contract, not by the model.

Example x-forma.checks: [{"id":"invoice-total", "rule":"sum_equals",
"left":"/total", "right":"/lines/*/amount", "tolerance":0.01}].
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from forma.ai.plausibility import values_at


def validate_checks(document: dict[str, Any]) -> list[str]:
    checks = document.get("x-forma", {}).get("checks", [])
    if not isinstance(checks, list) or len(checks) > 100:
        return ["x-forma.checks must be a list of at most 100 rules"]
    errors, seen = [], set()
    for check in checks:
        if not isinstance(check, dict):
            errors.append("Each contract check must be an object")
            continue
        cid = check.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            errors.append("Contract checks need unique string IDs")
        else:
            seen.add(cid)
        if check.get("rule") not in ("sum_equals", "less_equal", "equals"):
            errors.append(f"Unsupported contract check rule: {check.get('rule')}")
        for side in ("left", "right"):
            if not isinstance(check.get(side), str) or not check[side].startswith("/"):
                errors.append(f"Contract check {cid} needs a {side} JSON pointer")
        try:
            tolerance = Decimal(str(check.get("tolerance", 0)))
            if not tolerance.is_finite() or tolerance < 0:
                raise ValueError()
        except (InvalidOperation, ValueError):
            errors.append(f"Contract check {cid} needs a nonnegative finite tolerance")
    return errors


def evaluate(records: list[dict[str, Any]], document: dict[str, Any]) -> list[dict[str, Any]]:
    invalid = validate_checks(document)
    if invalid:
        return [{"message": message, "row": None, "target": None} for message in invalid]
    out = []
    for check in document.get("x-forma", {}).get("checks", []):
        failures, first = 0, None
        for i, record in enumerate(records):
            left, right = list(values_at(record, check["left"])), list(values_at(record, check["right"]))
            ok = False
            if len(left) == 1 and right and all(x is not None for x in [*left, *right]):
                if check["rule"] == "equals":
                    ok = len(right) == 1 and type(left[0]) is type(right[0]) and left[0] == right[0]
                else:
                    try:
                        if any(isinstance(x, bool) for x in [*left, *right]):
                            raise ValueError()
                        a, rest = Decimal(str(left[0])), [Decimal(str(v)) for v in right]
                        if not a.is_finite() or not all(v.is_finite() for v in rest):
                            raise ValueError()
                        b = sum(rest, Decimal(0))
                        tol = Decimal(str(check.get("tolerance", 0)))
                        ok = (
                            abs(a - b) <= tol
                            if check["rule"] == "sum_equals"
                            else len(rest) == 1 and a <= b + tol
                        )
                    except (InvalidOperation, ValueError):
                        pass
            if not ok:
                failures += 1
                first = i if first is None else first
        if failures:
            out.append(
                {
                    "message": f"Contract check {check['id']} failed for {failures} records: "
                    f"{check['left']} {check['rule']} {check['right']}",
                    "row": first,
                    "target": check["left"],
                }
            )
    return out
