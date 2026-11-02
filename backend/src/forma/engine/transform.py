"""Transformation engine: MappingDocument + bound source files → validated nested records.

Deterministic, chunk-friendly, no LLM anywhere. The same inputs always produce the same output.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from forma.engine.ops import CompileContext, compile_expr, python_value
from forma.ingestion.readers import read_raw
from forma.mapping_dsl.models import (
    CollectionMapping,
    Expr,
    JoinSpec,
    MappingDocument,
    QualityRule,
    SourceSpec,
)
from forma.profiling.tables import extract_table
from forma.schema_registry import PASSTHROUGH_KEY, PROVENANCE_KEY, TargetSchema

ROW_COL = "__row__"


@dataclass
class BoundSource:
    """Which physical file/sheet a DSL source id reads from."""

    path: Path
    sheet: int | str | None = None
    file_id: str | None = None
    file_name: str | None = None


@dataclass
class Finding:
    kind: str  # quality | validation | join | dedupe | compile
    severity: str  # error | warning | info
    message: str
    target: str | None = None
    row: int | None = None
    identity: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "message": self.message,
            "target": self.target,
            "row": self.row,
            "identity": self.identity,
            "details": self.details,
        }


@dataclass
class TransformResult:
    records: list[dict[str, Any]]
    root_frame: pl.DataFrame  # flat root columns (target pointers as names) incl. __row__
    findings: list[Finding]
    stats: dict[str, Any]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def summary(self) -> dict[str, Any]:
        by = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            by[f.severity] = by.get(f.severity, 0) + 1
        return {**self.stats, "findings": by}


# --------------------------------------------------------------------------------------------
# Loading sources
# --------------------------------------------------------------------------------------------
def resolve_sheet(spec: SourceSpec, bound: BoundSource) -> int | str | None:
    """The sheet (name or index) a source reads: bound by the binder, else named/indexed/matched by the spec."""
    sheet: int | str | None = bound.sheet
    if sheet is None and spec.sheet is not None:
        if spec.sheet.name is not None:
            sheet = spec.sheet.name
        elif spec.sheet.index is not None:
            sheet = spec.sheet.index
        elif spec.sheet.match is not None:
            from forma.ingestion.readers import read_meta

            rx = re.compile(spec.sheet.match, re.IGNORECASE)
            meta = read_meta(bound.path)
            hits = [s for s in meta.sheets if rx.search(s.name)]
            if not hits:
                raise ValueError(f"no sheet matches /{spec.sheet.match}/ in {bound.path.name}")
            if len(hits) != 1:
                raise ValueError(
                    f"sheet pattern /{spec.sheet.match}/ matches {len(hits)} sheets in {bound.path.name}; name one sheet"
                )
            sheet = hits[0].index
    return sheet


def load_source_frame(
    spec: SourceSpec,
    bound: BoundSource,
    *,
    limit: int | None = None,
    expected_columns: set[str] | None = None,
) -> pl.DataFrame:
    sheet = resolve_sheet(spec, bound)
    raw = read_raw(
        bound.path,
        sheet,
        csv_encoding=spec.csv.encoding,
        csv_delimiter=spec.csv.delimiter,
        csv_quote_char=spec.csv.quote_char,
    )
    frame = extract_table(raw, spec.table, rename=spec.rename)
    if expected_columns and spec.table.header_row != "auto":
        have = {_norm_col(c) for c in frame.columns}
        want = {_norm_col(c) for c in expected_columns}
        if want and len(want & have) < max(1, len(want) // 2):
            # header position drifted: pick the detected table whose headers cover the mapped columns best
            from forma.profiling.tables import detect_tables

            best_cand, best_hit = None, -1
            for cand in detect_tables(raw):
                hit_count = len(want & {_norm_col(h) for h in cand.headers})
                if hit_count > best_hit:
                    best_cand, best_hit = cand, hit_count
            if best_cand is not None and best_hit > len(want & have):
                alt = best_cand.to_spec().model_copy(
                    update={
                        "fill_down": spec.table.fill_down,
                        "unpivot": spec.table.unpivot,
                        "drop_total_rows": spec.table.drop_total_rows,
                        "drop_repeated_headers": spec.table.drop_repeated_headers,
                    }
                )
                frame = extract_table(raw, alt, rename=spec.rename)
    frame = frame.with_row_index(ROW_COL)
    if spec.table.filter is not None:
        ctx = CompileContext(default_source=spec.id, columns=set(_prefixed(frame, spec.id).columns))
        pf = _prefixed(frame, spec.id)
        pf = pf.filter(compile_expr(spec.table.filter, ctx).cast(pl.Boolean, strict=False).fill_null(False))
        frame = _unprefixed(pf, spec.id)
    if limit is not None:
        frame = frame.head(limit)
    return frame


def _norm_col(c: str) -> str:
    return re.sub(r"\s+", " ", str(c)).strip().lower()


def _prefixed(frame: pl.DataFrame, source_id: str) -> pl.DataFrame:
    return frame.rename({c: f"{source_id}.{c}" for c in frame.columns})


def _unprefixed(frame: pl.DataFrame, source_id: str) -> pl.DataFrame:
    p = f"{source_id}."
    return frame.rename({c: c[len(p) :] for c in frame.columns if c.startswith(p)})


def _normalize_key(expr: pl.Expr, ops: Sequence[str]) -> pl.Expr:
    e = expr.cast(pl.String, strict=False)
    for op in ops:
        if op == "trim":
            e = e.str.strip_chars()
        elif op == "upper":
            e = e.str.to_uppercase()
        elif op == "strip_leading_zeros":
            e = e.str.replace(r"^0+(\d)", "${1}")  # no look-around in Polars regex
        elif op == "normalize_whitespace":
            e = e.str.replace_all(r"\s+", " ")
    return e


# --------------------------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------------------------
class TransformEngine:
    def __init__(self, schema: TargetSchema, *, provenance_meta: dict[str, Any] | None = None) -> None:
        self.schema = schema
        self.provenance_meta = provenance_meta or {}

    # ---- public --------------------------------------------------------------------------
    def run(
        self, mapping: MappingDocument, bindings: dict[str, BoundSource], *, limit: int | None = None
    ) -> TransformResult:
        t0 = time.perf_counter()
        findings: list[Finding] = []
        frames: dict[str, pl.DataFrame] = {}
        used_sources = (
            {mapping.record.root_source}
            | {c.source for c in mapping.collections}
            | {j.right for j in mapping.joins}
            | {j.left for j in mapping.joins}
        )
        for spec in mapping.sources:
            b = bindings.get(spec.id)
            if b is None:
                if spec.id in used_sources:
                    findings.append(Finding("compile", "error", f"source {spec.id!r} is not bound to a file"))
                continue
            try:
                if spec.id in used_sources and b.path.suffix.lower() in {".xlsx", ".xlsm"}:
                    from forma.ingestion.ooxml import inspect_workbook

                    selected = resolve_sheet(spec, b)
                    checked = inspect_workbook(b.path)
                    sheet_info = (
                        checked[selected or 0]
                        if selected is None or isinstance(selected, int)
                        else next(s for s in checked if s.name == selected)
                    )
                    notices = []
                    if sheet_info.formula_cells:
                        notices.append(
                            "formula values are saved Excel caches; FORMA does not recalculate or verify cache freshness"
                        )
                    if sheet_info.hidden or sheet_info.hidden_rows or sheet_info.hidden_columns:
                        notices.append(
                            "hidden sheet/rows/columns are included; confirm they belong in this import"
                        )
                    if sheet_info.display_format_warnings:
                        notices.append(
                            "complex numeric display formats were not interpreted; verify values/identifiers: "
                            + ", ".join(sheet_info.display_format_warnings)
                        )
                    for notice in notices:
                        findings.append(Finding("source_review", "warning", f"{spec.id}: {notice}"))
                elif spec.id in used_sources and b.path.suffix.lower() in {".xls", ".xlsb"}:
                    findings.append(
                        Finding(
                            "source_review",
                            "warning",
                            f"{spec.id}: legacy/binary Excel values read without OOXML formula/style inspection; verify or save as .xlsx",
                        )
                    )
                frames[spec.id] = _prefixed(
                    load_source_frame(
                        spec, b, limit=limit, expected_columns=_source_columns(mapping, spec.id)
                    ),
                    spec.id,
                )
            except Exception as e:
                sev = "error" if spec.id in used_sources else "warning"
                findings.append(Finding("compile", sev, f"source {spec.id!r}: {e}"))
        if any(f.severity == "error" for f in findings):
            return TransformResult([], pl.DataFrame(), findings, {"rows_in": 0, "rows_out": 0})

        root_id = mapping.record.root_source
        root = frames[root_id]
        rows_in = root.height

        # joins (left side must already be reachable from root; applied in declared order)
        joined_ids = {root_id}
        for j in mapping.joins:
            if j.left not in joined_ids and j.left in frames:
                # join into a non-root frame (e.g. pricing ← price list names) : apply to that frame
                frames[j.left], fnd = self._join(frames[j.left], frames[j.right], j)
                findings.extend(fnd)
                continue
            root, fnd = self._join(root, frames[j.right], j)
            findings.extend(fnd)
            joined_ids.add(j.right)

        # root fields
        ctx = CompileContext(default_source=root_id, columns=set(root.columns))
        exprs: list[pl.Expr] = []
        for fm in mapping.fields:
            try:
                exprs.append(compile_expr(fm.expr, ctx).alias(fm.target))
            except Exception as e:
                findings.append(Finding("compile", "error", f"{fm.target}: {e}", target=fm.target))
        for m in dict.fromkeys(ctx.missing):
            findings.append(
                Finding("compile", "warning", f"source column {m!r} not found; values will be null")
            )
        if any(f.severity == "error" for f in findings):
            return TransformResult([], pl.DataFrame(), findings, {"rows_in": rows_in, "rows_out": 0})

        out = root.with_columns(exprs) if exprs else root
        out = self._coerce_types(out, [fm.target for fm in mapping.fields])

        # record filters
        for flt in mapping.record.filters:
            out = out.filter(compile_expr(flt, ctx).cast(pl.Boolean, strict=False).fill_null(False))

        # identity + dedupe
        identity_cols = mapping.record.dedupe.keys or self.schema.identity_keys
        identity_cols = [c for c in identity_cols if c in out.columns]
        child_columns = {
            name
            for cm in mapping.collections
            if cm.source == root_id
            for fm in cm.fields
            for name in _referenced_columns(fm.expr, cm.source)
        }
        # Child-level variation is preserved in collections; root output conflicts still matter.
        out, fnd = self._dedupe(out, identity_cols, mapping.record.dedupe.keep, ignored=child_columns)
        findings.extend(fnd)

        # collections
        collections: dict[str, pl.DataFrame] = {}
        for cm in mapping.collections:
            if cm.source not in frames:
                findings.append(
                    Finding("compile", "error", f"collection source {cm.source!r} unknown", target=cm.target)
                )
                continue
            try:
                items, fnd = self._collection(frames[cm.source], cm, out, root_id)
                findings.extend(fnd)
                if items.width:  # a collection that cannot be linked is left out; its finding says why
                    collections[cm.target] = items
            except Exception as e:
                findings.append(Finding("compile", "error", f"{cm.target}: {e}", target=cm.target))

        # attributes passthrough (unmapped root columns)
        mapped_cols = set()
        for fm in mapping.fields:
            mapped_cols |= _referenced_columns(fm.expr, root_id)
        for cm in mapping.collections:
            if cm.source == root_id:
                for fm in cm.fields:
                    mapped_cols |= _referenced_columns(fm.expr, root_id)
                mapped_cols |= {f"{root_id}.{k.right}" for k in cm.link}
        for j in mapping.joins:
            if j.left == root_id:
                mapped_cols |= {f"{root_id}.{k.left}" for k in j.on}
        passthrough = (
            [
                c
                for c in root.columns
                if c.startswith(f"{root_id}.") and c not in mapped_cols and c != f"{root_id}.{ROW_COL}"
            ]
            if mapping.attributes_passthrough
            else []
        )

        # quality rules
        findings.extend(self._quality(out, mapping.quality, identity_cols))

        # build records
        records = self._build_records(
            out, collections, passthrough, root_id, identity_cols, mapping, bindings
        )
        # schema validation
        v_errors, invalid = self._validate(records)
        findings.extend(v_errors)
        # Business invariants belong to the pinned contract. The proposed DSL cannot remove them.
        from forma.ai.contract_checks import evaluate as evaluate_contract_checks

        findings.extend(
            Finding("contract_check", "error", **item)
            for item in evaluate_contract_checks(records, self.schema.document)
        )

        stats = {
            "rows_in": rows_in,
            "rows_out": len(records),
            "sources": {k: v.height for k, v in frames.items()},
            "collections": {k: v.height for k, v in collections.items()},
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "identity_keys": identity_cols,
            "invalid_records": invalid,  # records that do not match the target schema (ADR-026)
        }
        _observe_engine(
            rows_in,
            len(records),
            time.perf_counter() - t0,
            errors=sum(1 for f in findings if f.severity == "error"),
        )
        return TransformResult(records, out, findings, stats)

    # ---- internals ----------------------------------------------------------------------
    def _join(
        self, left: pl.DataFrame, right: pl.DataFrame, j: JoinSpec
    ) -> tuple[pl.DataFrame, list[Finding]]:
        findings: list[Finding] = []
        lkeys = [f"{j.left}.{k.left}" for k in j.on]
        rkeys = [f"{j.right}.{k.right}" for k in j.on]
        for k in lkeys:
            if k not in left.columns:
                return left, [Finding("join", "error", f"join key {k!r} missing on left side")]
        for k in rkeys:
            if k not in right.columns:
                return left, [Finding("join", "error", f"join key {k!r} missing on right side")]
        ltmp = [f"__lk{i}" for i in range(len(lkeys))]
        rtmp = [f"__rk{i}" for i in range(len(rkeys))]
        left2 = left.with_columns(
            [_normalize_key(pl.col(k), j.normalize_keys).alias(t) for k, t in zip(lkeys, ltmp, strict=True)]
        )
        right2 = right.with_columns(
            [_normalize_key(pl.col(k), j.normalize_keys).alias(t) for k, t in zip(rkeys, rtmp, strict=True)]
        )
        # cardinality check on the right side
        dup = (
            right2.filter(pl.all_horizontal([pl.col(t).is_not_null() for t in rtmp]))
            .group_by(rtmp)
            .len()
            .filter(pl.col("len") > 1)
        )
        if dup.height and j.cardinality in ("1:1", "N:1"):
            findings.append(
                Finding(
                    "join",
                    "warning",
                    f"join {j.left}→{j.right}: {dup.height} right keys are duplicated but cardinality is {j.cardinality}",
                    details={"sample_keys": dup.head(5).select(rtmp).rows()},
                )
            )
            if j.cardinality != "1:N":  # type: ignore[comparison-overlap]
                right2 = right2.unique(subset=rtmp, keep="first", maintain_order=True)
        how: Literal["inner", "left"] = "inner" if j.how == "inner" else "left"
        merged = left2.join(right2, left_on=ltmp, right_on=rtmp, how=how, coalesce=False, suffix="__dup")
        merged = merged.drop([c for c in merged.columns if c.startswith("__lk") or c.startswith("__rk")])
        if j.how == "left":
            probe = f"{j.right}.{ROW_COL}"
            if probe in merged.columns:
                unmatched = merged.filter(pl.col(probe).is_null()).height
                if unmatched:
                    sev = (
                        "error"
                        if j.on_unmatched == "error"
                        else "warning"
                        if j.on_unmatched in ("review", "keep")
                        else "info"
                    )
                    findings.append(
                        Finding(
                            "join",
                            sev,
                            f"join {j.left}→{j.right}: {unmatched} left rows have no match",
                            details={"unmatched": unmatched, "policy": j.on_unmatched},
                        )
                    )
                    if j.on_unmatched == "drop":
                        merged = merged.filter(pl.col(probe).is_not_null())
        return merged, findings

    def _coerce_types(self, out: pl.DataFrame, targets: list[str]) -> pl.DataFrame:
        exprs = []
        for t in targets:
            if t not in out.columns:
                continue
            try:
                f = self.schema.field(t)
            except Exception:
                continue
            types = set(f.json_type)
            col = pl.col(t)
            if "integer" in types:
                exprs.append(_num(col).round(0).cast(pl.Int64, strict=False).alias(t))
            elif "number" in types:
                exprs.append(_num(col).alias(t))
            elif "boolean" in types:
                exprs.append(col.cast(pl.Boolean, strict=False).alias(t))
            elif "string" in types and f.fmt == "date":
                if out.schema[t] in (pl.Date, pl.Datetime):
                    exprs.append(col.cast(pl.Date, strict=False).dt.strftime("%Y-%m-%d").alias(t))
                else:
                    exprs.append(col.cast(pl.String, strict=False).alias(t))
            elif "string" in types and "array" not in types:
                e = col.cast(pl.String, strict=False) if out.schema[t] != pl.List(pl.String) else col
                if "null" not in types and not f.required:
                    pass
                exprs.append(e.alias(t))
        return out.with_columns(exprs) if exprs else out

    def _dedupe(
        self, out: pl.DataFrame, keys: list[str], keep: str, *, ignored: set[str] | None = None
    ) -> tuple[pl.DataFrame, list[Finding]]:
        if not keys:
            return out, []
        dups = (
            out.filter(pl.all_horizontal([pl.col(k).is_not_null() for k in keys]))
            .group_by(keys)
            .len()
            .filter(pl.col("len") > 1)
        )
        findings: list[Finding] = []
        if dups.height:
            sample = [",".join(str(x) for x in r[: len(keys)]) for r in dups.head(10).rows()]
            # the same row twice is noise; two rows of one identity that disagree are a decision nobody made
            others = [
                c
                for c in out.columns
                if c not in keys and c not in (ignored or set()) and not c.endswith(ROW_COL)
            ]
            disagree = (
                out.filter(pl.all_horizontal([pl.col(k).is_not_null() for k in keys]))
                .group_by(keys)
                .agg([pl.col(c).n_unique().alias(c) for c in others])
                .filter(pl.any_horizontal([pl.col(c) > 1 for c in others]) if others else pl.lit(False))
            )
            conflicts = [c for c in others if disagree.height and (disagree[c] > 1).any()]
            sev = "error" if keep == "error" or conflicts else "warning"
            what = (
                f"{disagree.height} of them with different values in {', '.join(_short(c) for c in conflicts[:4])}"
                if conflicts
                else "identical rows"
            )
            findings.append(
                Finding(
                    "dedupe",
                    sev,
                    f"{dups.height} duplicate identities on {keys} ({what}; keep={keep})",
                    details={
                        "sample": sample,
                        "count": int(dups.height),
                        "conflicting": int(disagree.height),
                    },
                )
            )
            if keep in ("first", "last", "review"):
                out = out.unique(subset=keys, keep="first" if keep == "review" else keep, maintain_order=True)  # type: ignore[arg-type]
        missing = out.filter(pl.any_horizontal([pl.col(k).is_null() for k in keys])).height
        if missing:
            findings.append(
                Finding(
                    "dedupe",
                    "error",
                    f"{missing} rows have a null identity key {keys}",
                    details={"count": missing},
                )
            )
            out = out.filter(pl.all_horizontal([pl.col(k).is_not_null() for k in keys]))
        return out, findings

    def _collection(
        self, src: pl.DataFrame, cm: CollectionMapping, root: pl.DataFrame, root_id: str
    ) -> tuple[pl.DataFrame, list[Finding]]:
        findings: list[Finding] = []
        ctx = CompileContext(default_source=cm.source, columns=set(src.columns))
        frame = src
        if cm.filter is not None:
            frame = frame.filter(compile_expr(cm.filter, ctx).cast(pl.Boolean, strict=False).fill_null(False))
        item_exprs = []
        rel_names = []
        for fm in cm.fields:
            rel = fm.target[len(cm.target) + 3 :]  # strip "/prices/*/"
            rel_names.append(rel)
            item_exprs.append(compile_expr(fm.expr, ctx).alias(rel))
        for m in dict.fromkeys(ctx.missing):
            findings.append(
                Finding("compile", "warning", f"{cm.target}: source column {m!r} not found", target=cm.target)
            )
        link_right = [f"{cm.source}.{k.right}" for k in cm.link]
        for k in link_right:
            if k not in frame.columns:
                return pl.DataFrame(), [
                    Finding("join", "error", f"{cm.target}: link column {k!r} missing", target=cm.target)
                ]
        items = frame.with_columns(item_exprs).with_columns(
            pl.concat_str(
                [_normalize_key(pl.col(k), cm.normalize_keys).fill_null("") for k in link_right],
                separator="\x1f",
            ).alias("__link__")
        )
        items = items.with_columns(pl.col(f"{cm.source}.{ROW_COL}").alias("__src_row__"))
        # type coercion per item field
        coerce = []
        for fm, rel in zip(cm.fields, rel_names, strict=True):
            f = self.schema.field(fm.target)
            types = set(f.json_type)
            c = pl.col(rel)
            if "integer" in types:
                coerce.append(_num(c).round(0).cast(pl.Int64, strict=False).alias(rel))
            elif "number" in types:
                coerce.append(_num(c).alias(rel))
            elif "boolean" in types:
                coerce.append(c.cast(pl.Boolean, strict=False).alias(rel))
            elif f.fmt == "date" and items.schema[rel] in (pl.Date, pl.Datetime):
                coerce.append(c.cast(pl.Date, strict=False).dt.strftime("%Y-%m-%d").alias(rel))
            elif "string" in types and "array" not in types and items.schema[rel] != pl.List(pl.String):
                coerce.append(c.cast(pl.String, strict=False).alias(rel))
        if coerce:
            items = items.with_columns(coerce)
        dedupe_keys = [k.lstrip("/") for k in cm.dedupe_keys if k.lstrip("/") in rel_names]
        if not dedupe_keys:
            # default: schema-declared item identity if fully mapped, else every mapped item field
            declared = [k.lstrip("/") for k in self.schema.collection_identity_keys(cm.target)]
            dedupe_keys = [k for k in declared if k in rel_names] or list(rel_names)
        before = items.height
        # Drop exact duplicates only. Conflicting alternatives must survive for a person to resolve.
        items = items.unique(subset=["__link__", *rel_names], keep="first", maintain_order=True)
        conflicts = items.group_by(["__link__", *dedupe_keys]).len().filter(pl.col("len") > 1)
        if conflicts.height:
            findings.append(
                Finding(
                    "dedupe",
                    "error",
                    f"{cm.target}: {conflicts.height} collection identities have conflicting values",
                    target=cm.target,
                    details={"conflicting": conflicts.height},
                )
            )
        if items.height < before:
            findings.append(
                Finding(
                    "dedupe",
                    "info",
                    f"{cm.target}: dropped {before - items.height} duplicate items",
                    target=cm.target,
                )
            )
        # orphan items: link key not present on any root record
        link_left = [f"{root_id}.{k.left}" for k in cm.link]
        if all(k in root.columns for k in link_left):
            root_keys = root.select(
                pl.concat_str(
                    [_normalize_key(pl.col(k), cm.normalize_keys).fill_null("") for k in link_left],
                    separator="\x1f",
                ).alias("__link__")
            ).unique()
            orphans = items.join(root_keys, on="__link__", how="anti").height
            if orphans:
                findings.append(
                    Finding(
                        "join",
                        "warning",
                        f"{cm.target}: {orphans} items reference unknown root records",
                        target=cm.target,
                        details={"orphans": orphans},
                    )
                )
        return items.select(["__link__", "__src_row__", *rel_names]), findings

    def _quality(
        self, out: pl.DataFrame, rules: list[QualityRule], identity_cols: list[str]
    ) -> list[Finding]:
        findings: list[Finding] = []
        ident = (
            pl.concat_str([pl.col(c).cast(pl.String).fill_null("") for c in identity_cols], separator="|")
            if identity_cols
            else pl.lit("")
        )
        for q in rules:
            if q.target not in out.columns:
                continue
            col = pl.col(q.target)
            bad: pl.Expr | None = None
            if q.rule == "not_null":
                bad = col.is_null()
            elif q.rule == "unique":
                bad = col.is_duplicated() & col.is_not_null()
            elif q.rule == "positive":
                bad = _num(col) <= 0
            elif q.rule == "range":
                lo, hi = q.params.get("min"), q.params.get("max")
                bad = pl.lit(False)
                if lo is not None:
                    bad = bad | (_num(col) < float(lo))
                if hi is not None:
                    bad = bad | (_num(col) > float(hi))
            elif q.rule == "enum":
                bad = (
                    ~col.cast(pl.String).is_in([str(v) for v in q.params.get("values", [])])
                    & col.is_not_null()
                )
            elif q.rule == "regex":
                bad = (
                    ~col.cast(pl.String).str.contains(str(q.params.get("pattern", ".*"))) & col.is_not_null()
                )
            elif q.rule == "max_length":
                bad = col.cast(pl.String).str.len_chars() > int(q.params.get("value", 255))
            elif q.rule == "date_order":
                other = q.params.get("after")
                if other in out.columns:
                    bad = (
                        (col.cast(pl.String) < pl.col(other).cast(pl.String))
                        & col.is_not_null()
                        & pl.col(other).is_not_null()
                    )
            if bad is None:
                continue
            hits = out.filter(bad.fill_null(False)).select(
                [pl.col(ROW_COL_OF(out)).alias("row"), ident.alias("id"), col.alias("value")]
            )
            if hits.height:
                findings.append(
                    Finding(
                        "quality",
                        q.severity,
                        q.message or f"{q.target}: {hits.height} rows fail rule {q.rule}",
                        target=q.target,
                        details={
                            "count": hits.height,
                            "rule": q.rule,
                            "sample": [
                                {"row": r[0], "identity": r[1], "value": python_value(r[2])}
                                for r in hits.head(10).rows()
                            ],
                        },
                    )
                )
        return findings

    def _build_records(
        self,
        out: pl.DataFrame,
        collections: dict[str, pl.DataFrame],
        passthrough: list[str],
        root_id: str,
        identity_cols: list[str],
        mapping: MappingDocument,
        bindings: dict[str, BoundSource],
    ) -> list[dict[str, Any]]:
        """Flat frame + collection frames → nested records.

        Hot path (ADR-010 / docs/PERFORMANCE.md): everything that can be computed per *column* is done
        in Polars first (date → ISO strings, collection link keys), then rows are walked as tuples with
        pre-split pointer paths. Output is byte-identical to the previous per-cell implementation.
        """
        target_cols = [c for c in out.columns if c.startswith("/")]
        row_col = ROW_COL_OF(out)

        # ---- collections: group items by link key -------------------------------------------
        coll_index: dict[str, dict[str, list[dict[str, Any]]]] = {}
        link_exprs: list[pl.Expr] = []
        for cm in mapping.collections:
            items = collections.get(cm.target)
            if items is None:
                continue
            cols = [c for c in items.columns if c not in ("__link__", "__src_row__")]
            items = items.with_columns(_date_to_str(items, cols))
            idx: dict[str, list[dict[str, Any]]] = {}
            link_i = items.columns.index("__link__")
            src_i = items.columns.index("__src_row__")
            col_i = [(items.columns.index(c), c) for c in cols]
            for row in items.iter_rows():
                item = {k: row[i] for i, k in col_i if row[i] is not None and row[i] != []}
                if mapping.provenance:
                    item["_row"] = row[src_i]
                idx.setdefault(row[link_i], []).append(item)
            coll_index[cm.target] = idx
            link_left = [f"{root_id}.{k.left}" for k in cm.link]
            link_exprs.append(
                pl.concat_str(
                    [
                        _normalize_key(pl.col(c), cm.normalize_keys).fill_null("")
                        if c in out.columns
                        else pl.lit("")
                        for c in link_left
                    ],
                    separator="\x1f",
                ).alias(f"__lk__{cm.target}")
            )

        root_bound = bindings.get(root_id)
        meta_base = {
            **self.provenance_meta,
            "file": root_bound.file_name or root_bound.path.name if root_bound else None,
        }
        meta_base = {k: v for k, v in meta_base.items() if v is not None}
        nullable = {f.pointer: f.nullable for f in self.schema.fields(include_system=True)}
        required_root = set(self.schema.required_leaf_pointers())

        # ---- vectorised prep: dates → strings, link keys as columns ---------------------------
        frame = out.with_columns([*_date_to_str(out, [*target_cols, *passthrough]), *link_exprs])
        cols = frame.columns
        plan = [
            (cols.index(t), _segs(t), (t not in required_root and not nullable.get(t, False)))
            for t in target_cols
        ]
        coll_plan = [
            (cols.index(f"__lk__{target}"), target, _segs(target), idx) for target, idx in coll_index.items()
        ]
        pass_plan = [(cols.index(c), c.split(".", 1)[1]) for c in passthrough]
        row_i = cols.index(row_col)
        provenance = mapping.provenance
        # lineage: the row of each joined lookup table this record took columns from
        join_plan = [
            (cols.index(f"{j.right}.{ROW_COL}"), j.right)
            for j in mapping.joins
            if j.left == root_id and f"{j.right}.{ROW_COL}" in cols
        ]

        records: list[dict[str, Any]] = []
        for row in frame.iter_rows():
            rec: dict[str, Any] = {}
            for i, segs, skip_null in plan:
                v = row[i]
                if v is None and skip_null:
                    continue
                _set_segs(rec, segs, v)
            item_rows: dict[str, list[Any]] = {}
            for i, target, segs, idx in coll_plan:
                linked = idx.get(row[i])
                if linked:
                    _set_segs(
                        rec, segs, [{k: v for k, v in it.items() if not k.startswith("_")} for it in linked]
                    )
                    if provenance:
                        item_rows[target] = [it.get("_row") for it in linked]
            if pass_plan:
                attrs = {name: row[i] for i, name in pass_plan if row[i] is not None}
                if attrs:
                    rec[PASSTHROUGH_KEY] = attrs
            if provenance:
                lineage: dict[str, Any] = {"root_row": row[row_i], "source": root_id}
                if item_rows:
                    lineage["items"] = item_rows  # collection → source row of each item, in order
                joined = {sid: row[i] for i, sid in join_plan if row[i] is not None}
                if joined:
                    lineage["joined"] = joined
                rec.setdefault(PROVENANCE_KEY, {}).update(meta_base | {"lineage": lineage})
            records.append(rec)
        return records

    def _validate(self, records: list[dict[str, Any]]) -> tuple[list[Finding], int]:
        """Schema errors as findings (the first 200) and the number of records that have any."""
        findings: list[Finding] = []
        try:
            import fastjsonschema

            validate = fastjsonschema.compile(self.schema.document)
        except Exception:  # pragma: no cover - fall back to jsonschema
            validate = None
        count = invalid = 0
        for i, rec in enumerate(records):
            errs: list[str] = []
            if validate is not None:
                view = self.schema.export_view(rec)  # validate what will be exported
                try:
                    validate(view)
                except Exception as e:
                    errs = [str(e)]
                if view is not rec:
                    rec.update(view)  # keep the defaults the validator filled in
            else:
                errs = [f"{x['pointer']}: {x['message']}" for x in self.schema.validate_record(rec)]
            invalid += bool(errs)
            for m in errs:
                count += 1
                if count <= 200:
                    ident = "|".join(str(_get_pointer(rec, k)) for k in self.schema.identity_keys)
                    findings.append(Finding("validation", "error", m, row=i, identity=ident))
        if count > 200:
            findings.append(
                Finding("validation", "error", f"... {count - 200} more schema validation errors")
            )
        return findings, invalid


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def _observe_engine(rows_in: int, rows_out: int, seconds: float, *, errors: int) -> None:
    """Metrics + a span event per engine run (ADR-014); never fails the run."""
    try:
        from forma.core import metrics
        from forma.core.telemetry import span

        metrics.ENGINE_ROWS.labels(stage="in").inc(rows_in)
        metrics.ENGINE_ROWS.labels(stage="out").inc(rows_out)
        metrics.ENGINE_DURATION.observe(seconds)
        with span(
            "engine.run", rows_in=rows_in, rows_out=rows_out, duration_s=round(seconds, 3), errors=errors
        ):
            pass
    except Exception:  # pragma: no cover - observability must never break the engine
        return


def _short(column: str) -> str:
    """A frame column as a person reads it: the target pointer, or the source column without its prefix."""
    return column if column.startswith("/") else column.split(".", 1)[-1]


def ROW_COL_OF(frame: pl.DataFrame) -> str:
    for c in frame.columns:
        if c.endswith(f".{ROW_COL}") or c == ROW_COL:
            return c
    return frame.columns[0]


def _num(col: pl.Expr) -> pl.Expr:
    """Thai-aware numeric coercion used for type casting of mapped values (the same forms as parse_number_th)."""
    from forma.engine.ops import number_text_expr

    return number_text_expr(col).cast(pl.Float64, strict=False)


def _py_norm(v: Any, ops: Sequence[str]) -> str:
    s = "" if v is None else str(v)
    for op in ops:
        if op == "trim":
            s = s.strip()
        elif op == "upper":
            s = s.upper()
        elif op == "strip_leading_zeros":
            s = re.sub(r"^0+(?=\d)", "", s)
        elif op == "normalize_whitespace":
            s = re.sub(r"\s+", " ", s)
    return s


def _date_to_str(frame: pl.DataFrame, cols: list[str]) -> list[pl.Expr]:
    """Date/Datetime columns → ISO date strings in Polars (what ``python_value`` did per cell)."""
    exprs: list[pl.Expr] = []
    for c in cols:
        if c not in frame.columns:
            continue
        dt_ = frame.schema[c]
        if dt_ == pl.Date:
            exprs.append(pl.col(c).cast(pl.String).alias(c))
        elif isinstance(dt_, pl.Datetime):
            exprs.append(pl.col(c).dt.date().cast(pl.String).alias(c))
    return exprs


def _segs(pointer: str) -> tuple[str, ...]:
    return tuple(p for p in pointer.split("/") if p)


def _set_segs(obj: dict[str, Any], segs: tuple[str, ...], value: Any) -> None:
    cur: Any = obj
    for p in segs[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur.setdefault(p, {})
    last = segs[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _set_pointer(obj: dict[str, Any], pointer: str, value: Any) -> None:
    parts = [p for p in pointer.split("/") if p]
    cur: Any = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if isinstance(cur, list) else cur.setdefault(p, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def _get_pointer(obj: dict[str, Any], pointer: str) -> Any:
    cur: Any = obj
    for p in [p for p in pointer.split("/") if p]:
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


def _source_columns(mapping: MappingDocument, source_id: str) -> set[str]:
    """Raw column names the mapping reads from one source (used to verify header detection)."""
    cols: set[str] = set()
    default = mapping.record.root_source
    exprs: list[tuple[Expr, str]] = [(f.expr, default) for f in mapping.fields]
    for c in mapping.collections:
        exprs.extend((f.expr, c.source) for f in c.fields)
        if c.source == source_id:
            cols |= {k.right for k in c.link}
        if default == source_id:
            cols |= {k.left for k in c.link}
    for j in mapping.joins:
        if j.left == source_id:
            cols |= {k.left for k in j.on}
        if j.right == source_id:
            cols |= {k.right for k in j.on}
    for e, d in exprs:
        for ref in _referenced_columns(e, d):
            src, _, name = ref.partition(".")
            if src == source_id:
                cols.add(name)
    return cols


def _referenced_columns(e: Expr, default_source: str) -> set[str]:
    from forma.mapping_dsl.models import CallExpr, ColExpr

    out: set[str] = set()
    stack: list[Expr] = [e]
    while stack:
        n = stack.pop()
        if isinstance(n, ColExpr):
            out.add(f"{n.source or default_source}.{n.name}")
        elif isinstance(n, CallExpr):
            stack.extend(n.args)
    return out


def iter_jsonl(records: list[dict[str, Any]]) -> Iterator[bytes]:
    import orjson

    for r in records:
        yield orjson.dumps(r) + b"\n"
