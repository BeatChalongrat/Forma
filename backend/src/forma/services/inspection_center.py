"""A scoped read model over existing receipts. Never synthesizes missing historical decisions."""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any

from sqlalchemy import String, cast, func, literal, or_, select, union_all
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import NotFound, ValidationFailed
from forma.db.models import (
    AssistantArtifact,
    AssistantArtifactVersion,
    AssistantConversation,
    AssistantTurn,
    AuditLog,
    Batch,
    BatchItem,
    ChangeSet,
    Correction,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    LLMCall,
    MappingVersion,
    ReviewTask,
    SourceFile,
)
from forma.jobs import activity
from forma.services import conversations, inspection

LABELS = {
    "file": "รับไฟล์ต้นทาง",
    "item": "รับไฟล์เข้ารอบงาน",
    "mapping": "สร้างการจับคู่ข้อมูล",
    "review": "เปิดรายการให้ตรวจ",
    "dataset": "สร้างผลลัพธ์ข้อมูล",
    "change": "บันทึกการแก้ไขข้อมูล",
    "export": "สร้างไฟล์สำหรับดาวน์โหลด",
    "knowledge": "บันทึกความรู้จากคำตอบ",
    "llm": "เรียกใช้โมเดล",
    "job": "สร้างงานเข้าคิว",
    "turn": "ข้อความในบทสนทนา",
    "artifact": "สร้างเวอร์ชันผลงาน",
    "round": "รับชุดไฟล์ของลูกค้า",
    "chat": "สร้างบทสนทนา",
}
AUDIT_LABELS = {
    "review.answers": "บันทึกคำตอบและตรวจผล",
    "mapping.propose": "เสนอการจับคู่ข้อมูล",
    "mapping.edit": "สร้างเวอร์ชันการจับคู่ใหม่",
    "dataset.transform": "สร้างข้อมูลผลลัพธ์",
    "dataset.edit": "แก้ข้อมูลผลลัพธ์",
    "dataset.export": "สร้างไฟล์ส่งออก",
    "dataset.rollback": "สร้างเวอร์ชันจากข้อมูลก่อนหน้า",
    "assistant.create": "สร้างบทสนทนา",
    "assistant.stop": "หยุดคำตอบของผู้ช่วย",
    "learning.use": "นำความรู้เดิมมาใช้",
    "mapping.approve": "อนุมัติการจับคู่",
    "mapping.reject": "ไม่อนุมัติการจับคู่",
    "dataset.publish": "เผยแพร่ผลลัพธ์",
    "assistant.report.queued": "ส่งรายงานเข้าคิว",
    "assistant.report.completed": "สร้างรายงานสำเร็จ",
}
MODELS: dict[str, Any] = {
    "round": ImportRound,
    "file": SourceFile,
    "item": BatchItem,
    "mapping": MappingVersion,
    "review": ReviewTask,
    "dataset": DatasetVersion,
    "change": ChangeSet,
    "export": ExportArtifact,
    "knowledge": Correction,
    "llm": LLMCall,
    "job": Job,
    "audit": AuditLog,
    "chat": AssistantConversation,
    "turn": AssistantTurn,
    "artifact": AssistantArtifactVersion,
}


def scoped(s: Session, p: Principal, scope: str, key: str) -> Any:
    if scope == "chat":
        return conversations.get_chat(s, p, key)
    model: Any = {"round": ImportRound, "item": BatchItem}.get(scope)
    row = s.get(model, key) if model else None
    if row is None or (p.customers is not None and row.customer_id not in p.customers):
        raise NotFound("Work not found")
    return row


def dates(start: dt.date | None, end: dt.date | None) -> tuple[dt.datetime | None, dt.datetime | None]:
    if start and end and start > end:
        raise ValidationFailed("วันที่เริ่มต้องไม่อยู่หลังวันที่สิ้นสุด")
    try:
        return (
            dt.datetime.combine(start, dt.time.min, dt.UTC) if start else None,
            dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, dt.UTC) if end else None,
        )
    except OverflowError as exc:
        raise ValidationFailed("ช่วงวันที่ไม่ถูกต้อง") from exc


