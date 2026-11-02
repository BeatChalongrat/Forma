"""Deterministic plausibility checks on transformed records (ADR-058).

A mapping can be valid against the contract and still wrong in a way a person would see at once: a date in
the year 9456, a promotion that ends before it starts, every price zero, one SKU for every record. These rules
name such results so the agent's self-check and the proposal's findings can show them. Fields are found by
meaning through the schema registry, never by name (rule 1).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from forma.schema_registry import TargetSchema

YEAR_MIN, YEAR_MAX = 1990, 2100
_YEAR_RE = re.compile(r"^(\d{4})-\d{2}-\d{2}")
_DATE_SEMANTICS = ("price.effective_from", "price.effective_to", "promo.start_date", "promo.end_date")
_RANGES = (("price.effective_from", "price.effective_to"), ("promo.start_date", "promo.end_date"))
_POSITIVE = ("price.amount", "promo.price")
_IDENTITY_MIN_RECORDS = 10


def values_at(record: dict[str, Any], pointer: str) -> Iterator[Any]:
    """Every value a pointer with ``*`` segments reaches; a missing branch yields nothing."""
    segs = [s for s in pointer.split("/") if s]

    def walk(cur: Any, i: int) -> Iterator[Any]:
        if i == len(segs):
            yield cur
            return
        seg = segs[i]
        if seg == "*":
            if isinstance(cur, list):
                for item in cur:
                    yield from walk(item, i + 1)
        elif isinstance(cur, dict) and seg in cur:
            yield from walk(cur[seg], i + 1)

    return walk(record, 0)


def _year(v: Any) -> int | None:
    m = _YEAR_RE.match(str(v)) if v is not None else None
    return int(m.group(1)) if m else None


def _items(records: list[dict[str, Any]], pointer: str) -> list[dict[str, Any]]:
    """The objects that hold a collection field (the records themselves for a root field)."""
    parent = pointer.rsplit("/*/", 1)[0] if "/*/" in pointer else None
    if parent is None:
        return records
    return [item for r in records for item in values_at(r, parent + "/*") if isinstance(item, dict)]


def check(records: list[dict[str, Any]], schema: TargetSchema) -> list[str]:
    """Human-readable problems, most telling first; empty when the output looks like real data."""
    if not records:
        return []
    out: list[str] = []
    n = len(records)
    # dates: a year outside the window is a sentinel or a misread (Buddhist year, day/month swapped)
    for f in schema.leaf_fields():
        if f.fmt != "date" and (f.semantic or "") not in _DATE_SEMANTICS:
            continue
        vals = [v for r in records for v in values_at(r, f.pointer) if v not in (None, "")]
        bad = [v for v in vals if (y := _year(v)) is not None and not YEAR_MIN <= y <= YEAR_MAX]
        if bad:
            out.append(
                f"{f.pointer}: {len(bad)} of {len(vals)} dates have a year outside {YEAR_MIN}-{YEAR_MAX} "
                f"(e.g. {bad[0]}): a Buddhist year read as Christian, or an open-ended sentinel for null"
            )
    # a start after its end
    for start_sem, end_sem in _RANGES:
        for start in [schema.semantic_field(start_sem)] + [
            schema.semantic_field(start_sem, in_collection=c) for c in _collections(schema)
        ]:
            if start is None:
                continue
            end = schema.semantic_field(end_sem, in_collection=start.in_collection)
            if end is None:
                continue
            start_rel, end_rel = _relative(start.pointer), _relative(end.pointer)
            pairs = [
                (item.get(start_rel), item.get(end_rel))
                for item in _items(records, start.pointer)
                if item.get(start_rel) and item.get(end_rel)
            ]
            wrong = [(a, b) for a, b in pairs if str(a) > str(b)]
            if wrong:
                out.append(
                    f"{start.pointer} is after {end.pointer} in {len(wrong)} of {len(pairs)} items "
                    f"(e.g. {wrong[0][0]} > {wrong[0][1]}); the day and month may be swapped"
                )
    # money at or below zero
    for sem in _POSITIVE:
        for p in sorted(schema.semantic_pointers(sem)):
            vals = [v for r in records for v in values_at(r, p) if isinstance(v, int | float)]
            nonpos = sum(1 for v in vals if v <= 0)
            if vals and nonpos and nonpos / len(vals) > 0.2:
                out.append(f"{p}: {nonpos} of {len(vals)} values are zero or negative")
    # identity with almost no distinct values: the wrong column was taken for the item code
    if n >= _IDENTITY_MIN_RECORDS:
        for key in schema.identity_keys:
            vals = [v for r in records for v in values_at(r, key) if v not in (None, "")]
            distinct = len({str(v) for v in vals})
            if vals and distinct <= max(1, len(vals) // 10):
                out.append(f"{key}: only {distinct} distinct value(s) across {len(vals)} records")
    # a price collection that some records have and others do not: a second price sheet taken for the only one
    for p in sorted(
        {f.in_collection for f in schema.leaf_fields() if f.semantic == "price.amount" and f.in_collection}
    ):
        rel = p.lstrip("/")
        without = sum(1 for r in records if not r.get(rel))
        if 0 < without < n:
            out.append(f"{p}: {without} of {n} records have no price while the others do")
    # required fields empty in most records
    for f in schema.leaf_fields():
        if not f.required or f.in_collection:
            continue
        empty = sum(1 for r in records if all(v in (None, "", []) for v in values_at(r, f.pointer)))
        if empty and empty / n > 0.5:
            out.append(f"{f.pointer}: empty in {empty} of {n} records")
    return out


def _collections(schema: TargetSchema) -> list[str]:
    return sorted({f.in_collection for f in schema.leaf_fields() if f.in_collection})


def _relative(pointer: str) -> str:
    return pointer.rsplit("/*/", 1)[1] if "/*/" in pointer else pointer.lstrip("/")
