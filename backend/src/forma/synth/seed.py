"""Load the synthetic corpus into the database as a realistic demo (customers, rounds, pipeline runs)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from forma.db.session import init_db, session_scope
from forma.services import datasets as ds
from forma.services import mapping as ms
from forma.services import rounds as rs
from forma.services.context import get_context


def seed_database(corpus: Path, *, run_pipeline: bool = True, limit: int | None = None) -> dict[str, Any]:
    init_db()
    ctx = get_context()
    index = json.loads((corpus / "index.json").read_text(encoding="utf-8"))
    variants = sorted(
        index["variants"], key=lambda m: (m.get("sequence") or "zz", m["round_no"], m["variant_id"])
    )
    if limit:
        variants = variants[:limit]
    created: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for m in variants:
        code = m["customer_code"]
        with session_scope() as s:
            if code not in created:
                from sqlalchemy import select

                from forma.db.models import Customer

                c = s.scalar(select(Customer).where(Customer.code == code))
                if c is None:
                    c = rs.create_customer(
                        s,
                        code,
                        f"{code} — {m['archetype'].replace('_', ' ').title()}",
                        erp_type=m["archetype"],
                        notes="synthetic demo customer",
                    )
                created[code] = c.id
            r = rs.create_round(s, created[code], label=f"Round {m['round_no'] + 1} · {m['variant_id']}")
            for fname in m["files"]:
                p = corpus / m["variant_id"] / "files" / fname
                rs.add_file(ctx, s, r, fname, p.read_bytes())
            rid = r.id
        row: dict[str, Any] = {"variant": m["variant_id"], "customer": code, "round_id": rid}
        if run_pipeline:
            with session_scope() as s:
                rs.profile_round(ctx, s, rid)
            with session_scope() as s:
                mv = ms.propose_for_round(ctx, s, rid, use_llm=False)
                row["mapping"] = mv.id
                row["decision"] = (mv.policy or {}).get("decision")
                mv_id, status = mv.id, mv.status
            # demo behaviour: first round of a returning-customer sequence gets approved by an operator so
            # later rounds can exercise memory / drift / wrong-file paths; single-round variants stay in review.
            if status == "proposed" and m.get("sequence") and m["round_no"] == 0:
                with session_scope() as s:
                    ms.approve_mapping(s, mv_id, "demo.operator", "approved after review (demo seed)")
                status = "approved"
                row["decision"] = "approved_by_operator"
            if status == "approved":
                with session_scope() as s:
                    dv = ds.transform_round(ctx, s, rid, mapping_version_id=mv_id, actor="demo.operator")
                    row["dataset"] = dv.id
                    row["rows"] = dv.row_count
                    if m.get("sequence") and m["round_no"] == 0:
                        ds.approve_dataset(s, dv.id, "demo.operator")
                        ds.export_dataset(ctx, s, dv.id, "json", "demo.operator")
        out.append(row)
    return {"customers": len(created), "rounds": len(out), "results": out}
