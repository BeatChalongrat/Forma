"""Structural fingerprints and drift classification (ADR-006).

A fingerprint summarises *shape*, not content: sheets → tables → normalised headers + inferred types.
Two uploads of the same ERP export in different rounds produce the same fingerprint; a renamed
column, a new sheet or an unrelated spreadsheet produce a measurable, classified difference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from forma.core.hashing import stable_hash
from forma.profiling.profile import WorkbookProfile


def norm_sheet(name: str) -> str:
    """Sheet/file names carry dates and counters ('stock_20261001') that change every round."""
    s = re.sub(r"[\d๐-๙]+", "", name.strip().lower())
    return re.sub(r"[\s_\-.]+", " ", s).strip() or "sheet"


def norm_header(h: str) -> str:
    s = h.strip().lower()
    s = re.sub(r"[\s_\-./()\[\]:]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


@dataclass
class TableShape:
    sheet: str
    headers: list[str]
    types: list[str]
    header_row: int
    n_rows_band: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Fingerprint:
    strict: str  # hash incl. sheet names + header positions
    loose: str  # hash of sorted normalised headers only (sheet/position independent)
    tables: list[TableShape]
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "strict": self.strict,
            "loose": self.loose,
            "kind": self.kind,
            "tables": [t.to_dict() for t in self.tables],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fingerprint:
        return cls(d["strict"], d["loose"], [TableShape(**t) for t in d["tables"]], d.get("kind", "excel"))

    def all_headers(self) -> list[str]:
        return [h for t in self.tables for h in t.headers]


def _band(n: int) -> str:
    if n < 100:
        return "xs"
    if n < 1_000:
        return "s"
    if n < 10_000:
        return "m"
    if n < 100_000:
        return "l"
    return "xl"


def fingerprint_profile(profile: WorkbookProfile) -> Fingerprint:
    tables: list[TableShape] = []
    for t in profile.tables:
        if t.n_rows < 3 or len(t.columns) < 2:
            continue  # noise blocks don't define the format
        tables.append(
            TableShape(
                sheet=norm_sheet(t.sheet),
                headers=[norm_header(c.name) for c in t.columns],
                types=[c.inferred_type for c in t.columns],
                header_row=t.table.header_row,
                n_rows_band=_band(t.n_rows),
            )
        )
    strict = stable_hash(sorted([t.sheet, t.headers] for t in tables))
    loose = stable_hash(sorted({h for t in tables for h in t.headers}))
    return Fingerprint(strict, loose, tables, profile.meta.kind)


def merge_fingerprints(fps: list[Fingerprint]) -> Fingerprint:
    """Round-level fingerprint: all tables of all files delivered in one round."""
    tables = [t for fp in fps for t in fp.tables]
    strict = stable_hash(sorted([t.sheet, t.headers] for t in tables))
    loose = stable_hash(sorted({h for t in tables for h in t.headers}))
    kind = "excel" if any(fp.kind == "excel" for fp in fps) else "csv"
    return Fingerprint(strict, loose, tables, kind)


# --------------------------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------------------------
DRIFT_KINDS = (
    "identical",
    "header_moved",
    "columns_renamed",
    "columns_added",
    "columns_removed",
    "types_changed",
    "sheets_added",
    "sheets_removed",
    "unrelated_file",
)


@dataclass
class DriftReport:
    classification: str
    similarity: float
    changes: list[dict[str, Any]] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)  # old header → new header (best guess)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def is_identical(self) -> bool:
        return self.classification == "identical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "similarity": round(self.similarity, 4),
            "changes": self.changes,
            "renamed": self.renamed,
            "added": self.added,
            "removed": self.removed,
        }


def compare(previous: Fingerprint, current: Fingerprint, *, unrelated_threshold: float = 0.35) -> DriftReport:
    if previous.strict == current.strict:
        return DriftReport("identical", 1.0)
    prev_h = set(previous.all_headers())
    cur_h = set(current.all_headers())
    common = prev_h & cur_h
    union = prev_h | cur_h
    jaccard = len(common) / len(union) if union else 1.0
    removed = sorted(prev_h - cur_h)
    added = sorted(cur_h - prev_h)

    # try to pair removed/added as renames
    renamed: dict[str, str] = {}
    for old in list(removed):
        best, score = None, 0.0
        for new in added:
            s = fuzz.token_set_ratio(old, new) / 100.0
            if s > score:
                best, score = new, s
        if best is not None and score >= 0.6 and best not in renamed.values():
            renamed[old] = best
    removed = [h for h in removed if h not in renamed]
    added = [h for h in added if h not in renamed.values()]
    rename_credit = len(renamed) / len(union) if union else 0.0
    similarity = min(1.0, jaccard + 0.75 * rename_credit)

    changes: list[dict[str, Any]] = []
    prev_sheets = {t.sheet for t in previous.tables}
    cur_sheets = {t.sheet for t in current.tables}
    for sh in sorted(cur_sheets - prev_sheets):
        changes.append({"kind": "sheets_added", "sheet": sh})
    for sh in sorted(prev_sheets - cur_sheets):
        changes.append({"kind": "sheets_removed", "sheet": sh})
    for old, new in renamed.items():
        changes.append({"kind": "columns_renamed", "from": old, "to": new})
    for h in added:
        changes.append({"kind": "columns_added", "header": h})
    for h in removed:
        changes.append({"kind": "columns_removed", "header": h})
    # header position / types for tables on the same sheet
    prev_by_sheet = {t.sheet: t for t in previous.tables}
    for t in current.tables:
        p = prev_by_sheet.get(t.sheet)
        if p is None:
            continue
        if p.header_row != t.header_row:
            changes.append(
                {"kind": "header_moved", "sheet": t.sheet, "from": p.header_row, "to": t.header_row}
            )
        ptypes = dict(zip(p.headers, p.types, strict=False))
        for h, ty in zip(t.headers, t.types, strict=False):
            if (
                h in ptypes
                and ptypes[h] != ty
                and "mixed" not in (ty, ptypes[h])
                and "empty" not in (ty, ptypes[h])
            ):
                changes.append({"kind": "types_changed", "header": h, "from": ptypes[h], "to": ty})

    # main-table check: the biggest previous table must find a counterpart; shared generic satellite
    # tables (e.g. an identical 'Promotions' sheet) must not make an unrelated file look related.
    main_prev = max(
        previous.tables, key=lambda t: ("xl", "l", "m", "s", "xs").index(t.n_rows_band) * -1, default=None
    )
    main_sim = 1.0
    if main_prev is not None and current.tables:
        ph = set(main_prev.headers)
        main_sim = max(
            (
                len(ph & set(t.headers)) / len(ph | set(t.headers))
                for t in current.tables
                if ph | set(t.headers)
            ),
            default=0.0,
        )
        # count renames as partial matches
        if renamed:
            ren = {renamed.get(h, h) for h in ph}
            main_sim = max(
                main_sim,
                max(
                    (len(ren & set(t.headers)) / len(ren | set(t.headers)) for t in current.tables),
                    default=0.0,
                ),
            )
    if main_sim < 0.3 or (similarity < unrelated_threshold and len(common) <= max(1, len(prev_h) // 4)):
        return DriftReport("unrelated_file", min(similarity, main_sim), changes, renamed, added, removed)
    if not changes:
        # same headers, different sheet names/order → treat as moved
        return DriftReport("header_moved", similarity, [{"kind": "header_moved"}], renamed, added, removed)
    priority = [
        "sheets_removed",
        "columns_removed",
        "columns_renamed",
        "types_changed",
        "sheets_added",
        "columns_added",
        "header_moved",
    ]
    kinds = {c["kind"] for c in changes}
    classification = next(k for k in priority if k in kinds)
    return DriftReport(classification, similarity, changes, renamed, added, removed)
