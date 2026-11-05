"""Observed interpretation boundaries. Samples can veto reuse, never prove business meaning."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from forma.db.models import ImportRound, MappingVersion, SourceFile

VERSION = 1
_LABELS = {
    "country": {"country", "countrycode", "ประเทศ"},
    "locale": {"locale", "languagecode"},
    "currency": {"currency", "currencycode", "สกุลเงิน"},
    "unit": {"unit", "uom", "salesunit", "sellunit", "หน่วย", "หน่วยขาย"},
}


def observe(files: list[SourceFile]) -> dict[str, Any]:
    """Keep only explicit context columns and parsing evidence; exclude prices/SKUs/counts.

    Filenames, language and monetary symbols do not establish a country or currency.
    Unit token additions are handled by per-token questions, not a blanket format rejection.
    """
    columns: dict[str, Any] = {}
    for file in files:
        for table in (file.profile or {}).get("tables", []):
            for col in table.get("columns", []):
                name = str(col.get("name", ""))
                key = f"{table.get('sheet_index', 0)}:{name}"
                values = (col.get("observations") or {}).get("representative_values") or col.get(
                    "samples", []
                )
                normal = re.sub(r"[\s_\-./()]+", "", name).casefold()
                facts: dict[str, Any] = {}
                for kind, labels in _LABELS.items():
                    if normal in labels:
                        facts[kind] = sorted({str(v).strip().upper() for v in values if str(v).strip()})
                hints = set(col.get("hints", []))
                date = sorted(hints & {"day_first", "month_first", "ambiguous_date", "thai_date"})
                if date:
                    facts["date"] = date
                separators = set()
                for value in values:
                    token = str(value).strip()
                    if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+,\d{1,2}", token) or re.fullmatch(
                        r"[+-]?\d+,\d{1,2}", token
                    ):
                        separators.add("decimal_comma")
                    elif re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+\.\d{1,2}", token) or re.fullmatch(
                        r"[+-]?\d+\.\d{1,2}", token
                    ):
                        separators.add("decimal_dot")
                if separators:
                    facts["number"] = sorted(separators)
                if facts:
                    # Multiple files can share sheet names: retain all observations, not last writer.
                    existing = columns.setdefault(key, {})
                    for kind, observed in facts.items():
                        existing[kind] = sorted(set(existing.get(kind, [])) | set(observed))
    return {"version": VERSION, "basis": "profile observations, not all rows", "columns": columns}


def snapshot(session: Session, mapping: MappingVersion) -> dict[str, Any]:
    stored = (mapping.policy or {}).get("format_context")
    if isinstance(stored, dict):
        return stored
    origin = session.get(ImportRound, mapping.round_id) if mapping.round_id else None
    return observe(origin.files) if origin else {"version": VERSION, "columns": {}}


def differences(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    old, new = before.get("columns", {}), after.get("columns", {})
    changes = []
    for column in sorted(old.keys() | new.keys()):
        for kind in ("country", "locale", "currency", "date", "number"):
            a, b = old.get(column, {}).get(kind), new.get(column, {}).get(kind)
            if a != b and (a or b):
                changes.append({"column": column, "kind": kind, "before": a, "after": b})
    return changes


def compatible(session: Session, mapping: MappingVersion, files: list[SourceFile]) -> bool:
    return not changes(session, mapping, files)


def changes(session: Session, mapping: MappingVersion, files: list[SourceFile]) -> list[dict[str, Any]]:
    before, after = snapshot(session, mapping), observe(files)
    result = differences(before, after)
    # A source-value rule handles new unit tokens separately. A fixed unit must be reconsidered.
    fields = [
        *(mapping.document or {}).get("fields", []),
        *(f for c in (mapping.document or {}).get("collections", []) for f in c.get("fields", [])),
    ]
    constants = {
        str(f.get("expr", {}).get("value")).upper() for f in fields if f.get("expr", {}).get("op") == "const"
    }
    for column, observed in before.get("columns", {}).items():
        old_units = observed.get("unit", [])
        new_units = after.get("columns", {}).get(column, {}).get("unit", [])
        if constants.intersection(old_units) and old_units != new_units:
            result.append({"column": column, "kind": "fixed_unit", "before": old_units, "after": new_units})
    return result
