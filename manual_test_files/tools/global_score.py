"""Evidence-field scoring without null inflation or duplicate-key overwrite.

Pure evaluator: never imported by the mapper. Counts array multiplicity and unsupported extra facts.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

MISSING = object()


def equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except InvalidOperation:
            return False
    return type(a) is type(b) and a == b


def at(record: Any, pointer: str) -> Any:
    for part in pointer.strip("/").split("/"):
        if not isinstance(record, dict):
            return None
        record = record.get(part.replace("~1", "/").replace("~0", "~"))
    return record


def leaves(value: Any) -> int:
    if isinstance(value, dict):
        return sum(leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(leaves(v) for v in value)
    return 1


def score(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]], schema: dict[str, Any]
) -> dict[str, Any]:
    extension = schema.get("x-forma", {})
    identity = extension.get("identity_keys") or ["/" + schema["required"][0]]
    collections = extension.get("collections", {})
    wrong: list[dict[str, Any]] = []
    extra: list[dict[str, Any]] = []
    correct = 0
    denominator = sum(leaves(r) for r in expected)
    count_extra = 0

    def key(row: Any, fields: list[str]) -> tuple[str, ...]:
        return tuple(repr(at(row, field)) for field in fields)

    def compare(
        got: Any, want: Any, spec: dict[str, Any], ptr: str, record_key: str
    ) -> None:
        nonlocal correct, count_extra
        if isinstance(want, dict):
            if not isinstance(got, dict):
                for name, value in want.items():
                    compare(
                        MISSING,
                        value,
                        spec.get("properties", {}).get(name, {}),
                        ptr + "/" + name,
                        record_key,
                    )
                return
            for name, value in want.items():
                compare(
                    got.get(name, MISSING),
                    value,
                    spec.get("properties", {}).get(name, {}),
                    ptr + "/" + name,
                    record_key,
                )
            for name, value in got.items():
                if (
                    name in want
                    or name == "source_meta"
                    or value is None
                    or value == []
                    or value == {}
                ):
                    continue
                child = spec.get("properties", {}).get(name, {})
                if "default" in child and equal(value, child["default"]):
                    continue
                extra.append(
                    {
                        "key": record_key,
                        "path": ptr + "/" + name,
                        "value": value,
                        "reason": "unasserted output fact",
                    }
                )
                count_extra += leaves(value)
        elif isinstance(want, list):
            values = list(got) if isinstance(got, list) else []
            fields = collections.get(ptr, {}).get("identity_keys")
            for index, value in enumerate(want):
                match = (
                    next(
                        (
                            i
                            for i, candidate in enumerate(values)
                            if key(candidate, fields) == key(value, fields)
                        ),
                        None,
                    )
                    if fields
                    else (0 if values else None)
                )
                candidate = values.pop(match) if match is not None else MISSING
                compare(
                    candidate,
                    value,
                    spec.get("items", {}),
                    ptr + "/" + str(index),
                    record_key,
                )
            for value in values:
                extra.append(
                    {
                        "key": record_key,
                        "path": ptr,
                        "value": value,
                        "reason": "extra collection member",
                    }
                )
                count_extra += leaves(value)
        elif got is not MISSING and equal(got, want):
            correct += 1
        else:
            wrong.append(
                {
                    "key": record_key,
                    "path": ptr,
                    "expected": want,
                    "actual": None if got is MISSING else got,
                    "missing": got is MISSING,
                }
            )

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in actual:
        groups[key(record, identity)].append(record)
    duplicate_records = sum(max(0, len(group) - 1) for group in groups.values())
    exact = 0
    matched = 0
    missing = []
    for want in expected:
        k = key(want, identity)
        record_key = " | ".join(k)
        group = groups.get(k, [])
        unique = len(group) == 1
        got = group.pop(0) if group else MISSING
        before = len(wrong) + len(extra)
        if got is not MISSING:
            matched += 1
        else:
            missing.append(record_key)
        compare(got, want, schema, "", record_key)
        if got is not MISSING and unique and before == len(wrong) + len(extra):
            exact += 1
    extras = [record for group in groups.values() for record in group]
    count_extra += sum(leaves(record) for record in extras)
    return {
        "field_accuracy": correct / denominator if denominator else None,
        "penalized_accuracy": correct / (denominator + count_extra)
        if denominator + count_extra
        else None,
        "exact_record_accuracy": exact / max(1, len(expected)),
        "correct_fields": correct,
        "expected_fields": denominator,
        "rows_got": len(actual),
        "rows_expected": len(expected),
        "identity_precision": matched / max(1, len(actual)),
        "identity_recall": matched / max(1, len(expected)),
        "duplicate_records": duplicate_records,
        "missing_rows": missing,
        "extra_rows": extras,
        "wrong": wrong,
        "extra_values": extra,
    }
