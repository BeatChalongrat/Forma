"""Evidence boundaries survive context compaction; partial rows never become totals."""

from __future__ import annotations

import json
from typing import Any


def compact(data: dict[str, Any], arguments: dict[str, Any], limit: int = 18000) -> dict[str, Any]:
    result = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    if "coverage" not in result and "total" in result and "next_offset" in result:
        rows = result.get("items", result.get("records", []))
        result["coverage"] = {
            "matched": result["total"],
            "returned": len(rows),
            "offset": result.get("offset", arguments.get("offset", 0)),
            "complete": result.get("offset", arguments.get("offset", 0)) == 0
            and result["next_offset"] is None,
        }
    metadata = {
        key: value
        for key, value in result.items()
        if key
        in {
            "total",
            "total_files",
            "total_rules",
            "total_changes",
            "offset",
            "limit",
            "next_offset",
            "next_column_offset",
            "complete_scan",
            "complete_search",
            "sample_only",
            "search",
            "scanned_records",
            "matched_records",
            "matched_rows",
            "total_rows",
            "total_columns",
            "operation",
            "value",
            "scope",
            "coverage",
            "generated_at",
            "pages_read",
            "stop_reason",
            "overview",
        }
    }
    if len(json.dumps(result, ensure_ascii=False)) <= limit:
        return result  # type: ignore[no-any-return]

    # Keep a valid object, full query/continuation metadata, and complete rows when they fit.
    out: dict[str, Any] = {
        **metadata,
        "truncated": True,
        "query": arguments,
        "detail_complete": False,
        "omitted_fields": [],
    }
    if "report" in result:
        out.update(
            report_available=True,
            **{
                k: result["report"][k]
                for k in (
                    "title",
                    "scope",
                    "notes",
                    "views",
                    "complete",
                    "coverage",
                    "filters",
                    "generated_at",
                    "categories_overlap",
                    "comparison",
                    "measurement",
                    "query",
                )
                if k in result["report"]
            },
        )
    for key, value in result.items():
        if key in metadata or key == "links" or (key == "report" and "report_available" in out):
            continue
        candidate = {**out, key: value}
        if len(json.dumps(candidate, ensure_ascii=False)) <= limit - 1000:
            out[key] = value
        elif isinstance(value, list):
            kept: list[Any] = []
            for row in value:
                if len(json.dumps({**out, key: [*kept, row]}, ensure_ascii=False)) > limit - 1000:
                    break
                kept.append(row)
            out[key] = kept
            out["omitted_fields"].append({"field": key, "shown": len(kept), "available": len(value)})
            if key in {"items", "records", "rules", "changes", "files", "rows"}:
                out["source_next_offset"] = result.get("next_offset")
                # Do not skip rows removed from the model's context when it asks for the next page.
                out["next_offset"] = int(result.get("offset", arguments.get("offset", 0))) + len(kept)
                if not kept:
                    out["continuation_requires_narrower_query"] = True
        else:
            out["omitted_fields"].append({"field": key})
    return out
