"""Streaming OOXML checks. Values remain calamine's; only unambiguous display semantics are restored.

Never execute formulas, macros, external links or embedded objects. Scan every row, not a sample.
The cache is bounded and keyed by the local immutable file's stat, not by a user-supplied file name.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from forma.core.errors import UnsupportedFile

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_XML_BYTES = 512 * 1024 * 1024
CELL = re.compile(r"([A-Z]+)([1-9][0-9]*)\Z")


@dataclass
class SheetInspection:
    name: str
    hidden: bool = False
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
    replacements: list[tuple[int, int, str]] = field(default_factory=list)


def inspect_workbook(path: Path) -> tuple[SheetInspection, ...]:
    stat = path.stat()
    return _inspect(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _formats(z: ZipFile) -> list[str]:
    if "xl/styles.xml" not in z.namelist():
        return ["General"]
    root = ET.fromstring(z.read("xl/styles.xml"))
    formats = {0: "General", 1: "0", 9: "0%", 10: "0.00%"}
    for node in root.findall(f"{{{NS}}}numFmts/{{{NS}}}numFmt"):
        formats[int(node.attrib["numFmtId"])] = node.get("formatCode", "")
    return [formats.get(int(n.get("numFmtId", "0")), "") for n in root.findall(f"{{{NS}}}cellXfs/{{{NS}}}xf")]


def _display_value(raw: str, fmt: str) -> tuple[str, str] | None:
    """Only pure zero masks and percentages. No rounding, locale inference or arbitrary format parsing."""
    zero_mask = re.fullmatch(r"0{2,}", fmt)
    percent = re.fullmatch(r"0(?:\.0+)?%", fmt)
    if not zero_mask and not percent:
        return None
    try:
        value = Decimal(raw)
        if not value.is_finite():
            return None
        if zero_mask and value == value.to_integral() and value >= 0:
            return str(int(value)).zfill(len(fmt)), "zero_padded_cells"
        if percent:
            text = format(value * 100, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text + "%", "percentage_cells"
    except (InvalidOperation, ValueError):
        return None
    return None


def _scan_sheet(z: ZipFile, member: str, info: SheetInspection, formats: list[str]) -> None:
    with z.open(member) as source:
        sheet_data = None
        for event, node in ET.iterparse(source, events=("start", "end")):
            tag = node.tag.rsplit("}", 1)[-1]
            if event == "start":
                if tag == "sheetData":
                    sheet_data = node
                continue
            if tag == "c":
                ref = node.get("r", "")
                value = node.find(f"{{{NS}}}v")
                raw = value.text if value is not None else None
                formula = node.find(f"{{{NS}}}f")
                if formula is not None:
                    info.formula_cells += 1
                    if "[" in (formula.text or ""):
                        info.external_formula_cells += 1
                    # An explicit empty string result is a valid cache; an empty numeric <v/> is not.
                    if value is None or (not raw and node.get("t") != "str"):
                        info.formula_without_cache += 1
                        if len(info.formula_locations) < 10:
                            info.formula_locations.append(ref)
                if node.get("t") == "e":
                    info.error_cells += 1
                    if len(info.error_locations) < 10:
                        info.error_locations.append(f"{ref}: {raw}")
                elif raw and node.get("t", "n") == "n":
                    style = int(node.get("s", "0"))
                    fmt = formats[style] if style < len(formats) else ""
                    converted = _display_value(raw, fmt)
                    if (
                        not converted
                        and ("%" in fmt or re.search(r"(?<![.0])0{2,}", fmt))
                        and fmt not in info.display_format_warnings
                        and len(info.display_format_warnings) < 10
                    ):
                        info.display_format_warnings.append(fmt)
                    coord = CELL.fullmatch(ref)
                    if converted and coord:
                        col = 0
                        for char in coord[1]:
                            col = col * 26 + ord(char) - ord("A") + 1
                        info.replacements.append((int(coord[2]) - 1, col - 1, converted[0]))
                        setattr(info, converted[1], getattr(info, converted[1]) + 1)
                node.clear()
            elif tag == "row":
                info.hidden_rows += node.get("hidden") in ("1", "true")
                node.clear()
                if sheet_data is not None:
                    sheet_data.clear()
            elif tag == "col" and node.get("hidden") in ("1", "true"):
                info.hidden_columns += int(node.get("max", "1")) - int(node.get("min", "1")) + 1
            elif tag == "mergeCell" and len(info.merged_ranges) < 200:
                info.merged_ranges.append(node.get("ref", ""))


@lru_cache(maxsize=8)
def _inspect(path: str, size: int, mtime_ns: int) -> tuple[SheetInspection, ...]:
    del size, mtime_ns  # cache invalidation keys
    try:
        with ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist() if i.filename.endswith(".xml")) > MAX_XML_BYTES:
                raise UnsupportedFile("Workbook XML exceeds the inspection limit; split the workbook first")
            workbook = ET.fromstring(z.read("xl/workbook.xml"))
            if workbook.tag != f"{{{NS}}}workbook":
                raise UnsupportedFile("Unsupported workbook XML namespace; save as a standard .xlsx file")
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            targets = {n.get("Id"): n.get("Target", "") for n in rels if n.get("TargetMode") != "External"}
            chart_ids = {n.get("Id") for n in rels if n.get("Type", "").endswith("/chartsheet")}
            formats = _formats(z)
            sheets = []
            for sheet in workbook.findall(f"{{{NS}}}sheets/{{{NS}}}sheet"):
                if sheet.get(f"{{{REL}}}id") in chart_ids:
                    # Calamine retains chart-only names as empty sheets. Keep the same indices.
                    sheets.append(
                        SheetInspection(sheet.get("name", ""), sheet.get("state", "visible") != "visible")
                    )
                    continue
                target = targets.get(sheet.get(f"{{{REL}}}id"), "")
                member = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/" + target)
                if not member.startswith("xl/") or member not in z.namelist():
                    raise UnsupportedFile("Workbook has an unsupported or missing worksheet")
                if not member.endswith(".xml"):
                    raise UnsupportedFile("Workbook sheet is not XML; save as .xlsx")
                info = SheetInspection(sheet.get("name", ""), sheet.get("state", "visible") != "visible")
                _scan_sheet(z, member, info, formats)
                sheets.append(info)
            return tuple(sheets)
    except (BadZipFile, KeyError, ET.ParseError, ValueError) as e:
        raise UnsupportedFile("Workbook structure is damaged or unsupported; save a new .xlsx copy") from e


def require_cached_values(info: SheetInspection) -> None:
    details = {
        "sheet": info.name,
        "formula_cells": info.formula_locations,
        "error_cells": info.error_locations,
    }
    if info.formula_without_cache:
        raise UnsupportedFile(
            f"Sheet '{info.name}' has {info.formula_without_cache} formulas without cached results; "
            "recalculate and save in Excel, or export values, then upload again",
            details=details,
        )
    if info.error_cells:
        raise UnsupportedFile(
            f"Sheet '{info.name}' has {info.error_cells} Excel error cells; fix the source and upload again",
            details=details,
        )
