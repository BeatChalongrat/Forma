"""Table detection and extraction inside a raw sheet grid.

A sheet is *not* a table. Real ERP exports have title rows, metadata blocks, several tables stacked
vertically (separated by blank rows) or side by side (separated by blank columns), multi-row headers,
merged cells and trailing total rows. This module finds the tables and turns one into a clean frame.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from forma.mapping_dsl.models import TableSpec

SECTION_COLUMN = "section"  # the column a detected table's heading rows become (ADR-058)
SECTION_MAX_CHARS = 60
SECTION_MIN_ROWS = 2  # one lone one-cell line is a note and stays data (ADR-055); two or more are headings

# A total row is labelled by its first non-null cell. Short markers must stand alone ("Total",
# "Total:", "รวม 25 รายการ") so data that merely starts with the same letters ("SUM-01", "Summer…",
# "รวมมิตร") is kept; Thai compounds are unambiguous and match as prefixes.
TOTAL_LABEL_RE = (
    r"^(?:(?:grand\s*|sub\s*-?\s*|sum\s*)?total|sum|รวม)(?:$|[\s:(\[])"
    r"|^(?:รวมทั้งสิ้น|รวมทั้งหมด|ยอดรวม|รวมยอด|รวมเงิน|รวมสุทธิ|รวมหมวด|รวมกลุ่ม|รวมย่อย|รวมประเภท|รวมรายการ"
    r"|รวมสินค้า|ผลรวม|subtotal)"
)
# Any other cell with a word in it means the row is data, not a total ("Total Care Shampoo", "PCS") —
# except a count beside the label ("5 รายการ", "12 items"), which is part of the total.
_WORD_RE = r"[A-Za-zก-๎]{2,}"
_COUNT_RE = r"^\s*\d+\s*(?:รายการ|ชิ้น|แถว|items?|rows?|records?|lines?)\s*$"
_NUM_RE = re.compile(r"^[-+]?[\d,]*\.?\d+%?$|^[-+]?฿?[\d,]+(\.\d+)?$")
_DATE_RE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}")


@dataclass
class TableCandidate:
    """A rectangular region that looks like a table."""

    index: int
    header_row: int
    header_rows: int
    data_start: int
    data_end: int  # exclusive
    first_column: int
    last_column: int  # exclusive
    headers: list[str]
    n_rows: int
    score: float
    title: str | None = None
    notes: list[str] = field(default_factory=list)
    section_rows: int = 0  # one-cell heading rows inside the data (ADR-058)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "header_row": self.header_row,
            "header_rows": self.header_rows,
            "data_start": self.data_start,
            "data_end": self.data_end,
            "first_column": self.first_column,
            "last_column": self.last_column,
            "headers": self.headers,
            "n_rows": self.n_rows,
            "score": round(self.score, 3),
            "title": self.title,
            "notes": self.notes,
            "section_rows": self.section_rows,
        }

    def to_spec(self) -> TableSpec:
        return TableSpec(
            header_row=self.header_row,
            header_rows=self.header_rows,
            data_start=self.data_start,
            data_end=self.data_end,
            first_column=self.first_column,
            last_column=self.last_column,
            section_column=SECTION_COLUMN if self.section_rows >= SECTION_MIN_ROWS else None,
        )


# --------------------------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------------------------
def _grid(df: pl.DataFrame, max_rows: int) -> list[list[str | None]]:
    return [list(r) for r in df.head(max_rows).iter_rows()]


def _is_blank_row(row: list[str | None]) -> bool:
    return all(v is None for v in row)


def _cell_kind(v: str | None) -> str:
    if v is None:
        return "null"
    s = v.strip()
    if _NUM_RE.match(s.replace(" ", "")):
        return "num"
    if _DATE_RE.match(s):
        return "date"
    return "text"


def _row_blocks(grid: list[list[str | None]], gap: int) -> list[tuple[int, int]]:
    """Split rows into blocks separated by ≥gap blank rows. Returns [start, end) pairs."""
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    blanks = 0
    for i, row in enumerate(grid):
        if _is_blank_row(row):
            blanks += 1
            if start is not None and gap and blanks >= gap:
                blocks.append((start, i - blanks + 1))
                start = None
        else:
            if start is None:
                start = i
            blanks = 0
    if start is not None:
        end = len(grid)
        while end > start and _is_blank_row(grid[end - 1]):
            end -= 1
        blocks.append((start, end))
    return blocks


def _col_blocks(grid: list[list[str | None]], r0: int, r1: int) -> list[tuple[int, int]]:
    """Split a row block into side-by-side tables separated by fully blank columns."""
    if r1 <= r0:
        return []
    width = max(len(r) for r in grid[r0:r1])
    used = [any(j < len(grid[i]) and grid[i][j] is not None for i in range(r0, r1)) for j in range(width)]
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for j, u in enumerate(used):
        if u and start is None:
            start = j
        if not u and start is not None:
            blocks.append((start, j))
            start = None
    if start is not None:
        blocks.append((start, width))
    # merge blocks separated by a single blank column (often just a spacer inside one table)
    # A single blank column inside a table is far more common (spacer / deleted field) than two tables
    # placed exactly one column apart, so merge blocks separated by ≤1 blank column.
    merged: list[tuple[int, int]] = []
    for b in blocks:
        if merged and b[0] - merged[-1][1] <= 1:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return merged


def _header_score(row: list[str | None], below: list[list[str | None]]) -> float:
    cells = [c for c in row if c is not None]
    if len(cells) < 2:
        return 0.0
    fill = len(cells) / max(len(row), 1)
    text_ratio = sum(_cell_kind(c) == "text" for c in cells) / len(cells)
    distinct = len({c.strip().lower() for c in cells}) / len(cells)
    short = sum(len(c) <= 40 for c in cells) / len(cells)
    # data rows below should contain numbers/dates or at least differ in kind from header
    below_kinds = [_cell_kind(c) for r in below[:8] for c in r if c is not None]
    below_numish = (sum(k in ("num", "date") for k in below_kinds) / len(below_kinds)) if below_kinds else 0.0
    below_fill = (
        (sum(1 for r in below[:8] for c in r if c is not None) / max(1, sum(len(r) for r in below[:8])))
        if below
        else 0
    )
    return (
        0.3 * fill + 0.3 * text_ratio + 0.15 * distinct + 0.1 * short + 0.1 * below_numish + 0.05 * below_fill
    )


def _is_section_row(row: list[str | None]) -> bool:
    """A heading inside the data: one short text cell and nothing else (not a number, date or total)."""
    cells = [c for c in row if c is not None and str(c).strip() != ""]
    if len(cells) != 1:
        return False
    s = str(cells[0]).strip()
    return (
        len(s) <= SECTION_MAX_CHARS
        and _cell_kind(s) == "text"
        and bool(re.search(_WORD_RE, s))
        and not re.search(TOTAL_LABEL_RE, s.lower())
    )


def _same_header(a: list[str | None], b: list[str | None]) -> bool:
    return [normalize_header(c) for c in a if c is not None] == [
        normalize_header(c) for c in b if c is not None
    ]


def _join_pages(tables: list[TableCandidate]) -> list[TableCandidate]:
    """Blank rows can split a printed report into blocks. Blocks under one header are one table."""
    joined: list[TableCandidate] = []
    for t in sorted(tables, key=lambda x: x.header_row):
        last = joined[-1] if joined else None
        if (
            last is not None
            and last.headers == t.headers
            and (last.first_column, last.last_column) == (t.first_column, t.last_column)
        ):
            last.data_end = t.data_end
            last.n_rows = last.data_end - last.data_start
        else:
            joined.append(t)
    return joined


def detect_tables(df: pl.DataFrame, *, max_scan_rows: int = 60, gap: int = 2) -> list[TableCandidate]:
    """Find table regions in a raw string grid (``c0..cN`` columns)."""
    if df.height == 0 or df.width == 0:
        return []
    # Find separators across the whole sheet with a compact boolean column. Only materialise a
    # bounded header window per block; a second table after row 5000 must not disappear.
    blank = df.select(pl.all_horizontal(pl.all().is_null()).alias("blank"))["blank"].to_list()
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    blanks = 0
    for i, empty in enumerate(blank):
        if empty:
            blanks += 1
            if start is not None and gap and blanks >= gap:
                blocks.append((start, i - blanks + 1))
                start = None
        else:
            if start is None:
                start = i
            blanks = 0
    if start is not None:
        blocks.append((start, df.height - blanks))
    out: list[TableCandidate] = []
    for r0, r1 in blocks:
        block = df.slice(r0, r1 - r0)
        grid = _grid(block, max_scan_rows + 10)
        # Column occupancy also covers the full block, including sparse columns near the end.
        occupied = block.select(pl.all().is_not_null().any()).row(0)
        col_grid = [["used" if used else None for used in occupied]]
        for c0, c1 in _col_blocks(col_grid, 0, 1):
            sub = [row[c0:c1] + [None] * max(0, c1 - len(row)) for row in grid]
            if len(sub) < 2:
                continue
            best_i, best_s = 0, -1.0
            for i in range(min(len(sub) - 1, max_scan_rows)):
                s = _header_score(sub[i], sub[i + 1 :])
                if s > best_s + 1e-9:
                    best_i, best_s = i, s
            if best_s < 0.35:
                continue
            # A printed report repeats its header on every page. The later ones can score better, because
            # only the first has another page's title below it, but everything above them is data.
            best_i = next(i for i in range(best_i + 1) if _same_header(sub[i], sub[best_i]))
            header_rows = 1
            # multi-row header: next row is also mostly text with a similar fill, and the row after is
            # numeric-ish. A row with one cell is a section heading, not a header (ADR-058).
            if best_i + 2 < len(sub):
                head_cells = [c for c in sub[best_i] if c is not None]
                nxt = sub[best_i + 1]
                nxt_cells = [c for c in nxt if c is not None]
                if (
                    nxt_cells
                    and len(nxt_cells) >= max(2, len(head_cells) // 2)
                    and sum(_cell_kind(c) == "text" for c in nxt_cells) / len(nxt_cells) > 0.8
                ):
                    after = [c for c in sub[best_i + 2] if c is not None]
                    if after and sum(_cell_kind(c) in ("num", "date") for c in after) / len(after) > 0.3:
                        header_rows = 2
            headers = merge_header_rows(sub[best_i : best_i + header_rows])
            data_start = r0 + best_i + header_rows
            section_rows = sum(1 for row in sub[best_i + header_rows :] if _is_section_row(row))
            # Boundaries cover the full sheet even though header scoring uses a bounded sample.
            data_end = r1
            title = None
            if best_i > 0:
                title_cells = [c for c in sub[0] if c is not None]
                if len(title_cells) == 1:
                    title = title_cells[0]
            n_rows = max(0, data_end - data_start)
            if n_rows == 0:
                continue
            out.append(
                TableCandidate(
                    index=len(out),
                    header_row=r0 + best_i,
                    header_rows=header_rows,
                    data_start=data_start,
                    data_end=data_end,
                    first_column=c0,
                    last_column=c1,
                    headers=headers,
                    n_rows=n_rows,
                    score=best_s,
                    title=title,
                    section_rows=section_rows,
                )
            )
    out = _join_pages(out)
    out.sort(key=lambda t: (-t.n_rows, t.header_row))
    for i, t in enumerate(out):
        t.index = i
    return out


def merge_header_rows(rows: list[list[str | None]]) -> list[str]:
    """Combine 1–3 header rows, forward-filling merged group headers, and de-duplicate names."""
    width = max(len(r) for r in rows)
    filled: list[list[str | None]] = []
    for i, row in enumerate(rows):
        r = list(row) + [None] * (width - len(row))
        if len(rows) > 1 and i == 0:
            # the top (group) header row is forward-filled across merged cells
            last = None
            ff: list[str | None] = []
            for v in r:
                if v is not None:
                    last = v
                ff.append(last)
            r = ff
        filled.append(r)
    names: list[str] = []
    for j in range(width):
        parts = [str(filled[i][j]).strip() for i in range(len(filled)) if filled[i][j] is not None]
        name = " ".join(dict.fromkeys(parts)) if parts else f"col_{j}"
        names.append(normalize_header(name))
    return dedupe_names(names)


def normalize_header(name: str) -> str:
    s = re.sub(r"\s+", " ", str(name)).strip()
    s = s.replace("\n", " ")
    return s or "col"


def dedupe_names(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for n in names:
        key = n.lower()
        if key in seen:
            seen[key] += 1
            out.append(f"{n}_{seen[key]}")
        else:
            seen[key] = 1
            out.append(n)
    return out


# --------------------------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------------------------
SHEET_ROW = "__sheet_row__"  # 0-based row of the raw grid a data row came from (lineage only)
PAGE_HEADER_MATCH = 0.8  # share of a header's cells a row must repeat to be that header printed again


def _drop_page_breaks(
    body: pl.DataFrame, data: list[str], header_rows: list[list[str | None]]
) -> pl.DataFrame:
    """Remove a header printed again on each page, with the title and page lines above it. Rows are found by
    the header's first cell, so a sheet with no page breaks costs one column scan."""
    repeats: set[int] = set()
    for header in header_rows:
        cells = [(data[i], str(c).strip()) for i, c in enumerate(header) if c is not None and i < len(data)]
        if not cells:
            continue
        first, text = cells[0]
        rows = body.with_row_index("__i__").filter(pl.col(first).str.strip_chars() == text)
        for row in rows.iter_rows(named=True):
            same = sum(str(row[column] or "").strip() == want for column, want in cells)
            if same / len(cells) >= PAGE_HEADER_MATCH:
                repeats.add(int(row["__i__"]))
    if not repeats:
        return body
    filled = body.select(pl.sum_horizontal(pl.col(data).is_not_null()).alias("n"))["n"].to_list()
    drop = set(repeats)
    for at in repeats:
        above = at - 1
        while above >= 0 and filled[above] <= 1:  # blank rows, and one-cell lines such as a title or "page 2"
            drop.add(above)
            above -= 1
    return body.with_row_index("__i__").filter(~pl.col("__i__").is_in(sorted(drop))).drop("__i__")


