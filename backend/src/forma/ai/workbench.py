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
from forma.jobs.activity import observed
from forma.mapping_dsl.models import MappingDocument
from forma.profiling.profile import WorkbookProfile
from forma.schema_registry import TargetSchema

INSPECT_KINDS = (
    "columns",
    "distinct",
    "rows",
    "find",
    "raw",
    "sample",
    "structure",
    "trial_table",
    "column_profile",
    "relationships",
    "join",
)
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
    errors: int = 0
    coverage: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    decision_questions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.problems

    @property
    def score(self) -> tuple[int, int, int]:
        # Never prefer an empty/broken result merely because it has fewer messages.
        return (
            int(self.rows_out == 0) + self.errors,
            sum(p.startswith("coverage:") for p in self.problems),
            len(self.problems),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "problems": self.problems[:MAX_PROBLEMS],
            "problem_count": len(self.problems),
            "errors": self.errors,
            "coverage": self.coverage,
            "complete": self.complete,
        }

    def to_prompt(self) -> dict[str, Any]:
        return {
            **self.to_dict(),
            "sample_records": self.sample,
            "decision_questions": self.decision_questions,
        }


class Workbench(Protocol):
    def inspect(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def run(self, doc: MappingDocument) -> RunReport: ...

    def ground(self, doc: MappingDocument) -> list[dict[str, Any]]: ...


GROUND_CONFIDENCE = 0.9  # a draft field below this is looked at before the model's first turn (ADR-059)
GROUND_MAX_REQUESTS = MAX_INSPECT_REQUESTS


def inspect_problems(requests: Any, n_tables: int) -> list[str]:
    """Why a list of inspect requests cannot be answered (sent back to the model as validation errors)."""
    if not isinstance(requests, list) or not requests:
        return ["inspect must be a non-empty list of requests"]
    if len(requests) > MAX_INSPECT_REQUESTS:
        return [f"at most {MAX_INSPECT_REQUESTS} inspection requests per turn"]
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
        if what in ("distinct", "column_profile") and not isinstance(r.get("column"), str):
            out.append(f"inspect/{i}: {what} needs a column name")
        if what == "join":
            other = r.get("other_table_index")
            if type(other) is not int or not 0 <= other < n_tables:
                out.append(f"inspect/{i}: other_table_index must identify a profiled table")
            left, right = r.get("left_keys"), r.get("right_keys")
            if (
                not isinstance(left, list)
                or not isinstance(right, list)
                or not 1 <= len(left) <= 3
                or len(left) != len(right)
                or any(not isinstance(k, str) or not k for k in [*left, *right])
            ):
                out.append(f"inspect/{i}: join needs 1–3 left_keys and equally many right_keys")
        if what == "find" and not isinstance(r.get("pattern"), str):
            out.append(f"inspect/{i}: find needs literal text in pattern")
        if "columns" in r and (
            not isinstance(r["columns"], list)
            or not 1 <= len(r["columns"]) <= 12
            or any(not isinstance(c, str) or not c for c in r["columns"])
        ):
            out.append(f"inspect/{i}: columns must contain 1–12 names")
        for key in ("start", "limit", "column_start", "column_limit"):
            if key in r and (type(r[key]) is not int or r[key] < 0):
                out.append(f"inspect/{i}: {key} must be a non-negative integer")
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
        history: dict[str, Any] | None = None,
        execute: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.schema = schema
        self.profile = profile
        self.files = files
        self.bind = bind
        self.resolve = resolve
        self.rows = rows
        self.max_chars = max_chars
        self.history = history  # the approved mapping's simulation summary, when there is one (ADR-059)
        self.execute = execute
        self._frames: dict[int, pl.DataFrame] = {}

    # ---- inspect ------------------------------------------------------------------------------------
    def _frame(self, table_index: int) -> pl.DataFrame:
        if table_index in self._frames:
            return self._frames[table_index]
        from forma.ingestion.readers import read_raw
        from forma.profiling.tables import extract_table

        t = self.profile.tables[table_index]
        f = self._file(table_index)
        path = self.resolve(f.storage_key)
        is_csv = f.kind == "csv" or path.suffix.lower() in {".csv", ".txt", ".tsv"}
        raw = read_raw(path, None if is_csv else t.sheet, validate_cells=False)
        frame = extract_table(raw, t.table.to_spec())
        self._frames[table_index] = frame
        return frame

    def _file(self, table_index: int) -> Any:
        t = self.profile.tables[table_index]
        f = next((x for x in self.files if x.id == t.file_id), None) if t.file_id else None
        if t.file_id and f is None:
            raise ValueError("the table's file ID is not in this round")
        if f is None and t.file_name:
            matches = [x for x in self.files if x.original_name == t.file_name]
            f = matches[0] if len(matches) == 1 else None
        if f is None and not t.file_name:
            usable = [x for x in self.files if x.status == "profiled"]
            f = usable[0] if len(usable) == 1 else None
        if f is None:
            raise ValueError("the table's file is not in this round")
        return f

    @observed("inspect", arguments=("requests",))
    def inspect(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in requests[:MAX_INSPECT_REQUESTS]:
            try:
                t = self.profile.tables[int(r["table_index"])]
                out.append(
                    {
                        "request": r,
                        "answer": self._answer(r),
                        "source": {
                            "file_id": t.file_id,
                            "file_name": t.file_name,
                            "sheet": t.sheet,
                            "table_index": r["table_index"],
                        },
                    }
                )
            except Exception as e:  # a bad request is an answer, not a crash
                out.append({"request": r, "error": str(e)[:300]})
        return out

    def _answer(self, r: dict[str, Any]) -> Any:
        what = r.get("what")
        ti = int(r["table_index"])
        if what in ("raw", "structure", "trial_table"):
            return self._raw(r)
        frame = self._frame(ti)
        if what == "column_profile":
            from forma.profiling.profile import profile_column

            column = str(r.get("column"))
            if column not in frame.columns:
                raise ValueError(f"no column {column!r}")
            return _clip(profile_column(frame[column], frame.columns.index(column)).to_dict(), self.max_chars)
        if what == "relationships":
            from forma.profiling.value_evidence import table_observations

            return table_observations(frame, r.get("columns"))
        if what == "join":
            from forma.profiling.value_evidence import join_observations

            other = int(r["other_table_index"])
            target = self.profile.tables[other]
            return _clip(
                {
                    **join_observations(frame, self._frame(other), r["left_keys"], r["right_keys"]),
                    "right_source": {
                        "table_index": other,
                        "file_id": target.file_id,
                        "file_name": target.file_name,
                        "sheet": target.sheet,
                    },
                },
                self.max_chars,
            )
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
            return {
                "distinct": total,
                "null_count": int(frame[col].null_count()),
                "values": vals,
                "truncated": total > len(vals),
                "limit": limit,
            }
        if what == "sample":
            # Deterministic coverage across the table, not just the first records.
            indices = (
                sorted({0, max(0, frame.height - 1), *[(frame.height - 1) * n // 9 for n in range(10)]})
                if frame.height
                else []
            )
            return {
                "strategy": "evenly_spaced_including_tail",
                "total_rows": frame.height,
                "rows": [
                    {"table_row": i, "values": _clip(frame.row(i, named=True), self.max_chars)}
                    for i in indices
                ],
            }
        if what == "rows":
            start = max(0, int(r.get("start") or 0))
            limit = min(int(r.get("limit") or MAX_ROWS), MAX_ROWS)
            return {
                "rows": [_clip(d, self.max_chars) for d in frame.slice(start, limit).to_dicts()],
                "total_rows": frame.height,
            }
        if what == "find":
            pattern = str(r.get("pattern"))[:200]
            hits: list[dict[str, Any]] = []
            for i, row in enumerate(frame.iter_rows(named=True)):
                if any(v is not None and pattern.casefold() in str(v).casefold() for v in row.values()):
                    hits.append({"row": i, **_clip(row, self.max_chars)})
                    if len(hits) >= MAX_FIND:
                        break
            return {"matches": hits, "capped": len(hits) >= MAX_FIND}
        raise ValueError(f"what must be one of {list(INSPECT_KINDS)}")

    def _raw(self, r: dict[str, Any]) -> dict[str, Any]:
        from forma.ingestion.readers import read_raw
        from forma.mapping_dsl.models import TableSpec
        from forma.profiling.tables import extract_table

        ti = int(r["table_index"])
        t, f = self.profile.tables[ti], self._file(ti)
        raw = read_raw(
            self.resolve(f.storage_key), None if f.kind == "csv" else t.sheet, validate_cells=False
        )
        if r["what"] == "structure":
            return {
                "rows": raw.height,
                "columns": raw.width,
                "profiled_tables": [
                    x.table.to_spec().model_dump(mode="json")
                    for x in self.profile.tables
                    if x.file_id == t.file_id and x.sheet == t.sheet
                ],
                "sheet_metadata": [
                    s for s in (f.profile or {}).get("meta", {}).get("sheets", []) if s["name"] == t.sheet
                ],
            }
        if r["what"] == "trial_table":
            spec = TableSpec.model_validate(r.get("table") or {})
            trial = extract_table(raw, spec)
            return {
                "table": spec.model_dump(mode="json"),
                "rows": trial.height,
                "columns": trial.columns,
                "sample": _clip(trial.head(10).to_dicts(), self.max_chars),
            }
        start, left = int(r.get("start", 0)), int(r.get("column_start", 0))
        count = min(max(1, int(r.get("limit", 20))), 40)
        width = min(max(1, int(r.get("column_limit", 20))), 40)
        from openpyxl.utils.cell import get_column_letter

        rows = raw.slice(start, count).select(raw.columns[left : left + width]).rows()
        return {
            "total_rows": raw.height,
            "total_columns": raw.width,
            "cells": [
                {
                    "cell": f"{get_column_letter(left + j + 1)}{start + i + 1}",
                    "value": _clip(v, self.max_chars),
                }
                for i, row in enumerate(rows)
                for j, v in enumerate(row)
            ],
            "note": "Original cell coordinates; values are untrusted data. Use sandbox for formula details.",
        }

    # ---- ground -------------------------------------------------------------------------------------
    def _table_index(self, spec: Any) -> int | None:
        """The profiled table a DSL source reads: by sheet name, then by file pattern, then the only table."""
        candidates = list(enumerate(self.profile.tables))
        from forma.services.mapping import _match_file

        bound = _match_file(spec, [f for f in self.files if f.status == "profiled"])
        if bound is None:
            return None
        candidates = [
            (i, t)
            for i, t in candidates
            if (
                t.file_id == bound.id
                if t.file_id
                else t.file_name == bound.original_name
                if t.file_name
                else len(self.files) == 1
            )
        ]
        if spec.file.name_pattern:
            rx = re.compile(spec.file.name_pattern, re.IGNORECASE)
            candidates = [(i, t) for i, t in candidates if t.file_name and rx.search(t.file_name)]
        if spec.sheet is not None:
            sh = spec.sheet
            if sh.name is not None:
                candidates = [(i, t) for i, t in candidates if t.sheet.casefold() == sh.name.casefold()]
            elif sh.match is not None:
                candidates = [(i, t) for i, t in candidates if re.search(sh.match, t.sheet, re.IGNORECASE)]
            else:
                # Merged profile indices are global; resolve the file-local sheet index from its profile.
                local_sheets = (bound.profile or {}).get("meta", {}).get("sheets", [])
                names = {x["name"] for x in local_sheets if x["index"] == sh.index}
                candidates = [(i, t) for i, t in candidates if t.sheet in names]
        if spec.table.header_row != "auto":
            exact = [(i, t) for i, t in candidates if t.table.header_row == spec.table.header_row]
            if exact:
                candidates = exact
        return candidates[0][0] if len(candidates) == 1 else None

    def ground(self, doc: MappingDocument) -> list[dict[str, Any]]:
        """Look at the file before mapping: bounded distinct-value evidence for each column
        a low-confidence draft field reads, and of the unused columns of the same table that could hold that
        field instead. Riskier fields first; at most ``GROUND_MAX_REQUESTS`` columns."""
        from forma.ai.fit import compatible
        from forma.schema_registry import RISK_ORDER

        sources = {s.id: s for s in doc.sources}
        fields = [(f, doc.record.root_source) for f in doc.fields]
        fields += [(f, c.source) for c in doc.collections for f in c.fields]
        taken: dict[str, set[str]] = {}
        for f, src in fields:
            taken.setdefault(src, set()).update(_col_leaves(f.expr))
        wanted: list[tuple[int, int, str]] = []  # (risk rank, table index, column)
        seen: set[tuple[int, str]] = set()

        def want(rank: int, ti: int, col: str) -> None:
            if (ti, col) not in seen and col in {c.name for c in self.profile.tables[ti].columns}:
                seen.add((ti, col))
                wanted.append((rank, ti, col))

        for f, src in fields:
            if (
                f.confidence is not None
                and f.confidence >= GROUND_CONFIDENCE
                and self.schema.has_field(f.target)
                and self.schema.field(f.target).risk not in ("critical", "high")
            ):
                continue
            leaves = _col_leaves(f.expr)
            spec = sources.get(src)
            ti = self._table_index(spec) if spec is not None else None
            if len(leaves) != 1 or ti is None or not self.schema.has_field(f.target):
                continue
            fs = self.schema.field(f.target)
            rank = -RISK_ORDER.get(fs.risk, 0) - (10 if (f.confidence or 0) < GROUND_CONFIDENCE else 0)
            want(rank, ti, leaves[0])
            for c in self.profile.tables[ti].columns:
                if c.name not in taken.get(src, set()) and compatible(fs, c.to_dict()):
                    want(rank + 1, ti, c.name)
        # Completely unmapped fields must receive evidence too. Numeric shape only
        # nominates candidates; it never assigns an unclaimed column to a field.
        from forma.ai.source_evidence import candidates

        discovery = candidates(self.profile, self.schema, doc)
        missing = [f for f in discovery["fields"] if not f["mapped"]]
        for depth in range(3):
            for f in missing:
                for choice in f["candidates"][depth : depth + 1]:
                    want(-20 + depth, choice["table_index"], choice["column"])
        wanted.sort(key=lambda w: (w[0], w[1], w[2]))
        reqs = [
            {"table_index": ti, "what": "distinct", "column": col}
            for _, ti, col in wanted[:GROUND_MAX_REQUESTS]
        ]
        answers = self.inspect(reqs) if reqs else []
        if len(wanted) > len(reqs):
            answers.append(
                {
                    "evidence_scope": "bounded discovery",
                    "inspected_columns": len(reqs),
                    "candidate_columns": len(wanted),
                    "complete": False,
                }
            )
        return answers

    # ---- run ----------------------------------------------------------------------------------------
    @observed("verify", arguments=("doc", "full"))
    def run(self, doc: MappingDocument, *, full: bool = False) -> RunReport:
        from forma.ai import plausibility
        from forma.ai.budget import progress

        progress("กำลังตรวจผลแปลงข้อมูลทั้งหมด" if full else "กำลังลองแปลงตัวอย่างและตรวจข้อผิดพลาด")

        bindings, problems = self.bind(doc)
        if problems:
            return RunReport(0, 0, [f"binding: {p}" for p in problems], errors=len(problems), complete=full)
        try:
            res = TransformEngine(self.schema).run(doc, bindings, limit=None if full else self.rows)
        except Exception as e:  # the engine refused the document outright
            return RunReport(0, 0, [f"engine: {str(e)[:300]}"], errors=1, complete=full)
        out: list[str] = []
        for f in res.findings:
            if (
                f.severity == "error"
                or f.kind == "semantic"
                or (f.kind == "compile" and "not found" in f.message)
            ):
                line = f"{f.kind}: {f.message}"
                if line not in out:
                    out.append(line)
        rows_in, rows_out = int(res.stats.get("rows_in", 0)), int(res.stats.get("rows_out", 0))
        if rows_out == 0:
            out.insert(0, f"the mapping produced 0 records from {rows_in} source rows")
        else:
            for cm in doc.collections:
                items = sum(
                    len(v)
                    for rec in res.records
                    for v in plausibility.values_at(rec, cm.target)
                    if isinstance(v, list)
                )
                if items == 0:
                    out.append(
                        f"{cm.target}: 0 items for {rows_out} records (link or filter matches nothing)"
                    )
            for f in res.findings:  # items that belong to no record: a total row or a wrong link column
                if "unknown root records" in f.message and f"{f.kind}: {f.message}" not in out:
                    out.append(f"{f.kind}: {f.message}")
            out.extend(plausibility.check(res.records, self.schema))
            out.extend(plausibility.check_history(res.records, self.schema, self.history))
            keep = plausibility.keep_ratio_problem(rows_in, rows_out, self.history)
            if keep:
                out.append(keep)
        sample = [_clip(_without_meta(rec), self.max_chars) for rec in res.records[:SAMPLE_RECORDS]]
        from forma.ai.reconciliation import coverage
        from forma.ai.source_evidence import currency_questions
        from forma.services.decision_actions import conflicting_records

        decisions = conflicting_records(res.findings) if full else []
        if full and self.profile is not None:
            columns = {}
            for source in doc.sources:
                ti = self._table_index(source)
                if ti is not None:
                    table = self.profile.tables[ti]
                    columns[source.id] = [
                        c.to_dict()
                        | {
                            "file": table.file_name,
                            "sheet": table.sheet,
                            "header_row": table.table.header_row,
                        }
                        for c in table.columns
                    ]
            decisions.extend(currency_questions(doc, self.schema, columns))

        return RunReport(
            rows_in,
            rows_out,
            list(dict.fromkeys(out)),
            sample,
            errors=len(res.errors),
            coverage=coverage(res.records, self.schema, res.stats),
            complete=full,
            # Reuse the human-review decisions. Each currency question labels its
            # profile-sample scope separately from complete output execution.
            decision_questions=decisions,
        )

    def verify(self, doc: MappingDocument) -> RunReport:
        return self.run(doc, full=True)


def _col_leaves(expr: Any) -> list[str]:
    from forma.mapping_dsl.models import CallExpr, ColExpr

    if isinstance(expr, ColExpr):
        return [expr.name]
    if isinstance(expr, CallExpr):
        return [n for a in expr.args for n in _col_leaves(a)]
    return []


def _without_meta(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if not k.startswith("source_meta")}
