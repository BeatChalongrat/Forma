"""Column / table / workbook profiling. Output feeds the AI agents, the fingerprint and the UI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from forma.engine.thai import THAI_DIGITS, looks_like_thai_date, parse_number_th_py
from forma.ingestion.readers import WorkbookMeta, read_meta, read_raw
from forma.profiling.tables import SECTION_COLUMN, TableCandidate, detect_tables, extract_table

_THAI_RE = re.compile(r"[฀-๿]")
_BARCODE_RE = re.compile(r"^\d{8}$|^\d{12,14}$")
_SKU_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,31}$")
_SLASH_DATE_RE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")
_BOOL_SET = {"y", "n", "yes", "no", "true", "false", "1", "0", "ใช่", "ไม่", "active", "inactive"}
TOP_VALUES = 10  # most frequent distinct values kept per column; the model sees them before it maps (ADR-059)


@dataclass
class ColumnProfile:
    name: str
    position: int
    inferred_type: str  # int | float | date | bool | string | mixed | empty
    null_ratio: float
    unique_ratio: float
    unique_count: int
    samples: list[str]
    min: str | None = None
    max: str | None = None
    avg_len: float = 0.0
    header_lang: str = "en"  # th | en | mixed
    hints: list[str] = field(
        default_factory=list
    )  # sku_like, barcode_like, price_like, thai_date, percent, ...
    top_values: list[list[Any]] = field(default_factory=list)  # [value, count] by frequency (ADR-059)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class TableProfile:
    sheet: str
    sheet_index: int
    table: TableCandidate
    columns: list[ColumnProfile]
    n_rows: int
    duplicate_key_candidates: list[str] = field(default_factory=list)
    key_candidates: list[str] = field(default_factory=list)
    file_id: str | None = None
    file_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "sheet_index": self.sheet_index,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "table": self.table.to_dict(),
            "columns": [c.to_dict() for c in self.columns],
            "n_rows": self.n_rows,
            "key_candidates": self.key_candidates,
            "duplicate_key_candidates": self.duplicate_key_candidates,
        }


@dataclass
class WorkbookProfile:
    meta: WorkbookMeta
    tables: list[TableProfile]

    def to_dict(self) -> dict[str, Any]:
        return {"meta": self.meta.to_dict(), "tables": [t.to_dict() for t in self.tables]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkbookProfile:
        from forma.ingestion.readers import SheetMeta

        m = d["meta"]
        meta = WorkbookMeta(
            path=Path(m.get("file_name", "file")),
            kind=m.get("kind", "excel"),
            sheets=[SheetMeta(**s) for s in m.get("sheets", [])],
            csv_encoding=m.get("csv_encoding"),
            csv_delimiter=m.get("csv_delimiter"),
        )
        tables = []
        for t in d.get("tables", []):
            cand = TableCandidate(**t["table"])
            cols = [ColumnProfile(**c) for c in t["columns"]]
            tables.append(
                TableProfile(
                    t["sheet"],
                    t["sheet_index"],
                    cand,
                    cols,
                    t["n_rows"],
                    t.get("duplicate_key_candidates", []),
                    t.get("key_candidates", []),
                    t.get("file_id"),
                    t.get("file_name"),
                )
            )
        return cls(meta, tables)


def merge_profiles(profiles: list[tuple[str, str, WorkbookProfile]]) -> WorkbookProfile:
    """Combine several files of one import round into a single profile (tables keep file refs)."""
    from forma.ingestion.readers import SheetMeta

    sheets: list[SheetMeta] = []
    tables: list[TableProfile] = []
    for file_id, file_name, prof in profiles:
        offset = len(sheets)
        for sh in prof.meta.sheets:
            sheets.append(SheetMeta(**{**sh.to_dict()}))
            sheets[-1].index = offset + sh.index
        for t in prof.tables:
            tables.append(
                TableProfile(
                    t.sheet,
                    offset + t.sheet_index,
                    t.table,
                    t.columns,
                    t.n_rows,
                    t.duplicate_key_candidates,
                    t.key_candidates,
                    file_id,
                    file_name,
                )
            )
    kind = "excel" if any(p.meta.kind == "excel" for _, _, p in profiles) else "csv"
    meta = WorkbookMeta(path=Path(profiles[0][1] if profiles else "round"), kind=kind, sheets=sheets)
    return WorkbookProfile(meta, tables)


def noise_tables(tables: list[TableProfile]) -> set[int]:
    """Indexes of blocks that do not define a file's format: under two columns, no rows, or a small
    secondary block (legend, footer) beside a sheet's main table. A sheet's main table counts even with
    one or two rows — a promotions sheet holding two promotions this round is data, not noise."""
    main: dict[int, int] = {}
    for i, t in enumerate(tables):
        j = main.get(t.sheet_index)
        if j is None or t.n_rows > tables[j].n_rows:
            main[t.sheet_index] = i
    return {
        i
        for i, t in enumerate(tables)
        if len(t.columns) < 2 or t.n_rows == 0 or (t.n_rows < 3 and main[t.sheet_index] != i)
    }


def _header_lang(name: str) -> str:
    th = bool(_THAI_RE.search(name))
    en = bool(re.search(r"[A-Za-z]", name))
    return "mixed" if th and en else "th" if th else "en"


def _date_order(values: pl.Series) -> list[str]:
    """What a whole column says about 03/04/2026. One value past 12 settles it: 31/10 can only be day-first
    and 10/31 only month-first. A Buddhist year comes from a Thai export, which writes the day first."""
    day = month = plain = False
    for value in values.to_list():
        m = _SLASH_DATE_RE.match(str(value).translate(THAI_DIGITS))
        if m is None:
            continue
        first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if first > 12 or year >= 2400 or 40 <= year < 100:
            day = True
        elif second > 12:
            month = True
        else:
            plain = True
    if day or month:
        return [*(["day_first"] if day else []), *(["month_first"] if month else [])]
    return ["ambiguous_date"] if plain else []


def profile_column(s: pl.Series, position: int, *, sample_n: int = 8) -> ColumnProfile:
    n = s.len()
    non_null = s.drop_nulls()
    nn = non_null.len()
    null_ratio = 1.0 if n == 0 else 1 - nn / n
    uniques = non_null.n_unique() if nn else 0
    vals = non_null.head(400).to_list()
    hints: list[str] = []
    inferred = "empty"
    mn = mx = None
    if nn:
        kinds = {"int": 0, "float": 0, "date": 0, "bool": 0, "string": 0}
        nums: list[float] = []
        for v in vals:
            sv = str(v).strip()
            num = parse_number_th_py(sv)
            if num is not None:
                nums.append(num)
                kinds["float" if ("." in sv or "%" in sv) else "int"] += 1
            elif looks_like_thai_date(sv):
                kinds["date"] += 1
            elif sv.lower() in _BOOL_SET:
                kinds["bool"] += 1
            else:
                kinds["string"] += 1
        total = len(vals)
        top = max(kinds, key=lambda k: kinds[k])
        share = kinds[top] / total
        if share >= 0.9:
            inferred = top
            if top == "int" and kinds["float"]:
                inferred = "float"
        elif kinds["int"] + kinds["float"] >= 0.9 * total:
            inferred = "float"
        else:
            inferred = "mixed" if share < 0.6 else top
        if nums and inferred in ("int", "float"):
            mn, mx = str(min(nums)), str(max(nums))
            if all(0 <= x <= 100 for x in nums) and any("%" in str(v) for v in vals):
                hints.append("percent")
            if max(nums) > 0 and inferred == "float":
                hints.append("price_like")
        if inferred == "string" or inferred == "int":
            if sum(bool(_BARCODE_RE.match(str(v))) for v in vals) / total > 0.8:
                hints.append("barcode_like")
            elif uniques / nn > 0.95 and sum(bool(_SKU_RE.match(str(v))) for v in vals) / total > 0.8:
                hints.append("sku_like")
        if kinds["date"] and kinds["date"] / total > 0.6:
            inferred = "date"
            if any(re.search(r"25[0-9]{2}|/[0-9]{2}$", str(v)) for v in vals):
                hints.append("thai_date")
            hints.extend(_date_order(non_null))
        if inferred == "string":
            avg = sum(len(str(v)) for v in vals) / total
            if avg > 40:
                hints.append("long_text")
            if any(_THAI_RE.search(str(v)) for v in vals):
                hints.append("thai_text")
            if any(
                re.search(r"\b(ชิ้น|แพ็ค|ลัง|กล่อง|โหล|pcs|pack|box|ctn|carton|dz)\b", str(v), re.I) for v in vals
            ):
                hints.append("unit_like")
    samples = [str(v) for v in non_null.unique(maintain_order=True).head(sample_n).to_list()]
    counts = non_null.value_counts(sort=True).head(TOP_VALUES) if nn else None
    top_values = [[str(v), int(k)] for v, k in counts.iter_rows()] if counts is not None else []
    return ColumnProfile(
        name=s.name,
        position=position,
        inferred_type=inferred,
        null_ratio=round(null_ratio, 4),
        unique_ratio=round(uniques / nn, 4) if nn else 0.0,
        unique_count=uniques,
        samples=samples,
        min=mn,
        max=mx,
        avg_len=round(sum(len(str(v)) for v in vals) / len(vals), 1) if vals else 0.0,
        header_lang=_header_lang(s.name),
        hints=hints,
        top_values=top_values,
    )


def profile_table(frame: pl.DataFrame, cand: TableCandidate, sheet: str, sheet_index: int) -> TableProfile:
    cols = [profile_column(frame[c], i) for i, c in enumerate(frame.columns)]
    if cand.section_rows:  # the heading rows became this column (ADR-058); say so to the mapper and the model
        for c in cols:
            if c.name == SECTION_COLUMN:
                c.hints.append("section_heading")
    key_candidates = [
        c.name
        for c in cols
        if c.null_ratio < 0.02 and c.unique_ratio >= 0.98 and c.inferred_type in ("string", "int")
    ]
    dup_candidates = [
        c.name
        for c in cols
        if c.null_ratio < 0.02
        and 0.5 <= c.unique_ratio < 0.98
        and ("sku_like" in c.hints or "barcode_like" in c.hints)
    ]
    return TableProfile(sheet, sheet_index, cand, cols, frame.height, dup_candidates, key_candidates)


def profile_workbook(path: Path, *, max_rows_per_table: int = 50_000) -> WorkbookProfile:
    meta = read_meta(path)
    tables: list[TableProfile] = []
    for sh in meta.sheets:
        # Profiling describes damaged cells too (metadata contains their coordinates). Execution
        # uses the strict reader and refuses missing caches/errors in any selected source sheet.
        raw = read_raw(path, sh.index if meta.kind == "excel" else None, validate_cells=False)
        if raw.height == 0:
            continue
        for cand in detect_tables(raw):
            spec = cand.to_spec()
            try:
                frame = extract_table(raw, spec)
            except Exception:  # pragma: no cover - defensive: a bad candidate must not kill profiling
                continue
            if frame.width == 0 or frame.height == 0:
                continue
            frame = frame.head(max_rows_per_table)
            tables.append(profile_table(frame, cand, sh.name, sh.index))
    return WorkbookProfile(meta, tables)
