"""Read Excel / CSV files into raw string grids.

Two engines on purpose (ADR-003):
* **fastexcel (calamine)** streams cell values fast — used for all data reads, always as strings so
  that parsing is explicit, deterministic and Thai-aware (done by DSL ops, not by Excel heuristics).
* **openpyxl** is used only for *structure*: hidden sheets, merged ranges, formula presence.
  Structure scans are bounded (row/size limits) so a 100K-row file never goes through openpyxl.
"""

from __future__ import annotations

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
STRUCTURE_SCAN_MAX_BYTES = 25 * 1024 * 1024
MERGED_SCAN_MAX_BYTES = 2 * 1024 * 1024  # full openpyxl load only for small files
STRUCTURE_SCAN_MAX_ROWS = 400


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

    return fastexcel.read_excel(str(path))


def read_excel_meta(path: Path) -> WorkbookMeta:
    reader = _fastexcel_reader(path)
    sheets: list[SheetMeta] = []
    for i, name in enumerate(reader.sheet_names):
        sh = reader.load_sheet(i, header_row=None, n_rows=1, dtypes="string")
        sheets.append(SheetMeta(name=name, index=i, n_rows=int(sh.total_height or 0), n_cols=int(sh.width)))

    if path.suffix.lower() in {".xlsx", ".xlsm"} and path.stat().st_size <= STRUCTURE_SCAN_MAX_BYTES:
        _scan_structure_openpyxl(path, sheets)
    return WorkbookMeta(path=path, kind="excel", sheets=sheets)


def _scan_structure_openpyxl(path: Path, sheets: list[SheetMeta]) -> None:
    """Bounded structure scan straight from the xlsx zip (no openpyxl on the hot path).

    * hidden state  → xl/workbook.xml <sheet state="hidden">
    * formulas      → first STRUCTURE_SCAN_MAX_ROWS rows of each sheet xml (<f> elements), streaming
    * merged ranges → <mergeCell> elements; they sit after <sheetData>, so only for files ≤ MERGED_SCAN_MAX_BYTES
    """
    import zipfile
    from xml.etree import ElementTree as ET

    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:  # pragma: no cover
        return
    with zf:
        try:
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        except KeyError:  # pragma: no cover
            return
        rid_to_target = {r.get("Id"): r.get("Target", "") for r in rels}
        by_name = {s.name: s for s in sheets}
        small = path.stat().st_size <= MERGED_SCAN_MAX_BYTES
        for sh in wb.findall("m:sheets/m:sheet", ns):
            name = sh.get("name", "")
            meta = by_name.get(name)
            if meta is None:
                continue
            meta.hidden = sh.get("state", "visible") != "visible"
            target = rid_to_target.get(sh.get(f"{{{ns['r']}}}id"), "").lstrip("/")
            member = target if target.startswith("xl/") else "xl/" + target
            if member not in zf.namelist():
                continue
            formulas = 0
            no_cache = 0
            merged: list[str] = []
            rows_seen = 0
            with zf.open(member) as fh:
                for _event, el in ET.iterparse(fh, events=("end",)):
                    tag = el.tag.rsplit("}", 1)[-1]
                    if tag == "row":
                        rows_seen += 1
                        el.clear()
                        if rows_seen >= STRUCTURE_SCAN_MAX_ROWS and not small:
                            break
                    elif tag == "c" and rows_seen < STRUCTURE_SCAN_MAX_ROWS:
                        f = el.find("m:f", ns)
                        if f is not None:
                            formulas += 1
                            v = el.find("m:v", ns)
                            if v is None or not (v.text or "").strip():
                                no_cache += 1
                    elif tag == "mergeCell":
                        ref = el.get("ref")
                        if ref and len(merged) < 200:
                            merged.append(ref)
                    elif tag == "sheetData" and not small:
                        break
            meta.formula_cells = formulas
            meta.formula_without_cache = no_cache
            meta.merged_ranges = merged


def read_excel_sheet_raw(
    path: Path, sheet: int | str, *, n_rows: int | None = None, skip_rows: int = 0
) -> pl.DataFrame:
    """Whole sheet as strings, columns named c0..cN, no header interpretation."""
    reader = _fastexcel_reader(path)
    sh = reader.load_sheet(
        sheet, header_row=None, n_rows=n_rows, skip_rows=skip_rows or None, dtypes="string"
    )
    df: pl.DataFrame = sh.to_polars()
    df = df.rename({c: f"c{i}" for i, c in enumerate(df.columns)})
    return _clean_strings(df)


# --------------------------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------------------------
_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp874", "tis-620", "cp1252", "latin-1")


def sniff_csv(path: Path, encoding: str = "auto", delimiter: str = "auto") -> tuple[str, str]:
    raw = path.read_bytes()[: 256 * 1024]
    enc = encoding
    if enc == "auto":
        enc = _detect_encoding(raw)
    text = raw.decode(enc, errors="replace")
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
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8")
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
    text = path.read_text(encoding=enc, errors="replace")
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text), delimiter=delim, quotechar=quote_char or '"')
    width = 0
    for i, row in enumerate(reader):
        if n_rows is not None and i >= n_rows:
            break
        rows.append(row)
        width = max(width, len(row))
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
) -> pl.DataFrame:
    kind = file_kind(path)
    if kind == "excel":
        return read_excel_sheet_raw(path, 0 if sheet is None else sheet, n_rows=n_rows)
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
