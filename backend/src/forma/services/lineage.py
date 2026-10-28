"""Explainable lineage (ADR-020): any value of a record traced back to the cell it came from — file, sheet,
Excel row and column, the raw text — through the mapping expression that read it, the persistent overrides
and manual edits on the way, to the final value. When the value differs from the previous round, the trace
says why, with the evidence: the ERP value changed, the mapping changed, or a person changed it.

Nothing is re-derived by guesswork: the engine records which source row fed the record, each collection item
and each joined lookup (``source_meta.lineage``); this module re-reads those rows from the immutable files.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.db.models import ChangeSet, DatasetVersion, ImportRound
from forma.engine.ops import CompileContext, compile_expr
from forma.engine.transform import BoundSource, _get_pointer, resolve_sheet
from forma.ingestion.readers import file_kind, read_raw
from forma.mapping_dsl.models import FieldMapping, MappingDocument, SourceSpec
from forma.profiling.tables import SHEET_ROW, extract_table
from forma.schema_registry import PROVENANCE_KEY, TargetSchema
from forma.services.context import ServiceContext
from forma.services.datasets import (
    _flat_frame,
    _identity_of,
    _where_mask,
    get_dataset,
    iter_records,
    previous_published,
    where_names,
)
from forma.services.mapping import active_overrides, bind_sources, get_mapping
from forma.services.sandbox import _delta

MAX_ITEMS = 50  # collection items traced per record
MAX_VERSIONS = 50
VERIFY_MAX_ROW = 200_000  # beyond this, reading the cell back with openpyxl is too slow to be worth it


# --------------------------------------------------------------------------------------------
# where a source row lives in the file
# --------------------------------------------------------------------------------------------
@dataclass
class _Located:
    spec: SourceSpec
    frame: pl.DataFrame  # the extracted table, with the raw grid row of every data row
    positions: dict[str, int]  # header → raw grid column
    file: str | None
    path: Path
    sheet: str | None
    origin: tuple[int, int]  # Excel row / column (1-based) of the raw grid's first cell
    excel: bool


def _sheet_info(path: Path, sheet: int | str | None) -> tuple[str | None, tuple[int, int]]:
    """Sheet name and where its used range starts: the reader drops leading empty rows and columns."""
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet or 0]
            return ws.title, (int(ws.min_row or 1), int(ws.min_column or 1))
        finally:
            wb.close()
    except Exception:  # .xls or an unreadable dimension: positions are relative to the used range
        return (sheet if isinstance(sheet, str) else None), (1, 1)


def _column_letter(n: int) -> str:
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


class _Sources:
    """The mapping's sources, re-read lazily with row numbers (files are immutable, so this is exact)."""

    def __init__(self, doc: MappingDocument, bindings: dict[str, BoundSource]) -> None:
        self.doc = doc
        self.bindings = bindings
        self._cache: dict[str, _Located | None] = {}

    def get(self, sid: str) -> _Located | None:
        if sid not in self._cache:
            self._cache[sid] = self._load(sid)
        return self._cache[sid]

    def _load(self, sid: str) -> _Located | None:
        spec = next((s for s in self.doc.sources if s.id == sid), None)
        bound = self.bindings.get(sid)
        if spec is None or bound is None:
            return None
        try:
            sheet = resolve_sheet(spec, bound)
            raw = read_raw(
                bound.path,
                sheet,
                csv_encoding=spec.csv.encoding,
                csv_delimiter=spec.csv.delimiter,
                csv_quote_char=spec.csv.quote_char,
            )
            positions: dict[str, int] = {}
            frame = extract_table(raw, spec.table, rename=spec.rename, row_numbers=True, positions=positions)
        except Exception:
            return None
        excel = file_kind(bound.path) == "excel"
        name, origin = _sheet_info(bound.path, sheet) if excel else (None, (1, 1))
        return _Located(spec, frame, positions, bound.file_name, bound.path, name, origin, excel)

    def _column(self, loc: _Located, name: str) -> str | None:
        if name in loc.frame.columns:
            return name
        want = _norm(name)
        return next((c for c in loc.frame.columns if _norm(c) == want), None)

    def row(self, sid: str, row: int | None) -> dict[str, Any] | None:
        loc = self.get(sid)
        if loc is None or row is None or not 0 <= int(row) < loc.frame.height:
            return None
        return loc.frame.row(int(row), named=True)

    def cell(self, sid: str, row: int | None, name: str) -> dict[str, Any] | None:
        loc = self.get(sid)
        values = self.row(sid, row)
        if loc is None or values is None:
            return None
        col = self._column(loc, name)
        raw_row = values.get(SHEET_ROW)
        pos = loc.positions.get(col) if col else None
        unpivot = loc.spec.table.unpivot
        if pos is None and unpivot is not None and col == unpivot.value_name:
            pos = loc.positions.get(str(values.get(unpivot.variable_name)))  # the month/branch column it was
        excel_row = loc.origin[0] + int(raw_row) if raw_row is not None else None
        column = _column_letter(loc.origin[1] + pos) if pos is not None else None
        out = {
            "source": sid,
            "file": loc.file,
            "sheet": loc.sheet,
            "row": excel_row,
            "column": column,
            "header": col or name,
            "raw": values.get(col) if col else None,
        }
        out["verified"] = (
            _verify(loc, excel_row, loc.origin[1] + pos, out["raw"])
            if loc.excel and excel_row is not None and pos is not None
            else None
        )
        return out


