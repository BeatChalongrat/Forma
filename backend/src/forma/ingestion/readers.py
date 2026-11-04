"""Read Excel / CSV files into raw string grids.

Two engines on purpose (ADR-003):
* **fastexcel (calamine)** streams cell values fast — used for all data reads, always as strings so
  that parsing is explicit, deterministic and Thai-aware (done by DSL ops, not by Excel heuristics).
* Streaming **OOXML** inspection checks all rows for formulas/errors and preserves simple numeric
  identifier/percentage formats. Formulas are never calculated; missing cached results fail closed.
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from forma.core.errors import UnsupportedFile

EXCEL_EXT = {".xlsx", ".xlsm", ".xls", ".xlsb"}
CSV_EXT = {".csv", ".txt", ".tsv"}


@dataclass
class SheetMeta:
    name: str
    index: int
    hidden: bool = False
    n_rows: int = 0
    n_cols: int = 0
    merged_ranges: list[str] = field(default_factory=list)
    formula_cells: int = 0
    formula_without_cache: int = 0
    error_cells: int = 0
    formula_locations: list[str] = field(default_factory=list)
    error_locations: list[str] = field(default_factory=list)
    hidden_rows: int = 0
    hidden_columns: int = 0
    external_formula_cells: int = 0
    zero_padded_cells: int = 0
    percentage_cells: int = 0
    display_format_warnings: list[str] = field(default_factory=list)
    structure_scan_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": self.index,
            "hidden": self.hidden,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "merged_ranges": self.merged_ranges,
            "formula_cells": self.formula_cells,
            "formula_without_cache": self.formula_without_cache,
            "error_cells": self.error_cells,
            "formula_locations": self.formula_locations,
            "error_locations": self.error_locations,
            "hidden_rows": self.hidden_rows,
            "hidden_columns": self.hidden_columns,
            "external_formula_cells": self.external_formula_cells,
            "zero_padded_cells": self.zero_padded_cells,
            "percentage_cells": self.percentage_cells,
            "display_format_warnings": self.display_format_warnings,
            "structure_scan_complete": self.structure_scan_complete,
        }


@dataclass
class WorkbookMeta:
    path: Path
    kind: str  # excel | csv
    sheets: list[SheetMeta]
    csv_encoding: str | None = None
    csv_delimiter: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.path.name,
            "kind": self.kind,
            "sheets": [s.to_dict() for s in self.sheets],
            "csv_encoding": self.csv_encoding,
            "csv_delimiter": self.csv_delimiter,
        }


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in EXCEL_EXT:
        return "excel"
    if ext in CSV_EXT:
        return "csv"
    raise UnsupportedFile(f"Unsupported file type {ext!r}", details={"file": path.name})


# --------------------------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------------------------
def _fastexcel_reader(path: Path) -> Any:
    import fastexcel

    try:
        return fastexcel.read_excel(str(path))
    except Exception as e:
        raise UnsupportedFile(
            f"{path.name}: cannot read workbook; check for damage, encryption or an unsupported format",
            details={"file": path.name},
        ) from e


def read_excel_meta(path: Path) -> WorkbookMeta:
    reader = _fastexcel_reader(path)
    sheets: list[SheetMeta] = []
    for i, name in enumerate(reader.sheet_names):
        sh = reader.load_sheet(i, header_row=None, skip_rows=0, n_rows=1, dtypes="string")
        cols = sh.selected_columns
        width = max((c.absolute_index + 1 for c in cols), default=0)
        sheets.append(SheetMeta(name=name, index=i, n_rows=int(sh.total_height or 0), n_cols=width))

    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        _scan_structure_openpyxl(path, sheets)
    return WorkbookMeta(path=path, kind="excel", sheets=sheets)


def _scan_structure_openpyxl(path: Path, sheets: list[SheetMeta]) -> None:
    """Full streaming OOXML scan; retain the historic helper name for callers."""
    from forma.ingestion.ooxml import inspect_workbook

    by_name = {s.name: s for s in sheets}
    for inspected in inspect_workbook(path):
        meta = by_name.get(inspected.name)
        if meta is not None:
            for name, value in vars(inspected).items():
                if name not in ("name", "replacements", "invalid_columns"):
                    setattr(meta, name, value.copy() if isinstance(value, list) else value)
            meta.structure_scan_complete = True


def read_excel_sheet_raw(
    path: Path,
    sheet: int | str,
    *,
    n_rows: int | None = None,
    skip_rows: int = 0,
    validate_cells: bool = True,
) -> pl.DataFrame:
    """Whole sheet as strings, columns named c0..cN, no header interpretation."""
    inspected = None
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        from forma.ingestion.ooxml import inspect_workbook, require_cached_values

        sheets = inspect_workbook(path)
        inspected = sheets[sheet] if isinstance(sheet, int) else next(s for s in sheets if s.name == sheet)
        if validate_cells:
            require_cached_values(inspected)
    reader = _fastexcel_reader(path)
    sh = reader.load_sheet(sheet, header_row=None, n_rows=n_rows, skip_rows=skip_rows, dtypes="string")
    df: pl.DataFrame = sh.to_polars()
    physical = [c.absolute_index for c in sh.selected_columns]
    df = df.rename({c: f"c{i}" for c, i in zip(df.columns, physical, strict=True)})
    if physical:
        width = max(physical) + 1
        missing = [pl.lit(None, dtype=pl.String).alias(f"c{i}") for i in range(width) if i not in physical]
        df = df.with_columns(missing).select([f"c{i}" for i in range(width)])
    if inspected and inspected.replacements:
        patches: dict[int, dict[int, str]] = {}
        for row, col, value in inspected.replacements:
            if skip_rows <= row < skip_rows + df.height and col < df.width:
                patches.setdefault(col, {})[row - skip_rows] = value
        # Replace only affected columns; keep ordinary large sheets on the fast calamine path.
        columns = []
        for col, changes in patches.items():
            values = df[f"c{col}"].to_list()
            for row, value in changes.items():
                values[row] = value
            columns.append(pl.Series(f"c{col}", values, dtype=pl.String))
        df = df.with_columns(columns)
    return _clean_strings(df)


# --------------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------------
_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp874", "tis-620", "cp1252", "latin-1")


def sniff_csv(path: Path, encoding: str = "auto", delimiter: str = "auto") -> tuple[str, str]:
    with path.open("rb") as f:
        raw = f.read(256 * 1024)
    enc = encoding
    if enc == "auto":
        enc = _detect_encoding(raw)
    try:
        text = codecs.getincrementaldecoder(enc)(errors="strict").decode(raw, final=False)
    except (UnicodeError, LookupError) as e:
        raise UnsupportedFile(f"{path.name}: cannot decode CSV with encoding {enc}") from e
    delim = delimiter
    if delim == "auto":
        sample = "\n".join(text.splitlines()[:50])
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            counts = {d: sample.count(d) for d in ",;\t|"}
            delim = max(counts, key=lambda k: counts[k]) if any(counts.values()) else ","
    return enc, "\t" if delim == "\\t" else delim


def _detect_encoding(raw: bytes) -> str:
    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return "utf-32"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(raw, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # Thai single-byte encodings: cp874 is a superset of tis-620
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None and best.encoding:
            enc = best.encoding.lower()
            if enc in {"cp874", "tis_620", "tis-620", "iso8859_11", "iso-8859-11"}:
                return "cp874"
            return enc
    except Exception:  # pragma: no cover
        pass
    return "cp874"


def read_csv_meta(path: Path, encoding: str = "auto", delimiter: str = "auto") -> WorkbookMeta:
    enc, delim = sniff_csv(path, encoding, delimiter)
    df = read_csv_raw(path, encoding=enc, delimiter=delim)
    sheet = SheetMeta(name=path.stem, index=0, n_rows=df.height, n_cols=df.width)
    return WorkbookMeta(path=path, kind="csv", sheets=[sheet], csv_encoding=enc, csv_delimiter=delim)


def read_csv_raw(
    path: Path,
    *,
    encoding: str = "auto",
    delimiter: str = "auto",
    n_rows: int | None = None,
    quote_char: str = '"',
) -> pl.DataFrame:
    enc, delim = sniff_csv(path, encoding, delimiter)
    try:
        text = path.read_text(encoding=enc, errors="strict")
    except (UnicodeError, LookupError) as e:
        raise UnsupportedFile(f"{path.name}: cannot decode CSV with encoding {enc}") from e
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text), delimiter=delim, quotechar=quote_char or '"', strict=True)
    width = 0
    try:
        for i, row in enumerate(reader):
            if n_rows is not None and i >= n_rows:
                break
            rows.append(row)
            width = max(width, len(row))
    except csv.Error as e:
        raise UnsupportedFile(f"{path.name}: malformed CSV near line {reader.line_num}: {e}") from e
    if not rows:
        return pl.DataFrame()
    data = {f"c{j}": [(r[j] if j < len(r) else None) for r in rows] for j in range(width)}
    df = pl.DataFrame(data, schema={f"c{j}": pl.String for j in range(width)})
    return _clean_strings(df)


# --------------------------------------------------------------------------------------------
# Generic
# --------------------------------------------------------------------------------------------
def read_meta(path: Path, *, csv_encoding: str = "auto", csv_delimiter: str = "auto") -> WorkbookMeta:
    kind = file_kind(path)
    if kind == "excel":
        return read_excel_meta(path)
    return read_csv_meta(path, csv_encoding, csv_delimiter)


def read_raw(
    path: Path,
    sheet: int | str | None = None,
    *,
    n_rows: int | None = None,
    csv_encoding: str = "auto",
    csv_delimiter: str = "auto",
    csv_quote_char: str = '"',
    validate_cells: bool = True,
) -> pl.DataFrame:
    kind = file_kind(path)
    if kind == "excel":
        return read_excel_sheet_raw(
            path, 0 if sheet is None else sheet, n_rows=n_rows, validate_cells=validate_cells
        )
    return read_csv_raw(
        path, encoding=csv_encoding, delimiter=csv_delimiter, n_rows=n_rows, quote_char=csv_quote_char
    )


_WS_RE = re.compile(r"[​‌‍﻿ ]")


def _clean_strings(df: pl.DataFrame) -> pl.DataFrame:
    """Strip zero-width / NBSP characters and turn blank strings into nulls."""
    if df.width == 0:
        return df
    exprs = []
    for c in df.columns:
        e = pl.col(c).cast(pl.String).str.replace_all(_WS_RE.pattern, " ").str.strip_chars()
        exprs.append(pl.when(e == "").then(None).otherwise(e).alias(c))
    return df.select(exprs)
