"""Exports that are a spreadsheet in name only (ADR-074).

"Export to Excel" in several ERP systems writes text and calls it a workbook. NetSuite writes XML Spreadsheet
2003 named ``.xls``. SAP's ALV and Oracle's OTBI write an HTML table inside a MIME web archive (``.mhtml``,
``.mht``, or ``.xls``). SAP's "Text with Tabs" list is tab-separated text that users save as ``.xls``. Excel
opens all of them, so the people who send them see a spreadsheet.

Nothing here is executed, fetched or rendered. A cell is the text the file holds for it. An XML file that
declares a DTD or an entity is refused before any of it is expanded.
"""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.parsers import expat

from forma.core.errors import UnsupportedFile

MARKUP_EXT = {".mhtml", ".mht"}
MAX_BYTES = 64 * 1024 * 1024  # these exports are text; a larger one is exported as CSV or XLSX
MAX_SPAN = 1000  # a colspan or rowspan beyond this is not a table
HEAD = 64 * 1024
SS = "urn:schemas-microsoft-com:office:spreadsheet"
_ZIP = b"PK\x03\x04"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_META_CHARSET = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
_SPACE = re.compile(r"[\s ]+")
_MIDNIGHT = re.compile(r"T00:00:00(?:\.0+)?\Z")


@dataclass
class MarkupSheet:
    name: str
    rows: list[list[str | None]] = field(default_factory=list)
    hidden: bool = False
    hidden_rows: int = 0
    hidden_columns: int = 0
    merged_ranges: list[str] = field(default_factory=list)
    formula_cells: int = 0
    formula_without_cache: int = 0
    formula_locations: list[str] = field(default_factory=list)
    error_cells: int = 0
    error_locations: list[str] = field(default_factory=list)
    invalid_columns: dict[int, list[str]] = field(default_factory=dict)  # column -> cells that hold no value
    merged_down: list[tuple[int, int, int]] = field(default_factory=list)  # column, first row, last row

    @property
    def width(self) -> int:
        return max((len(r) for r in self.rows), default=0)


def _text(sample: bytes) -> str | None:
    """The start of a file as text, or None when it is not text."""
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return sample.decode("utf-16", errors="ignore")
    if b"\x00" in sample:
        return None
    for encoding in ("utf-8-sig", "cp874"):
        try:
            return sample.decode(encoding)
        except UnicodeDecodeError:
            continue
    return sample.decode("utf-8", errors="ignore")  # a sample may end inside a character


def container_of(sample: bytes) -> str:
    """What the bytes are, whatever the file is called: zip, ole, xmlss, mhtml, html, text or unknown."""
    if sample.startswith(_ZIP):
        return "zip"
    if sample.startswith(_OLE):
        return "ole"
    text = _text(sample[:HEAD])
    if text is None or not text.strip():
        return "unknown"
    low = text.lower()
    if SS in low and "<workbook" in low:
        return "xmlss"
    if low.lstrip().startswith("mime-version:") or "content-type: multipart/related" in low[:2000]:
        return "mhtml"
    if "<table" in low or (low.lstrip().startswith(("<html", "<!doctype html")) and "<body" in low):
        return "html"
    return "text"


def declares_dtd(sample: bytes) -> bool:
    """An XML spreadsheet has no DTD. One that declares it is refused at the door, and again by the parser."""
    low = (_text(sample[:HEAD]) or "").lower()
    return "<!doctype" in low or "<!entity" in low


def looks_tabular(sample: bytes) -> bool:
    """Tab-separated lines: what SAP's "Text with Tabs" writes. One stray tab is not a table."""
    text = _text(sample[:HEAD]) or ""
    return sum("\t" in line for line in text.splitlines()) >= 2


@lru_cache(maxsize=64)
def _container(path: str, size: int, mtime_ns: int) -> str:
    del size, mtime_ns  # cache invalidation keys
    with open(path, "rb") as f:
        return container_of(f.read(HEAD))


