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
from forma.schema_registry import TargetSchema

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
def load_source_frame(
    spec: SourceSpec,
    bound: BoundSource,
    *,
    limit: int | None = None,
    expected_columns: set[str] | None = None,
) -> pl.DataFrame:
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
            hit = next((s for s in meta.sheets if rx.search(s.name)), None)
            if hit is None:
                raise ValueError(f"no sheet matches /{spec.sheet.match}/ in {bound.path.name}")
            sheet = hit.index
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
        out, fnd = self._dedupe(out, identity_cols, mapping.record.dedupe.keep)
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
        v_errors = self._validate(records)
        findings.extend(v_errors)

        stats = {
            "rows_in": rows_in,
            "rows_out": len(records),
            "sources": {k: v.height for k, v in frames.items()},
            "collections": {k: v.height for k, v in collections.items()},
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "identity_keys": identity_cols,
        }
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

    def _dedupe(self, out: pl.DataFrame, keys: list[str], keep: str) -> tuple[pl.DataFrame, list[Finding]]:
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
            sev = "error" if keep == "error" else "warning"
            findings.append(
                Finding(
                    "dedupe",
                    sev,
                    f"{dups.height} duplicate identities on {keys} (keep={keep})",
                    details={"sample": sample, "count": int(dups.height)},
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
            dedupe_keys = declared if declared and all(k in rel_names for k in declared) else list(rel_names)
        before = items.height
        items = items.unique(subset=["__link__", *dedupe_keys], keep="first", maintain_order=True)
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
        target_cols = [c for c in out.columns if c.startswith("/")]
        row_col = ROW_COL_OF(out)
        # group collection items by link key
        coll_index: dict[str, dict[str, list[dict[str, Any]]]] = {}
        coll_link_left: dict[str, list[str]] = {}
        for cm in mapping.collections:
            items = collections.get(cm.target)
            if items is None:
                continue
            idx: dict[str, list[dict[str, Any]]] = {}
            cols = [c for c in items.columns if c not in ("__link__", "__src_row__")]
            for row in items.iter_rows(named=True):
                item = {k: python_value(row[k]) for k in cols if row[k] is not None and row[k] != []}
                if mapping.provenance:
                    item["_row"] = row["__src_row__"]
                idx.setdefault(row["__link__"], []).append(item)
            coll_index[cm.target] = idx
            coll_link_left[cm.target] = [f"{root_id}.{k.left}" for k in cm.link]
        norm_cache: dict[str, Sequence[str]] = {cm.target: cm.normalize_keys for cm in mapping.collections}

        root_bound = bindings.get(root_id)
        meta_base = {
            **self.provenance_meta,
            "file": root_bound.file_name or root_bound.path.name if root_bound else None,
        }
        nullable = {f.pointer: f.nullable for f in self.schema.fields(include_system=True)}
        required_root = set(self.schema.required_leaf_pointers())

        records: list[dict[str, Any]] = []
        for row in out.iter_rows(named=True):
            rec: dict[str, Any] = {}
            for t in target_cols:
                v = python_value(row[t])
                if v is None and t not in required_root and not nullable.get(t, False):
                    continue
                _set_pointer(rec, t, v)
            for target, idx in coll_index.items():
                key = "\x1f".join(_py_norm(row.get(c), norm_cache[target]) for c in coll_link_left[target])
                linked = idx.get(key)
                if linked:
                    _set_pointer(
                        rec, target, [{k: v for k, v in it.items() if not k.startswith("_")} for it in linked]
                    )
            if passthrough:
                attrs = {
                    c.split(".", 1)[1]: python_value(row[c]) for c in passthrough if row.get(c) is not None
                }
                if attrs:
                    rec["attributes"] = attrs
            if mapping.provenance:
                rec.setdefault("source_meta", {}).update(
                    {k: v for k, v in meta_base.items() if v is not None}
                    | {"lineage": {"root_row": row[row_col], "source": root_id}}
                )
            records.append(rec)
        return records

    def _validate(self, records: list[dict[str, Any]]) -> list[Finding]:
        findings: list[Finding] = []
        try:
            import fastjsonschema

            validate = fastjsonschema.compile(self.schema.document)
        except Exception:  # pragma: no cover - fall back to jsonschema
            validate = None
        count = 0
        for i, rec in enumerate(records):
            errs: list[str] = []
            if validate is not None:
                try:
                    validate(rec)
                except Exception as e:
                    errs = [str(e)]
            else:
                errs = [f"{x['pointer']}: {x['message']}" for x in self.schema.validate_record(rec)]
            for m in errs:
                count += 1
                if count <= 200:
                    ident = "|".join(str(_get_pointer(rec, k)) for k in self.schema.identity_keys)
                    findings.append(Finding("validation", "error", m, row=i, identity=ident))
        if count > 200:
            findings.append(
                Finding("validation", "error", f"... {count - 200} more schema validation errors")
            )
        return findings


# --------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------
def ROW_COL_OF(frame: pl.DataFrame) -> str:
    for c in frame.columns:
        if c.endswith(f".{ROW_COL}") or c == ROW_COL:
            return c
    return frame.columns[0]


def _num(col: pl.Expr) -> pl.Expr:
    """Thai-aware numeric coercion used for type casting of mapped values."""
    s = col.cast(pl.String, strict=False).str.strip_chars()
    s = s.str.replace_many(list("๐๑๒๓๔๕๖๗๘๙"), list("0123456789"))
    s = s.str.replace_all(r"(?i)[,\s฿$€£]|บาท|thb|baht", "").str.strip_chars_end("%")
    return s.cast(pl.Float64, strict=False)


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
