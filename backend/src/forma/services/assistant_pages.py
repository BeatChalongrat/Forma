"""Read multiple pages in one bounded, read-only database snapshot."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import ValidationFailed
from forma.db.session import get_engine
from forma.jobs.execution import check
from forma.services.context import ServiceContext

SOURCES = [
    "review_queue",
    "customers",
    "search_imports",
    "customer_rules",
    "inspect_mapping",
    "dataset_history",
    "processing_jobs",
    "audit_history",
    "bulk_uploads",
    "work_rounds",
]
TOOL = {
    "name": "read_pages",
    "description": "Automatically follow pages in one read-only snapshot. Use when the user needs ALL details, "
    "not totals: review_summary/operations_summary/build_report compute totals over the FULL scope. "
    "Choose a listed source and its normal arguments. Max 1000 items per call, with explicit continuation. "
    "Context may show fewer details; never count a preview as the population. "
    "New calls are new snapshots; do not add their totals when data changes.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"enum": SOURCES},
            "arguments": {"type": "object"},
            "max_items": {"type": "integer", "minimum": 20, "maximum": 1000},
        },
        "required": ["source", "arguments"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}


def read(ctx: ServiceContext, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    from forma.services.workspace_assistant import run_tool

    source, arguments = a["source"], dict(a["arguments"])
    if source not in SOURCES:
        raise ValidationFailed("Select a paginated read-only source")
    start = arguments.get("offset", 0)
    offset: int | None = start
    maximum = a.get("max_items", 1000)
    result: dict[str, Any] = {}
    items: list[Any] = []
    pages = 0
    key = "items"
    engine = get_engine()
    # Each page sees the same database population, even if uploads/reviews happen concurrently.
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin(), Session(bind=connection) as snapshot:
            if engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN")
            while offset is not None and pages < 50 and len(items) < maximum:
                check()
                page = run_tool(ctx, snapshot, p, source, {**arguments, "offset": offset})
                key = next((k for k in ("items", "rules", "files") if isinstance(page.get(k), list)), "items")
                rows = page.get(key, [])
                if not pages:
                    result = {k: v for k, v in page.items() if k != key}
                pages += 1
                remaining = maximum - len(items)
                items.extend(rows[:remaining])
                continuation = page.get("next_offset")
                if len(rows) > remaining:
                    continuation = offset + remaining
                if continuation is None:
                    offset = None
                    break
                if not rows or continuation <= offset:
                    raise ValidationFailed("Pagination did not advance; no complete result was returned")
                offset = continuation
    result.update(
        {
            key: items,
            "source": source,
            "offset": start,
            "next_offset": offset,
            "pages_read": pages,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "scope": arguments,
            "stop_reason": "complete" if offset is None else "item_budget",
            "coverage": {
                "returned": len(items),
                "matched": result.get("total", result.get("total_rules")),
                "complete": start == 0 and offset is None,
                "snapshot": "single database read transaction",
            },
        }
    )
    return result