def items(
    s: Session,
    p: Principal,
    *,
    kind: str,
    search: str,
    customer: str,
    batch: str,
    status: str,
    start: dt.date | None,
    end: dt.date | None,
    offset: int,
) -> dict[str, Any]:
    def projection(model: Any, scope: str, title: Any, state: Any, customer_id: Any) -> Any:
        return select(
            literal(scope).label("scope"),
            model.id.label("id"),
            title.label("title"),
            state.label("status"),
            customer_id.label("customer_id"),
            model.created_at.label("at"),
        )

    if kind == "assistant":
        q = projection(
            AssistantConversation, "chat", AssistantConversation.title, literal("conversation"), literal(None)
        ).where(
            AssistantConversation.id.in_(conversations.visible(p).with_only_columns(AssistantConversation.id))
        )
        if search:
            report_chats = select(Job.payload["chat_id"].as_string()).where(
                Job.kind == "create_document", Job.id.icontains(search, autoescape=True)
            )
            q = q.where(
                AssistantConversation.title.icontains(search, autoescape=True)
                | AssistantConversation.id.icontains(search, autoescape=True)
                | AssistantConversation.id.in_(report_chats)
            )
        if customer or batch or status:
            q = q.where(literal(False))  # import filters must never broaden private-chat results
    else:
        r, b = ImportRound, BatchItem
        rq = projection(r, "round", func.coalesce(r.label, r.id), r.status, r.customer_id)
        bq = projection(b, "item", b.file_name, b.status, b.customer_id).where(b.round_id.is_(None))
        if p.customers is not None:
            rq, bq = rq.where(r.customer_id.in_(p.customers)), bq.where(b.customer_id.in_(p.customers))
        if customer:
            customers = select(Customer.id).where(
                or_(
                    Customer.id == customer,
                    Customer.code.icontains(customer, autoescape=True),
                    Customer.name.icontains(customer, autoescape=True),
                )
            )
            rq, bq = rq.where(r.customer_id.in_(customers)), bq.where(b.customer_id.in_(customers))
        if batch:
            batches = select(Batch.id).where(
                (Batch.id == batch) | Batch.label.icontains(batch, autoescape=True)
            )
            rq = rq.where(r.id.in_(select(b.round_id).where(b.batch_id.in_(batches))))
            bq = bq.where(b.batch_id.in_(batches))
        if status:
            rq, bq = rq.where(r.status == status), bq.where(b.status == status)
        if search:
            rounds_with_file = select(SourceFile.round_id).where(
                SourceFile.original_name.icontains(search, autoescape=True)
            )
            rounds_with_job = select(Job.round_id).where(Job.id.icontains(search, autoescape=True))
            rq = rq.where(
                or_(
                    r.label.icontains(search, autoescape=True),
                    r.id.icontains(search, autoescape=True),
                    r.id.in_(rounds_with_file),
                    r.id.in_(rounds_with_job),
                )
            )
            bq = bq.where(
                b.file_name.icontains(search, autoescape=True)
                | b.id.icontains(search, autoescape=True)
                | b.id.in_(
                    select(Job.payload["item_id"].as_string()).where(
                        Job.id.icontains(search, autoescape=True)
                    )
                )
            )
        q = union_all(rq, bq)
    sub = q.subquery()
    query = select(sub)
    lower, upper = dates(start, end)
    if lower:
        query = query.where(sub.c.at >= lower)
    if upper:
        query = query.where(sub.c.at < upper)
    total = s.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = [
        dict(r)
        for r in s.execute(
            query.order_by(sub.c.at.desc(), sub.c.id.desc()).offset(offset).limit(26)
        ).mappings()
    ]
    customer_ids = {r["customer_id"] for r in rows if r["customer_id"]}
    names = {
        c.id: f"{c.code} · {c.name}" for c in s.scalars(select(Customer).where(Customer.id.in_(customer_ids)))
    }
    rids = [r["id"] for r in rows if r["scope"] == "round"]
    files: dict[str, list[str]] = {}
    for f in s.scalars(select(SourceFile).where(SourceFile.round_id.in_(rids))):
        files.setdefault(f.round_id, []).append(f.original_name)
    return {
        "items": [
            {**r, "customer": names.get(r["customer_id"]), "files": files.get(r["id"], [])} for r in rows[:25]
        ],
        "total": total,
        "next_offset": offset + 25 if len(rows) > 25 else None,
    }