def extract_table(
    df: pl.DataFrame,
    spec: TableSpec,
    *,
    rename: dict[str, str] | None = None,
    row_numbers: bool = False,
    positions: dict[str, int] | None = None,
) -> pl.DataFrame:
    """Turn a raw grid into a header-named frame according to ``spec`` (all columns stay strings).

    For lineage, ``row_numbers`` adds a ``__sheet_row__`` column (the raw grid row each data row came
    from) and ``positions`` is filled with header → raw grid column. Without them the output is unchanged.
    """
    if df.height == 0:
        return pl.DataFrame()
    if spec.header_row == "auto":
        cands = detect_tables(df)
        if not cands:
            raise ValueError("no table detected")
        resolved = cands[0].to_spec()
        spec = spec.model_copy(
            update={
                "header_row": resolved.header_row,
                "header_rows": resolved.header_rows,
                "data_start": spec.data_start if spec.data_start is not None else resolved.data_start,
                "data_end": spec.data_end if spec.data_end is not None else resolved.data_end,
                "first_column": spec.first_column if spec.first_column is not None else resolved.first_column,
                "last_column": spec.last_column if spec.last_column is not None else resolved.last_column,
            }
        )
    header_row = int(spec.header_row)  # type: ignore[arg-type]
    c0 = spec.first_column or 0
    c1 = spec.last_column if spec.last_column is not None else df.width
    c1 = min(c1, df.width)
    cols = df.columns[c0:c1]
    if header_row >= df.height or not cols:
        return pl.DataFrame()
    header_rows = [list(r) for r in df.slice(header_row, spec.header_rows).select(cols).iter_rows()]
    if not header_rows or sum(1 for v in header_rows[0] if v is not None) < 2:
        # header is not where the spec says (title rows added/removed) → re-detect
        cands = detect_tables(df)
        if not cands:
            return pl.DataFrame()
        return extract_table(
            df,
            cands[0]
            .to_spec()
            .model_copy(
                update={
                    "fill_down": spec.fill_down,
                    "unpivot": spec.unpivot,
                    "drop_total_rows": spec.drop_total_rows,
                    "drop_repeated_headers": spec.drop_repeated_headers,
                    "stop_at_blank_rows": spec.stop_at_blank_rows,
                }
            ),
            rename=rename,
            row_numbers=row_numbers,
            positions=positions,
        )
    headers = merge_header_rows(header_rows)
    data_start = spec.data_start if spec.data_start is not None else header_row + spec.header_rows
    data_end = spec.data_end if spec.data_end is not None else df.height
    body = df.slice(data_start, max(0, data_end - data_start)).select(cols)
    body.columns = headers
    if positions is not None:
        positions.update({(rename or {}).get(h, h): c0 + i for i, h in enumerate(headers)})
    if row_numbers:
        body = body.with_columns(pl.int_range(data_start, data_start + body.height).alias(SHEET_ROW))
    data = [c for c in body.columns if c != SHEET_ROW]  # every column unless lineage asked for rows
    # before the blank-row stop: the blanks between two pages must not end the table
    if spec.drop_repeated_headers and body.height:
        body = _drop_page_breaks(body, data, header_rows)

    # stop at N consecutive blank rows
    if spec.stop_at_blank_rows and body.height:
        blank = body.select(pl.all_horizontal(pl.col(data).is_null()).alias("b"))["b"].to_list()
        run = 0
        cut = len(blank)
        for i, b in enumerate(blank):
            run = run + 1 if b else 0
            if run >= spec.stop_at_blank_rows:
                cut = i - run + 1
                break
        body = body.head(cut)
    # drop fully blank rows
    if body.height:
        body = body.filter(~pl.all_horizontal(pl.col(data).is_null()))
    # section headings: one text cell on a row of its own becomes a column, filled down (ADR-058)
    if spec.section_column and body.height and data:
        filled = pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.Int32) for c in data])
        first = pl.coalesce([pl.col(c) for c in data]).str.strip_chars()
        heading = (
            (filled == 1)
            & (first.str.len_chars() <= SECTION_MAX_CHARS)
            & first.str.contains(_WORD_RE)
            & ~first.str.contains(_NUM_RE.pattern)
            & ~first.str.to_lowercase().str.contains(TOTAL_LABEL_RE)
        ).fill_null(False)
        body = (
            body.with_columns(pl.when(heading).then(first).otherwise(None).alias(spec.section_column))
            .with_columns(pl.col(spec.section_column).fill_null(strategy="forward"))
            .filter(~heading)
        )
    # drop total rows: a total label in the first non-null cell and nothing but numbers beside it
    if spec.drop_total_rows and body.height and data:
        first_text = pl.coalesce([pl.col(c) for c in data]).str.to_lowercase().str.strip_chars()
        words = pl.sum_horizontal(
            [
                (pl.col(c).str.contains(_WORD_RE) & ~pl.col(c).str.contains(_COUNT_RE))
                .fill_null(False)
                .cast(pl.Int32)
                for c in data
            ]
        )
        cond = first_text.str.contains(TOTAL_LABEL_RE).fill_null(False) & (words <= 1)
        body = body.filter(~cond)
    # fill down (merged cells)
    for c in spec.fill_down:
        if c in body.columns:
            body = body.with_columns(pl.col(c).fill_null(strategy="forward"))
    if spec.drop_empty_columns and body.height:
        keep = [c for c in body.columns if c == SHEET_ROW or body[c].null_count() < body.height]
        body = body.select(keep)
    if spec.unpivot is not None:
        u = spec.unpivot
        value_cols = u.value_columns
        if value_cols is None and u.value_columns_regex:
            rx = re.compile(u.value_columns_regex)
            value_cols = [c for c in body.columns if rx.search(c) and c not in u.id_columns]
        if value_cols is None:
            value_cols = [c for c in body.columns if c not in u.id_columns and c != SHEET_ROW]
        index = [*u.id_columns, SHEET_ROW] if row_numbers else u.id_columns
        body = body.unpivot(
            on=value_cols, index=index, variable_name=u.variable_name, value_name=u.value_name
        )
    if rename:
        body = body.rename({k: v for k, v in rename.items() if k in body.columns})
    return body
