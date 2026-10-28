"""Deterministic baseline mapper.

Produces a complete MappingDocument proposal from a WorkbookProfile using the target schema's
synonyms, profiling hints and simple value-overlap checks. It runs offline, is fully explainable
(every field carries evidence) and is the floor the LLM agents improve on — never the ceiling.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from forma.mapping_dsl.models import (
    CollectionMapping,
    FieldMapping,
    FileMatch,
    JoinKey,
    MappingDocument,
    MappingMeta,
    QualityRule,
    RecordSpec,
    SheetSelect,
    SourceRole,
    SourceSpec,
    Unpivot,
    call,
    col,
    const,
)
from forma.profiling.profile import ColumnProfile, TableProfile, WorkbookProfile, noise_tables
from forma.schema_registry import COLLECTION_KINDS, FieldSpec, TargetSchema

ROLE_SIGNALS: dict[str, list[str]] = {
    "pricing": ["price", "ราคา", "amount", "vat", "price list", "ประเภทราคา"],
    "inventory": [
        "stock",
        "qty",
        "quantity",
        "on hand",
        "warehouse",
        "คงเหลือ",
        "สต็อก",
        "คลัง",
        "จำนวน",
        "balance",
    ],
    "promotions": [
        "promo",
        "promotion",
        "discount",
        "campaign",
        "โปร",
        "ส่วนลด",
        "แคมเปญ",
        "ลด",
        "แถม",
        "buy",
        "get",
    ],
    "products": ["name", "ชื่อ", "description", "category", "หมวด", "brand", "unit", "หน่วย", "barcode", "ยี่ห้อ"],
}
HINT_BOOST: dict[str, dict[str, float]] = {
    "sku": {"sku_like": 0.25, "barcode_like": -0.3, "long_text": -0.5},
    "barcode": {"barcode_like": 0.4, "sku_like": -0.1},
    "product_name": {"thai_text": 0.05, "long_text": 0.05, "sku_like": -0.4, "barcode_like": -0.5},
    "description": {"long_text": 0.25},
    "price.amount": {"price_like": 0.2, "percent": -0.4},
    "discount.percent": {"percent": 0.3},
    "promo.start_date": {"thai_date": 0.2},
    "promo.end_date": {"thai_date": 0.2},
    "price.effective_from": {"thai_date": 0.15},
    "price.effective_to": {"thai_date": 0.15},
    "unit.sell": {"unit_like": 0.3},
    "stock.qty": {"price_like": -0.1},
}
TYPE_COMPAT: dict[str, set[str]] = {
    "number": {"int", "float", "mixed", "string"},
    "integer": {"int", "float", "mixed", "string"},
    "string": {"string", "mixed", "int", "float", "date", "bool"},
    "boolean": {"bool", "string", "int", "mixed"},
    "array": {"string", "mixed"},
}


def file_name_pattern(name: str) -> str:
    """Loose regex for a file name: digits/dates vary between rounds, the rest stays."""
    stem = re.sub(r"\.[^.]+$", "", name)
    parts = re.split(r"\d+", stem)
    core = ".*".join(re.escape(p) for p in parts if p.strip(" _-"))
    return f"(?i)^{core}" if core else "(?i).*"


def norm(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[\s_\-./()\[\]:%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Match:
    column: ColumnProfile
    field: FieldSpec
    score: float
    evidence: list[str] = dataclasses.field(default_factory=list)


@dataclass
class HeuristicProposal:
    mapping: MappingDocument
    confidence: float
    evidence: list[str]
    open_questions: list[str]
    table_roles: dict[int, str]  # profile table index → role
    unmatched_columns: dict[str, list[str]]  # source id → columns we could not place

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "evidence": self.evidence,
            "open_questions": self.open_questions,
            "table_roles": self.table_roles,
            "unmatched_columns": self.unmatched_columns,
        }


# --------------------------------------------------------------------------------------------
def score_header(header: str, f: FieldSpec) -> tuple[float, str | None]:
    h = norm(header)
    if not h:
        return 0.0, None
    candidates = [*f.synonyms_en, *f.synonyms_th, f.name.replace("_", " ")]
    best, why = 0.0, None
    for c in candidates:
        cn = norm(c)
        if not cn:
            continue
        if h == cn:
            return 1.0, f"header '{header}' == synonym '{c}'"
        # containment both ways (short synonyms must be whole-word to avoid 'id' in 'width')
        if len(cn) >= 3 and (cn in h or h in cn):
            s = 0.85 if cn in h.split() or h in cn.split() else 0.75
        else:
            s = fuzz.token_set_ratio(h, cn) / 100.0
            s = s * 0.9 if s >= 0.8 else s * 0.6
        if s > best:
            best, why = s, f"header '{header}' ≈ synonym '{c}' ({s:.2f})"
    return best, why


def match_columns(table: TableProfile, fields: list[FieldSpec]) -> list[Match]:
    """Greedy best-first assignment of columns to target fields (one column → one field)."""
    pairs: list[Match] = []
    for c in table.columns:
        for f in fields:
            s, why = score_header(c.name, f)
            if s < 0.55:
                continue
            ev = [why] if why else []
            for hint, boost in HINT_BOOST.get(f.semantic or "", {}).items():
                if hint in c.hints:
                    s += boost
                    ev.append(f"column hint {hint} ({boost:+.2f})")
            ftype = next((t for t in f.json_type if t != "null"), "string")
            if c.inferred_type not in TYPE_COMPAT.get(ftype, {"string"}) and c.inferred_type != "empty":
                s -= 0.3
                ev.append(f"type mismatch {c.inferred_type}→{ftype} (-0.30)")
            if f.semantic == "sku" and c.unique_ratio < 0.5 and c.null_ratio < 0.5:
                s -= 0.25
                ev.append("low uniqueness for a key (-0.25)")
            # tie-breakers: required / riskier fields win equal header scores
            s += (0.04 if f.required else 0.0) + 0.01 * {"critical": 3, "high": 2, "medium": 1, "low": 0}[
                f.risk
            ]
            if s >= 0.5:
                pairs.append(Match(c, f, min(1.0, s), ev))
    pairs.sort(key=lambda m: -m.score)
    used_cols: set[str] = set()
    used_fields: set[str] = set()
    out: list[Match] = []
    for m in pairs:
        if m.column.name in used_cols or m.field.pointer in used_fields:
            continue
        used_cols.add(m.column.name)
        used_fields.add(m.field.pointer)
        out.append(m)
    return out


def collection_roles(schema: TargetSchema) -> dict[str, str]:
    """Table role → target collection pointer. Collections FORMA has rules for are named by their kind
    (pricing, inventory, promotions); any other collection of the schema is ``collection:<pointer>``."""
    out: dict[str, str] = {}
    for kind in COLLECTION_KINDS:
        ptr = schema.collection_for(kind)
        if ptr:
            out[kind] = ptr
    for c in schema.collections():
        if c.pointer not in out.values():
            out[f"collection:{c.pointer}"] = c.pointer
    return out


def table_role(table: TableProfile, schema: TargetSchema) -> tuple[str, float]:
    """Classify a table by which part of the target schema its columns explain best."""
    root_fields = [
        f for f in schema.leaf_fields() if f.in_collection is None and f.semantic not in ("sku", "currency")
    ]
    scores: dict[str, float] = {"products": 0.0}
    scores["products"] = sum(m.score for m in match_columns(table, root_fields))
    for role, coll in collection_roles(schema).items():
        fields = [f for f in schema.leaf_fields() if f.in_collection == coll]
        scores[role] = sum(m.score for m in match_columns(table, fields))
    headers = " ".join(norm(c.name) for c in table.columns)
    for role, words in ROLE_SIGNALS.items():
        if role == "products" or role not in scores:
            continue
        scores[role] += 0.3 * sum(1 for w in words if norm(w) and norm(w) in headers)
    # products tables are wide and keyed; satellites are narrow (key + 1-3 fields)
    if len(table.columns) >= 5:
        scores["products"] += 0.5
    if table.key_candidates and any(norm(k) in headers for k in table.key_candidates):
        scores["products"] += 0.3
    # crosstab (months as columns) → pricing
    if "pricing" in scores and crosstab_value_columns(table):
        scores["pricing"] += 2.0
    role = max(scores, key=lambda k: scores[k])
    total = sum(scores.values()) or 1.0
    if scores[role] <= 0.5:
        return "other", 0.2
    return role, round(scores[role] / total, 2)


_MONTH_HDR_RE = re.compile(
    r"^(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[ -/]?\d{2,4}|\d{4}[-/]\d{1,2}|\d{1,2}[-/]\d{4}|"
    r"(?:ม\.ค|ก\.พ|มี\.ค|เม\.ย|พ\.ค|มิ\.ย|ก\.ค|ส\.ค|ก\.ย|ต\.ค|พ\.ย|ธ\.ค)\.?\s*\d{2,4}|q[1-4][ -]?\d{2,4}|w\d{1,2})$",
    re.IGNORECASE,
)


def crosstab_value_columns(table: TableProfile) -> list[str]:
    """Columns whose *headers* are periods (months/quarters) and whose values are numeric."""
    cols = [
        c.name
        for c in table.columns
        if _MONTH_HDR_RE.match(norm(c.name).replace(" ", "")) or _MONTH_HDR_RE.match(c.name.strip())
    ]
    numeric = [c.name for c in table.columns if c.name in cols and c.inferred_type in ("int", "float")]
    return numeric if len(numeric) >= 2 else []


# --------------------------------------------------------------------------------------------
def _expr_for(c: ColumnProfile, f: FieldSpec, source: str | None = None) -> Any:
    src = col(c.name, source)
    sem = f.semantic or ""
    t = set(f.json_type)
    if sem == "unit.sell":
        return call("unit_normalize", src)
    if sem == "unit.pack_size":
        return call("pack_size", src)
    if sem == "category_path":
        sep = ">" if any(">" in s for s in c.samples) else "/" if any("/" in s for s in c.samples) else None
        return call("split", src, separator=sep) if sep else call("to_list", src)
    if sem == "status":
        inverted = bool(re.search(r"block|ระงับ|lvorm|deleted|ลบ|disabled|inactive", norm(c.name)))
        if inverted:  # header asks "is it blocked/deleted?" → yes means NOT active
            return _map_values(
                f,
                src,
                {
                    "x": "discontinued",
                    "yes": "inactive",
                    "y": "inactive",
                    "true": "inactive",
                    "1": "inactive",
                    "ใช่": "inactive",
                    "no": "active",
                    "n": "active",
                    "false": "active",
                    "0": "active",
                    "": "active",
                },
                default="active",
            )
        return _map_values(
            f,
            src,
            {
                "active": "active",
                "y": "active",
                "yes": "active",
                "true": "active",
                "1": "active",
                "ขาย": "active",
                "ใช้งาน": "active",
                "เปิด": "active",
                "inactive": "inactive",
                "n": "inactive",
                "no": "inactive",
                "false": "inactive",
                "0": "inactive",
                "ไม่ใช้งาน": "inactive",
                "ไม่ขาย": "inactive",
                "ปิด": "inactive",
                "discontinued": "discontinued",
                "เลิกขาย": "discontinued",
                "ยกเลิก": "discontinued",
                "x": "discontinued",
            },
            default="active",
        )
    if sem == "price.list_name":
        return _map_values(
            f,
            src,
            {
                "standard": "standard",
                "retail": "standard",
                "ปลีก": "standard",
                "ราคาปลีก": "standard",
                "list": "standard",
                "default": "standard",
                "zpr0": "standard",
                "wholesale": "wholesale",
                "ส่ง": "wholesale",
                "ราคาส่ง": "wholesale",
                "ขายส่ง": "wholesale",
                "dealer": "wholesale",
                "member": "member",
                "สมาชิก": "member",
                "online": "online",
                "ออนไลน์": "online",
            },
            keep_unmapped=True,
            fuzzy=True,
        )
    if sem == "promo.type":
        return _map_values(
            f,
            src,
            {
                "percent": "percent_off",
                "%": "percent_off",
                "ลด%": "percent_off",
                "ลด %": "percent_off",
                "percent_off": "percent_off",
                "amount": "amount_off",
                "บาท": "amount_off",
                "ลดบาท": "amount_off",
                "amount_off": "amount_off",
                "fixed": "fixed_price",
                "ราคาพิเศษ": "fixed_price",
                "fixed_price": "fixed_price",
                "tier": "tiered",
                "tiered": "tiered",
                "ขั้นบันได": "tiered",
                "buy x get y": "buy_x_get_y",
                "แถม": "buy_x_get_y",
                "bxgy": "buy_x_get_y",
                "buy_x_get_y": "buy_x_get_y",
                "bundle": "bundle",
                "ชุด": "bundle",
                "set": "bundle",
                "ซื้อแถม": "buy_x_get_y",
                "แถมฟรี": "buy_x_get_y",
                "fixed price": "fixed_price",
                "special": "fixed_price",
                "amount off": "amount_off",
                "percent off": "percent_off",
            },
            default="percent_off",
            fuzzy=True,
        )
    if "boolean" in t:
        return call("parse_bool", src)
    if f.fmt == "date":
        return call("parse_date_th", src)
    if "number" in t or "integer" in t:
        return call("parse_number_th", src)
    if "array" in t:
        return call("split", src, separator=",")
    if sem == "sku":
        return call("trim", src)
    return call("normalize_whitespace", src)


def _map_values(f: FieldSpec, src: Any, mapping: dict[str, str], **params: Any) -> Any:
    """``map_values`` onto FORMA's canonical words ('active', 'percent_off'), spelled the way this
    schema's enum spells them ('ACTIVE', 'PercentOff' …) when it declares one."""
    if f.enum:
        spelling = {re.sub(r"[\s_\-]", "", str(e).lower()): str(e) for e in f.enum}

        def fit(v: str) -> str:
            return spelling.get(re.sub(r"[\s_\-]", "", v.lower()), v)

        mapping = {k: fit(v) for k, v in mapping.items()}
        if isinstance(params.get("default"), str):
            params["default"] = fit(params["default"])
    return call("map_values", src, mapping=mapping, **params)