def sources(s: Session, p: Principal, scope: str, key: str) -> tuple[Any, dict[str, Any]]:
    row = scoped(s, p, scope, key)
    if scope == "round":
        mappings = select(MappingVersion.id).where(MappingVersion.round_id == key)
        datasets = select(DatasetVersion.id).where(DatasetVersion.round_id == key)
        conditions = {
            "round": ImportRound.id == key,
            "file": SourceFile.round_id == key,
            "item": BatchItem.round_id == key,
            "mapping": MappingVersion.round_id == key,
            "review": ReviewTask.round_id == key,
            "dataset": DatasetVersion.round_id == key,
            "change": ChangeSet.dataset_version_id.in_(datasets),
            "export": ExportArtifact.dataset_version_id.in_(datasets),
            "knowledge": (Correction.round_id == key) | Correction.mapping_version_id.in_(mappings),
            "llm": LLMCall.round_id == key,
            "job": (
                (Job.round_id == key)
                | Job.payload["item_id"]
                .as_string()
                .in_(select(BatchItem.id).where(BatchItem.round_id == key))
            )
            & (Job.kind != "create_document"),
        }
    elif scope == "item":
        conditions = {
            "item": BatchItem.id == key,
            "job": (Job.payload["item_id"].as_string() == key) & (Job.kind != "create_document"),
        }
        if row.round_id:
            return sources(s, p, "round", row.round_id)
    else:
        conditions = {
            "chat": AssistantConversation.id == key,
            "turn": AssistantTurn.conversation_id == key,
            "job": Job.kind.in_(("create_document", "assistant_message"))
            & (Job.payload["chat_id"].as_string() == key),
            "artifact": AssistantArtifactVersion.artifact_id.in_(
                select(AssistantArtifact.id).where(AssistantArtifact.conversation_id == key)
            ),
        }
    # Exact entity relations only. Never join by filename, customer name or a model's guessed ID.
    relations = [AuditLog.entity_id.in_(select(MODELS[k].id).where(c)) for k, c in conditions.items()]
    if scope == "round":
        relations.append(AuditLog.details["round_id"].as_string() == key)
    conditions["audit"] = or_(*relations)
    if scope != "chat":
        conditions["audit"] &= ~AuditLog.action.startswith("assistant.")
    return row, conditions


def projection(kind: str, condition: Any) -> Any:
    model = MODELS[kind]
    title = model.action if kind == "audit" else literal(LABELS[kind])
    actor = getattr(model, "actor", getattr(model, "author", getattr(model, "created_by", literal("system"))))
    return select(
        (literal(kind + ":") + model.id).label("key"),
        literal(kind).label("kind"),
        model.id.label("id"),
        model.created_at.label("at"),
        title.label("title"),
        actor.label("actor"),
    ).where(condition)


def cursor_decode(value: str) -> tuple[dt.datetime, str] | None:
    if not value:
        return None
    try:
        at, key = json.loads(base64.urlsafe_b64decode(value.encode()))
        when = dt.datetime.fromisoformat(at)
        if when.tzinfo is None or not isinstance(key, str):
            raise ValueError
        return when.astimezone(dt.UTC), key
    except Exception as exc:
        raise ValidationFailed("Invalid history cursor") from exc


def timeline(s: Session, p: Principal, scope: str, key: str, cursor: str, category: str) -> dict[str, Any]:
    root, conditions = sources(s, p, scope, key)
    if category and category not in {*MODELS, "activity"}:
        raise ValidationFailed("Unknown event type")
    queries = [projection(k, c) for k, c in conditions.items() if not category or category == k]
    previous = cursor_decode(cursor)
    rows: list[dict[str, Any]] = []
    if queries:
        union = union_all(*queries).subquery()
        q = select(union)
        if previous:
            at, after = previous
            q = q.where((union.c.at < at) | ((union.c.at == at) & (union.c.key < after)))
        rows = [
            dict(r) for r in s.execute(q.order_by(union.c.at.desc(), union.c.key.desc()).limit(51)).mappings()
        ]
    journal_available = True
    if not category or category == "activity":
        # Activity may live in a separate SQLite journal. Both streams use the same cursor order.
        job_ids = list(s.scalars(select(Job.id).where(conditions["job"])))
        try:
            e = activity.events
            q = select(e.c.id, e.c.job_id, e.c.at, e.c.action, e.c.state, e.c.role, e.c.attempt)
            q = q.where(e.c.job_id.in_(job_ids))
            if previous:
                at, after = previous
                event_key = literal("activity:") + cast(e.c.id, String)
                q = q.where((e.c.at < at.isoformat()) | ((e.c.at == at.isoformat()) & (event_key < after)))
            with activity.engine().connect() as conn:
                for event in conn.execute(
                    q.order_by(e.c.at.desc(), cast(e.c.id, String).desc()).limit(51)
                ).mappings():
                    rows.append(
                        {
                            "key": f"activity:{event['id']}",
                            "kind": "activity",
                            "id": str(event["id"]),
                            "at": dt.datetime.fromisoformat(event["at"]),
                            "title": event["action"],
                            "actor": event["role"],
                            "state": event["state"],
                            "attempt": event["attempt"],
                            "job_id": event["job_id"],
                        }
                    )
        except Exception:
            journal_available = False
    rows.sort(key=lambda r: (r["at"], r["key"]), reverse=True)
    page = rows[:50]
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps([page[-1]["at"].isoformat(), page[-1]["key"]]).encode()).decode()
        if len(rows) > 50
        else None
    )
    for r in page:
        if r["kind"] == "audit":
            r["action"] = r["title"]
            r["title"] = AUDIT_LABELS.get(r["title"], r["title"])
    return {
        "scope": scope,
        "id": key,
        "title": getattr(root, "label", None)
        or getattr(root, "title", None)
        or getattr(root, "file_name", None)
        or root.id,
        "status": getattr(root, "status", "conversation"),
        "events": page,
        "next_cursor": next_cursor,
        "journal_available": journal_available,
        "notice": "แสดงสิ่งที่ระบบบันทึกไว้จริง เรียงล่าสุดก่อน งานเก่าอาจไม่มีรายละเอียดทุกขั้น และสถานะในรายละเอียดเป็นค่าปัจจุบัน",
    }


