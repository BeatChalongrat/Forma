"""Permission-first discovery over live metadata, approved knowledge and personal chats.

Concept expansion is explicit and inspectable; this is not an embedding index and never
claims to have searched all cells in every source file. Numerical answers use report tools.
"""

from __future__ import annotations

import heapq
import json
import re
import unicodedata
from typing import Any

from rapidfuzz.fuzz import partial_ratio
from sqlalchemy import and_, false, or_, select
from sqlalchemy.orm import Session

from forma.auth import Principal, ensure_customer
from forma.db.models import (
    AssistantConversation,
    AssistantTurn,
    Correction,
    Customer,
    ImportRound,
    MappingVersion,
    ReviewTask,
    SourceFile,
)
from forma.jobs.execution import check

CONCEPTS = (
    ("vat", "ภาษี", "tax", "ภาษีมูลค่าเพิ่ม"),
    ("stock", "inventory", "สินค้าคงคลัง", "สต็อก", "จำนวนคงเหลือ"),
    ("price", "ราคา", "ราคาขาย", "selling price", "sales price"),
    ("unit", "หน่วย", "pack", "แพ็ก", "บรรจุ", "sell_unit", "pack_size"),
    ("failed", "error", "ล้มเหลว", "ผิดพลาด", "ไม่สำเร็จ"),
    ("mapping", "จับคู่", "แมป", "map field"),
    ("warehouse", "คลัง", "โกดัง"),
)
SOURCES = ["customers", "files", "rounds", "reviews", "knowledge", "chats"]
TOOL = {
    "name": "search_workspace",
    "description": "Discover related files, customers, rounds, reviews, approved knowledge and your own chats. Searches ALL authorized metadata/saved chat text, not all cells or attachment bodies. Hybrid exact/word/fuzzy search with explicit Thai-English concept expansion. Supply alternate_queries for semantic paraphrases (not assumed facts). Follow next_offset. Read matched source tools for evidence; never count search hits as business metrics.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 200},
            "alternate_queries": {
                "type": "array",
                "maxItems": 5,
                "items": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            "sources": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": SOURCES}},
            "customer_id": {"type": "string", "maxLength": 40},
            "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True},
}


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def terms(query: str, alternatives: list[str] | None = None) -> list[str]:
    selected = [normalize(query), *(normalize(s) for s in alternatives or [])]
    selected.extend(t for t in re.split(r"\s+", normalize(query)) if len(t) >= 2)
    for group in CONCEPTS:
        if any(t in s for t in group for s in selected[:6]):
            selected.extend(group)
    return list(dict.fromkeys(selected))


def relevance(body: str, words: list[str]) -> tuple[float, str]:
    normalized = normalize(body)
    hits = [t for t in words if t in normalized]
    if hits:
        return 100.0 + min(len(hits), 20) + (30 if words[0] in normalized else 0), hits[0]
    # Fuzzy discovery is never confirmation of a value or a business convention.
    best = max(((partial_ratio(t, normalized), t) for t in words if len(t) >= 5), default=(0.0, ""))
    return best if best[0] >= 88 else (0.0, "")


def search(session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    from forma.services import config, conversations, decision_memory, knowledge_gate

    if a.get("customer_id"):
        ensure_customer(p, a["customer_id"])
    words = terms(a["query"], a.get("alternate_queries"))
    offset = a.get("offset", 0)
    best: list[tuple[float, str, dict[str, Any]]] = []
    scanned = matched = 0
    counts: dict[str, int] = {}
    excluded: dict[str, str] = {}

    def offer(kind: str, rid: str, title: str, body: Any, url: str, **extra: Any) -> None:
        nonlocal scanned, matched
        check()
        scanned += 1
        raw = (
            title
            + "\n"
            + (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, default=str))
        )
        score, term = relevance(raw, words)
        if not score:
            return
        matched += 1
        counts[kind] = counts.get(kind, 0) + 1
        pos = max(0, normalize(raw).find(term) - 100)
        item = {
            "kind": kind,
            "id": rid,
            "title": title,
            "excerpt": raw[pos : pos + 650],
            "match": term,
            "match_type": "exact_or_expanded" if score >= 100 else "fuzzy",
            "url": url,
            **extra,
        }
        entry = (score, kind + ":" + rid, item)
        if len(best) < offset + 20:
            heapq.heappush(best, entry)
        elif entry[:2] > best[0][:2]:
            heapq.heapreplace(best, entry)

    def scoped(model: Any) -> Any:
        query = select(model)
        shared = (
            and_(Correction.customer_id.is_(None), Correction.kind == "promoted")
            if model is Correction
            else false()
        )
        if p.customers is not None:
            query = query.where(or_(model.customer_id.in_(sorted(p.customers)), shared))
        if a.get("customer_id"):
            query = query.where(or_(model.customer_id == a["customer_id"], shared))
        return query.execution_options(yield_per=200)

    for kind in a.get("sources", SOURCES):
        if kind == "customers":
            query = select(Customer)
            if p.customers is not None:
                query = query.where(Customer.id.in_(sorted(p.customers)))
            if a.get("customer_id"):
                query = query.where(Customer.id == a["customer_id"])
            for row in session.scalars(query.execution_options(yield_per=200)):
                offer(kind, row.id, f"{row.code} · {row.name}", row.notes or "", "/customers/" + row.id)
        elif kind == "files":
            for row in session.scalars(scoped(SourceFile)):
                offer(
                    kind,
                    row.id,
                    row.original_name,
                    row.profile or {},
                    "/rounds/" + row.round_id,
                    file_id=row.id,
                    round_id=row.round_id,
                    body_scope="name_and_saved_profile_not_all_cells",
                )
        elif kind == "rounds":
            for row in session.scalars(scoped(ImportRound)):
                offer(
                    kind,
                    row.id,
                    row.label or row.id,
                    {"notes": row.notes, "status": row.status, "summary": row.summary},
                    "/rounds/" + row.id,
                    round_id=row.id,
                )
        elif kind == "reviews":
            for row in session.scalars(scoped(ReviewTask)):
                offer(
                    kind,
                    row.id,
                    row.kind,
                    row.reasons,
                    "/review?round=" + row.round_id,
                    mapping_id=row.mapping_version_id,
                    status=row.status,
                )
        elif kind == "knowledge":
            for row in session.scalars(scoped(Correction).where(Correction.status == "confirmed")):
                if not knowledge_gate.allowed(session, row):
                    continue
                mapping = (
                    session.get(MappingVersion, row.mapping_version_id) if row.mapping_version_id else None
                )
                if row.mapping_version_id and mapping is None:
                    continue
                if mapping and (
                    mapping.status != "approved"
                    or not knowledge_gate.allowed(session, mapping)
                    or not decision_memory.verification_allows_recall(mapping)
                ):
                    continue
                offer(
                    kind,
                    row.id,
                    row.target or row.header or row.kind,
                    row.after,
                    "/settings?tab=learning",
                    customer_id=row.customer_id,
                    mapping_id=row.mapping_version_id,
                    scope_required_before_reuse=True,
                )
        elif kind == "chats":
            if not config.personal_preferences(session, p.subject, p.mode).get("chat_search", True):
                excluded[kind] = "chat search disabled in personal preferences"
                continue
            chats = conversations.owned(p).where(
                AssistantConversation.incognito.is_(False), AssistantConversation.memory_off.is_(False)
            )
            if a.get("customer_id"):
                chats = chats.where(
                    AssistantConversation.round_id.in_(
                        select(ImportRound.id).where(ImportRound.customer_id == a["customer_id"])
                    )
                )
            chat_query = (
                select(AssistantTurn, AssistantConversation.title)
                .join(AssistantConversation, AssistantConversation.id == AssistantTurn.conversation_id)
                .where(
                    AssistantTurn.conversation_id.in_(chats.with_only_columns(AssistantConversation.id)),
                    AssistantTurn.status == "complete",
                )
            )
            for turn, title in session.execute(chat_query.execution_options(yield_per=200)):
                offer(
                    kind,
                    turn.id,
                    title,
                    turn.question + "\n" + turn.answer,
                    "/assistant?chat=" + turn.conversation_id,
                    historical_not_live=True,
                )
    items = [entry[2] for entry in sorted(best, key=lambda e: e[:2], reverse=True)[offset : offset + 20]]
    return {
        "items": items,
        "total": matched,
        "offset": offset,
        "next_offset": offset + 20 if offset + 20 < matched else None,
        "coverage": {
            "scanned": scanned,
            "matched": matched,
            "returned": len(items),
            "complete": offset == 0 and matched <= 20,
            "scan_complete": True,
        },
        "scope": {
            "sources": a.get("sources", SOURCES),
            "customer_id": a.get("customer_id"),
            "contents": "saved metadata, confirmed knowledge and accessible chat text; not all source cells/attachment bodies",
        },
        "expanded_terms": words,
        "excluded_sources": excluded,
        "matching_by_source": counts,
        "retrieval": "exact + concept expansion + fuzzy; no embedding index",
        "links": [{"label": i["title"], "url": i["url"]} for i in items],
    }
