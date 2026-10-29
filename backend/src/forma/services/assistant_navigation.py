"""Build navigation choices from scoped tool results. Models select IDs, never URLs."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from forma.auth import Principal, ensure_customer
from forma.db.models import Customer, DatasetVersion, ImportRound, MappingVersion


def navigation_choices(
    session: Session, p: Principal, name: str, args: dict[str, Any], data: dict[str, Any], language: str
) -> list[dict[str, Any]]:
    choices: dict[str, dict[str, Any]] = {}

    def add(url: str, en: str, th: str, detail: str = "") -> None:
        choices[url] = {
            "id": sha256(url.encode()).hexdigest()[:16],
            "url": url,
            "label": th if language == "th" else en,
            "detail": detail[:160],
        }

    def add_act(r: ImportRound) -> None:
        """A one-click acceptance of the round's verdict (ADR-036) the person confirms in the UI (ADR-040).
        Only when the verdict needs no decision; the digest ties the action to exactly this evidence."""
        v = (r.summary or {}).get("verdict") or {}
        if v.get("outcome") != "accept" or v.get("must_decide") or not v.get("digest"):
            return
        if not p.has_role("reviewer"):
            return
        key = f"act:verdict.accept:{r.id}:{v['digest']}"
        stage = v.get("stage")
        preview = [
            {
                "en": f"Approve the {'field matching' if stage == 'mapping' else 'data'} of this file as FORMA proposed",
                "th": f"อนุมัติ{'การจับคู่คอลัมน์' if stage == 'mapping' else 'ข้อมูล'}ของไฟล์นี้ตามที่ FORMA เสนอ",
            },
            {
                "en": "Recorded in the audit log with the rules that fired: "
                + ", ".join(v.get("rules_fired") or []),
                "th": "บันทึกใน audit log พร้อมกฎที่ใช้ตัดสิน: " + ", ".join(v.get("rules_fired") or []),
            },
            {
                "en": "The policy still decides; a changed file makes this action stale",
                "th": "นโยบายยังเป็นผู้ตัดสิน ถ้าไฟล์เปลี่ยนไปแล้ว คำสั่งนี้จะใช้ไม่ได้",
            },
        ]
        if stage == "data":
            preview.append(
                {
                    "en": "Exported as JSON when the round publishes automatically",
                    "th": "ออก JSON ให้เองถ้ารอบนี้ตั้งค่าให้ publish อัตโนมัติ",
                }
            )
        conclusion = (v.get("conclusion") or {}).get("th" if language == "th" else "en", "")
        choices[key] = {
            "id": sha256(key.encode()).hexdigest()[:16],
            "url": "",
            "kind": "act",
            "label": "ยอมรับคำตัดสินของ FORMA" if language == "th" else "Accept FORMA's verdict",
            "detail": str(conclusion)[:160],
            "act": {"type": "verdict.accept", "round_id": r.id, "digest": v["digest"], "preview": preview},
        }

    def round_choices(rid: str | None, *, review: bool = False, status: str = "open") -> None:
        r = session.get(ImportRound, rid) if rid else None
        if r is None:
            return
        ensure_customer(p, r.customer_id)
        c = session.get(Customer, r.customer_id)
        detail = " · ".join(x for x in (c.code if c else "", r.label or "") if x)
        if review:
            add(
                "/review?" + urlencode({"round": r.id, "status": status}),
                "Check this file's issues",
                "เปิดตรวจปัญหาไฟล์นี้",
                detail,
            )
        add("/rounds/" + quote(r.id, safe=""), "Open this file", "เปิดไฟล์นี้", detail)
        add_act(r)

    def dataset_choices(did: str | None, *, findings: bool = False) -> None:
        d = session.get(DatasetVersion, did) if did else None
        if d is None:
            return
        ensure_customer(p, d.customer_id)
        r = session.get(ImportRound, d.round_id)
        detail = f"{r.label if r and r.label else 'Dataset'} · v{d.version}"
        path = "/datasets/" + quote(d.id, safe="")
        if findings:
            add(path + "?tab=findings", "Check result issues", "เปิดตรวจปัญหาผลลัพธ์", detail)
        add(path, "Open results", "เปิดข้อมูลผลลัพธ์", detail)

    def mapping_choices(mid: str | None) -> None:
        m = session.get(MappingVersion, mid) if mid else None
        if m is None or m.round_id is None:
            return
        ensure_customer(p, m.customer_id)
        r = session.get(ImportRound, m.round_id)
        if r is None or r.customer_id != m.customer_id:
            return
        path = f"/rounds/{quote(r.id, safe='')}/mapping/{quote(m.id, safe='')}"
        add(path, "Check column matching", "เปิดตรวจการจับคู่คอลัมน์", f"{r.label or 'Mapping'} · v{m.version}")

    if name == "workspace_summary":
        if data.get("open_reviews"):
            add("/review", "Open pending reviews", "เปิดงานที่รอตรวจ")
        add("/imports", "Open upload history", "เปิดประวัติไฟล์")
    elif name == "work_rounds" and p.customers is None:
        from forma.db.models import Batch

        for row in ([data["round"]] if "round" in data else data.get("items", []))[:3]:
            batch = session.get(Batch, row.get("id"))
            if batch:
                add(
                    "/uploads/" + quote(batch.id, safe=""), "Open work round", "เปิดรอบงานนี้", batch.label or ""
                )
    elif name == "feature_guide":
        add("/guide", "Open feature guide", "เปิดคู่มือการใช้งาน")
        add("/imports/new", "Upload files", "ไปอัปโหลดไฟล์")
        add("/imports", "Find past files", "ค้นหาไฟล์ย้อนหลัง")
        if p.has_role("admin"):
            add("/settings?tab=ai", "Open AI settings", "เปิดการเชื่อมต่อ AI")
    elif name == "search_imports":
        filters = {
            k: args[k] for k in ("search", "customer_id", "status", "start", "end", "timezone") if args.get(k)
        }
        add(
            "/imports" + ("?" + urlencode(filters) if filters else ""),
            "Open matching history",
            "เปิดประวัติตามที่ค้นหา",
        )
        for item in data.get("items", [])[:5]:
            round_choices(item.get("id"))
    elif name == "review_queue":
        for item in data.get("items", [])[:5]:
            if item.get("mapping_version_id"):
                round_choices(item.get("round_id"), review=True, status=item.get("status", "open"))
            elif item.get("dataset_version_id"):
                dataset_choices(item["dataset_version_id"], findings=True)
            else:
                round_choices(item.get("round_id"))
        add(
            "/review?" + urlencode({"status": args.get("status", "open")}),
            "Open review queue",
            "เปิดรายการตรวจทั้งหมด",
        )
    elif name == "inspect_import":
        round_choices(data.get("id"), review=data.get("status") in ("review", "blocked"))
        mapping_choices(data.get("mapping_id"))
        for d in data.get("datasets", [])[:2]:
            dataset_choices(d.get("id"))
    elif name == "inspect_mapping":
        mapping_choices(data.get("mapping", {}).get("id"))
    elif name in ("inspect_dataset", "search_records", "query_records", "explain_dataset"):
        dataset_choices(args.get("dataset_id"), findings=name == "explain_dataset")
    elif name in ("dataset_history", "processing_jobs"):
        round_choices(args.get("round_id"))
    elif name == "read_source":
        from forma.db.models import SourceFile

        f = session.get(SourceFile, args.get("file_id"))
        if f:
            ensure_customer(p, f.customer_id)
            round_choices(f.round_id)
    elif name in ("customers", "customer_rules"):
        add("/customers", "Open customers", "เปิดข้อมูลลูกค้า")
    elif name == "bulk_uploads":
        add("/imports", "Open uploads", "เปิดประวัติการอัปโหลด")
    elif name in ("runtime_configuration", "target_formats", "audit_history") and p.has_role("admin"):
        tab = {"runtime_configuration": "ai", "target_formats": "formats", "audit_history": "audit"}[name]
        add("/settings?" + urlencode({"tab": tab}), "Open settings", "เปิดหน้าตั้งค่าที่เกี่ยวข้อง")
    return list(choices.values())
