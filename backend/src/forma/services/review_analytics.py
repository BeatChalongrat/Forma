"""Exact review counts and transparent, versioned reason tagging. Never model-estimated."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal, ensure_customer
from forma.core.errors import ValidationFailed
from forma.db.models import BatchItem, ImportRound, Job, ReviewTask
from forma.jobs.execution import check

# Tags describe the saved reasons, not whether the source data is actually wrong.
TAGS = {
    "vat": ("VAT / ภาษี", r"\bvat\b|vat_included|ภาษี"),
    "unit": ("หน่วยและจำนวนต่อแพ็ก", r"pack_size|sell_unit|unit[/._ ]|หน่วย|แพ็ก"),
    "price": ("ราคาและส่วนลด", r"prices?[/._ ]|discount|ราคา|ส่วนลด"),
    "stock": ("สต็อกและคลังสินค้า", r"inventory|warehouse|stock|สต็อก|คลัง"),
    "identity": (
        "ตรวจเจ้าของไฟล์",
        r"another customer|customer .+approved format|belongs|wrong customer|ผิดลูกค้า|เจ้าของไฟล์",
    ),
    "structure": (
        "รูปแบบหรือคอลัมน์เปลี่ยน",
        r"schema drift|columns_removed|columns_added|structure|โครงสร้าง|คอลัมน์หาย",
    ),
    "confidence": ("ต้องยืนยันการจับคู่", r"confidence|first time|ความมั่นใจ|ครั้งแรก"),
}


def tags(reasons: list[Any]) -> set[str]:
    found: set[str] = set()
    for reason in reasons:
        raw = json.dumps(reason, ensure_ascii=False) if not isinstance(reason, str) else reason
        matches = {key for key, (_, pattern) in TAGS.items() if re.search(pattern, raw, re.I)}
        found.update(matches or {"unclassified"})
    return found or {"unclassified"}


def query_scope(query: Any, model: Any, p: Principal, a: dict[str, Any]) -> Any:
    if p.customers is not None:
        query = query.where(model.customer_id.in_(sorted(p.customers)))
    if a.get("customer_id"):
        ensure_customer(p, a["customer_id"])
        query = query.where(model.customer_id == a["customer_id"])
    round_column = model.id if model is ImportRound else model.round_id
    if a.get("round_id"):
        query = query.where(round_column == a["round_id"])
    if a.get("batch_id"):
        query = query.where(
            round_column.in_(select(BatchItem.round_id).where(BatchItem.batch_id == a["batch_id"]))
        )
    if a.get("status") and a["status"] != "all":
        query = query.where(model.status == a["status"])
    if a.get("start") or a.get("end"):
        try:
            if not a.get("start") or not a.get("end") or not a.get("timezone"):
                raise ValueError
            start, end = dt.date.fromisoformat(a["start"]), dt.date.fromisoformat(a["end"])
            zone = ZoneInfo(a["timezone"])
            if end < start:
                raise ValueError
            lower = dt.datetime.combine(start, dt.time.min, zone)
            upper = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, zone)
        except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
            raise ValidationFailed("Provide start/end dates in order and an IANA timezone") from exc
        query = query.where(
            model.created_at >= lower.astimezone(dt.UTC), model.created_at < upper.astimezone(dt.UTC)
        )
    return query


def summary(session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    filters = {"status": "open", **a}
    counts: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    rounds: set[str] = set()
    examples: dict[str, list[str]] = {}
    example_details: dict[str, list[dict[str, Any]]] = {}
    total = unclassified = 0
    # One statement: row population cannot shift between pages during this snapshot.
    query = query_scope(
        select(
            ReviewTask.id,
            ReviewTask.round_id,
            ReviewTask.mapping_version_id,
            ReviewTask.kind,
            ReviewTask.status,
            ReviewTask.reasons,
        ),
        ReviewTask,
        p,
        filters,
    )
    for row in session.execute(query.execution_options(yield_per=1000)):
        check()
        total += 1
        rounds.add(row.round_id)
        kinds[row.kind] += 1
        statuses[row.status] += 1
        categories = tags(row.reasons or [])
        unclassified += "unclassified" in categories
        for category in sorted(categories):
            counts[category] += 1
            if len(examples.setdefault(category, [])) < 3:
                examples[category].append(row.id)
                example_details.setdefault(category, []).append(
                    {
                        "review_id": row.id,
                        "round_id": row.round_id,
                        "mapping_id": row.mapping_version_id,
                    }
                )
    return {
        "total": total,
        "distinct_rounds": len(rounds),
        "unit": "review_tasks",
        "categories": [
            {
                "id": k,
                "label": TAGS[k][0] if k in TAGS else "เหตุผลอื่น / ยังจัดประเภทไม่ได้",
                "count": v,
                "example_review_ids": examples[k],
                "examples": example_details[k],
            }
            for k, v in sorted(counts.items())
        ],
        "kinds": dict(kinds),
        "statuses": dict(statuses),
        "coverage": {
            "matched": total,
            "scanned": total,
            "complete": True,
            "tasks_with_unclassified_reasons": unclassified,
        },
        "scope": filters,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "classification": "saved-reason-tags-v1",
        "categories_overlap": True,
        "notes": [
            "นับงานตรวจ ไม่ใช่จำนวนไฟล์หรือจำนวนคำถาม งานเดียวมีได้หลายหมวด ห้ามบวกหมวดเป็นยอดงานทั้งหมด",
            "จัดหมวดตามเหตุผลที่บันทึกด้วยกฎคงที่ ไม่ได้ตัดสินว่าข้อมูลผิด เหตุผลที่ไม่รู้จักคงอยู่ในหมวดอื่น",
            "วันที่กรองคือวันที่สร้างงานตรวจ ไม่ใช่วันที่อัปโหลดหรือวันที่แก้เสร็จ",
        ],
        "links": [{"label": "Questions & issues", "url": "/review"}],
    }


def operations(session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    resource = a.get("resource", "imports")
    if resource == "imports":
        query = query_scope(select(ImportRound.status), ImportRound, p, a)
    else:
        # Only import jobs. Assistant/report jobs are private and must never leak here.
        visible = query_scope(
            select(ImportRound.id),
            ImportRound,
            p,
            {k: v for k, v in a.items() if k not in {"status", "start", "end", "timezone"}},
        )
        query = select(Job.status).where(Job.round_id.in_(visible))
        if a.get("status") and a["status"] != "all":
            query = query.where(Job.status == a["status"])
        if a.get("start") or a.get("end"):
            # Date validation and filtering are shared without opening private jobs.
            query = query_scope(
                query,
                Job,
                Principal(subject=p.subject, roles=p.roles, customers=None),
                {k: v for k, v in a.items() if k in {"start", "end", "timezone"}},
            )
    counts: Counter[str] = Counter()
    for status in session.scalars(query.execution_options(yield_per=1000)):
        check()
        counts[status] += 1
    total = sum(counts.values())
    return {
        "total": total,
        "statuses": dict(counts),
        "resource": resource,
        "scope": a,
        "coverage": {"matched": total, "scanned": total, "complete": True},
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "unit": "import_rounds" if resource == "imports" else "import_jobs",
        "links": [{"label": "Imports", "url": "/imports"}],
    }
