"""Actionable operational signals shared by the admin UI and scrape-time metrics."""

from __future__ import annotations

import datetime as dt
import shutil
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.db.models import Job
from forma.services.context import ServiceContext


def collect(
    session: Session,
    ctx: ServiceContext,
    queue: dict[str, Any],
    housekeeping: dict[str, Any],
    ready: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.UTC)
    alerts: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, action: str, value: float = 1) -> None:
        alerts.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "action": action,
                "value": value,
                "href": "/ops",
            }
        )

    if ready and ready.get("status") != "ok":
        add("dependencies", "critical", "มีบริการที่เชื่อมต่อไม่ได้", "ตรวจฐานข้อมูล Storage และคิวงาน")
    age = queue.get("oldest_queued_age_s") or 0
    if age > 900:
        add(
            "queue_age",
            "warning",
            "มีงานรอประมวลผลเกิน 15 นาที",
            "ตรวจ worker และข้อจำกัดของผู้ให้บริการก่อนเพิ่มจำนวนงานพร้อมกัน",
            age,
        )
    expired = (
        session.scalar(
            select(func.count()).select_from(Job).where(Job.status == "running", Job.lease_expires_at < now)
        )
        or 0
    )
    if expired:
        add(
            "expired_leases",
            "critical",
            f"มี {expired} งานที่ worker ขาดการติดต่อ",
            "ตรวจ worker และการกู้คืนงาน อย่าส่งไฟล์เดิมซ้ำ",
            expired,
        )
    failed = (
        session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "failed", Job.finished_at >= now - dt.timedelta(minutes=15))
        )
        or 0
    )
    if failed:
        add(
            "recent_failures",
            "warning",
            f"มี {failed} งานไม่สำเร็จใน 15 นาทีล่าสุด",
            "เปิดรายละเอียดข้อผิดพลาด แก้สาเหตุแล้วลองงานเดิมอีกครั้ง",
            failed,
        )
    backup = housekeeping["backup"]
    if ctx.settings.is_prod and (backup.get("age_hours") is None or backup["age_hours"] > 26):
        add(
            "backup_overdue",
            "warning",
            "ยังไม่มี backup ที่สำเร็จใน 26 ชั่วโมงล่าสุด",
            "ตรวจงานสำรองข้อมูลและพื้นที่ปลายทาง แล้วทดสอบกู้คืนในชุดแยก",
        )
    if backup.get("last_at") and backup.get("files_included") is not True:
        add(
            "backup_without_files",
            "warning",
            "backup ล่าสุดไม่ได้รวมไฟล์ต้นทางและผลลัพธ์",
            "ตรวจว่ามีสำเนา Storage ที่กู้คืนได้ หรือสำรองพร้อมไฟล์",
        )
    if housekeeping["retention"].get("last_errors"):
        add(
            "retention_errors",
            "warning",
            "การจัดเก็บข้อมูลตามอายุครั้งล่าสุดมีข้อผิดพลาด",
            "ตรวจ retention log ก่อนสั่งทำซ้ำ",
        )
    usage = shutil.disk_usage(ctx.settings.data_path)
    if usage.free < max(1024**3, usage.total * 0.1):
        add(
            "local_disk_low",
            "critical",
            "พื้นที่ดิสก์ที่แอปใช้สำหรับข้อมูลชั่วคราวเหลือน้อย",
            "เพิ่มพื้นที่หรือตรวจ retention; ค่านี้ไม่ได้วัดพื้นที่ MinIO ที่อยู่คนละเครื่อง",
            usage.free,
        )
    return alerts
