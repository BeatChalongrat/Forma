"""Comparable readings, not merely comparable column names (ADR-060)."""

from __future__ import annotations

from typing import Any

from forma.mapping_dsl.models import MappingDocument


def _where(source: Any, files: list[Any] | None) -> dict[str, Any]:
    """What a source reads, not how its selector is spelled: the file it binds to in this round (its id,
    or the pattern when there are no files to bind), the sheet, and the parts of the table spec that change
    which rows and cells are read (range, unpivot, filter, fill-down). Two selectors for the same sheet of
    the same file — one by pattern, one by sheet name, one with the header row spelled out, one saying
    "auto" — read the same cells and must agree; a different range or filter must not."""
    bound = None
    if files:
        from forma.services.mapping import _match_file

        bound = _match_file(source, [f for f in files if getattr(f, "status", "profiled") == "profiled"])
    sheet = source.sheet
    table = source.table
    # A column range covering the whole physical sheet is the same range as an
    # omitted range. Only collapse it when metadata proves the width; retain
    # genuinely partial ranges and all row/filter boundaries.
    sheet_meta = None
    if bound is not None and getattr(bound, "profile", None) and sheet is not None:
        candidates = ((bound.profile or {}).get("meta") or {}).get("sheets", [])
        hits = [
            s
            for s in candidates
            if (sheet.name is not None and s.get("name", "").casefold() == sheet.name.casefold())
            or (sheet.index is not None and s.get("index") == sheet.index)
        ]
        if len(hits) == 1:
            sheet_meta = hits[0]
    width = sheet_meta.get("n_cols") if sheet_meta else None
    last_column = None if width and table.last_column == width else table.last_column
    return {
        "file": bound.id if bound is not None else (source.file.name_pattern or source.file.fingerprint),
        "sheet": (sheet.name.strip().casefold() if sheet and sheet.name else None)
        or (sheet.match if sheet and sheet.match else None)
        or (sheet.index if sheet else None),
        "data_start": table.data_start,
        "data_end": table.data_end,
        "first_column": table.first_column or 0,
        "last_column": last_column,
        "unpivot": table.unpivot.model_dump(mode="json") if table.unpivot else None,
        "filter": table.filter.model_dump(mode="json", exclude_none=True) if table.filter else None,
        "fill_down": list(table.fill_down),
        "rename": dict(source.rename),
    }


def _canonical_with(sources: dict[str, Any]) -> Any:
    def canonical(value: Any, default: str) -> Any:
        if isinstance(value, list):
            return [canonical(v, default) for v in value]
        if not isinstance(value, dict):
            return value
        out = {k: canonical(v, default) for k, v in value.items()}
        if value.get("op") == "col":
            location = dict(sources.get(value.get("source") or default, {"unresolved": True}))
            renames = location.pop("rename", {})
            matches = [raw for raw, alias in renames.items() if alias == value.get("name")]
            if len(matches) == 1:
                out["name"] = matches[0]
            out["source"] = location
        return out

    return canonical


def reading_signature(doc: MappingDocument, target: str, *, files: list[Any] | None = None) -> dict[str, Any]:
    """What one field reads: its expression with every column resolved to the file, sheet and range it
    comes from, and for a collection field how the items attach (link, filter, item dedupe). Source aliases
    and the spelling of selectors do not count. How *records* are selected — root filters, dedupe, joins —
    is the record's signature (``record_signature``), not every field's: a model that dedupes on the SKU has
    not read the currency column differently.

    Agreement is a consistency signal, not an independent second opinion: the model has
    already seen the deterministic draft. Even identical signatures need execution checks.
    """
    sources = {s.id: _where(s, files) for s in doc.sources}
    canonical = _canonical_with(sources)
    for fm in doc.fields:
        if fm.target == target:
            return {
                "expr": canonical(fm.expr.model_dump(mode="json", exclude_none=True), doc.record.root_source),
                "source": sources.get(doc.record.root_source),
            }
    for cm in doc.collections:
        for fm in cm.fields:
            if fm.target == target:
                return {
                    "expr": canonical(fm.expr.model_dump(mode="json", exclude_none=True), cm.source),
                    "source": sources.get(cm.source),
                    "root": sources.get(doc.record.root_source),
                    "collection": canonical(
                        cm.model_dump(mode="json", exclude={"fields", "source"}, exclude_none=True), cm.source
                    ),
                }
    return {}


def record_signature(doc: MappingDocument, *, files: list[Any] | None = None) -> dict[str, Any]:
    """How records are selected: root filters, dedupe and joins, with every column resolved."""
    sources = {s.id: _where(s, files) for s in doc.sources}
    canonical = _canonical_with(sources)
    return {
        "root": sources.get(doc.record.root_source),
        "dedupe": doc.record.dedupe.model_dump(mode="json"),
        "filters": canonical([x.model_dump(mode="json") for x in doc.record.filters], doc.record.root_source),
        "joins": [
            {
                **canonical(j.model_dump(mode="json", exclude={"left", "right"}), doc.record.root_source),
                "left": sources.get(j.left),
                "right": sources.get(j.right),
            }
            for j in doc.joins
        ],
    }


# Ops that change what a value means or is: arithmetic, VAT, lookups, conditionals, and the ones that cut
# or rewrite identifiers. Everything else (trim, case, whitespace, casts, Thai number and date parsing,
# unit and pack normalisation, value maps, coalesce, default) normalises the same cell to the same meaning.
VALUE_OPS = frozenset(
    {
        "add",
        "subtract",
        "multiply",
        "divide",
        "round",
        "vat_adjust",
        "lookup",
        "conditional",
        "strip_leading_zeros",
        "regex_replace",
        "regex_extract",
        "split",
    }
)


def reading_core(doc: MappingDocument, target: str, *, files: list[Any] | None = None) -> dict[str, Any]:
    """The part of a reading two readers must share to be said to agree: the cells read (file, sheet, range,
    column), the constants, and the value-changing ops with their options. A wrapper that only normalises
    (``trim``, ``normalize_whitespace``, ``cast``, ``parse_number_th`` …) is not a different reading of the
    file; multiplying by 1 000, adjusting VAT or stripping zeros is."""
    sig = reading_signature(doc, target, files=files)
    if not sig:
        return {}
    cells: list[Any] = []
    consts: list[Any] = []
    ops: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        op = node.get("op")
        if op == "col":
            cells.append((node.get("source"), node.get("name")))
        elif op == "const":
            consts.append(node.get("value"))
        elif op in VALUE_OPS:
            ops.append((op, node.get("params") or {}))
        for v in node.values():
            walk(v)

    walk(sig["expr"])
    return {
        "cells": sorted(cells, key=repr),
        "consts": sorted(consts, key=repr),
        "value_ops": sorted(ops, key=repr),
        # Source location is already carried by each physical column. Root/join selection
        # is compared separately; moving a price read into a joined source isn't a new cell.
    }


def collection_signature(doc: MappingDocument, *, files: list[Any] | None = None) -> dict[str, Any]:
    """How each collection's items attach to the record: link, filter, item dedupe, key normalisation."""
    sources = {s.id: _where(s, files) for s in doc.sources}
    canonical = _canonical_with(sources)
    return {
        cm.target: canonical(cm.model_dump(mode="json", exclude={"fields"}, exclude_none=True), cm.source)
        for cm in doc.collections
    }
