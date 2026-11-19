"""Verify source grain before losing dimensions or summing quantities.

No header vocabulary or SKU/warehouse/lot assumptions: the mapping supplies the
business key, and every source row is checked. Samples only bound the audit UI.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from forma.mapping_dsl.models import CollectionMapping


def check_source_identity(
    items: pl.DataFrame,
    cm: CollectionMapping,
    rel_names: list[str],
    output_keys: list[str],
    *,
    require_grain: bool = False,
) -> tuple[pl.DataFrame, list[Any]]:
    from forma.engine.ops import python_value
    from forma.engine.transform import Finding, _referenced_columns

    prefix = cm.source + "."
    columns = [c for c in items.columns if c.startswith(prefix) and not c[len(prefix) :].startswith("__")]
    available = [c[len(prefix) :] for c in columns]
    target = next(
        (f.target for f in cm.fields if cm.aggregate and f.target[len(cm.target) + 2 :] in cm.aggregate.sum),
        cm.fields[0].target,
    )
    base = {"collection": cm.target, "source": cm.source, "target": target, "available_columns": available}

    def issue(reason: str, message: str, **details: Any) -> Any:
        return Finding(
            "source_identity", "error", message, target=target, details={**base, "reason": reason, **details}
        )

    if items.is_empty():
        return items, []
    identity = cm.source_identity
    if identity is None:
        if cm.aggregate:
            return items, [issue("missing_key", "ยังไม่ทราบว่าคอลัมน์ใดใช้แยกแต่ละล็อตหรือรายการ จึงยังรวมยอดไม่ได้")]
        # Same output identity but distinct source rows: projection would erase
        # a dimension (e.g. lot/bin), even if the quantities happen to be equal.
        groups = items.group_by(["__link__", *output_keys]).agg(
            pl.struct(columns).n_unique().alias("variants")
        )
        ambiguous = groups.filter(pl.col("variants") > 1)
        if ambiguous.height:
            # Existing conflicting-item questions already cover visibly different
            # output values. Add the grain question for silent projection losses.
            projected = items.group_by(["__link__", *rel_names]).agg(
                pl.struct(columns).n_unique().alias("variants")
            )
            if require_grain or projected.filter(pl.col("variants") > 1).height:
                return items, [
                    issue(
                        "missing_key",
                        "หลายแถวต้นทางได้ค่าเหมือนกัน แต่มีรายละเอียดต่างกัน ต้องระบุคีย์รายการก่อนตัดแถวซ้ำหรือรวมยอด",
                        groups=ambiguous.height,
                    )
                ]
        return items, []

    sum_inputs: set[str] = set()
    if cm.aggregate:
        for f in cm.fields:
            if f.target[len(cm.target) + 2 :] in cm.aggregate.sum:
                sum_inputs |= _referenced_columns(f.expr, cm.source)
    elif require_grain:
        for f, rel in zip(cm.fields, rel_names, strict=True):
            if rel not in output_keys:
                sum_inputs |= _referenced_columns(f.expr, cm.source)
    if set(prefix + k for k in identity.keys) & sum_inputs:
        return items, [
            issue("incomplete_key", "ห้ามใช้ยอดที่กำลังรวมเป็นคีย์แยกรายการ เพราะจะซ่อนยอดที่ขัดกัน กรุณาเลือกคีย์ธุรกิจ")
        ]

    key_cols = list(
        dict.fromkeys([*(prefix + k.right for k in cm.link), *(prefix + k for k in identity.keys)])
    )
    missing = [c[len(prefix) :] for c in key_cols if c not in columns]
    if missing:
        return items, [
            issue("missing_columns", "คอลัมน์ที่ใช้แยกรายการไม่มีในไฟล์นี้ กรุณาตรวจคีย์อีกครั้ง", missing_columns=missing)
        ]
    blank = items.filter(
        pl.any_horizontal(
            [
                pl.col(c).is_null() | (pl.col(c).cast(pl.String, strict=False).str.strip_chars() == "")
                for c in key_cols
            ]
        )
    )
    if blank.height:
        blank_columns = [
            c[len(prefix) :]
            for c in key_cols
            if blank.select(
                (
                    pl.col(c).is_null() | (pl.col(c).cast(pl.String, strict=False).str.strip_chars() == "")
                ).any()
            ).item()
        ]
        return items, [
            issue(
                "blank_key",
                f"คอลัมน์ {', '.join(blank_columns)} ว่างใน {blank.height} แถว "
                "จึงยังแยกรายการและรวมยอดไม่ได้ กรุณาเติมข้อมูลต้นทาง",
                count=blank.height,
                blank_columns=blank_columns,
                rows=blank["__src_row__"].head(8).to_list(),
            )
        ]

    # Compare raw source values, before conversion/rounding can hide a conflict.
    distinct = items.unique(subset=columns, maintain_order=True)
    conflicts = distinct.group_by(key_cols, maintain_order=True).len().filter(pl.col("len") > 1)
    findings: list[Any] = []
    # Full count is recorded; choices are bounded. Remaining conflicts are
    # regenerated on the next review and continue to block approval.
    for key in conflicts.head(8).iter_rows(named=True):
        group = items.filter(pl.all_horizontal([pl.col(c) == pl.lit(key[c]) for c in key_cols]))
        changed = [c for c in columns if group[c].n_unique() > 1]
        variants = group.group_by(columns, maintain_order=True).agg(pl.col("__src_row__"))
        rows = []
        for variant in variants.head(8).iter_rows(named=True):
            rows.append(
                {
                    "rows": variant["__src_row__"],
                    "values": {c[len(prefix) :]: python_value(variant[c]) for c in columns},
                }
            )
        findings.append(
            issue(
                "conflicting_values"
                if set(changed) <= sum_inputs and variants.height <= 8
                else "incomplete_key",
                "คีย์เดียวกันมียอดขัดกัน ให้เลือกข้อมูลที่ถูกต้องก่อนรวมยอด"
                if set(changed) <= sum_inputs
                else "คีย์เดียวกันยังมีรายละเอียดต่างกัน อาจเป็นคนละตำแหน่งหรือคนละสถานะ กรุณาตรวจคีย์รายการ",
                identity={c[len(prefix) :]: python_value(key[c]) for c in key_cols},
                variants=rows,
                conflicting_groups=conflicts.height,
            )
        )
    removed = items.height - distinct.height
    if not cm.aggregate:
        collisions = distinct.group_by(["__link__", *output_keys]).agg(
            pl.struct(key_cols).n_unique().alias("variants")
        )
        if collisions.filter(pl.col("variants") > 1).height:
            findings.append(
                issue(
                    "needs_aggregation",
                    "หลายล็อตหรือรายการจะถูกรวมเป็นรายการเดียวในปลายทาง "
                    "ต้องกำหนดการรวมยอดหรือเก็บคีย์แยกรายการในรูปแบบปลายทางก่อน",
                )
            )
    if removed:
        retained_rows = distinct["__src_row__"]
        dropped = items.filter(~pl.col("__src_row__").is_in(retained_rows.implode()))
        findings.append(
            Finding(
                "source_identity",
                "info",
                f"ตัดแถวต้นทางซ้ำที่คีย์และข้อมูลตรงกัน {removed} แถวก่อนรวมยอด",
                target=target,
                details={
                    **base,
                    "input_rows": items.height,
                    "retained_rows": distinct.height,
                    "dropped_rows": removed,
                    "dropped_row_sample": dropped["__src_row__"].head(20).to_list(),
                    "source_keys": identity.keys,
                    "evidence": identity.evidence,
                },
            )
        )
    return distinct, findings


def validate_selection(
    original: pl.DataFrame, selected: pl.DataFrame, findings: list[Any], cm: CollectionMapping
) -> list[Any]:
    """A row decision can resolve one conflicting source identity, never erase another lot.

    Structural uncertainties are checked against the original input, before filters.
    No field/header vocabulary is used here. This also fences old row-choice mappings.
    """
    from forma.engine.transform import Finding

    errors = [f for f in findings if f.severity == "error"]
    structural = [f for f in errors if f.details.get("reason") != "conflicting_values"]
    if structural:
        return structural
    conflicts = [f for f in errors if f.details.get("reason") == "conflicting_values"]
    if not conflicts:
        return []
    removed = set(original["__src_row__"].to_list()) - set(selected["__src_row__"].to_list())
    allowed = {row for f in conflicts for v in f.details["variants"] for row in v["rows"]}
    kept = set(selected["__src_row__"].to_list())
    # Do not let a filter remove every alternative, or discard an unrelated identity.
    if removed - allowed or any(
        not kept.intersection(r for v in f.details["variants"] for r in v["rows"]) for f in conflicts
    ):
        return [
            Finding(
                "source_identity",
                "error",
                "การเลือกคำตอบตัดรายการอื่นหรือทุกทางเลือกออก ต้องตรวจคีย์และเลือกเฉพาะค่าที่ขัดกันในรายการเดียวกัน",
                target=conflicts[0].target,
                details={**conflicts[0].details, "reason": "invalid_selection", "collection": cm.target},
            )
        ]
    return []