def path_container(path: Path) -> str:
    stat = path.stat()
    return _container(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def kind(path: Path) -> str | None:
    """``markup`` for a file read here, ``text`` for tab text named ``.xls``, None for anything else."""
    ext = path.suffix.lower()
    if ext not in MARKUP_EXT and ext != ".xls":
        return None
    try:
        what = path_container(path)
    except OSError:
        return None
    if what in ("xmlss", "mhtml", "html"):
        return "markup"
    return "text" if what == "text" and ext == ".xls" else None


# --------------------------------------------------------------------------------------------
# XML Spreadsheet 2003
# --------------------------------------------------------------------------------------------
def _refuse_declaration(*_: Any) -> None:
    raise UnsupportedFile(
        "The XML spreadsheet declares a DTD or entities, which FORMA does not expand; export it again"
    )


def _ref(row: int, col: int) -> str:
    name = ""
    n = col + 1
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(ord("A") + rem) + name
    return f"{name}{row + 1}"


def _put(rows: list[list[str | None]], row: int, col: int, value: str | None) -> None:
    while len(rows) <= row:
        rows.append([])
    cells = rows[row]
    if len(cells) <= col:
        cells.extend([None] * (col + 1 - len(cells)))
    cells[col] = value


def _span(raw: str | None, *, default: int = 0) -> int:
    try:
        return max(0, min(int(raw), MAX_SPAN)) if raw else default
    except ValueError:
        return default


class _Workbook:
    """Expat handlers for XML Spreadsheet 2003. Names arrive as ``namespace local``."""

    def __init__(self) -> None:
        self.sheets: list[MarkupSheet] = []
        self.sheet: MarkupSheet | None = None
        self.row = self.col = -1
        self.cell: dict[str, str] | None = None
        self.data: list[str] | None = None  # text of the <Data> being read, through any markup inside it
        self.data_type: str | None = None
        self.depth = 0  # elements open inside <Data>
        self.visible: list[str] | None = None

    def start(self, name: str, attrs: dict[str, str]) -> None:
        if self.data is not None:
            self.depth += 1
            return
        a = SS + " "
        if name == a + "Worksheet":
            self.sheet = MarkupSheet(attrs.get(a + "Name") or f"Sheet{len(self.sheets) + 1}")
            self.sheets.append(self.sheet)
            self.row = -1
        elif self.sheet is None:
            return
        elif name == a + "Row":
            index = _span(attrs.get(a + "Index"))
            self.row = index - 1 if index else self.row + 1
            self.col = -1
            self.sheet.hidden_rows += attrs.get(a + "Hidden") == "1"
        elif name == a + "Column" and attrs.get(a + "Hidden") == "1":
            self.sheet.hidden_columns += 1 + _span(attrs.get(a + "Span"))
        elif name == a + "Cell" and self.row >= 0:
            index = _span(attrs.get(a + "Index"))
            self.col = index - 1 if index else self.col + 1
            self.cell = attrs
            self.data_type = None
        elif name == a + "Data" and self.cell is not None:
            self.data, self.depth = [], 0
            self.data_type = attrs.get(a + "Type")
            self.cell = {**self.cell, "__value__": ""}
        elif name.endswith(" Visible"):
            self.visible = []

    def text(self, data: str) -> None:
        if self.data is not None:
            self.data.append(data)
        elif self.visible is not None:
            self.visible.append(data)

    def end(self, name: str) -> None:
        a = SS + " "
        if self.data is not None:
            if self.depth:
                self.depth -= 1
                return
            assert self.cell is not None
            self.cell["__value__"] = "".join(self.data)
            self.data = None
            return
        if self.sheet is None:
            return
        if name == a + "Cell" and self.cell is not None:
            self._cell(self.sheet, self.cell)
            self.cell = None
        elif name.endswith(" Visible") and self.visible is not None:
            state = "".join(self.visible).strip()
            self.sheet.hidden = state.startswith("Sheet") and state != "SheetVisible"
            self.visible = None
        elif name == a + "Worksheet":
            self.sheet = None

    def _cell(self, sheet: MarkupSheet, attrs: dict[str, str]) -> None:
        a = SS + " "
        row, col = self.row, self.col
        value = attrs.get("__value__") or None
        if self.data_type == "DateTime" and value:
            value = _MIDNIGHT.sub("", value).replace("T", " ")
        fault = None
        if a + "Formula" in attrs:
            sheet.formula_cells += 1
            if not value and self.data_type != "String":
                sheet.formula_without_cache += 1
                fault = "formula without cached result"
                if len(sheet.formula_locations) < 10:
                    sheet.formula_locations.append(_ref(row, col))
        if self.data_type == "Error":
            sheet.error_cells += 1
            fault = f"Excel error {value}"
            if len(sheet.error_locations) < 10:
                sheet.error_locations.append(f"{_ref(row, col)}: {value}")
        if fault:
            examples = sheet.invalid_columns.setdefault(col, [])
            if len(examples) < 10:
                examples.append(f"{_ref(row, col)}: {fault}")
        _put(sheet.rows, row, col, value)
        across, down = _span(attrs.get(a + "MergeAcross")), _span(attrs.get(a + "MergeDown"))
        if (across or down) and len(sheet.merged_ranges) < 200:
            sheet.merged_ranges.append(f"{_ref(row, col)}:{_ref(row + down, col + across)}")
        if down:
            sheet.merged_down.append((col, row, row + down))
        self.col += across


def _xmlss(path: Path) -> list[MarkupSheet]:
    book = _Workbook()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    parser.StartElementHandler = book.start
    parser.EndElementHandler = book.end
    parser.CharacterDataHandler = book.text
    # a DTD is where entities are declared: none is expanded, because none is accepted
    parser.StartDoctypeDeclHandler = _refuse_declaration
    parser.EntityDeclHandler = _refuse_declaration
    try:
        with path.open("rb") as source:
            parser.ParseFile(source)
    except expat.ExpatError as e:
        raise UnsupportedFile(
            f"{path.name}: the XML spreadsheet is damaged ({e}); export it again",
            details={"file": path.name},
        ) from e
    return book.sheets


# --------------------------------------------------------------------------------------------
# HTML tables, alone or inside a MIME web archive
# --------------------------------------------------------------------------------------------
@dataclass
class _Table:
    rows: list[list[str | None]] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    merged_down: list[tuple[int, int, int]] = field(default_factory=list)
    taken: dict[int, set[int]] = field(default_factory=dict)  # row -> columns a rowspan from above holds
    has_child: bool = False
    row: int = -1
    col: int = 0
    cell: list[str] | None = None
    span: tuple[int, int] = (1, 1)


class _Tables(HTMLParser):
    """Each table that holds no other table is a sheet. A table that only lays out the page is not data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[_Table] = []
        self.done: list[_Table] = []
        self.skip = 0

    def _close_cell(self, table: _Table) -> None:
        if table.cell is None:
            return
        value = _SPACE.sub(" ", "".join(table.cell)).strip() or None
        held = table.taken.get(table.row, set())
        while table.col in held:
            table.col += 1
        across, down = table.span
        _put(table.rows, table.row, table.col, value)
        for extra in range(1, down):
            table.taken.setdefault(table.row + extra, set()).update(range(table.col, table.col + across))
        if (across > 1 or down > 1) and len(table.merged) < 200:
            first, last = _ref(table.row, table.col), _ref(table.row + down - 1, table.col + across - 1)
            table.merged.append(f"{first}:{last}")
        if down > 1:
            table.merged_down.append((table.col, table.row, table.row + down - 1))
        table.col += across
        table.cell = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag == "table":
            if self.stack:
                self.stack[-1].has_child = True
            self.stack.append(_Table())
            return
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == "tr":
            self._close_cell(table)
            table.row += 1
            table.col = 0
        elif tag in ("td", "th"):
            self._close_cell(table)
            if table.row < 0:  # cells with no <tr>: still one row
                table.row = 0
            given = dict(attrs)
            table.span = (
                _span(given.get("colspan"), default=1) or 1,
                _span(given.get("rowspan"), default=1) or 1,
            )
            table.cell = []
        elif tag == "br" and table.cell is not None:
            table.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in ("td", "th", "tr"):
            self._close_cell(table)
        elif tag == "table":
            self._close_cell(table)
            self.stack.pop()
            if not table.has_child and any(any(c is not None for c in r) for r in table.rows):
                self.done.append(table)

    def handle_data(self, data: str) -> None:
        if self.stack and not self.skip and self.stack[-1].cell is not None:
            self.stack[-1].cell.append(data)


def _decode(data: bytes, declared: str | None = None) -> str:
    """HTML as text: the charset the part or the page declares, else UTF-8, else the Thai Windows code page."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    meta = _META_CHARSET.search(data[:4096])
    candidates = [declared, meta.group(1).decode("ascii") if meta else None, "utf-8-sig", "cp874"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _html_pages(path: Path, what: str) -> list[str]:
    data = path.read_bytes()
    if what != "mhtml":
        return [_decode(data)]
    message = email.message_from_bytes(data, policy=email.policy.compat32)
    pages = []
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes):
            pages.append(_decode(payload, part.get_content_charset()))
    return pages


def _html(path: Path, what: str) -> list[MarkupSheet]:
    sheets: list[MarkupSheet] = []
    for page in _html_pages(path, what):
        parser = _Tables()
        parser.feed(page)
        parser.close()
        for table in parser.done:
            sheets.append(
                MarkupSheet(
                    f"Sheet{len(sheets) + 1}",
                    table.rows,
                    merged_ranges=table.merged,
                    merged_down=table.merged_down,
                )
            )
    return sheets


@lru_cache(maxsize=4)
def _read(path: str, size: int, mtime_ns: int) -> tuple[MarkupSheet, ...]:
    del mtime_ns  # cache invalidation key
    file = Path(path)
    if size > MAX_BYTES:
        raise UnsupportedFile(
            f"{file.name}: this export is text and larger than {MAX_BYTES // (1024 * 1024)} MB; "
            "export it as CSV or XLSX",
            details={"file": file.name},
        )
    what = path_container(file)
    sheets = _xmlss(file) if what == "xmlss" else _html(file, what)
    return tuple(sheets)


def read_sheets(path: Path) -> tuple[MarkupSheet, ...]:
    """Every sheet of an XML Spreadsheet, or every data table of an HTML export, as rows of text."""
    stat = path.stat()
    return _read(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
