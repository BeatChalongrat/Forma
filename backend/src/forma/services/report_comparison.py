"""Compare explicit snapshots without adding versions or inventing missing values."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from forma.auth import Principal, ensure_customer
from forma.core.errors import ValidationFailed
from forma.services import datasets
from forma.services.assistant_reports import _grouped, column, report, text
from forma.services.context import ServiceContext
from forma.services.report_numbers import add, percentage


def compare(ctx: ServiceContext, session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    ids = a.get("dataset_ids", [])
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValidationFailed("Select two distinct dataset versions in before/after order")
    selected = [datasets.get_dataset(session, i) for i in ids]
    for d in selected:
        ensure_customer(p, d.customer_id)
    if selected[0].customer_id != selected[1].customer_id:
        raise ValidationFailed(
            "Compare versions of the same customer; cross-customer semantics need a separate confirmed contract"
        )
    parts = [_grouped(ctx, session, p, {**a, "dataset_id": i})["report"] for i in ids]
    first, second = [part["measurement"] for part in parts]
    if a["operation"] != "count" and any(part["coverage"]["missing_numeric"] for part in parts):
        raise ValidationFailed(
            "Some numeric values are missing or invalid. Inspect/filter them before comparing totals; unknown is not zero"
        )
    if first != second:
        raise ValidationFailed(
            "Contract, metric or currencies differ between snapshots; do not compare incompatible values"
        )
    if a["operation"] != "count" and not first["monetary"] and not a.get("unit"):
        raise ValidationFailed("Ask the user to confirm the business unit before comparing numeric snapshots")
    unit = (
        "records"
        if a["operation"] == "count"
        else (first["currencies"][0] if first["currencies"] else a["unit"])
    )
    if first["monetary"] and a.get("unit") and a["unit"] != unit:
        raise ValidationFailed("Requested unit does not match the saved currency")
    indexed = []
    for part in parts:
        index = {r["label"]: r for r in part["rows"]}
        if len(index) != len(part["rows"]):
            raise ValidationFailed("Ambiguous group labels; normalize grouping values before comparing")
        indexed.append(index)
    labels = sorted(set(indexed[0]) | set(indexed[1]))
    if len(labels) > 30:
        raise ValidationFailed("More than 30 groups; narrow the comparison. No partial totals returned")
    rows = []
    for label in labels:
        left, right = (idx.get(label, {}).get("value") for idx in indexed)
        delta = (
            add(Decimal(right), Decimal(left).copy_negate())
            if left is not None and right is not None
            else None
        )
        percent = percentage(delta, Decimal(left)) if delta is not None and Decimal(left) else None
        rows.append(
            {
                "label": label,
                "before": left,
                "after": right,
                "change": str(delta) if delta is not None else None,
                "percent": str(percent) if percent is not None else None,
            }
        )
    result = report(
        text("Snapshot comparison", "เปรียบเทียบข้อมูลก่อน–หลัง"),
        [
            column("label", "Group", "กลุ่ม", "text"),
            column("before", "Before", "ก่อน"),
            column("after", "After", "หลัง"),
            column("change", "Change", "เปลี่ยนแปลง"),
            column("percent", "Change (%)", "เปลี่ยนแปลง (%)"),
        ],
        rows,
        ["before", "after"],
        text(f"{ids[0]} → {ids[1]} · {unit}", f"{ids[0]} → {ids[1]} · หน่วย {unit}"),
        [
            text(
                "Missing groups/values are unknown, not zero. Percentage = change / absolute baseline × 100, rounded half-up to 2 decimals; undefined for zero. Versions are never added together.",
                "กลุ่มหรือค่าที่หายไปถือว่าไม่ทราบ ไม่ใช่ศูนย์ เปอร์เซ็นต์ = ผลต่าง / ค่าสัมบูรณ์ของฐาน × 100 ปัดครึ่งขึ้นสองตำแหน่ง ไม่คำนวณเมื่อฐานเป็นศูนย์ และไม่นำหลายเวอร์ชันมาบวกซ้ำ",
            ),
            *parts[0]["notes"],
            *parts[1]["notes"],
        ],
        [link for part in parts for link in part["links"]],
    )
    result["report"]["comparison"] = {
        "dataset_ids": ids,
        "unit": unit,
        "coverage": [part["coverage"] for part in parts],
        "operation": a["operation"],
    }
    return result
