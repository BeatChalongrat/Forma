"""The agent's workbench (ADR-058): look at the file, run the mapping, read the result.

The model never executes anything. It asks FORMA read-only questions about a table (``inspect``) and FORMA
runs the model's mapping on the first rows (``run``) and reports what came out. Both answers are data, clipped
like every sample a model sees (ADR-013). ``Workbench`` is the contract; ``FileWorkbench`` reads the round's
files; tests pass their own.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from forma.engine.transform import BoundSource, TransformEngine
from forma.mapping_dsl.models import MappingDocument
from forma.profiling.profile import WorkbookProfile
from forma.schema_registry import TargetSchema

INSPECT_KINDS = ("columns", "distinct", "rows", "find")
MAX_INSPECT_REQUESTS = 8
MAX_DISTINCT = 40
MAX_ROWS = 20
MAX_FIND = 20
MAX_PROBLEMS = 12
SAMPLE_RECORDS = 3


@dataclass
class RunReport:
    """What the engine made of a mapping on the first rows."""

    rows_in: int
    rows_out: int
    problems: list[str] = field(default_factory=list)
    sample: list[dict[str, Any]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {"rows_in": self.rows_in, "rows_out": self.rows_out, "problems": self.problems[:MAX_PROBLEMS]}

    def to_prompt(self) -> dict[str, Any]:
        return {**self.to_dict(), "sample_records": self.sample}


class Workbench(Protocol):
    def inspect(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def run(self, doc: MappingDocument) -> RunReport: ...


def inspect_problems(requests: Any, n_tables: int) -> list[str]:
    """Why a list of inspect requests cannot be answered (sent back to the model as validation errors)."""
    if not isinstance(requests, list) or not requests:
        return ["inspect must be a non-empty list of requests"]
    out: list[str] = []
    for i, r in enumerate(requests[:MAX_INSPECT_REQUESTS]):
        if not isinstance(r, dict):
            out.append(
                f"inspect/{i}: a request is an object {{table_index, what, column?, pattern?, start?}}"
            )
            continue
        what = r.get("what")
        if what not in INSPECT_KINDS:
            out.append(f"inspect/{i}: what must be one of {list(INSPECT_KINDS)}")
        ti = r.get("table_index")
        if not isinstance(ti, int) or isinstance(ti, bool) or not 0 <= ti < n_tables:
            out.append(f"inspect/{i}: table_index must be an integer from 0 to {n_tables - 1}")
        if what == "distinct" and not isinstance(r.get("column"), str):
            out.append(f"inspect/{i}: distinct needs a column name")
        if what == "find" and not isinstance(r.get("pattern"), str):
            out.append(f"inspect/{i}: find needs a pattern (regular expression)")
    return out


def _clip(v: Any, max_chars: int) -> Any:
    if isinstance(v, str):
        return v if len(v) <= max_chars else v[:max_chars] + "…"
    if isinstance(v, dict):
        return {k: _clip(x, max_chars) for k, x in v.items()}
    if isinstance(v, list):
        return [_clip(x, max_chars) for x in v]
    return v


class FileWorkbench:
    """Answers from the round's own files. ``bind`` resolves a mapping's sources to files (the service's
    ``bind_sources``); ``resolve`` turns a storage key into a local path (``ctx.storage.local_path``)."""

    def __init__(
        self,
        schema: TargetSchema,
        profile: WorkbookProfile,
        files: list[Any],
        *,
        bind: Callable[[MappingDocument], tuple[dict[str, BoundSource], list[str]]],
        resolve: Callable[[str], Path],
        rows: int = 50,
        max_chars: int = 200,
    ) -> None:
        self.schema = schema
        self.profile = profile
        self.files = files
        self.bind = bind
        self.resolve = resolve
        self.rows = rows
        self.max_chars = max_chars
        self._frames: dict[int, pl.DataFrame] = {}

    # ---- inspect ------------------------------------------------------------------------------------
    def _frame(self, table_index: int) -> pl.DataFrame:
        if table_index in self._frames:
            return self._frames[table_index]
        from forma.ingestion.readers import read_raw
        from forma.profiling.tables import extract_table

        t = self.profile.tables[table_index]
        f = next((x for x in self.files if x.id == t.file_id), None) if t.file_id else None
        if f is None:
            usable = [x for x in self.files if x.status == "profiled"]
            f = usable[0] if len(usable) == 1 else None
        if f is None:
            raise ValueError("the table's file is not in this round")
        path = self.resolve(f.storage_key)
        is_csv = f.kind == "csv" or path.suffix.lower() in {".csv", ".txt", ".tsv"}
        raw = read_raw(path, None if is_csv else t.sheet, validate_cells=False)
        frame = extract_table(raw, t.table.to_spec())
        self._frames[table_index] = frame
        return frame

    def inspect(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in requests[:MAX_INSPECT_REQUESTS]:
            try:
                out.append({"request": r, "answer": self._answer(r)})
            except Exception as e:  # a bad request is an answer, not a crash
                out.append({"request": r, "error": str(e)[:300]})
        return out

    def _answer(self, r: dict[str, Any]) -> Any:
        frame = self._frame(int(r["table_index"]))
        what = r.get("what")
        if what == "columns":
            return [
                {
                    "name": c,
                    "non_null": int(frame[c].drop_nulls().len()),
                    "distinct": int(frame[c].n_unique()),
                }
                for c in frame.columns
            ]
        if what == "distinct":
            col = str(r.get("column"))
            if col not in frame.columns:
                raise ValueError(f"no column {col!r}; columns: {frame.columns[:30]}")
            limit = min(int(r.get("limit") or MAX_DISTINCT), MAX_DISTINCT)
            counts = frame[col].drop_nulls().value_counts(sort=True).head(limit)
            total = int(frame[col].n_unique())
            vals = [{"value": _clip(v, self.max_chars), "count": int(n)} for v, n in counts.iter_rows()]
            return {"distinct": total, "null_count": int(frame[col].null_count()), "values": vals}
        if what == "rows":
            start = max(0, int(r.get("start") or 0))
            limit = min(int(r.get("limit") or MAX_ROWS), MAX_ROWS)
            return {
                "rows": [_clip(d, self.max_chars) for d in frame.slice(start, limit).to_dicts()],
                "total_rows": frame.height,
            }
        if what == "find":
            pattern = str(r.get("pattern"))[:200]
            rx = re.compile(pattern, re.IGNORECASE)
            hits: list[dict[str, Any]] = []
            for i, row in enumerate(frame.iter_rows(named=True)):
                if any(v is not None and rx.search(str(v)) for v in row.values()):
                    hits.append({"row": i, **_clip(row, self.max_chars)})
                    if len(hits) >= MAX_FIND:
                        break
            return {"matches": hits, "capped": len(hits) >= MAX_FIND}
        raise ValueError(f"what must be one of {list(INSPECT_KINDS)}")

    # ---- run ----------------------------------------------------------------------------------------
    def run(self, doc: MappingDocument) -> RunReport:
        from forma.ai import plausibility

        bindings, problems = self.bind(doc)
        if problems:
            return RunReport(0, 0, [f"binding: {p}" for p in problems])
        try:
            res = TransformEngine(self.schema).run(doc, bindings, limit=self.rows)
        except Exception as e:  # the engine refused the document outright
            return RunReport(0, 0, [f"engine: {str(e)[:300]}"])
        out: list[str] = []
        for f in res.findings:
            if f.severity == "error" or (f.kind == "compile" and "not found" in f.message):
                line = f"{f.kind}: {f.message}"
                if line not in out:
                    out.append(line)
        rows_in, rows_out = int(res.stats.get("rows_in", 0)), int(res.stats.get("rows_out", 0))
        if rows_out == 0:
            out.insert(0, f"the mapping produced 0 records from {rows_in} source rows")
        else:
            for cm in doc.collections:
                items = sum(len(rec.get(cm.target.strip("/"), []) or []) for rec in res.records)
                if items == 0:
                    out.append(
                        f"{cm.target}: 0 items for {rows_out} records (link or filter matches nothing)"
                    )
            out.extend(plausibility.check(res.records, self.schema))
        sample = [_clip(_without_meta(rec), self.max_chars) for rec in res.records[:SAMPLE_RECORDS]]
        return RunReport(rows_in, rows_out, out[:MAX_PROBLEMS], sample)


def _without_meta(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("source_meta")}
