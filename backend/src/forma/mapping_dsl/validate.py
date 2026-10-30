"""Static validation of a MappingDocument against a TargetSchema (no data needed)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forma.mapping_dsl.models import (
    CallExpr,
    ColExpr,
    CollectionMapping,
    ConstExpr,
    Expr,
    MappingDocument,
    Op,
)
from forma.schema_registry import TargetSchema
from forma.schema_registry.registry import mapping_support_issues


@dataclass
class Issue:
    level: str  # error | warning
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"level": self.level, "code": self.code, "message": self.message, "path": self.path}


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.issues)

    def error(self, code: str, message: str, path: str = "") -> None:
        self.issues.append(Issue("error", code, message, path))

    def warn(self, code: str, message: str, path: str = "") -> None:
        self.issues.append(Issue("warning", code, message, path))

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


def _walk_expr(e: Expr) -> list[Expr]:
    out: list[Expr] = [e]
    if isinstance(e, CallExpr):
        for a in e.args:
            out.extend(_walk_expr(a))
    return out


def _check_expr(e: Expr, path: str, rep: ValidationReport, source_ids: set[str]) -> None:
    for node in _walk_expr(e):
        if isinstance(node, ColExpr) and node.source and node.source not in source_ids:
            rep.error(
                "unknown_source", f"column {node.name!r} refers to unknown source {node.source!r}", path
            )
        if isinstance(node, CallExpr):
            p = node.params
            if (
                node.op in (Op.REGEX_EXTRACT, Op.REGEX_REPLACE, Op.CONTAINS, Op.STARTS_WITH)
                and "pattern" not in p
            ):
                rep.error("missing_param", f"{node.op} requires params.pattern", path)
            if node.op == Op.CAST and p.get("to") not in {"string", "int", "float", "bool", "date"}:
                rep.error("bad_param", "cast requires params.to in string|int|float|bool|date", path)
            if node.op == Op.MAP_VALUES and not isinstance(p.get("mapping"), dict):
                rep.error("missing_param", "map_values requires params.mapping (dict)", path)
            if node.op == Op.VAT_ADJUST and p.get("mode") not in {"add", "remove"}:
                rep.error("bad_param", "vat_adjust requires params.mode in add|remove", path)
            if node.op == Op.IN and not isinstance(p.get("values"), list):
                rep.error("missing_param", "in requires params.values (list)", path)
            if node.op == Op.LOOKUP and not isinstance(p.get("table"), dict) and "source" not in p:
                rep.error("missing_param", "lookup requires params.table (dict) or params.source", path)


def validate_mapping(doc: MappingDocument, schema: TargetSchema) -> ValidationReport:
    rep = ValidationReport()
    for issue in mapping_support_issues(schema.document):
        rep.error("unsupported_schema", issue["message"], issue["pointer"])
    if not rep.ok:
        return rep
    source_ids = set(doc.source_ids())

    # sources ---------------------------------------------------------------------------------
    if len(source_ids) != len(doc.sources):
        rep.error("duplicate_source", "source ids must be unique", "/sources")
    if doc.record.root_source not in source_ids:
        rep.error(
            "unknown_source", f"record.root_source {doc.record.root_source!r} is not a source", "/record"
        )
    for s in doc.sources:
        if s.table.filter is not None:
            _check_expr(s.table.filter, f"/sources/{s.id}/table/filter", rep, source_ids)

    # joins -----------------------------------------------------------------------------------
    for i, j in enumerate(doc.joins):
        for side in (j.left, j.right):
            if side not in source_ids:
                rep.error("unknown_source", f"join refers to unknown source {side!r}", f"/joins/{i}")
        if j.left == j.right:
            rep.error("self_join", "join left and right must differ", f"/joins/{i}")

    # fields ----------------------------------------------------------------------------------
    seen: set[str] = set()
    schema_pointers = {f.pointer for f in schema.fields(include_system=True)}
    collection_pointers = {f.pointer for f in schema.collections()}
    for i, fm in enumerate(doc.fields):
        path = f"/fields/{i}"
        if fm.target not in schema_pointers:
            rep.error("unknown_target", f"target {fm.target!r} is not in schema {schema.ref}", path)
        elif "/*/" in fm.target:
            rep.error(
                "collection_field_at_root",
                f"{fm.target} belongs to a collection; map it under /collections",
                path,
            )
        if fm.target in seen:
            rep.error("duplicate_target", f"target {fm.target!r} mapped twice", path)
        seen.add(fm.target)
        _check_expr(fm.expr, path, rep, source_ids)

    # collections ------------------------------------------------------------------------------
    for i, c in enumerate(doc.collections):
        path = f"/collections/{i}"
        _check_collection(c, path, rep, schema, schema_pointers, collection_pointers, source_ids)

    # required root fields ---------------------------------------------------------------------
    for ptr in schema.required_leaf_pointers():
        if ptr not in seen:
            rep.error("missing_required", f"required target {ptr} is not mapped", "/fields")
    for ptr in schema.identity_keys:
        if ptr not in seen:
            rep.error("missing_identity", f"identity key {ptr} is not mapped", "/fields")

    # quality --------------------------------------------------------------------------------
    for i, q in enumerate(doc.quality):
        if q.target not in schema_pointers:
            rep.error("unknown_target", f"quality rule target {q.target!r} is not in schema", f"/quality/{i}")

    # advisory ---------------------------------------------------------------------------------
    for f in schema.leaf_fields():
        if (
            f.risk == "critical"
            and f.in_collection is None
            and f.pointer not in seen
            and f.default is None  # a schema default (e.g. currency THB) covers an unmapped field
        ):
            rep.warn("critical_unmapped", f"critical field {f.pointer} is not mapped", "/fields")
    for i, fm in enumerate(doc.fields):
        if isinstance(fm.expr, ConstExpr) and schema.risk_of(fm.target) == "critical":
            rep.warn(
                "critical_constant",
                f"critical field {fm.target} is a constant {fm.expr.value!r}",
                f"/fields/{i}",
            )
    return rep


def _check_collection(
    c: CollectionMapping,
    path: str,
    rep: ValidationReport,
    schema: TargetSchema,
    schema_pointers: set[str],
    collection_pointers: set[str],
    source_ids: set[str],
) -> None:
    if c.target not in collection_pointers:
        rep.error(
            "unknown_collection", f"{c.target!r} is not an array-of-objects field in {schema.ref}", path
        )
        return
    if c.source not in source_ids:
        rep.error("unknown_source", f"collection source {c.source!r} unknown", path)
    seen: set[str] = set()
    for i, fm in enumerate(c.fields):
        fpath = f"{path}/fields/{i}"
        if not fm.target.startswith(c.target + "/*/"):
            rep.error("target_outside_collection", f"{fm.target} must start with {c.target}/*/", fpath)
        elif fm.target not in schema_pointers:
            rep.error("unknown_target", f"target {fm.target!r} is not in schema {schema.ref}", fpath)
        if fm.target in seen:
            rep.error("duplicate_target", f"target {fm.target!r} mapped twice", fpath)
        seen.add(fm.target)
        _check_expr(fm.expr, fpath, rep, source_ids)
    # required item fields
    item_required = [
        f.pointer
        for f in schema.fields()
        if f.in_collection == c.target and f.required and f.is_leaf and f.default is None
    ]
    for ptr in item_required:
        if ptr not in seen:
            rep.error("missing_required", f"required collection field {ptr} is not mapped", path)
