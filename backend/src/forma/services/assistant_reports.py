"""Bounded, server-computed chat reports. Models select reports; they never supply chart values."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal, ensure_customer
from forma.core.errors import Forbidden, NotFound, ValidationFailed
from forma.db.models import Batch, Customer, ImportRound, MappingVersion, SourceFile
from forma.services import batches, datasets
from forma.services.context import ServiceContext
from forma.services.report_numbers import add


def text(en: str, th: str) -> dict[str, str]:
    return {"en": en, "th": th}


def column(key: str, en: str, th: str, kind: str = "number") -> dict[str, Any]:
    return {"key": key, "label": text(en, th), "type": kind}


def report(
    title: dict[str, str],
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    series: list[str],
    scope: dict[str, str],
    notes: list[dict[str, str]],
    links: list[dict[str, str]],
    *,
    line: bool = False,
    complete: bool = True,
) -> dict[str, Any]:
    views = ["table"]
    if (
        rows
        and series
        and all(r.get(k) is None or abs(Decimal(str(r[k]))) <= Decimal("1e15") for r in rows for k in series)
    ):
        views += ["bar"]
        if line:
            views += ["line"]
    out = {
        "schema_version": 1,
        "title": title,
        "columns": columns,
        "rows": rows,
        "series": series,
        "scope": scope,
        "notes": notes,
        "links": links,
        "views": views,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "complete": complete,
    }
    out["id"] = hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return {"report": out, "links": links}


def _work_rounds(session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    if p.customers is not None:
        raise Forbidden("Work-round reports require unrestricted customer access")
    ids = a.get("batch_ids", [])
    if not ids or len(ids) > 8 or len(set(ids)) != len(ids):
        raise ValidationFailed(
            "Choose 1 to 8 distinct work rounds from work_rounds; do not assume the latest"
        )
    selected = []
    for batch_id in ids:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise NotFound("Work round not found")
        selected.append(batches.batch_view(session, batch))
    links = [{"label": b["label"] or "Work round", "url": "/uploads/" + b["id"]} for b in selected]
    if a["report"] == "work_round_files":
        if len(selected) != 1:
            raise ValidationFailed("Select one work round for the file list")
        b = selected[0]
        states = a.get("states", [])
        files = [f for f in b["items"] if not states or f["state"] in states]
        rows = [
            {
                "label": f["file_name"],
                "customer": (f["customer"] or {}).get("code"),
                "status": f["state"],
                "source_url": "/review?mapping=" + f["mapping_version_id"]
                if f.get("mapping_version_id")
                else "/imports?round=" + f["round_id"]
                if f["round_id"]
                else links[0]["url"],
            }
            for f in files[:50]
        ]
        return report(
            text("Files in this work round", "ไฟล์ในรอบงานนี้"),
            [
                column("label", "File", "ไฟล์", "text"),
                column("customer", "Customer", "ลูกค้า", "text"),
                column("status", "Current status", "สถานะปัจจุบัน", "status"),
            ],
            rows,
            [],
            text(
                f"{b['label'] or 'Work round'} · showing {len(rows)} of {len(files)} matching files",
                f"{b['label'] or 'รอบงาน'} · แสดง {len(rows)} จาก {len(files)} ไฟล์ที่ตรงเงื่อนไข",
            ),
            [
                text(
                    "Saved status snapshot. Open a file to see its latest state.",
                    "สถานะ ณ เวลาที่ตอบ เปิดไฟล์เพื่อดูสถานะล่าสุด",
                )
            ],
            links,
            complete=len(files) <= 50,
        )
    rows = []
    for b in selected:
        counts = b["counts"]
        rows.append(
            {
                "label": b["label"] or b["work_date"] or "Untitled round",
                "work_date": b["work_date"],
                "expected": b["expected_files"],
                "received": b["total"],
                "ready": counts.get("ready", 0),
                "review": sum(
                    counts.get(k, 0) for k in ("needs_customer", "needs_review", "needs_data_check")
                ),
                "processing": counts.get("processing", 0),
                "failed": counts.get("failed", 0),
                "source_url": "/uploads/" + b["id"],
            }
        )
    return report(
        text("Work-round overview", "ภาพรวมรอบงาน"),
        [
            column("label", "Work round", "รอบงาน", "text"),
            column("expected", "Expected", "คาดว่าจะได้รับ"),
            column("received", "Received", "รับแล้ว"),
            column("ready", "Ready to download", "พร้อมดาวน์โหลด"),
            column("review", "Needs attention", "ต้องตรวจ"),
            column("processing", "Processing", "กำลังประมวลผล"),
            column("failed", "Failed", "ผิดพลาด"),
        ],
        rows,
        ["received", "ready", "review", "failed"],
        text("Files · selected work rounds", "หน่วย: ไฟล์ · รอบงานที่เลือก"),
        [
            text(
                "Ready, attention, processing and failed are subsets of received; do not add them to received.",
                "พร้อมดาวน์โหลด ต้องตรวจ กำลังประมวลผล และผิดพลาด เป็นส่วนหนึ่งของไฟล์ที่รับแล้ว",
            ),
            text(
                "An unset expected count is unknown, not zero. Work dates are labels, not upload timestamps.",
                "จำนวนที่คาดว่าจะได้รับซึ่งยังไม่ระบุ หมายถึงไม่ทราบ ไม่ใช่ศูนย์ วันที่รอบงานเป็นป้ายกำกับ",
            ),
        ],
        links,
    )


def _daily(session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    try:
        start, end = dt.date.fromisoformat(a["start"]), dt.date.fromisoformat(a["end"])
        zone = ZoneInfo(a["timezone"])
    except (KeyError, ValueError, ZoneInfoNotFoundError) as e:
        raise ValidationFailed(
            "Specify start/end dates and timezone; ask the user if the period is unclear"
        ) from e
    days = (end - start).days + 1
    if not 1 <= days <= 92:
        raise ValidationFailed("Choose a date range of 1 to 92 days")
    lower = dt.datetime.combine(start, dt.time.min, zone).astimezone(dt.UTC)
    upper = dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, zone).astimezone(dt.UTC)
    q = (
        select(SourceFile, ImportRound)
        .join(ImportRound, ImportRound.id == SourceFile.round_id)
        .where(
            SourceFile.created_at >= lower, SourceFile.created_at < upper, SourceFile.duplicate_of.is_(None)
        )
    )
    if p.customers is not None:
        q = q.where(SourceFile.customer_id.in_(sorted(p.customers)))
    customer_label = ""
    if a.get("customer_id"):
        ensure_customer(p, a["customer_id"])
        customer = session.get(Customer, a["customer_id"])
        if customer is None:
            raise NotFound("Customer not found")
        customer_label = f" · {customer.code} — {customer.name}"
        q = q.where(SourceFile.customer_id == a["customer_id"])
    rows: dict[str, dict[str, Any]] = {
        (start + dt.timedelta(days=i)).isoformat(): {
            "label": (start + dt.timedelta(days=i)).isoformat(),
            "received": 0,
            "ready": 0,
            "review": 0,
            "failed": 0,
        }
        for i in range(days)
    }
    for f, r in session.execute(q):
        created = f.created_at.replace(tzinfo=dt.UTC) if f.created_at.tzinfo is None else f.created_at
        row = rows[created.astimezone(zone).date().isoformat()]
        row["received"] += 1
        if f.status == "error" or r.status == "failed":
            row["failed"] += 1
        elif r.status == "published" and f.status != "ignored":
            row["ready"] += 1
        elif r.status in ("review", "blocked", "rejected", "transformed"):
            row["review"] += 1
    return report(
        text("File status by upload date", "สถานะไฟล์แยกตามวันที่อัปโหลด"),
        [
            column("label", "Upload date", "วันที่อัปโหลด", "date"),
            column("received", "Received", "รับแล้ว"),
            column("ready", "In published rounds", "อยู่ในรอบที่เผยแพร่แล้ว"),
            column("review", "Needs attention", "ต้องตรวจ"),
            column("failed", "Failed", "ผิดพลาด"),
        ],
        list(rows.values()),
        ["received", "ready", "review", "failed"],
        text(
            f"Files · {start} – {end} · {zone.key}{customer_label}",
            f"หน่วย: ไฟล์ · {start} – {end} · {zone.key}{customer_label}",
        ),
        [
            text(
                "Current status grouped by upload date, not completion date. Published refers to the containing import round.",
                "แสดงสถานะปัจจุบันตามวันที่อัปโหลด ไม่ใช่จำนวนที่ทำเสร็จในวันนั้น สถานะเผยแพร่อ้างอิงรอบนำเข้า",
            ),
            text(
                "Only files assigned to accessible customers; duplicate copies and unassigned bulk files are excluded.",
                "เฉพาะไฟล์ที่ผูกกับลูกค้าที่เข้าถึงได้ ไม่รวมสำเนาซ้ำและไฟล์อัปโหลดรวมที่ยังไม่ระบุลูกค้า",
            ),
        ],
        [{"label": "Upload history", "url": "/imports"}],
        line=True,
    )


def _at(record: Any, pointer: str) -> Any:
    for key in pointer.strip("/").split("/"):
        record = record.get(key.replace("~1", "/").replace("~0", "~")) if isinstance(record, dict) else None
    return record


def _grouped(ctx: ServiceContext, session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    if not a.get("dataset_id") or not a.get("group_by") or not a.get("operation"):
        raise ValidationFailed("Choose one dataset version, a grouping field and an operation")
    d = datasets.get_dataset(session, a["dataset_id"])
    ensure_customer(p, d.customer_id)
    m = session.get(MappingVersion, d.mapping_version_id)
    if m is None:
        raise NotFound("Mapping not found")
    schema = ctx.registry.get_ref(m.target_schema)
    fields = {
        f.pointer: f
        for f in schema.leaf_fields()
        if f.in_collection is None and not set(f.json_type) & {"array", "object"}
    }
    group, measure, operation = a["group_by"], str(a.get("field", "")), a["operation"]
    if group not in fields or operation not in ("count", "sum", "average", "min", "max"):
        raise ValidationFailed(
            "Choose a scalar field from this dataset's target format; collection grouping is not supported"
        )
    if operation != "count" and (
        measure not in fields or not set(fields[measure].json_type) & {"number", "integer"}
    ):
        raise ValidationFailed("Choose a numeric scalar measure from this dataset's target format")
    filters = a.get("filters", [])
    if any(f["field"] not in fields for f in filters):
        raise ValidationFailed("Filters must use scalar fields from this dataset's target format")
    currency = schema.semantic_field("currency")
    monetary = operation != "count" and (fields[measure].semantic or "") in (
        "price.amount",
        "promo.price",
        "discount.amount",
    )
    if monetary and (currency is None or currency.in_collection is not None):
        raise ValidationFailed(
            "Confirm a scalar currency field in the target format before aggregating money"
        )
    currencies: set[str] = set()
    grouped: dict[str, dict[str, Any]] = {}
    scanned = matched = skipped = 0
    for record in datasets.iter_records(ctx.records_path(d)):
        from forma.jobs.execution import check

        check()
        scanned += 1
        if any(_at(record, f["field"]) != f["value"] for f in filters):
            continue
        matched += 1
        if monetary and currency:
            currencies.add(str(_at(record, currency.pointer) or "unknown"))
        raw = _at(record, group)
        if isinstance(raw, (dict, list)):
            raise ValidationFailed("Grouping field contains non-scalar values")
        label = "(not specified / ไม่ระบุ)" if raw is None else str(raw)
        if len(label) > 160:
            raise ValidationFailed("Grouping labels are too long; choose a shorter grouping field")
        key = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        g = grouped.setdefault(
            key, {"label": label, "count": 0, "n": 0, "sum": Decimal(0), "min": None, "max": None}
        )
        if len(grouped) > 30:
            raise ValidationFailed(
                "More than 30 groups; narrow the filters or choose a broader grouping. No partial totals were returned"
            )
        g["count"] += 1
        if operation == "count":
            continue
        value = _at(record, measure)
        try:
            if value is None or isinstance(value, bool):
                raise InvalidOperation
            number = Decimal(str(value))
            if not number.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            skipped += 1
            continue
        g["n"] += 1
        g["sum"] = add(g["sum"], number)
        g["min"] = number if g["min"] is None else min(g["min"], number)
        g["max"] = number if g["max"] is None else max(g["max"], number)
    if len(currencies) > 1 or "unknown" in currencies:
        raise ValidationFailed(
            "Mixed or missing currencies. Filter to one confirmed currency before aggregating money"
        )
    rows = []
    for g in sorted(grouped.values(), key=lambda g: g["label"]):
        value = (
            g["count"]
            if operation == "count"
            else None
            if not g["n"]
            else (g["sum"] / g["n"] if operation == "average" else g[operation])
        )
        rows.append(
            {
                "label": g["label"],
                "value": str(value) if value is not None else None,
                "records": g["count"],
                "numeric": g["n"] if operation != "count" else None,
            }
        )
    label = fields[group].name
    name = "Records" if operation == "count" else f"{operation} · {fields[measure].name}"
    operation_th = {
        "count": "จำนวนรายการ",
        "sum": "ผลรวม",
        "average": "ค่าเฉลี่ย",
        "min": "ค่าต่ำสุด",
        "max": "ค่าสูงสุด",
    }
    name_th = (
        operation_th[operation]
        if operation == "count"
        else f"{operation_th[operation]} · {fields[measure].name}"
    )
    unit = (
        "records / รายการ"
        if operation == "count"
        else next(iter(currencies), "as defined by the field / ตามนิยามฟิลด์")
    )
    notes = [
        text(
            f"Scanned {scanned} records; {matched} matched; {skipped} missing/non-numeric measure values excluded. Null is not zero.",
            f"อ่านครบ {scanned} รายการ ตรงเงื่อนไข {matched} รายการ ไม่นับค่าที่ว่างหรือไม่ใช่ตัวเลข {skipped} ค่า ค่าว่างไม่ใช่ศูนย์",
        ),
        text(
            "One saved dataset version only; versions are not added together. View source to inspect the contract and units.",
            "ใช้ข้อมูลเวอร์ชันเดียว ไม่รวมหลายเวอร์ชันซ้ำกัน เปิดต้นทางเพื่อตรวจนิยามและหน่วย",
        ),
    ]
    if filters:
        shown = "; ".join(f"{fields[f['field']].name} = {f['value']}" for f in filters)
        notes.append(text("Filters: " + shown, "ตัวกรอง: " + shown))
    result = report(
        text(f"{name} by {label}", f"{name_th} แยกตาม {label}"),
        [
            column("label", label, label, "text"),
            column("value", name, name_th),
            column("records", "Matching records", "รายการที่ตรงเงื่อนไข"),
            *(
                [column("numeric", "Numeric values used", "ค่าตัวเลขที่นำมาคำนวณ")]
                if operation != "count"
                else []
            ),
        ],
        rows,
        ["value"],
        text(f"Dataset v{d.version} · {unit}", f"ข้อมูลเวอร์ชัน {d.version} · หน่วย: {unit}"),
        notes,
        [{"label": f"Dataset v{d.version}", "url": "/datasets/" + d.id}],
    )
    result["report"]["measurement"] = {
        "operation": operation,
        "field": measure,
        "currencies": sorted(currencies),
        "monetary": monetary,
        "contract": m.target_schema,
    }
    result["report"]["coverage"] = {
        "scanned": scanned,
        "matched": matched,
        "missing_numeric": skipped,
        "complete": True,
    }
    return result


def build_report(ctx: ServiceContext, session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    from forma.services import review_analytics

    analytics = {"review_issues", "review_statuses", "import_statuses", "processing_statuses"}
    allowed = {
        "work_rounds": {"batch_ids"},
        "work_round_files": {"batch_ids", "states"},
        "uploads_by_day": {"start", "end", "timezone", "customer_id"},
        "dataset_grouped": {"dataset_id", "group_by", "field", "operation", "filters"},
        "dataset_comparison": {"dataset_ids", "group_by", "field", "operation", "filters", "unit"},
        **{
            name: {"customer_id", "round_id", "batch_id", "status", "start", "end", "timezone"}
            for name in analytics
        },
    }
    if set(a) - {"report"} - allowed.get(a["report"], set()):
        raise ValidationFailed(
            "Some filters do not apply to this report; choose the correct report rather than ignoring filters"
        )
    if a["report"] in analytics:
        filters = {k: v for k, v in a.items() if k != "report"}
        review = a["report"].startswith("review_")
        summary = (
            review_analytics.summary(session, p, filters)
            if review
            else review_analytics.operations(
                session, p, {**filters, "resource": "imports" if a["report"] == "import_statuses" else "jobs"}
            )
        )
        rows = (
            [{"label": c["label"], "value": c["count"]} for c in summary["categories"]]
            if a["report"] == "review_issues"
            else [{"label": k, "value": v} for k, v in sorted(summary["statuses"].items())]
        )
        title = {
            "review_issues": "งานตรวจแยกตามประเภทปัญหา",
            "review_statuses": "สถานะงานตรวจ",
            "import_statuses": "สถานะชุดนำเข้า",
            "processing_statuses": "สถานะงานประมวลผล",
        }[a["report"]]
        unit = "งานตรวจ" if review else "ชุดนำเข้า" if a["report"] == "import_statuses" else "งานประมวลผล"
        notes = [text(n, n) for n in summary.get("notes", [])]
        status_labels = {
            "open": "รอตรวจ",
            "waiting": "รอข้อมูล",
            "approved": "อนุมัติแล้ว",
            "rejected": "ไม่อนุมัติ",
            "dismissed": "ปิดรายการ",
            "all": "ทุกสถานะ",
        }
        selected_status = summary["scope"].get("status", "all")
        scope_note = "สถานะ: " + status_labels.get(selected_status, selected_status)
        if filters.get("customer_id"):
            customer = session.get(Customer, filters["customer_id"])
            scope_note += " · ลูกค้า: " + (customer.code if customer else "ไม่พบ")
        if filters.get("round_id"):
            scope_note += " · เฉพาะชุดนำเข้าที่เลือกและมีสิทธิ์เข้าถึง"
        if filters.get("batch_id"):
            scope_note += " · เฉพาะรอบงานที่เลือกและมีสิทธิ์เข้าถึง"
        if filters.get("start"):
            scope_note += f" · วันที่สร้าง: {filters['start']} – {filters['end']} ({filters['timezone']})"
        else:
            scope_note += " · ทุกวันที่สร้างในข้อมูลที่มีสิทธิ์เข้าถึง"
        notes.append(text(scope_note, scope_note))
        notes.append(
            text(
                "Counts cover every matching record in the selected scope.",
                "นับครบทุกระเบียนที่ตรงเงื่อนไข ณ เวลาสรุป ไม่ได้นับจากตัวอย่างหน้าแรก",
            )
        )
        result = report(
            text(a["report"].replace("_", " ").title(), title),
            [column("label", "Category", "หมวด", "text"), column("value", "Count", "จำนวน")],
            rows,
            ["value"],
            text(f"{summary['total']} {summary['unit']}", f"ตรวจครบ {summary['total']} {unit}"),
            notes,
            summary["links"],
        )
        result["report"]["coverage"] = summary["coverage"]
        result["report"]["filters"] = summary["scope"]
        result["report"]["categories_overlap"] = a["report"] == "review_issues"
    elif a["report"] in ("work_rounds", "work_round_files"):
        result = _work_rounds(session, p, a)
    elif a["report"] == "uploads_by_day":
        result = _daily(session, p, a)
    elif a["report"] == "dataset_comparison":
        from forma.services.report_comparison import compare

        result = compare(ctx, session, p, a)
    else:
        result = _grouped(ctx, session, p, a)
    result["report"]["query"] = dict(a)
    return result
