"""Personal preferences and upload history. No pipeline or policy state is changed here."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from forma.api import serialize as ser
from forma.auth import Principal
from forma.core.errors import ValidationFailed
from forma.db.models import Batch, BatchItem, Customer, ImportRound, SourceFile
from forma.services.round_lifecycle import CLOSED


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(default="", max_length=80)
    avatar: str = Field(default="", max_length=140000)
    language: Literal["th", "en"] = "th"
    theme: Literal["light", "dark", "system"] = "light"
    timezone: str = "Asia/Bangkok"
    date_format: Literal["day-first", "iso"] = "day-first"
    memory_enabled: bool = True
    chat_search: bool = True
    memory_sensitive: bool = False

    @field_validator("avatar")
    @classmethod
    def valid_avatar(cls, value: str) -> str:
        if not value:
            return value
        if not value.startswith("data:image/jpeg;base64,"):
            raise ValueError("Profile photos must be a JPEG image")
        try:
            raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error) as e:
            raise ValueError("Invalid profile image") from e
        if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9") or len(raw) > 100000:
            raise ValueError("Invalid or oversized profile image")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError("Unknown time zone") from e
        return value


def history(
    session: Session,
    p: Principal,
    *,
    status: str = "",
    customer_id: str = "",
    search: str = "",
    start: date | None = None,
    end: date | None = None,
    timezone: str = "Asia/Bangkok",
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValidationFailed("Unknown time zone") from e
    if start and end and start > end:
        raise ValidationFailed("Start date must not be after end date")
    # Bulk files were received before they were assigned a customer/round. Use that original time.
    batch_at = (
        select(func.min(BatchItem.created_at)).where(BatchItem.round_id == ImportRound.id).scalar_subquery()
    )
    source_at = (
        select(func.min(SourceFile.created_at)).where(SourceFile.round_id == ImportRound.id).scalar_subquery()
    )
    uploaded = func.coalesce(batch_at, source_at)  # an empty round has not been uploaded
    q = select(ImportRound, uploaded.label("uploaded_at")).where(uploaded.is_not(None))
    if p.customers is not None:
        q = q.where(ImportRound.customer_id.in_(sorted(p.customers)))
    # Closed rounds (ADR-052) are history, not work: shown only when asked for.
    if status == "closed":
        q = q.where(ImportRound.status.in_(CLOSED))
    elif status:
        q = q.where(ImportRound.status == status)
    else:
        q = q.where(ImportRound.status.not_in(CLOSED))
    if customer_id:
        q = q.where(ImportRound.customer_id == customer_id)
    if start:
        q = q.where(uploaded >= datetime.combine(start, time.min, zone).astimezone(UTC))
    if end:
        q = q.where(uploaded < datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(UTC))
    if search.strip():
        # contains(autoescape) treats %, _ and / literally, unlike free-form LIKE patterns.
        word = search.strip()[:200]
        q = q.where(
            or_(
                ImportRound.label.icontains(word, autoescape=True),
                ImportRound.id.icontains(word, autoescape=True),
                ImportRound.id.in_(
                    select(SourceFile.round_id).where(
                        SourceFile.original_name.icontains(word, autoescape=True)
                    )
                ),
                ImportRound.id.in_(
                    select(BatchItem.round_id).join(Batch).where(Batch.label.icontains(word, autoescape=True))
                ),
                ImportRound.customer_id.in_(
                    select(Customer.id).where(
                        or_(
                            Customer.code.icontains(word, autoescape=True),
                            Customer.name.icontains(word, autoescape=True),
                        )
                    )
                ),
            )
        )
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = session.execute(
        q.order_by(uploaded.desc(), ImportRound.id.desc()).offset(offset).limit(limit)
    ).all()
    items = []
    for r, at in rows:
        item = ser.import_round(r, customer_obj=session.get(Customer, r.customer_id))
        item["uploaded_at"] = (at if at.tzinfo else at.replace(tzinfo=UTC)).isoformat()
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit, "timezone": timezone}