def _verify(loc: _Located, row: int, col: int, raw: Any) -> bool | None:
    """Read the cell back from the workbook: the position shown must hold the value the engine read."""
    if row > VERIFY_MAX_ROW:
        return None
    try:
        import openpyxl

        wb = openpyxl.load_workbook(loc.path, read_only=True, data_only=True)
        try:
            ws = wb[loc.sheet] if loc.sheet else wb.worksheets[0]
            got = next(ws.iter_rows(min_row=row, max_row=row, min_col=col, max_col=col, values_only=True))[0]
        finally:
            wb.close()
    except Exception:
        return None
    if got is None or raw is None:
        return got is None and raw is None
    if str(got).strip() == str(raw).strip():
        return True
    try:
        return abs(float(str(got).replace(",", "")) - float(str(raw).replace(",", ""))) < 1e-9
    except ValueError:
        return False


# --------------------------------------------------------------------------------------------
# one dataset version: how each value of a record was made
# --------------------------------------------------------------------------------------------
def _expr_columns(node: Any, default: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        if node.get("op") == "col" and node.get("name"):
            out.append((str(node.get("source") or default), str(node["name"])))
        for v in node.values():
            out.extend(_expr_columns(v, default))
    elif isinstance(node, list):
        for v in node:
            out.extend(_expr_columns(v, default))
    return list(dict.fromkeys(out))


def _plain(v: Any) -> Any:
    if isinstance(v, dt.date | dt.datetime):
        return v.isoformat()[:10]
    if isinstance(v, pl.Series):
        return v.to_list()
    return v


def _evaluate(fm: FieldMapping, default: str, rows: dict[str, dict[str, Any]]) -> Any:
    """What the mapping expression yields for these source rows (before overrides and manual edits)."""
    data = {f"{sid}.{k}": [v] for sid, row in rows.items() for k, v in row.items() if k != SHEET_ROW}
    if not data:
        return None
    frame = pl.DataFrame({k: pl.Series(k, v, dtype=pl.String, strict=False) for k, v in data.items()})
    try:
        ctx = CompileContext(default_source=default, columns=set(frame.columns))
        return _plain(frame.select(compile_expr(fm.expr, ctx).alias("v"))["v"].to_list()[0])
    except Exception:
        return None


def _coerce(v: Any, like: Any) -> Any:
    """Show the mapped value in the final value's type (the engine coerces numbers after the expression)."""
    if isinstance(like, bool) or not isinstance(like, int | float) or not isinstance(v, str):
        return v
    try:
        n = float(v.replace(",", ""))
    except ValueError:
        return v
    return int(n) if isinstance(like, int) and n.is_integer() else n


class _Tracer:
    def __init__(self, ctx: ServiceContext, session: Session, dv: DatasetVersion) -> None:
        self.ctx, self.session, self.dv = ctx, session, dv
        self.mv = get_mapping(session, dv.mapping_version_id)
        self.schema: TargetSchema = ctx.registry.get_ref(self.mv.target_schema)
        self.doc = MappingDocument.model_validate(self.mv.document)
        r = session.get(ImportRound, dv.round_id)
        bindings, self.problems = bind_sources(self.doc, r.files if r else [], resolve=ctx.storage.local_path)
        self.sources = _Sources(self.doc, bindings)

    def find(self, identity: str, dv: DatasetVersion | None = None) -> dict[str, Any] | None:
        for rec in iter_records(self.ctx.records_path(dv or self.dv)):
            if _identity_of(rec, self.schema) == identity:
                return rec
        return None

    def fields(self, rec: dict[str, Any]) -> list[dict[str, Any]]:
        lin = (rec.get(PROVENANCE_KEY) or {}).get("lineage") or {}
        root = self.doc.record.root_source
        root_row, joined = lin.get("root_row"), lin.get("joined") or {}
        out: list[dict[str, Any]] = []
        for fm in self.doc.fields:
            rows = {sid: joined.get(sid) if sid != root else root_row for sid in {root, *joined}}
            out.append(self._entry(fm, fm.target, root, rows, rec))
        for cm in self.doc.collections:
            for k, row in enumerate((lin.get("items") or {}).get(cm.target, [])[:MAX_ITEMS]):
                for fm in cm.fields:
                    pointer = fm.target.replace("/*/", f"/{k}/", 1)
                    out.append(self._entry(fm, pointer, cm.source, {cm.source: row}, rec, item=k))
        return out

    def _entry(
        self,
        fm: FieldMapping,
        pointer: str,
        default: str,
        rows: dict[str, Any],
        rec: dict[str, Any],
        item: int | None = None,
    ) -> dict[str, Any]:
        expr = fm.expr.model_dump(mode="json", exclude_none=True)
        cols = _expr_columns(expr, default)
        cells = [c for sid, name in cols if (c := self.sources.cell(sid, rows.get(sid), name)) is not None]
        values = {sid: v for sid in {s for s, _ in cols} if (v := self.sources.row(sid, rows.get(sid)))}
        final = _get_pointer(rec, pointer)
        mapped = _coerce(_evaluate(fm, default, values), final) if values or not cols else None
        return {
            "pointer": pointer,
            "field": fm.target,
            "item": item,
            "value": final,
            "mapped": mapped,
            "expr": expr,
            "confidence": fm.confidence,
            "evidence": fm.evidence,
            "cells": cells,
        }


# --------------------------------------------------------------------------------------------
# history and overrides
# --------------------------------------------------------------------------------------------
def _chain(session: Session, dv: DatasetVersion) -> list[DatasetVersion]:
    out: list[DatasetVersion] = []
    cur: DatasetVersion | None = dv
    while cur is not None and len(out) < MAX_VERSIONS:
        out.append(cur)
        cur = session.get(DatasetVersion, cur.parent_id) if cur.parent_id else None
    return out[::-1]


def _history(tr: _Tracer, identity: str) -> list[dict[str, Any]]:
    """Every version of this round's dataset that changed the record, oldest first, with who and why."""
    chain = _chain(tr.session, tr.dv)
    sets = {
        cs.result_version_id: cs
        for cs in tr.session.scalars(
            select(ChangeSet).where(ChangeSet.result_version_id.in_([v.id for v in chain]))
        )
    }
    steps: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for i, v in enumerate(chain):
        rec = tr.find(identity, v)
        cs = sets.get(v.id)
        kind = cs.kind if cs else ("rollback" if (v.note or "").startswith("rollback") else "transform")
        step = {
            "dataset_id": v.id,
            "version": v.version,
            "kind": kind,
            "by": cs.author if cs else v.created_by,
            "at": (cs.created_at if cs else v.created_at).isoformat(),
            "note": cs.note if cs else v.note,
            "present": rec is not None,
        }
        if i == 0:
            steps.append({**step, "changes": []})
        else:
            changes = _delta(prev, rec) if prev is not None and rec is not None else []
            if changes or (prev is None) != (rec is None):
                steps.append({**step, "changes": changes[:50]})
        prev = rec
    return steps


def _overrides(tr: _Tracer, identity: str, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Persistent overrides of this customer that apply to the record (re-applied on every import)."""
    out = []
    frame: pl.DataFrame | None = None
    for ov in active_overrides(tr.session, tr.dv.customer_id):
        where = ov["where"]
        if "identity" in where:
            hit = str(where["identity"]) == identity
        else:
            frame = frame if frame is not None else _flat_frame([rec], tr.schema, where_names(where))
            try:
                hit = bool(_where_mask(frame, where or None))
            except Exception:
                hit = False
        if hit:
            out.append(ov)
    return out


# --------------------------------------------------------------------------------------------
# why the value differs from the previous round
# --------------------------------------------------------------------------------------------
def _cell_text(c: dict[str, Any]) -> str:
    where = f"{c.get('sheet') or c.get('source')}!{c.get('column') or c.get('header')}{c.get('row') or ''}"
    return f"{where} ({c.get('header')}) = {c.get('raw')!r}"


def _cause(
    focus: dict[str, Any],
    prev: dict[str, Any] | None,
    prev_round: dict[str, Any] | None,
    edits_now: list[dict[str, Any]],
) -> dict[str, Any]:
    final = focus["value"]
    if prev_round is None:
        return {
            "kind": "no_baseline",
            "summary": "no earlier published round of this customer to compare with",
        }
    if prev is None:
        return {
            "kind": "new_record",
            "summary": f"this record is new: it was not in round {prev_round['label'] or prev_round['id']}",
        }
    before = prev["value"]
    if before == final:
        return {"kind": "unchanged", "summary": f"same value as the previous round ({final})"}
    evidence = [f"previous round: {before}", f"this round: {final}"]
    if edits_now:
        last = edits_now[-1]
        return {
            "kind": "manual",
            "summary": f"changed by hand: {last['by']} set it to {last['after']} ({last['kind']})",
            "evidence": [
                *evidence,
                *(f"v{e['version']}: {e['before']} → {e['after']} by {e['by']}" for e in edits_now),
            ],
        }
    raw_now = [c.get("raw") for c in focus.get("cells", [])]
    raw_before = [c.get("raw") for c in prev.get("cells", [])]
    if raw_now and raw_before and raw_now != raw_before:
        return {
            "kind": "source_changed",
            "summary": "the ERP file changed: the source cell holds a different value this round",
            "evidence": [
                *evidence,
                *(f"before: {_cell_text(c)}" for c in prev["cells"]),
                *(f"now: {_cell_text(c)}" for c in focus["cells"]),
            ],
        }
    if prev.get("expr") != focus.get("expr"):
        return {
            "kind": "mapping_changed",
            "summary": "the mapping changed: the same source is now read or transformed differently",
            "evidence": [*evidence, f"before: {prev.get('expr')}", f"now: {focus.get('expr')}"],
        }
    if prev.get("mapped") is not None and prev.get("mapped") != before:
        return {
            "kind": "previous_edit",
            "summary": "last round's value came from a manual edit that does not apply this round",
            "evidence": [*evidence, f"last round the mapping produced {prev['mapped']}, edited to {before}"],
        }
    return {
        "kind": "unknown",
        "summary": "the value changed but the source, mapping and edits look the same",
        "evidence": evidence,
    }


def trace(
    ctx: ServiceContext, session: Session, dataset_id: str, identity: str, pointer: str | None = None
) -> dict[str, Any]:
    from forma.core.errors import NotFound

    dv = get_dataset(session, dataset_id)
    tr = _Tracer(ctx, session, dv)
    rec = tr.find(identity)
    if rec is None:
        raise NotFound(f"record {identity} not in dataset")
    meta = rec.get(PROVENANCE_KEY) or {}
    root_cell = tr.sources.cell(tr.doc.record.root_source, (meta.get("lineage") or {}).get("root_row"), "")
    fields = tr.fields(rec)
    history = _history(tr, identity)
    out: dict[str, Any] = {
        "identity": identity,
        "dataset_id": dv.id,
        "version": dv.version,
        "mapping_version_id": tr.mv.id,
        "mapping_version": tr.mv.version,
        "source": {
            "file": meta.get("file"),
            "sheet": root_cell["sheet"] if root_cell else None,
            "row": root_cell["row"] if root_cell else None,
            "source": tr.doc.record.root_source,
        },
        "fields": fields,
        "history": history,
        "overrides": _overrides(tr, identity, rec),
        "problems": tr.problems,
        "focus": None,
    }
    if pointer:
        focus = next((f for f in fields if f["pointer"] == pointer), None) or {
            "pointer": pointer,
            "value": _get_pointer(rec, pointer),
            "cells": [],
            "expr": None,
            "mapped": None,
        }
        edits = [
            {
                "version": s["version"],
                "kind": s["kind"],
                "by": s["by"],
                "at": s["at"],
                "note": s["note"],
                "before": c["before"],
                "after": c["after"],
            }
            for s in history
            for c in s["changes"]
            if c["pointer"] == pointer and s["kind"] != "transform"
        ]
        prev_dv = previous_published(session, dv.customer_id, dv.round_id)
        prev_round = prev_entry = None
        if prev_dv is not None:
            pr = session.get(ImportRound, prev_dv.round_id)
            prev_round = {"id": prev_dv.round_id, "label": pr.label if pr else None, "dataset_id": prev_dv.id}
            ptr = _Tracer(ctx, session, prev_dv)
            prev_rec = ptr.find(identity)
            if prev_rec is not None:
                prev_entry = next((f for f in ptr.fields(prev_rec) if f["pointer"] == pointer), None) or {
                    "pointer": pointer,
                    "value": _get_pointer(prev_rec, pointer),
                    "cells": [],
                }
        out["focus"] = {
            **focus,
            "edits": edits,
            "overrides": [o for o in out["overrides"] if o["target"] in (pointer, focus.get("field"))],
            "previous": {"round": prev_round, **(prev_entry or {})} if prev_round else None,
            "cause": _cause(focus, prev_entry, prev_round, edits),
        }
    return out
