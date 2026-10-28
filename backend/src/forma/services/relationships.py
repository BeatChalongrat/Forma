"""Relationship discovery for the visual mapping (ADR-019).

Every table the round's files contain, the key columns that connect them — found by header *and* by the
values the columns actually share — and, for each connection, its cardinality and how many rows match,
measured on real data. Links the mapping already uses are always reported with the same measurements, so
a wrong join (a key that repeats, rows that match nothing) is visible before anything is approved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import polars as pl
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.ai.heuristics import file_name_pattern, norm, propose_mapping
from forma.db.models import MappingVersion, SourceFile
from forma.engine.transform import BoundSource, load_source_frame
from forma.mapping_dsl.models import FileMatch, MappingDocument, SheetSelect, SourceRole, SourceSpec
from forma.profiling.profile import TableProfile, WorkbookProfile, noise_tables
from forma.services.context import ServiceContext
from forma.services.mapping import bind_sources
from forma.services.rounds import get_round, round_profile

KEY_WORDS = ("sku", "code", "id", "item", "material", "article", "product", "no", "รหัส", "เลขที่", "บาร์โค้ด")
KEYS_PER_TABLE = 6
MIN_FOUND = 0.5  # share of a column's values found in another table's key
_DECIMAL_RE = re.compile(r"^-?[\d,]*\.\d+$")


# --------------------------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------------------------
def source_spec_for(t: TableProfile, profile: WorkbookProfile, sid: str) -> SourceSpec:
    """How the mapping would read a table it does not use yet (same rules as the mapper's sources)."""
    is_csv = bool(t.file_name and t.file_name.lower().endswith((".csv", ".txt", ".tsv")))
    sheet_name = profile.meta.sheets[t.sheet_index].name
    return SourceSpec(
        id=sid,
        role=SourceRole.OTHER,
        file=FileMatch(
            name_pattern=file_name_pattern(t.file_name) if t.file_name else None,
            sheet_names_any=[] if is_csv else [sheet_name],
        ),
        sheet=None if is_csv else SheetSelect(name=sheet_name),
        table=t.table.to_spec().model_copy(update={"data_end": None, "data_start": None}),
    )


def _columns_used(doc: MappingDocument) -> dict[str, set[str]]:
    """Source id → columns the mapping reads (fields, collection items, links, joins)."""
    used: dict[str, set[str]] = {}

    def walk(node: Any, default: str) -> None:
        if isinstance(node, dict):
            if node.get("op") == "col" and node.get("name"):
                used.setdefault(node.get("source") or default, set()).add(str(node["name"]))
            for v in node.values():
                walk(v, default)
        elif isinstance(node, list):
            for v in node:
                walk(v, default)

    root = doc.record.root_source
    for f in doc.fields:
        walk(f.expr.model_dump(mode="json"), root)
    for c in doc.collections:
        for f in c.fields:
            walk(f.expr.model_dump(mode="json"), c.source)
        for k in c.link:
            used.setdefault(root, set()).add(k.left)
            used.setdefault(c.source, set()).add(k.right)
    for j in doc.joins:
        for k in j.on:
            used.setdefault(j.left, set()).add(k.left)
            used.setdefault(j.right, set()).add(k.right)
    return used


# --------------------------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------------------------
def _key_series(frame: pl.DataFrame, col: str) -> pl.Series:
    """Key values as the engine compares them (trim + upper), blanks and nulls dropped."""
    s = frame[col].drop_nulls().cast(pl.String).str.strip_chars().str.to_uppercase()
    return s.filter(s != "")


@dataclass
class _Col:
    values: set[str]
    rows: int
    unique: bool
    keyword: bool


def _columns(frame: pl.DataFrame) -> dict[str, _Col]:
    """Every column that could carry a key: not decimals (prices, quantities), at least two values."""
    out: dict[str, _Col] = {}
    for c in frame.columns:
        if c.startswith("__"):
            continue
        s = _key_series(frame, c)
        if s.len() == 0:
            continue
        sample = s.head(200).to_list()
        if sum(1 for v in sample if _DECIMAL_RE.match(v)) > 0.8 * len(sample):
            continue
        values = set(s.unique().to_list())
        if len(values) < 2:
            continue
        out[c] = _Col(values, s.len(), len(values) == s.len(), any(w in norm(c) for w in KEY_WORDS))
    return out


def _primary_keys(cols: dict[str, _Col]) -> list[str]:
    """The columns a table could be looked up by: (nearly) unique values, key-like headers first."""
    ranked = [
        (c, len(v.values) / v.rows + (0.4 if v.keyword else 0.0))
        for c, v in cols.items()
        if len(v.values) / v.rows >= 0.9
    ]
    ranked.sort(key=lambda x: -x[1])
    return [c for c, _ in ranked[:KEYS_PER_TABLE]]


def measure(a: pl.DataFrame, ca: str, b: pl.DataFrame, cb: str) -> dict[str, Any]:
    """How table ``a`` column ``ca`` and table ``b`` column ``cb`` connect, on the actual values."""
    sa, sb = _key_series(a, ca), _key_series(b, cb)
    va, vb = set(sa.unique().to_list()), set(sb.unique().to_list())
    overlap = len(va & vb)
    a_unique = sa.n_unique() == sa.len()
    b_unique = sb.n_unique() == sb.len()
    return {
        "overlap": overlap,
        "containment": round(overlap / min(len(va), len(vb)), 4) if va and vb else 0.0,
        "from_match": round(overlap / len(va), 4) if va else 0.0,  # share of a's keys found in b
        "to_match": round(overlap / len(vb), 4) if vb else 0.0,  # share of b's keys found in a
        "from_orphan_rows": int((~sa.is_in(list(vb))).sum()) if va else 0,
        "to_orphan_rows": int((~sb.is_in(list(va))).sum()) if vb else 0,
        "cardinality": f"{'1' if a_unique else 'N'}:{'1' if b_unique else 'N'}",
        "from_unique": a_unique,
        "to_unique": b_unique,
    }


def _header_similarity(a: str, b: str) -> float:
    return 1.0 if norm(a) == norm(b) else round(fuzz.token_set_ratio(norm(a), norm(b)) / 100.0, 3)


# --------------------------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------------------------
def discover(
    ctx: ServiceContext,
    session: Session,
    round_id: str,
    document: dict[str, Any] | None = None,
    *,
    limit: int | None = 20_000,
    probe: tuple[str, str, str, str] | None = None,
) -> dict[str, Any]:
    r = get_round(session, round_id)
    profile = round_profile(session, round_id)
    schema = ctx.registry.get_ref(r.target_schema)
    if document is not None:
        doc = MappingDocument.model_validate(document)
    else:
        latest = session.scalar(
            select(MappingVersion)
            .where(MappingVersion.round_id == r.id)
            .order_by(MappingVersion.created_at.desc())
        )
        doc = (
            MappingDocument.model_validate(latest.document)
            if latest is not None
            else propose_mapping(profile, schema, customer_id=r.customer_id).mapping
        )
    root = doc.record.root_source
    used_cols = _columns_used(doc)
    coll_of: dict[str, list[str]] = {}
    for c in doc.collections:
        coll_of.setdefault(c.source, []).append(c.target)
    # listed as a source is not enough: a table is used when the mapping reads from it
    read = {root, *coll_of, *(j.left for j in doc.joins), *(j.right for j in doc.joins), *used_cols}

    # nodes: the mapping's sources, then tables of the files the mapping does not read yet
    bindings, problems = bind_sources(doc, r.files, resolve=ctx.storage.local_path)
    frames: dict[str, pl.DataFrame] = {}
    nodes: list[dict[str, Any]] = []
    covered: set[tuple[str | None, str]] = set()
    for s in doc.sources:
        b = bindings.get(s.id)
        if b is None:
            continue
        try:
            frames[s.id] = load_source_frame(s, b, limit=limit)
        except Exception as e:  # a source that cannot be read is shown, not fatal
            problems.append(f"source {s.id!r}: {e}")
            continue
        sheet = s.sheet.name if s.sheet and s.sheet.name else (str(b.sheet) if b.sheet is not None else None)
        covered.add((b.file_id, sheet or ""))
        nodes.append(
            _node(
                s.id,
                frames[s.id],
                sheet,
                b.file_name,
                s.role.value,
                s.id == root,
                s.id in read,
                coll_of.get(s.id, []),
                used_cols.get(s.id, set()),
            )
        )
    files = {f.id: f for f in session.scalars(select(SourceFile).where(SourceFile.round_id == r.id))}
    noise = noise_tables(profile.tables)
    for i, t in enumerate(profile.tables):
        sheet = profile.meta.sheets[t.sheet_index].name
        if i in noise or (t.file_id, sheet) in covered or t.file_id not in files:
            continue
        sid = f"table_{i}"
        spec = source_spec_for(t, profile, sid)
        f = files[t.file_id]
        try:
            frames[sid] = load_source_frame(
                spec,
                BoundSource(ctx.storage.local_path(f.storage_key), file_id=f.id, file_name=f.original_name),
                limit=limit,
            )
        except Exception:
            continue
        node = _node(sid, frames[sid], sheet, f.original_name, "other", False, False, [], set())
        node["source_spec"] = spec.model_dump(mode="json", exclude_none=True)  # ready to add to the mapping
        nodes.append(node)

    # edges: what the mapping uses, measured; then the best discovered key pair per pair of tables
    edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def add(a: str, ca: str, b: str, cb: str, **extra: Any) -> None:
        if a not in frames or b not in frames or ca not in frames[a].columns or cb not in frames[b].columns:
            edges[(a, ca, b, cb)] = {
                "from": {"node": a, "column": ca},
                "to": {"node": b, "column": cb},
                "missing": True,
                **extra,
            }
            return
        m = measure(frames[a], ca, frames[b], cb)
        edges[(a, ca, b, cb)] = {
            "from": {"node": a, "column": ca},
            "to": {"node": b, "column": cb},
            **m,
            **extra,
        }

    for c in doc.collections:
        if c.source == root:
            continue  # e.g. prices read from the main table itself: no link to draw
        for k in c.link:
            add(root, k.left, c.source, k.right, kind="link", used=True, collection=c.target)
    for j in doc.joins:
        for k in j.on:
            add(j.left, k.left, j.right, k.right, kind="join", used=True, expected=j.cardinality)
    ids = [n["id"] for n in nodes]
    label = {n["id"]: n["sheet"] or n["id"] for n in nodes}  # what people see: the sheet name
    cols = {i: _columns(frames[i]) for i in ids}
    pks = {i: _primary_keys(cols[i]) for i in ids}
    # two tables that already hang off the main table by the same key are siblings, not related
    via_root = {(c.source, k.right) for c in doc.collections for k in c.link if c.source != root}
    for x, a in enumerate(ids):
        for b in ids[x + 1 :]:
            if any({e["from"]["node"], e["to"]["node"]} == {a, b} for e in edges.values()):
                continue  # the mapping already connects these two tables
            best: tuple[float, str, str, str, str, float] | None = None
            for one, many in ((a, b), (b, a)):
                for pk in pks[one]:
                    for fk, fc in cols[many].items():
                        if (one, pk) in via_root and (many, fk) in via_root:
                            continue
                        overlap = len(fc.values & cols[one][pk].values)
                        if overlap < 2:
                            continue
                        found = overlap / len(fc.values)  # share of the referencing values that resolve
                        if found < MIN_FOUND:
                            continue
                        hs = _header_similarity(pk, fk)
                        score = round(0.7 * found + 0.3 * hs, 3)
                        if best is None or score > best[0]:
                            best = (score, one, pk, many, fk, hs)
            if best is None:
                continue
            score, one, pk, many, fk, hs = best
            if many == root:  # the main table stays on the left, as in the mapping's links
                one, pk, many, fk = many, fk, one, pk
            m = measure(frames[one], pk, frames[many], fk)
            edges[(one, pk, many, fk)] = {
                "from": {"node": one, "column": pk},
                "to": {"node": many, "column": fk},
                **m,
                "kind": "suggested",
                "used": False,
                "score": score,
                "evidence": [
                    f"{m['overlap']} shared key values; {m['to_match']:.0%} of {label[many]}.{fk} found in {label[one]}.{pk}",
                    f"header similarity {hs:.2f}",
                ],
            }
    out = []
    for edge in edges.values():
        edge["warnings"] = _warnings(edge, root, label)
        out.append(edge)
    graph: dict[str, Any] = {
        "root": root,
        "rows_limit": limit,
        "nodes": nodes,
        "edges": out,
        "problems": problems,
    }
    if probe is not None:
        graph["probe"] = _probe(frames, probe, label)
    return graph


def _probe(
    frames: dict[str, pl.DataFrame], probe: tuple[str, str, str, str], label: dict[str, str]
) -> dict[str, Any]:
    """Measure a connection drawn by hand, so a wrong key pair shows before it is added."""
    a, ca, b, cb = probe
    e: dict[str, Any] = {"from": {"node": a, "column": ca}, "to": {"node": b, "column": cb}, "kind": "probe"}
    if a not in frames or b not in frames or ca not in frames[a].columns or cb not in frames[b].columns:
        return {**e, "used": False, "missing": True, "warnings": ["one of these columns is not in the file"]}
    m = measure(frames[a], ca, frames[b], cb)
    w: list[str] = []
    if m["overlap"] == 0:
        w.append(
            f"no value of {label.get(a, a)}.{ca} appears in {label.get(b, b)}.{cb} — these columns do not connect"
        )
    elif min(m["from_match"], m["to_match"]) < MIN_FOUND:
        w.append(f"only {min(m['from_match'], m['to_match']):.0%} of the values match on one side")
    return {**e, **m, "used": False, "warnings": w}


def _node(
    sid: str,
    frame: pl.DataFrame,
    sheet: str | None,
    file_name: str | None,
    role: str,
    is_root: bool,
    used: bool,
    collections: list[str],
    mapped: set[str],
) -> dict[str, Any]:
    keys = set(_primary_keys(_columns(frame)))
    return {
        "id": sid,
        "sheet": sheet,
        "file": file_name,
        "role": role,
        "root": is_root,
        "used": used,
        "collections": collections,
        "rows": frame.height,
        "columns": [
            {"name": c, "key": c in keys, "mapped": c in mapped}
            for c in frame.columns
            if not c.startswith("__")
        ],
    }


def _warnings(e: dict[str, Any], root: str, label: dict[str, str]) -> list[str]:
    if e.get("missing"):
        return ["a column of this link is not in the file — the link matches nothing"]
    w: list[str] = []
    b = label.get(e["to"]["node"], e["to"]["node"])
    if e.get("kind") == "link" and e["from"]["node"] == root:
        if not e["from_unique"]:
            w.append("the main table's key repeats: records are deduplicated on it")
        if e["to_orphan_rows"]:
            w.append(f"{e['to_orphan_rows']} rows of {b} match no record and are dropped")
    if e.get("kind") == "join" and e.get("expected") in ("1:1", "N:1") and not e["to_unique"]:
        w.append(f"{b}'s key repeats: joined rows multiply (expected {e['expected']})")
    return w
