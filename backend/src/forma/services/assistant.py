"""Data repair copilot (ADR-023): plain-language instructions become reviewable operations, and plain-language
questions ("ทำไม SKU 120 รายการ join ไม่สำเร็จ") get answers built from evidence — findings, the measured
relationships between tables, lineage and anomalies — never from guesses.

Planning without an LLM understands the common shapes: a price change by percent or a status change, scoped
by category or brand, with conditions across collections (``stock > 100``, ``not in a promotion``). With an
LLM configured, the copilot agent plans instead and sees the same derived figures. Nothing is applied here:
operations go to the editor's pending list, are previewed, and saved as a new version by a person.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from forma.mapping_dsl.models import MappingDocument
from forma.schema_registry import TargetSchema
from forma.services.context import ServiceContext
from forma.services.datasets import _identity_of, get_dataset, records_of

# ------------------------------------------------------------------------------------------------
# planning
# ------------------------------------------------------------------------------------------------
_NUM = r"(\d+(?:[.,]\d+)?)"
_GT = r"มากกว่าหรือเท่ากับ|ตั้งแต่|อย่างน้อย|at least|>=|มากกว่า|เกิน|สูงกว่า|more than|greater than|over|above|>"
_LT = r"น้อยกว่าหรือเท่ากับ|ไม่เกิน|at most|<=|น้อยกว่า|ต่ำกว่า|ไม่ถึง|less than|fewer than|under|below|<"
_GTE_WORDS = ("มากกว่าหรือเท่ากับ", "ตั้งแต่", "อย่างน้อย", "at least", ">=")
_LTE_WORDS = ("น้อยกว่าหรือเท่ากับ", "ไม่เกิน", "at most", "<=")
_STOCK = r"(?:stock|สต็อก|สต๊อก|สต็อค|สต๊อค|คงเหลือ|จำนวนคงเหลือ|on hand|qty|quantity)"
_PRICE = r"(?:ราคา|price)"
_PROMO = r"(?:promotions?|promo|โปรโมชั่น|โปรโมชัน|โปร)"
_EXCLUDE = r"(?:ไม่รวม|ยกเว้น|ไม่เอา|ไม่อยู่ใน|ไม่มี|exclude|excluding|except|not in|without)"


def _col(name: str) -> dict[str, Any]:
    return {"op": "col", "name": name}


def _contains(pointer: str, value: str) -> dict[str, Any]:
    return {"op": "contains", "args": [_col(pointer)], "params": {"pattern": value, "literal": True}}


def _compare(op: str, name: str, value: float) -> dict[str, Any]:
    return {"op": op, "args": [_col(name), {"op": "const", "value": value}]}


def _figure(schema: TargetSchema, semantic: str, fn: str) -> str | None:
    """Where a meaning lives for a condition: the root field, else an aggregate over its collection."""
    ptrs = sorted(schema.semantic_pointers(semantic))
    root = next((p for p in ptrs if "/*/" not in p), None)
    if root:
        return root
    return f"{fn}({ptrs[0]})" if ptrs else None


def _conditions(text: str, schema: TargetSchema) -> tuple[list[dict[str, Any]], list[str]]:
    conds: list[dict[str, Any]] = []
    labels: list[str] = []
    for word, semantic, fn, label in (
        (_STOCK, "stock.qty", "sum", "stock รวม"),
        (_PRICE, "price.amount", "min", "ราคา"),
    ):
        target = _figure(schema, semantic, fn)
        if target is None:
            continue
        for m in re.finditer(rf"{word}\s*(?:รวม|ทั้งหมด|total)?\s*({_GT}|{_LT})\s*{_NUM}", text, re.I):
            cmp, n = m.group(1).lower(), float(m.group(2).replace(",", ""))
            if re.fullmatch(_GT, cmp, re.I):
                op = "gte" if cmp in _GTE_WORDS else "gt"
            else:
                op = "lte" if cmp in _LTE_WORDS else "lt"
            conds.append(_compare(op, target, n))
            sym = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}[op]
            labels.append(f"{label} {sym} {n:g}")
    promo = schema.collection_for("promotions")
    if promo:
        if re.search(rf"{_EXCLUDE}[^,.;]*?{_PROMO}", text, re.I):
            conds.append(_compare("lt", f"count({promo})", 1))
            labels.append("ไม่อยู่ในโปรโมชั่น")
        elif re.search(rf"(?:อยู่ใน|ที่มี|เฉพาะที่มี|in a|in|with|on)\s*{_PROMO}", text, re.I):
            conds.append(_compare("gte", f"count({promo})", 1))
            labels.append("อยู่ในโปรโมชั่น")
    return conds, labels


def rule_based_plan(text: str, schema: TargetSchema) -> list[dict[str, Any]] | None:
    """Plan an instruction without an LLM; ``None`` when it is not understood or names no records."""
    t = " ".join(text.strip().split())
    category = schema.semantic_field("category_path")
    status = schema.semantic_field("status")
    brand = schema.semantic_field("brand")
    prices = sorted(schema.semantic_pointers("price.amount"))
    scope: list[dict[str, Any]] = []
    scope_label = ""
    m = re.search(r"(?:หมวด(?:หมู่)?|category)\s*[:=]?\s*([^\s,]+)", t, re.I)
    if m and category is not None:
        scope.append(_contains(category.pointer, m.group(1)))
        scope_label = f"หมวด {m.group(1)}"
    m = re.search(r"(?:ยี่ห้อ|แบรนด์|brand)\s*[:=]?\s*([^\s,]+)", t, re.I)
    if m and brand is not None:
        scope.append(_contains(brand.pointer, m.group(1)))
        scope_label = (scope_label + " " if scope_label else "") + f"ยี่ห้อ {m.group(1)}"
    conds, labels = _conditions(t, schema)
    parts = scope + conds
    if not parts:
        return None  # an edit must say which records — never "everything" by accident
    where = parts[0] if len(parts) == 1 else {"op": "and", "args": parts}
    only = (" เฉพาะ " + " และ ".join(labels)) if labels else ""

    m = re.search(
        r"(ลดราคา|ลด|discount|reduce|decrease|lower|เพิ่มราคา|ขึ้นราคา|เพิ่ม|ขึ้น|increase|raise).*?"
        rf"{_NUM}\s*%",
        t,
        re.I,
    )
    if m and prices:
        pct = float(m.group(2).replace(",", ""))
        down = m.group(1).lower() in ("ลดราคา", "ลด", "discount", "reduce", "decrease", "lower")
        verb = "ลดราคา" if down else "ขึ้นราคา"
        what = f"สินค้า{scope_label}" if scope_label else "สินค้า"
        return [
            {
                "kind": "scale_field",
                "target": prices[0],
                "factor": round(1 - pct / 100 if down else 1 + pct / 100, 4),
                "where": where,
                "explanation": f"{verb}{what} {'ลง' if down else 'ขึ้น'} {pct:g}%{only}",
            }
        ]
    m = re.search(r"(?:ตั้งสถานะ|set status)\s*(active|inactive|discontinued)", t, re.I)
    if m and status is not None:
        value = m.group(1).lower()
        return [
            {
                "kind": "set_field",
                "target": status.pointer,
                "value": value,
                "where": where,
                "explanation": f"ตั้งสถานะ {value} ให้{scope_label or 'สินค้า'}{only}",
            }
        ]
    return None


def is_question(text: str) -> bool:
    t = text.strip().lower()
    return t.endswith("?") or bool(re.match(r"^(ทำไม|เพราะอะไร|อะไร|ทำไง|ยังไง|why|what|how|which)", t))


# ------------------------------------------------------------------------------------------------
# answering "why" from evidence
# ------------------------------------------------------------------------------------------------
_FIELD_WORDS: tuple[tuple[str, str], ...] = (
    (r"ราคา|price", "price.amount"),
    (r"สต็อก|สต๊อก|stock|คงเหลือ|qty", "stock.qty"),
    (r"ส่วนลด|discount", "discount.percent"),
    (r"ชื่อ|name", "product_name"),
    (r"สถานะ|status", "status"),
)


def _normalized(v: str, how: str) -> str:
    v = v.strip().upper()
    if how == "strip_leading_zeros":
        return re.sub(r"^0+(\d)", r"\1", v)
    if how == "alnum":
        return re.sub(r"[^0-9A-Zก-๙]", "", v)
    return v


def _link_diagnosis(
    ctx: ServiceContext, session: Session, round_id: str, doc: MappingDocument
) -> list[dict[str, Any]]:
    """Each link/join that drops rows, with the keys that fail and whether a key format explains it."""
    from forma.engine.transform import load_source_frame
    from forma.services.mapping import bind_sources
    from forma.services.relationships import _key_series, discover
    from forma.services.rounds import get_round

    graph = discover(ctx, session, round_id, doc.model_dump(mode="json", exclude_none=True))
    r = get_round(session, round_id)
    bindings, _ = bind_sources(doc, r.files, resolve=ctx.storage.local_path)
    names = {n["id"]: n["sheet"] or n["id"] for n in graph["nodes"]}
    out: list[dict[str, Any]] = []
    for e in graph["edges"]:
        if e["kind"] not in ("link", "join") or (not e.get("to_orphan_rows") and not e.get("missing")):
            continue
        item: dict[str, Any] = {
            "from": f"{names.get(e['from']['node'])}.{e['from']['column']}",
            "to": f"{names.get(e['to']['node'])}.{e['to']['column']}",
            "orphan_rows": e.get("to_orphan_rows", 0),
            "missing": bool(e.get("missing")),
            "warnings": e["warnings"],
        }
        if not e.get("missing"):
            try:
                frames = {}
                for side in ("from", "to"):
                    sid = e[side]["node"]
                    spec = next(s for s in doc.sources if s.id == sid)
                    frames[side] = load_source_frame(spec, bindings[sid], limit=20_000)
                parent = set(_key_series(frames["from"], e["from"]["column"]).to_list())
                child = _key_series(frames["to"], e["to"]["column"]).to_list()
                orphans = sorted({k for k in child if k not in parent})
                item["orphan_keys"] = orphans[:10]
                item["orphan_key_count"] = len(orphans)
                for how, fix in (
                    ("strip_leading_zeros", "add strip_leading_zeros to the link's normalize_keys"),
                    ("alnum", "the keys differ only in spaces, dashes or dots — clean them in the link"),
                ):
                    pn = {_normalized(k, how) for k in parent}
                    hits = sum(1 for k in orphans if _normalized(k, how) in pn)
                    if hits:
                        item["format_fix"] = {"how": how, "would_match": hits, "fix": fix}
                        break
            except Exception:  # evidence is best effort; the counts above still stand
                pass
        out.append(item)
    return out


def answer(ctx: ServiceContext, session: Session, dataset_id: str, question: str) -> dict[str, Any]:
    from forma.services import lineage
    from forma.services.datasets import anomaly_report
    from forma.services.mapping import get_mapping

    dv = get_dataset(session, dataset_id)
    mv = get_mapping(session, dv.mapping_version_id)
    schema = ctx.registry.get_ref(mv.target_schema)
    doc = MappingDocument.model_validate(mv.document)
    q = question.strip()
    ql = q.lower()

    # 1) about one record: "ทำไมราคา A001 เปลี่ยน"
    ids = {_identity_of(r, schema) for r in records_of(ctx, dv)}
    named = [tok for tok in re.findall(r"[\w\-./]+", q) if tok in ids]
    if named:
        semantic = next((s for rx, s in _FIELD_WORDS if re.search(rx, ql)), "price.amount")
        ptrs = sorted(schema.semantic_pointers(semantic))
        pointer = ptrs[0].replace("/*/", "/0/", 1) if ptrs else None
        t = lineage.trace(ctx, session, dataset_id, named[0], pointer)
        focus = t.get("focus") or {}
        cause = focus.get("cause") or {}
        cells = focus.get("cells") or []
        where = ", ".join(
            f"{c['sheet'] or c['source']}!{c['column']}{c['row']} = {c['raw']!r}" for c in cells
        )
        return {
            "intent": "record",
            "ai": False,
            "answer": f"{named[0]} {pointer}: {focus.get('value')} — {cause.get('summary', '')}",
            "evidence": [*(cause.get("evidence") or []), *([f"source cell: {where}"] if where else [])],
            "suggestions": [],
            "trace": {"identity": named[0], "pointer": pointer},
        }

    # 2) links that drop rows: "ทำไม SKU 120 รายการ join ไม่สำเร็จ"
    if re.search(r"join|link|เชื่อม|จับคู่|match|ไม่เจอ|หาไม่เจอ|ไม่ครบ|orphan|หาย", ql):
        diag = _link_diagnosis(ctx, session, dv.round_id, doc)
        if not diag:
            return {
                "intent": "join",
                "ai": False,
                "answer": "ทุกตารางเชื่อมกันครบ: ไม่มีแถวใดถูกทิ้งเพราะหา key ไม่เจอ",
                "evidence": [],
                "suggestions": [],
            }
        lines, fixes = [], []
        for d in diag:
            if d["missing"]:
                lines.append(f"{d['from']} ↔ {d['to']}: คอลัมน์ที่ใช้เชื่อมไม่มีในไฟล์")
                fixes.append(f"เลือกคอลัมน์เชื่อมใหม่ระหว่าง {d['from']} และ {d['to']} ใน Mapping Studio")
                continue
            sample = ", ".join(d.get("orphan_keys", [])[:5])
            lines.append(
                f"{d['to']}: {d['orphan_rows']} แถวไม่เจอ {d['from']} "
                f"({d.get('orphan_key_count', '?')} key เช่น {sample})"
            )
            if d.get("format_fix"):
                ff = d["format_fix"]
                fixes.append(f"{ff['would_match']} key จะเจอคู่ถ้าปรับรูปแบบ key: {ff['fix']}")
            else:
                fixes.append(f"key เหล่านี้ไม่มีใน {d['from']} จริง ๆ — ตรวจกับ ERP ว่าเป็นสินค้าที่ถูกลบหรือยัง")
        return {
            "intent": "join",
            "ai": False,
            "answer": "; ".join(lines),
            "evidence": diag,
            "suggestions": fixes,
        }

    # 3) anomalies / what to check first
    if re.search(r"ผิดปกติ|แปลก|anomal|ควรตรวจ|ตรวจอะไร|check first|unusual", ql):
        rep = anomaly_report(ctx, session, dv)
        return {
            "intent": "anomalies",
            "ai": False,
            "answer": rep["headline"],
            "evidence": [f"{a['identity']}: {a['message']}" for a in rep["items"][:10]]
            + [a["message"] for a in rep["dataset"]],
            "suggestions": [],
        }

    # 4) duplicates and errors: from the engine's findings
    kind = "dedupe" if re.search(r"ซ้ำ|duplicate", ql) else None
    findings = [f for f in dv.findings if kind is None or f.get("kind") == kind]
    if findings:
        return {
            "intent": kind or "findings",
            "ai": False,
            "answer": f"พบ {len(findings)} ประเด็นจากการแปลงข้อมูล",
            "evidence": [f"{f.get('severity')}: {f.get('message')}" for f in findings[:15]],
            "suggestions": [],
        }
    return {
        "intent": "unknown",
        "ai": False,
        "answer": "ยังตอบคำถามนี้จากหลักฐานไม่ได้ ลองถามเรื่อง SKU ที่ระบุรหัส, การ join ที่ไม่สำเร็จ, ข้อมูลผิดปกติ หรือข้อมูลซ้ำ",
        "evidence": [],
        "suggestions": [],
    }