def detail(
    s: Session, p: Principal, scope: str, key: str, kind: str, entity_id: str, offset: int
) -> dict[str, Any]:
    _, conditions = sources(s, p, scope, key)
    links = []
    if kind == "activity":
        e = activity.events
        try:
            number = int(entity_id)
        except ValueError as exc:
            raise NotFound("Event not found") from exc
        with activity.engine().connect() as conn:
            event = conn.execute(select(e).where(e.c.id == number)).mappings().first()
        if not event or not s.scalar(select(Job.id).where(Job.id == event["job_id"], conditions["job"])):
            raise NotFound("Event not found")
        data = {**event, "detail": json.loads(event["detail"])}
        title = event["action"]
    else:
        if kind not in conditions:
            raise NotFound("Event not found")
        model = MODELS[kind]
        row = s.scalar(select(model).where(model.id == entity_id, conditions[kind]))
        if row is None:
            raise NotFound("Event not found")
        title = LABELS.get(kind, getattr(row, "action", kind))
        if kind == "mapping":
            data = {
                **inspection.mapping_trace(s, row, offset=offset),
                "document": row.document,
                "questions": row.open_questions,
                "validation": row.validation,
            }
            links.append({"label": "เปิดการตรวจไฟล์", "url": f"/review?mapping={row.id}&round={row.round_id}"})
        elif kind == "job":
            data = {c.name: getattr(row, c.name) for c in model.__table__.columns if c.name != "payload"}
            if scope == "chat":
                if row.kind == "assistant_message":
                    from forma.db.models import AssistantRun

                    run = s.get(AssistantRun, row.payload.get("turn_id"))
                    if run:
                        data["recovery"] = {
                            "calls": run.calls,
                            "estimated_cost_usd": run.cost_usd,
                            "expires_at": run.expires_at,
                            "retry_at": run.state.get("retry_at"),
                            "last_failure": run.state.get("last_failure"),
                            "protocol": run.state.get("protocol"),
                            "steps": [
                                {
                                    "step": n,
                                    "name": v["name"],
                                    "attempts": v["attempts"],
                                    "complete": v.get("done", False),
                                }
                                for n, v in run.state.get("steps", {}).items()
                            ],
                        }
                spec = row.payload.get("snapshot", {})
                data["sources"] = spec.get("sources", [])
                data["template"] = spec.get("template")
                data["snapshot_id"] = spec.get("snapshot_id")
                links.append({"label": "เปิดบทสนทนา", "url": f"/assistant?chat={key}"})
            else:
                links.append({"label": "เปิดห้องทำงาน", "url": f"/activity?job={row.id}"})
        else:
            excluded = {"storage_key", "records_key", "owner_key", "access_key", "busy_until", "active_run"}
            data = {c.name: getattr(row, c.name) for c in model.__table__.columns if c.name not in excluded}
            if kind == "dataset":
                links.append({"label": "เปิดผลลัพธ์", "url": f"/datasets/{row.id}"})
            if kind == "knowledge":
                links.append(
                    {"label": "เปิดความรู้และเงื่อนไขการใช้", "url": f"/settings?tab=learning&knowledge={row.id}"}
                )
            if kind == "review" and row.mapping_version_id:
                links.append(
                    {
                        "label": "เปิดคำถามของไฟล์นี้",
                        "url": f"/review?mapping={row.mapping_version_id}&round={row.round_id}",
                    }
                )
            if kind == "export":
                links.append({"label": "เปิดผลลัพธ์และดาวน์โหลด", "url": f"/datasets/{row.dataset_version_id}"})
            if kind == "file":
                links.append({"label": "เปิดไฟล์ต้นทาง", "url": f"/rounds/{row.round_id}"})
            if kind == "artifact":
                links.append(
                    {"label": "เปิดผลงานและเวอร์ชัน", "url": f"/assistant?chat={key}&artifact={row.artifact_id}"}
                )
        if kind == "audit":
            title = AUDIT_LABELS.get(row.action, row.action)
    # Datetimes remain ISO strings, never replaced by opaque type names in the safe journal serializer.
    data = json.loads(
        json.dumps(data, default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v))
    )
    return {
        "key": f"{kind}:{entity_id}",
        "title": title,
        "kind": kind,
        "data": activity.safe(data),
        "links": links,
        "notice": "ข้อมูลยาวอาจย่อในมุมมองนี้ ใช้หน้าต้นทางหรือหน้าถัดไปสำหรับรายละเอียดเพิ่มเติม",
    }
