"""On-demand evidence for the assistant. No model-generated SQL, code, or writes."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from forma.api import serialize as ser
from forma.auth import Principal, ensure_customer
from forma.core.errors import Forbidden, NotFound, ValidationFailed
from forma.db.models import (
    AuditLog,
    BatchItem,
    ChangeSet,
    Customer,
    DatasetVersion,
    ExportArtifact,
    ImportRound,
    Job,
    MappingVersion,
    Override,
    ReviewTask,
    SourceFile,
)
from forma.ingestion.readers import read_meta, read_raw
from forma.services import config
from forma.services import datasets as ds
from forma.services.context import ServiceContext

TEXT = {"type": "string", "maxLength": 200}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 10000000}
SCOPE = {
    "customer_id": TEXT,
    "round_id": TEXT,
    "batch_id": TEXT,
    "status": TEXT,
    "start": TEXT,
    "end": TEXT,
    "timezone": TEXT,
}


def tool(
    name: str, description: str, props: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    }


EXTRA_TOOLS = [
    tool(
        "review_summary",
        "Exact full-scope review counts and overlapping saved-reason categories. "
        "Default status open; use all for all statuses. Counts REVIEW TASKS, not files or questions. "
        "Unknown reasons remain unclassified. Optional dates filter task creation, not upload/completion. "
        "Use this instead of counting review_queue pages. Read coverage and overlap notes.",
        SCOPE,
    ),
    tool(
        "operations_summary",
        "Exact full-scope counts by status for imports or import processing jobs. "
        "Never includes private chat/report jobs. Dates filter creation of the selected resource. "
        "Counts import rounds or jobs, NOT Excel files. Use build_report for charts.",
        {**SCOPE, "resource": {"enum": ["imports", "jobs"]}},
    ),
    tool(
        "build_report",
        "Build an exact server-computed table/chart snapshot, never model-generated values. "
        "review_issues counts ALL review tasks by overlapping saved-reason categories (unknown reasons stay unclassified); "
        "review_statuses counts review status; both default to open, status=all includes other states. "
        "import_statuses counts import rounds, processing_statuses counts import jobs (never private chat jobs). "
        "These four support customer_id/round_id/batch_id/status and optional start/end/timezone for CREATION dates. "
        "work_rounds compares 1-8 explicit batch_ids (discover with work_rounds); work_round_files lists up to 50 files of ONE batch, optional states filter. "
        "uploads_by_day requires explicit start/end YYYY-MM-DD and timezone (max 92 days); CURRENT status grouped by upload date, NOT completions by date, excludes unassigned/duplicate files. "
        "dataset_grouped scans ONE dataset version; requires dataset_id, scalar group_by pointer, operation; numeric field for non-count operations. "
        "dataset_comparison compares exactly two explicit dataset_ids in before/after order; same customer and contract, never sums versions. Non-monetary numeric comparison requires a confirmed unit. "
        "Inspect target_formats first. Optional exact-match filters; no collection cross-products, no mixed currencies, max 30 groups. "
        "Ask for missing scope, period, metric or business units; never silently pick latest rounds or add dataset versions. "
        "Select the returned report via visualizations in the final reply; values, labels, sources and permitted views are server-owned.",
        {
            "report": {
                "enum": [
                    "work_rounds",
                    "work_round_files",
                    "uploads_by_day",
                    "dataset_grouped",
                    "dataset_comparison",
                    "review_issues",
                    "review_statuses",
                    "import_statuses",
                    "processing_statuses",
                ]
            },
            "status": TEXT,
            "round_id": TEXT,
            "batch_id": TEXT,
            "batch_ids": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": TEXT},
            "states": {
                "type": "array",
                "uniqueItems": True,
                "items": {
                    "enum": [
                        "ready",
                        "needs_customer",
                        "needs_review",
                        "needs_data_check",
                        "processing",
                        "failed",
                    ]
                },
            },
            "start": TEXT,
            "end": TEXT,
            "timezone": TEXT,
            "customer_id": TEXT,
            "dataset_id": TEXT,
            "dataset_ids": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": True,
                "items": TEXT,
            },
            "unit": {"type": "string", "minLength": 1, "maxLength": 80},
            "group_by": TEXT,
            "field": TEXT,
            "operation": {"enum": ["count", "sum", "average", "min", "max"]},
            "filters": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {"field": TEXT, "value": {"type": ["string", "number", "boolean", "null"]}},
                    "required": ["field", "value"],
                    "additionalProperties": False,
                },
            },
        },
        ["report"],
    ),
    tool(
        "inspect_review",
        "Explain one exact review version: questions, actual source evidence, changes and checks. "
        "Includes bounded examples, NOT a complete dataset. Changes are paginated (10 per page). "
        "Optional field returns its cell evidence and change details; inspect_mapping reads full rules.",
        {"mapping_id": TEXT, "field": TEXT, "offset": OFFSET},
        ["mapping_id"],
    ),
    tool(
        "customers",
        "Find customers by code/name, including notes, ERP and default target format. Paginated.",
        {"search": TEXT, "offset": OFFSET},
    ),
    tool(
        "customer_rules",
        "List a customer's mapping versions or persistent override rules, including history.",
        {"customer_id": TEXT, "kind": {"enum": ["mappings", "overrides"]}, "offset": OFFSET},
        ["customer_id", "kind"],
    ),
    tool(
        "inspect_mapping",
        "Read mapping rules, joins/sources, validation and open questions. Rules are paginated.",
        {"mapping_id": TEXT, "offset": OFFSET},
        ["mapping_id"],
    ),
    tool(
        "read_source",
        "Read actual source cells (not profile samples). Lists sheets; search scans the whole selected sheet. Row/column offsets are zero-based, displayed row numbers are one-based. Returns 20 rows x 20 columns.",
        {"file_id": TEXT, "sheet": TEXT, "search": TEXT, "offset": OFFSET, "column_offset": OFFSET},
        ["file_id"],
    ),
    tool(
        "query_records",
        "Search ALL records of one dataset and compute exact numeric aggregates. First inspect target_formats to obtain the field JSON pointer. Wildcards traverse collections; count is matching records, numeric aggregates count field values. Never estimates from samples.",
        {
            "dataset_id": TEXT,
            "search": TEXT,
            "field": TEXT,
            "offset": OFFSET,
            "operation": {"enum": ["count", "sum", "average", "min", "max"]},
        },
        ["dataset_id"],
    ),
    tool(
        "dataset_history",
        "Paginated dataset versions, changes or exports for an import round. Change details include operations and impact.",
        {"round_id": TEXT, "kind": {"enum": ["versions", "changes", "exports"]}, "offset": OFFSET},
        ["round_id", "kind"],
    ),
    tool(
        "review_queue",
        "Prioritized page of questions/issues with a full-scope overview on the first page. Defaults to open; all includes all statuses. Includes reasons and IDs for deeper inspection. Dates filter creation of review tasks.",
        {**SCOPE, "offset": OFFSET},
    ),
    tool(
        "bulk_uploads",
        "Find bulk-upload files, including files not yet assigned to a customer. Requires unrestricted customer access.",
        {"search": TEXT, "status": TEXT, "offset": OFFSET},
    ),
    tool(
        "work_rounds",
        "Read work-round dashboards: expected files, received files, missing files and processing counts. Dates are operator labels, not upload timestamps. Requires unrestricted customer access. Paginated; inspect batch_id for one round's file page.",
        {"search": TEXT, "batch_id": TEXT, "offset": OFFSET},
    ),
    tool(
        "processing_jobs",
        "Read processing state, progress and attempts for one visible import. No raw worker errors or internal payloads.",
        {"round_id": TEXT, "offset": OFFSET},
        ["round_id"],
    ),
    tool(
        "runtime_configuration",
        "Read safe AI connection status and, for administrators, current approval policy. Never exposes keys or credentials.",
        {},
    ),
    tool(
        "audit_history",
        "Admin-only change history metadata (who, what, when). Requires unrestricted customer access; credential payloads are excluded.",
        {"entity_id": TEXT, "action": TEXT, "offset": OFFSET},
    ),
]


def scoped(p: Principal, query: Any, column: Any) -> Any:
    return query if p.customers is None else query.where(column.in_(sorted(p.customers)))


def page(session: Session, query: Any, offset: int, serialize: Any) -> dict[str, Any]:
    total = session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    items = [serialize(x) for x in session.scalars(query.offset(offset).limit(20)).all()]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": 20,
        "next_offset": offset + 20 if offset + 20 < total else None,
    }


def attrs(row: Any, names: str) -> dict[str, Any]:
    return {key: getattr(row, key) for key in names.split()}


def connection_status(ctx: ServiceContext, session: Session) -> dict[str, Any]:
    view = config.ai_view(session, ctx.settings)
    host = urlparse(view.get("base_url") or "").hostname
    provider = "DeepSeek" if host == "api.deepseek.com" else view.get("active_provider", "mock")
    return {
        "enabled": not ctx.llm.is_mock,
        "provider": provider,
        "model": ctx.llm.model,
        "tools": [t["name"] for t in EXTRA_TOOLS],
    }


def values_at(value: Any, parts: list[str]) -> list[Any]:
    if not parts:
        return [value]
    head, *tail = parts
    if head == "*" and isinstance(value, list):
        return [item for child in value for item in values_at(child, tail)]
    if isinstance(value, dict) and head in value:
        return values_at(value[head], tail)
    return []


def query_records(ctx: ServiceContext, session: Session, p: Principal, a: dict[str, Any]) -> dict[str, Any]:
    dataset = ds.get_dataset(session, a["dataset_id"])
    ensure_customer(p, dataset.customer_id)
    operation = a.get("operation", "count")
    pointer = a.get("field", "")
    if operation != "count":
        mapping = session.get(MappingVersion, dataset.mapping_version_id)
        if mapping is None:
            raise NotFound("Mapping not found")
        fields = {f.pointer for f in ctx.registry.get_ref(mapping.target_schema).leaf_fields()}
        if pointer not in fields:
            raise ValidationFailed("Select a leaf field pointer from target_formats")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer.lstrip("/").split("/")]
    offset, search = a.get("offset", 0), a.get("search", "").casefold()
    scanned = matched = numeric = skipped = 0
    total = Decimal(0)
    low: Decimal | None = None
    high: Decimal | None = None
    rows = []
    for record in ds.iter_records(ctx.records_path(dataset)):
        from forma.jobs.execution import check

        check()
        scanned += 1
        if search not in json.dumps(record, ensure_ascii=False, default=str).casefold():
            continue
        if offset <= matched < offset + 20:
            rows.append(record)
        matched += 1
        if operation == "count":
            continue
        for raw in values_at(record, parts) or [None]:
            try:
                if raw is None or isinstance(raw, bool):
                    raise InvalidOperation
                number = Decimal(str(raw))
                if not number.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                skipped += 1
                continue
            numeric += 1
            total += number
            low = number if low is None else min(low, number)
            high = number if high is None else max(high, number)
    result: Any = matched
    if operation != "count":
        result = {
            "sum": total if numeric else None,
            "average": total / numeric if numeric else None,
            "min": low,
            "max": high,
        }[operation]
        result = str(result) if result is not None else None
    return {
        "dataset_id": dataset.id,
        "operation": operation,
        "field": pointer or None,
        "value": result,
        "scanned_records": scanned,
        "matched_records": matched,
        "numeric_values": numeric,
        "missing_or_non_numeric_values": skipped,
        "complete_scan": True,
        "scope": "Only this immutable dataset version; collection values are counted individually.",
        "records": rows,
        "offset": offset,
        "limit": 20,
        "next_offset": offset + 20 if offset + 20 < matched else None,
        "links": [{"label": f"Dataset v{dataset.version}", "url": "/datasets/" + dataset.id}],
    }


def run_extra(
    ctx: ServiceContext, session: Session, p: Principal, name: str, a: dict[str, Any]
) -> dict[str, Any]:
    offset = a.get("offset", 0)
    q: Any
    result: dict[str, Any]
    if name in {"review_summary", "operations_summary"}:
        from forma.services import review_analytics

        return (review_analytics.summary if name == "review_summary" else review_analytics.operations)(
            session, p, a
        )
    if name == "build_report":
        from forma.services.assistant_reports import build_report

        return build_report(ctx, session, p, a)
    if name == "inspect_review":
        from forma.services import review_evidence, review_questions

        m = session.get(MappingVersion, a["mapping_id"])
        if m is None:
            raise NotFound("Mapping not found")
        ensure_customer(p, m.customer_id)
        overview = review_evidence.overview(ctx, session, m)
        questions = review_questions.questions(ctx, m)
        pointers = [a["field"]] if a.get("field") else [q["id"] for q in questions["questions"][:2]]
        cells = []
        for pointer in pointers:
            cells.append(review_evidence.evidence(ctx, session, m, pointer))
        latest = session.scalar(
            select(MappingVersion.id)
            .where(MappingVersion.round_id == m.round_id)
            .order_by(MappingVersion.version.desc())
            .limit(1)
        )
        changes = overview["changes"]
        if a.get("field"):
            changes = [f for f in changes if f["pointer"] == a["field"]]
        compact = []
        for change in changes[offset : offset + 10]:
            check = change.get("check") or {}
            compact.append(
                {
                    "pointer": change["pointer"],
                    "status": change["status"],
                    "risk": change["risk"],
                    "field": {
                        k: change["field"].get(k)
                        for k in ("name", "semantic", "description", "description_th")
                    },
                    "check": {**check, "examples": check.get("examples", [])[:2]},
                }
            )
        return {
            "context": overview["context"],
            "mapping_id": m.id,
            "status": m.status,
            "is_latest_version": latest == m.id,
            "questions": questions["questions"],
            "open_questions": questions["open_questions"],
            "reasons": questions["reasons"],
            "checks": overview["checks"],
            "source_evidence": cells,
            "changes": compact,
            "total_changes": len(changes),
            "next_offset": offset + 10 if offset + 10 < len(changes) else None,
            "comparison_dataset_id": overview["comparison_dataset_id"],
            "scope": "This exact mapping version. Source examples sample at most 50 records; use read_source for more. User chat answers do not save or approve review answers.",
            "links": [{"label": f"Review v{m.version}", "url": f"/review?mapping={m.id}&round={m.round_id}"}],
        }
    if name == "query_records":
        return query_records(ctx, session, p, a)
    if name == "customers":
        q = scoped(p, select(Customer), Customer.id)
        if a.get("search"):
            q = q.where(
                or_(
                    Customer.name.icontains(a["search"], autoescape=True),
                    Customer.code.icontains(a["search"], autoescape=True),
                )
            )
        result = page(session, q.order_by(Customer.code, Customer.id), offset, ser.customer)
        result["links"] = [{"label": "Customers", "url": "/customers"}]
        return result
    if name == "customer_rules":
        ensure_customer(p, a["customer_id"])
        if a["kind"] == "mappings":
            q = select(MappingVersion).where(MappingVersion.customer_id == a["customer_id"])
            return page(
                session,
                q.order_by(MappingVersion.created_at.desc(), MappingVersion.id),
                offset,
                lambda m: attrs(
                    m, "id round_id version status target_schema confidence parent_id created_at"
                ),
            )
        q = select(Override).where(Override.customer_id == a["customer_id"])
        return page(
            session,
            q.order_by(Override.created_at.desc(), Override.id),
            offset,
            lambda r: attrs(r, "id target where value active author note created_at"),
        )
    if name == "inspect_mapping":
        m = session.get(MappingVersion, a["mapping_id"])
        if m is None:
            raise NotFound("Mapping not found")
        ensure_customer(p, m.customer_id)
        doc = m.document
        rules = [{"collection": None, **f} for f in doc.get("fields", [])]
        rules += [
            {"collection": c.get("target"), **f}
            for c in doc.get("collections", [])
            for f in c.get("fields", [])
        ]
        return {
            "mapping": attrs(
                m,
                "id round_id version status target_schema confidence parent_id open_questions validation comment",
            ),
            "document_structure": {k: v for k, v in doc.items() if k not in ("fields", "collections")},
            "collections": [
                {k: v for k, v in c.items() if k != "fields"} for c in doc.get("collections", [])
            ],
            "rules": rules[offset : offset + 20],
            "total_rules": len(rules),
            "offset": offset,
            "next_offset": offset + 20 if offset + 20 < len(rules) else None,
            "links": [{"label": f"Mapping v{m.version}", "url": f"/rounds/{m.round_id}/mapping/{m.id}"}]
            if m.round_id
            else [{"label": "Customer mappings", "url": "/customers"}],
        }
    if name == "read_source":
        f = session.get(SourceFile, a["file_id"])
        if f is None:
            raise NotFound("File not found")
        ensure_customer(p, f.customer_id)
        path = ctx.storage.local_path(f.storage_key)
        meta = read_meta(path)
        sheet = a.get("sheet") or meta.sheets[0].name
        if sheet not in {s.name for s in meta.sheets}:
            raise ValidationFailed("Sheet not found; use a sheet name from inspect_import")
        frame = read_raw(path, sheet)
        column = a.get("column_offset", 0)
        search = a.get("search", "").casefold()
        matches = 0
        rows = []
        for index, row in enumerate(frame.iter_rows()):
            if search and not any(search in str(cell).casefold() for cell in row if cell is not None):
                continue
            if offset <= matches < offset + 20:
                rows.append({"row_number": index + 1, "cells": list(row[column : column + 20])})
            matches += 1
        return {
            "file_id": f.id,
            "name": f.original_name,
            "sheets": [s.to_dict() for s in meta.sheets],
            "sheet": sheet,
            "rows": rows,
            "total_rows": frame.height,
            "total_columns": frame.width,
            "matched_rows": matches,
            "complete_search": True,
            "offset": offset,
            "column_offset": column,
            "next_offset": offset + 20 if offset + 20 < matches else None,
            "next_column_offset": column + 20 if column + 20 < frame.width else None,
            "cell_values": "Reader-normalized strings; formula cells use cached values, not recalculation.",
            "links": [{"label": f.original_name, "url": "/rounds/" + f.round_id}],
        }
    if name in ("dataset_history", "processing_jobs"):
        r = session.get(ImportRound, a["round_id"])
        if r is None:
            raise NotFound("Import not found")
        ensure_customer(p, r.customer_id)
        dataset_ids = select(DatasetVersion.id).where(
            DatasetVersion.round_id == r.id, DatasetVersion.customer_id == r.customer_id
        )
        if name == "processing_jobs":
            q = select(Job).where(Job.round_id == r.id).order_by(Job.created_at.desc(), Job.id)
            result = page(
                session,
                q,
                offset,
                lambda j: attrs(j, "id kind status progress attempts created_at started_at finished_at"),
            )
        elif a["kind"] == "versions":
            q = (
                select(DatasetVersion)
                .where(DatasetVersion.id.in_(dataset_ids))
                .order_by(DatasetVersion.version.desc(), DatasetVersion.id)
            )
            result = page(session, q, offset, ser.dataset_version)
        elif a["kind"] == "changes":
            q = (
                select(ChangeSet)
                .where(ChangeSet.dataset_version_id.in_(dataset_ids))
                .order_by(ChangeSet.created_at.desc(), ChangeSet.id)
            )
            result = page(session, q, offset, ser.change_set)
        else:
            q = (
                select(ExportArtifact)
                .where(ExportArtifact.dataset_version_id.in_(dataset_ids))
                .order_by(ExportArtifact.created_at.desc(), ExportArtifact.id)
            )
            result = page(
                session, q, offset, lambda e: attrs(e, "id dataset_version_id fmt row_count created_at")
            )
        result["links"] = [{"label": r.label or r.id, "url": "/rounds/" + r.id}]
        return result
    if name == "review_queue":
        from forma.services.review_analytics import query_scope, summary

        q = query_scope(select(ReviewTask), ReviewTask, p, {"status": "open", **a})
        result = page(
            session,
            q.order_by(ReviewTask.priority.desc(), ReviewTask.created_at, ReviewTask.id),
            offset,
            lambda t: attrs(
                t,
                "id round_id customer_id mapping_version_id dataset_version_id kind priority status reasons comment created_at resolved_at",
            ),
        )
        result["links"] = [{"label": "Questions & issues", "url": "/review"}]
        if offset == 0:
            result["overview"] = summary(session, p, a)
        result["coverage"] = {
            "matched": result["total"],
            "returned": len(result["items"]),
            "complete": offset == 0 and result["next_offset"] is None,
            "unit": "review_tasks",
            "offset": offset,
        }
        return result
    if name == "work_rounds":
        from forma.services import batches

        if p.customers is not None:
            raise Forbidden("Work rounds require unrestricted customer access")
        work_rows: list[dict[str, Any]]
        if a.get("batch_id"):
            work_view = batches.batch_view(session, batches.get_batch(session, a["batch_id"]))
            all_items = work_view.pop("items")
            result = {
                "round": work_view,
                "files": all_items[offset : offset + 20],
                "next_offset": offset + 20 if offset + 20 < len(all_items) else None,
            }
            work_rows = [work_view]
        else:
            work_rows = batches.list_batches(session, 20, offset, a.get("search", ""))
            result = {"items": work_rows, "next_offset": offset + 20 if len(work_rows) == 20 else None}
        for work_row in work_rows:
            expected_count = work_row["expected_files"]
            work_row["received_files_ready"] = work_row.pop("complete")
            work_row["receipt_status"] = (
                "unknown"
                if expected_count is None
                else "received"
                if work_row["total"] >= expected_count
                else "missing_files"
            )
            work_row["missing_files"] = (
                max(0, expected_count - work_row["total"]) if expected_count is not None else None
            )
            work_row["over_target"] = (
                max(0, work_row["total"] - expected_count) if expected_count is not None else None
            )
        result["links"] = [
            {"label": work_row["label"] or "Work round", "url": "/uploads/" + work_row["id"]}
            for work_row in work_rows
        ]
        return result
    if name == "bulk_uploads":
        if p.customers is not None:
            raise Forbidden("Unassigned bulk files require unrestricted customer access")
        q = select(BatchItem)
        if a.get("search"):
            q = q.where(BatchItem.file_name.icontains(a["search"], autoescape=True))
        if a.get("status"):
            q = q.where(BatchItem.status == a["status"])
        result = page(
            session,
            q.order_by(BatchItem.created_at.desc(), BatchItem.id),
            offset,
            lambda b: attrs(b, "id batch_id file_name status customer_id round_id created_at"),
        )
        result["links"] = [{"label": "Imports", "url": "/imports"}]
        return result
    if name == "runtime_configuration":
        result = {"ai": connection_status(ctx, session), "read_only": True}
        if p.has_role("admin") and p.customers is None:
            result["approval_policy"] = config.policy_view(session, ctx.settings)
        result["links"] = [{"label": "Settings", "url": "/settings"}] if p.has_role("admin") else []
        return result
    if name == "audit_history":
        if not p.has_role("admin") or p.customers is not None:
            raise Forbidden("Audit history requires unrestricted administrator access")
        q = select(AuditLog)
        if a.get("entity_id"):
            q = q.where(AuditLog.entity_id == a["entity_id"])
        if a.get("action"):
            q = q.where(AuditLog.action == a["action"])
        return page(
            session,
            q.order_by(AuditLog.created_at.desc(), AuditLog.id),
            offset,
            lambda r: attrs(r, "id actor action entity entity_id created_at"),
        )
    raise ValidationFailed("Unknown tool")
