"""Unified investigation center; every drilldown repeats the root ownership check."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from forma.auth import Principal, require_role
from forma.db.session import get_session
from forma.services import inspection_center as center

router = APIRouter(prefix="/api/inspection", tags=["inspection"])


@router.get("/items")
def items(
    kind: Literal["imports", "assistant"] = "imports",
    search: str = Query("", max_length=200),
    customer: str = Query("", max_length=200),
    batch: str = Query("", max_length=200),
    status: str = Query("", max_length=40),
    start: date | None = None,
    end: date | None = None,
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    return center.items(
        session,
        p,
        kind=kind,
        search=search,
        customer=customer,
        batch=batch,
        status=status,
        start=start,
        end=end,
        offset=offset,
    )


@router.get("/{scope}/{key}/events")
def timeline(
    scope: Literal["round", "item", "chat"],
    key: str,
    cursor: str = Query("", max_length=500),
    category: str = Query("", max_length=30),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    return center.timeline(session, p, scope, key, cursor, category)


@router.get("/{scope}/{key}/events/{kind}/{entity_id}")
def detail(
    scope: Literal["round", "item", "chat"],
    key: str,
    kind: str,
    entity_id: str,
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    p: Principal = Depends(require_role("importer")),
) -> dict[str, Any]:
    return center.detail(session, p, scope, key, kind, entity_id, offset)