def _vat_included_guess(headers: list[str]) -> tuple[bool, str]:
    text = " ".join(norm(h) for h in headers)
    if re.search(r"ไม่รวม|ก่อน vat|ex vat|excl|exclude|before vat|net", text):
        return False, "header indicates VAT-exclusive"
    if re.search(r"รวม vat|รวมภาษี|incl|inc vat|including|with vat", text):
        return True, "header indicates VAT-inclusive"
    return True, "no VAT marker in headers; assumed VAT-inclusive (Thai retail convention) — confirm"


def propose_mapping(
    profile: WorkbookProfile, schema: TargetSchema, *, customer_id: str | None = None
) -> HeuristicProposal:
    """Every target field is found by its ``x-forma.semantic`` (ADR-001), so a new target schema that
    tags its fields with the documented semantics is mapped without code changes."""
    evidence: list[str] = []
    questions: list[str] = []
    if not profile.tables:
        raise ValueError("profile contains no tables")
    identity = schema.identity_field()
    if identity is None:
        raise ValueError(f"target schema {schema.ref} declares no identity key")
    currency = schema.semantic_field("currency")
    colls = collection_roles(schema)

    roles: dict[int, tuple[str, float]] = {i: table_role(t, schema) for i, t in enumerate(profile.tables)}
    for i in noise_tables(profile.tables):
        roles[i] = ("other", 0.1)  # legends, footers, hidden calc sheets
    # root = table that explains most root-level target fields (key + name weigh double); ties → more rows
    root_fields_all = [
        f
        for f in schema.leaf_fields()
        if f.in_collection is None and (currency is None or f.pointer != currency.pointer)
    ]

    def is_key(f: FieldSpec) -> bool:
        return f.pointer == identity.pointer or f.semantic == "sku"

    def root_score(i: int) -> tuple[float, int]:
        if roles[i][0] == "other":
            return (-1.0, 0)
        ms = match_columns(profile.tables[i], root_fields_all)
        keyed = any(is_key(m.field) for m in ms)
        named = any(m.field.semantic == "product_name" for m in ms)
        score = sum(m.score for m in ms) + (2.0 if keyed and named else 0.0)
        if roles[i][0] != "products":
            score -= 1.5
        return (score, profile.tables[i].n_rows)

    root_idx = max(range(len(profile.tables)), key=root_score)
    roles[root_idx] = ("products", max(roles[root_idx][1], 0.6))
    root = profile.tables[root_idx]
    evidence.append(
        f"root table: sheet '{root.sheet}' ({root.n_rows} rows) classified as {roles[root_idx][0]}"
    )

    root_fields = [f for f in schema.leaf_fields() if f.in_collection is None]
    root_matches = match_columns(root, root_fields)
    sku_match = next((m for m in root_matches if m.field.pointer == identity.pointer), None) or next(
        (m for m in root_matches if m.field.semantic == "sku"), None
    )
    if sku_match is None:
        # fall back to best key candidate
        key = root.key_candidates[0] if root.key_candidates else root.columns[0].name
        questions.append(f"No SKU-like header found in root table; using '{key}' as identity — confirm")
        sku_col = next(c for c in root.columns if c.name == key)
        root_matches.append(Match(sku_col, identity, 0.4, [f"fallback: key candidate '{key}'"]))
        sku_match = root_matches[-1]

    def src_id(i: int) -> str:
        role = roles[i][0]
        base = (
            "table"
            if role == "other"
            else role.rsplit("/", 1)[-1]
            if role.startswith("collection:")
            else role
        )
        return f"{base}_{i}" if sum(1 for j in roles if roles[j][0] == roles[i][0]) > 1 else base

    sources: list[SourceSpec] = []
    for i, t in enumerate(profile.tables):
        if roles[i][0] == "other" and i != root_idx:
            continue  # legends / footers / hidden calc sheets are not sources
        is_csv = bool(t.file_name and t.file_name.lower().endswith((".csv", ".txt", ".tsv"))) or (
            profile.meta.kind == "csv" and not t.file_name
        )
        sheet = None if is_csv else SheetSelect(name=profile.meta.sheets[t.sheet_index].name)
        spec = t.table.to_spec().model_copy(
            update={"data_end": None, "data_start": None}
        )  # row counts change every round
        fm = FileMatch(
            name_pattern=file_name_pattern(t.file_name) if t.file_name else None,
            sheet_names_any=[] if is_csv else [profile.meta.sheets[t.sheet_index].name],
        )
        role_val = roles[i][0]
        sources.append(
            SourceSpec(
                id=src_id(i),
                role=SourceRole(role_val) if role_val in {r.value for r in SourceRole} else SourceRole.OTHER,
                file=fm,
                sheet=sheet,
                table=spec,
            )
        )
    root_sid = src_id(root_idx)

    fields: list[FieldMapping] = []
    field_scores: list[float] = []
    for m in root_matches:
        if currency is not None and m.field.pointer == currency.pointer:
            continue
        fields.append(
            FieldMapping(
                target=m.field.pointer,
                expr=_expr_for(m.column, m.field),
                confidence=round(m.score, 2),
                evidence=m.evidence,
            )
        )
        field_scores.append(m.score)
    mapped_targets = {f.target for f in fields}
    if currency is not None and currency.pointer not in mapped_targets:
        code = str(currency.default or "THB")
        fields.append(
            FieldMapping(
                target=currency.pointer,
                expr=const(code),
                confidence=0.9,
                evidence=[f"default currency {code}"],
            )
        )
    sell, pack = schema.semantic_field("unit.sell"), schema.semantic_field("unit.pack_size")
    if (
        sell is not None
        and pack is not None
        and sell.pointer in mapped_targets
        and pack.pointer not in mapped_targets
    ):
        unit_col = next(m.column for m in root_matches if m.field.pointer == sell.pointer)
        if any(re.search(r"\d", s) for s in unit_col.samples):
            fields.append(
                FieldMapping(
                    target=pack.pointer,
                    expr=call("pack_size", col(unit_col.name)),
                    confidence=0.6,
                    evidence=["pack size parsed from unit text"],
                )
            )
    # a flat contract may carry the price on the record itself: its VAT flag comes from the header
    root_amount = schema.semantic_field("price.amount")
    root_vat = schema.semantic_field("price.vat_included")
    if (
        root_amount is not None
        and root_amount.pointer in mapped_targets
        and root_vat is not None
        and root_vat.pointer not in mapped_targets
    ):
        amount_col = next(m.column for m in root_matches if m.field.pointer == root_amount.pointer)
        vat_inc, why = _vat_included_guess([amount_col.name])
        fields.append(
            FieldMapping(
                target=root_vat.pointer,
                expr=const(vat_inc),
                confidence=0.6 if "assumed" in why else 0.9,
                evidence=[why],
            )
        )
        if "assumed" in why:
            questions.append(f"Price column '{amount_col.name}': {why}")

    collections: list[CollectionMapping] = []
    unmatched: dict[str, list[str]] = {}
    used_root_cols = {m.column.name for m in root_matches}

    def item(kind: str, semantic: str) -> FieldSpec | None:
        ptr = colls.get(kind)
        return schema.semantic_field(semantic, in_collection=ptr) if ptr else None

    def missing_required(target: str, items: list[FieldMapping]) -> list[str]:
        got = {f.target for f in items}
        return [
            f.pointer
            for f in schema.leaf_fields()
            if f.in_collection == target and f.required and f.default is None and f.pointer not in got
        ]

    # price in root table → the pricing collection, fed from the root source
    price_ptr = colls.get("pricing")
    if price_ptr is not None:
        price_fields = [f for f in schema.leaf_fields() if f.in_collection == price_ptr]
        root_price = match_columns(_subset(root, exclude=used_root_cols), price_fields)
        amount = next((m for m in root_price if m.field.semantic == "price.amount"), None)
        if amount is not None:
            vat_inc, why = _vat_included_guess([amount.column.name])
            items: list[FieldMapping] = []
            plist, vat = item("pricing", "price.list_name"), item("pricing", "price.vat_included")
            if plist is not None:
                items.append(
                    FieldMapping(
                        target=plist.pointer,
                        expr=const(str(plist.default or "standard")),
                        confidence=0.8,
                        evidence=["single price column → 'standard' list"],
                    )
                )
            items.append(
                FieldMapping(
                    target=amount.field.pointer,
                    expr=call("parse_number_th", col(amount.column.name)),
                    confidence=round(amount.score, 2),
                    evidence=amount.evidence,
                )
            )
            if vat is not None:
                items.append(
                    FieldMapping(
                        target=vat.pointer,
                        expr=const(vat_inc),
                        confidence=0.6 if "assumed" in why else 0.9,
                        evidence=[why],
                    )
                )
            for m in root_price:
                if m.field.semantic in ("price.effective_from", "price.effective_to"):
                    items.append(
                        FieldMapping(
                            target=m.field.pointer,
                            expr=_expr_for(m.column, m.field),
                            confidence=round(m.score, 2),
                            evidence=m.evidence,
                        )
                    )
            if not missing_required(price_ptr, items):
                used_root_cols |= {m.column.name for m in root_price}
                collections.append(
                    CollectionMapping(
                        target=price_ptr,
                        source=root_sid,
                        link=[JoinKey(left=sku_match.column.name, right=sku_match.column.name)],
                        fields=items,
                    )
                )
                if "assumed" in why and vat is not None:
                    questions.append(f"Price column '{amount.column.name}': {why}")
                field_scores.append(amount.score)

    # satellite tables → collections with link keys
    source_ids = {sp.id for sp in sources}
    for i, t in enumerate(profile.tables):
        if i == root_idx or src_id(i) not in source_ids:
            continue
        role = roles[i][0]
        target = colls.get(role)
        sid = src_id(i)
        link_col = _find_link_column(t, sku_match.column)
        if target is None or link_col is None:
            unmatched[sid] = [c.name for c in t.columns]
            if target is not None:
                questions.append(
                    f"Table '{t.sheet}' looks like {role} but no column links to root key '{sku_match.column.name}'"
                )
            continue
        item_fields = [f for f in schema.leaf_fields() if f.in_collection == target]
        p_amount = item("pricing", "price.amount")
        value_cols = crosstab_value_columns(t) if role == "pricing" and p_amount is not None else []
        if value_cols and p_amount is not None:
            # months-as-columns price grid → unpivot to (sku, period, value)
            src_spec = next(sp for sp in sources if sp.id == sid)
            src_spec.table = src_spec.table.model_copy(
                update={
                    "unpivot": Unpivot(
                        id_columns=[link_col.name],
                        value_columns=value_cols,
                        variable_name="period",
                        value_name="value",
                    )
                }
            )
            vat_inc, why = _vat_included_guess([c.name for c in t.columns] + [t.sheet])
            items = []
            plist, vat = item("pricing", "price.list_name"), item("pricing", "price.vat_included")
            eff = item("pricing", "price.effective_from")
            if plist is not None:
                items.append(
                    FieldMapping(
                        target=plist.pointer,
                        expr=const(str(plist.default or "standard")),
                        confidence=0.7,
                        evidence=["crosstab price grid → 'standard'"],
                    )
                )
            items.append(
                FieldMapping(
                    target=p_amount.pointer,
                    expr=call("parse_number_th", col("value", sid)),
                    confidence=0.8,
                    evidence=[f"unpivoted {len(value_cols)} period columns: {value_cols[:4]}"],
                )
            )
            if vat is not None:
                items.append(
                    FieldMapping(target=vat.pointer, expr=const(vat_inc), confidence=0.6, evidence=[why])
                )
            if eff is not None:
                items.append(
                    FieldMapping(
                        target=eff.pointer,
                        expr=call(
                            "parse_date_th", call("concat", const("1 "), col("period", sid), separator="")
                        ),
                        confidence=0.6,
                        evidence=["period header → first day of period"],
                    )
                )
            questions.append(
                f"Table '{t.sheet}' is a price grid by period ({value_cols[:3]}…); mapped every period as a price row — confirm which period applies"
            )
            collections.append(
                CollectionMapping(
                    target=target,
                    source=sid,
                    link=[JoinKey(left=sku_match.column.name, right=link_col.name)],
                    fields=items,
                )
            )
            evidence.append(f"table '{t.sheet}' → {target} via unpivot of {len(value_cols)} period columns")
            field_scores.append(0.8)
            continue
        matches = match_columns(_subset(t, exclude={link_col.name}), item_fields)
        items = []
        for m in matches:
            items.append(
                FieldMapping(
                    target=m.field.pointer,
                    expr=_expr_for(m.column, m.field, sid),
                    confidence=round(m.score, 2),
                    evidence=m.evidence,
                )
            )
            field_scores.append(m.score)
        got = {f.target for f in items}
        sems = {m.field.semantic for m in matches}
        if role == "pricing":
            if "price.amount" not in sems:
                unmatched[sid] = [c.name for c in t.columns]
                questions.append(f"Pricing table '{t.sheet}' has no recognisable amount column")
                continue
            plist, vat = item("pricing", "price.list_name"), item("pricing", "price.vat_included")
            if plist is not None and plist.pointer not in got:
                items.append(
                    FieldMapping(
                        target=plist.pointer,
                        expr=const(str(plist.default or "standard")),
                        confidence=0.7,
                        evidence=["no price-list column; 'standard'"],
                    )
                )
            if vat is not None and vat.pointer not in got:
                vat_inc, why = _vat_included_guess([c.name for c in t.columns])
                items.append(
                    FieldMapping(target=vat.pointer, expr=const(vat_inc), confidence=0.6, evidence=[why])
                )
                if "assumed" in why:
                    questions.append(f"Pricing table '{t.sheet}': {why}")
        if role == "inventory":
            if "stock.qty" not in sems:
                unmatched[sid] = [c.name for c in t.columns]
                questions.append(f"Inventory table '{t.sheet}' has no recognisable quantity column")
                continue
            wh = item("inventory", "stock.warehouse")
            if wh is not None and wh.pointer not in got:
                items.append(
                    FieldMapping(
                        target=wh.pointer,
                        expr=const(str(wh.default or "MAIN")),
                        confidence=0.7,
                        evidence=["single warehouse assumed → MAIN"],
                    )
                )
        if role == "promotions":
            promo_code = item("promotions", "promo.code")
            if promo_code is not None and promo_code.pointer not in got:
                # synthesise a code from link + row? prefer any unique column
                uniq = next((c for c in t.columns if c.unique_ratio > 0.95 and c.name != link_col.name), None)
                if uniq is not None:
                    items.append(
                        FieldMapping(
                            target=promo_code.pointer,
                            expr=col(uniq.name, sid),
                            confidence=0.5,
                            evidence=[f"unique column '{uniq.name}' used as promo code"],
                        )
                    )
                else:
                    items.append(
                        FieldMapping(
                            target=promo_code.pointer,
                            expr=call("concat", const("PROMO-"), col(link_col.name, sid), separator=""),
                            confidence=0.4,
                            evidence=["no promo code column; derived from SKU"],
                        )
                    )
                    questions.append(
                        f"Promotions table '{t.sheet}' has no promo code column; derived code from SKU"
                    )
            ptype = item("promotions", "promo.type")
            if ptype is not None and ptype.pointer not in got:
                guess = (
                    "buy_x_get_y"
                    if "promo.get_qty" in sems
                    else "percent_off"
                    if "discount.percent" in sems
                    else "amount_off"
                    if "discount.amount" in sems
                    else "percent_off"
                )
                items.append(
                    FieldMapping(
                        target=ptype.pointer,
                        expr=_map_values(ptype, const(guess), {guess: guess}, default=guess)
                        if ptype.enum
                        else const(guess),
                        confidence=0.6,
                        evidence=[f"type inferred from present columns → {guess}"],
                    )
                )
        if not items:
            unmatched[sid] = [c.name for c in t.columns]
            continue
        missing = missing_required(target, items)
        if missing:
            unmatched[sid] = [c.name for c in t.columns]
            questions.append(f"Table '{t.sheet}' → {target}: no column for required {', '.join(missing)}")
            continue
        rest = [c.name for c in t.columns if c.name not in {m.column.name for m in matches} | {link_col.name}]
        if rest:
            unmatched[sid] = rest
        collections.append(
            CollectionMapping(
                target=target,
                source=sid,
                link=[JoinKey(left=sku_match.column.name, right=link_col.name)],
                fields=items,
            )
        )
        evidence.append(
            f"table '{t.sheet}' → {target} linked on '{link_col.name}' ↔ root '{sku_match.column.name}'"
        )

    rest_root = [c.name for c in root.columns if c.name not in used_root_cols]
    if rest_root:
        unmatched[root_sid] = rest_root

    quality = [QualityRule(target=identity.pointer, rule="unique", severity="warning")]
    name = schema.semantic_field("product_name")
    if name is not None:
        quality.append(QualityRule(target=name.pointer, rule="not_null"))
    p_amount = item("pricing", "price.amount")
    if price_ptr is not None and p_amount is not None and any(c.target == price_ptr for c in collections):
        quality.append(QualityRule(target=p_amount.pointer, rule="positive", severity="warning"))
    elif root_amount is not None and root_amount.pointer in mapped_targets:
        quality.append(QualityRule(target=root_amount.pointer, rule="positive", severity="warning"))

    conf = round(sum(field_scores) / len(field_scores), 3) if field_scores else 0.0
    conf = max(0.0, min(1.0, conf - 0.05 * len(questions)))
    doc = MappingDocument(
        meta=MappingMeta(
            customer_id=customer_id,
            target_schema=schema.ref,
            created_by="system",
            confidence=conf,
            evidence=evidence,
        ),
        sources=sources,
        joins=[],
        record=RecordSpec(root_source=root_sid),
        fields=fields,
        collections=collections,
        quality=quality,
    )
    return HeuristicProposal(doc, conf, evidence, questions, {i: r[0] for i, r in roles.items()}, unmatched)


def _subset(t: TableProfile, exclude: set[str]) -> TableProfile:
    return TableProfile(
        t.sheet,
        t.sheet_index,
        t.table,
        [c for c in t.columns if c.name not in exclude],
        t.n_rows,
        t.duplicate_key_candidates,
        t.key_candidates,
    )


def _find_link_column(t: TableProfile, root_key: ColumnProfile) -> ColumnProfile | None:
    """Column in a satellite table that refers to the root key: same header, SKU synonyms, or sample overlap."""
    root_samples = {s.strip().upper().lstrip("0") for s in root_key.samples}
    best, best_s = None, 0.0
    for c in t.columns:
        s = 0.0
        if norm(c.name) == norm(root_key.name):
            s += 1.0
        s += fuzz.token_set_ratio(norm(c.name), norm(root_key.name)) / 200.0
        for syn in ("sku", "item", "code", "รหัส", "product", "material", "article"):
            if syn in norm(c.name):
                s += 0.3
                break
        overlap = len({x.strip().upper().lstrip("0") for x in c.samples} & root_samples)
        s += 0.15 * overlap
        if "sku_like" in c.hints:
            s += 0.2
        if s > best_s:
            best, best_s = c, s
    return best if best_s >= 0.5 else None
