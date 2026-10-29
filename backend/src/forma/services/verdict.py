"""The verdict layer (ADR-036): one conclusion per file, built from the reports FORMA already stores.

A verdict is deterministic: the same stored reports and the same ``RULES_VERSION`` give the same
conclusion, outcome, confidence and digest. It never decides anything — the policy still does — it tells
the reviewer what the evidence adds up to, what only a person can settle, and what was folded away.
Fields are named by meaning through the registry (rule 1); a model may narrate a verdict later
(``narrative``), never change it.
"""

from __future__ import annotations

import statistics
from typing import Any

from forma.core.hashing import stable_hash
from forma.schema_registry import TargetSchema

RULES_VERSION = "2026.10.3"
OUTCOMES = (
    "accept",
    "assign_customer",
    "answer",
    "fix_mapping",
    "check_data",
    "fix_data",
    "reject_file",
    "wait",
)
WORDS = (("sure", 0.85), ("likely", 0.6), ("unsure", 0.0))
RISKY_CHECKS = ("vat_added", "vat_removed", "scaled", "different")
DEFAULTS: dict[str, Any] = {
    "min_pairs": 3,  # identity-matched SKUs before data checks may carry a conclusion
    "different_share": 0.8,  # share of checked critical/high fields that no longer match → wrong file
    "min_group": 5,  # anomalies of one kind before they are one conclusion
    "max_must_decide": 3,  # decisions shown; the rest are counted
    "narrative": "on_open",  # on_open | never — when a model may narrate a verdict
}


def settings(policy: dict[str, Any]) -> dict[str, Any]:
    """The ``verdict`` section of the effective policy (validated in ``ai.policy``), with defaults."""
    return {**DEFAULTS, **(policy.get("verdict") or {})}


def word(confidence: float) -> str:
    return next(w for w, lo in WORDS if confidence >= lo)


def _text(en: str, th: str) -> dict[str, str]:
    return {"en": en, "th": th}


def _label(schema: TargetSchema, pointer: str) -> dict[str, str]:
    try:
        f = schema.field(pointer)
    except Exception:
        return _text(pointer, pointer)
    name = f.name.replace("_", " ")
    return _text(
        f.description or name, f.description_th or THAI_LABELS.get(f.semantic or "") or f.description or name
    )


AMOUNT_SEMANTICS = (
    "price.amount",
    "promo.price",
    "discount.amount",
    "discount.percent",
    "stock.qty",
    "stock.reserved",
    "unit.pack_size",
)
THAI_LABELS = {
    "sku": "รหัสสินค้า",
    "product_name": "ชื่อสินค้า",
    "description": "รายละเอียดสินค้า",
    "barcode": "บาร์โค้ด",
    "brand": "ยี่ห้อ",
    "category_path": "หมวดสินค้า",
    "currency": "สกุลเงิน",
    "status": "สถานะสินค้า",
    "unit.sell": "หน่วยขาย",
    "unit.pack_size": "จำนวนชิ้นต่อแพ็ก",
    "price.amount": "ราคาขาย",
    "price.list_name": "รายการราคา",
    "price.vat_included": "การรวม VAT",
    "price.vat_rate": "อัตรา VAT",
    "price.effective_from": "วันที่เริ่มใช้ราคา",
    "price.effective_to": "วันที่สิ้นสุดราคา",
    "stock.qty": "จำนวนสต็อก",
    "stock.warehouse": "คลังสินค้า",
    "stock.reserved": "จำนวนที่จองไว้",
    "promo.code": "รหัสโปรโมชัน",
    "promo.name": "ชื่อโปรโมชัน",
    "promo.type": "ประเภทโปรโมชัน",
    "promo.start_date": "วันเริ่มโปรโมชัน",
    "promo.end_date": "วันสิ้นสุดโปรโมชัน",
    "promo.price": "ราคาโปรโมชัน",
    "discount.percent": "ส่วนลดเปอร์เซ็นต์",
    "discount.amount": "ส่วนลดจำนวนเงิน",
}
THAI_QUESTIONS = {
    "price.vat_included": ("Do this round's prices include VAT?", "ราคารอบนี้รวม VAT แล้วหรือยัง?"),
    "unit.pack_size": ("How many units are in one pack?", "หนึ่งแพ็กมีสินค้ากี่ชิ้น?"),
}


def _question(q: dict[str, Any]) -> dict[str, str]:
    f = q["field"]
    known = THAI_QUESTIONS.get(f.get("semantic") or "")
    if known and q["kind"] != "confirm":
        return _text(*known)
    name = str(f.get("name") or "").replace("_", " ")
    en = f.get("description") or name
    th = f.get("description_th") or THAI_LABELS.get(f.get("semantic") or "") or f.get("description") or name
    return _text(f"Is {en} read correctly?", f"{th} อ่านถูกต้องไหม?")


def _is_amount(schema: TargetSchema, pointer: str) -> bool:
    try:
        return schema.field(pointer).semantic in AMOUNT_SEMANTICS
    except Exception:
        return False


def _clamp(x: float, lo: float = 0.05, hi: float = 0.98) -> float:
    return round(max(lo, min(hi, x)), 2)


def _mode(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


# --------------------------------------------------------------------------------------------
# mapping stage
# --------------------------------------------------------------------------------------------
def _checks_of(review: dict[str, Any] | None, schema: TargetSchema) -> list[dict[str, Any]]:
    """Data checks of changed fields with risk attached (the field review's ``checks`` list)."""
    out = []
    for c in (review or {}).get("checks") or []:
        risk = c.get("risk")
        if risk is None:
            try:
                risk = schema.field(c["pointer"]).risk
            except Exception:
                risk = "medium"
        out.append({**c, "risk": risk})
    return out


def _suppress(review: dict[str, Any] | None, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold field tables a reviewer does not need: same rule with different data, repeated patterns."""
    fields = (review or {}).get("fields") or []
    data_only = [
        f["pointer"]
        for f in fields
        if f["status"] == "changed"
        and f.get("before")
        and f.get("after")
        and f["before"].get("expr") == f["after"].get("expr")
    ]
    no_data = [c["pointer"] for c in checks if c.get("verdict") == "no_data"]
    groups: list[dict[str, Any]] = []
    if data_only:
        groups.append(
            {
                "reason": "data_not_mapping",
                "text": _text(
                    f"{len(data_only)} fields keep the same rule; only this round's values differ (a data change)",
                    f"{len(data_only)} ฟิลด์ใช้กฎเดิม ต่างกันแค่ค่าของรอบนี้ (ข้อมูลเปลี่ยน ไม่ใช่การจับคู่เปลี่ยน)",
                ),
                "pointers": sorted(data_only),
            }
        )
    by_pattern: dict[tuple[str, float | None], list[str]] = {}
    for c in checks:
        if c.get("verdict") in RISKY_CHECKS and c["pointer"] not in data_only:
            key = (c["verdict"], round(float(c["ratio"]), 2) if c.get("ratio") is not None else None)
            by_pattern.setdefault(key, []).append(c["pointer"])
    for (verdict, ratio), ptrs in sorted(by_pattern.items(), key=lambda kv: -len(kv[1])):
        if len(ptrs) > 1:
            how = f"×{ratio}" if ratio is not None else verdict
            groups.append(
                {
                    "reason": "same_pattern",
                    "text": _text(
                        f"{len(ptrs)} fields share one pattern ({how})",
                        f"{len(ptrs)} ฟิลด์เปลี่ยนแบบเดียวกัน ({how})",
                    ),
                    "pointers": sorted(ptrs),
                }
            )
    if no_data:
        groups.append(
            {
                "reason": "no_data",
                "text": _text(
                    f"{len(no_data)} fields had too few matching SKUs to compare",
                    f"{len(no_data)} ฟิลด์มี SKU ที่ตรงกันน้อยเกินกว่าจะเทียบ",
                ),
                "pointers": sorted(no_data),
            }
        )
    return {"count": sum(len(g["pointers"]) for g in groups), "groups": groups}


def for_mapping(
    schema: TargetSchema,
    cfg: dict[str, Any],
    *,
    policy: dict[str, Any],
    open_questions: list[Any],
    questions: list[dict[str, Any]],
    summary: dict[str, Any],
    round_id: str,
    mapping_id: str,
    customer_code: str,
) -> dict[str, Any]:
    """The verdict of a proposed/approved mapping version from its stored policy report."""
    decision = policy.get("decision")
    checks_d = policy.get("checks") or {}
    review = policy.get("field_review")
    sandbox = (policy.get("sandbox") or {}).get("delta") or {}
    sim = policy.get("simulation") or {}
    rows_out = int(sim.get("rows_out") or checks_d.get("rows_out") or 0)
    drift = summary.get("drift") if summary.get("has_previous_approval") else None
    other = summary.get("matches_other_customer") or {}
    other_code = other.get("customer_code") or other.get("customer_id")
    llm_fallback = bool(checks_d.get("llm_fallback"))
    errors = int(checks_d.get("errors") or 0)
    checks = _checks_of(review, schema)
    identity = set(schema.identity_keys)  # identity-matched pairs always agree on their keys
    constants = {
        f["pointer"]
        for f in (review or {}).get("fields") or []
        if ((f.get("after") or {}).get("expr") or {}).get("op") == "const"
    }  # a fixed value is the same in every round; it says nothing about the file
    for c in checks:
        # a "scale" of ×1 or on a code/text field is not a scale: the values simply differ
        if c.get("verdict") in ("scaled", "vat_added", "vat_removed") and (
            not _is_amount(schema, c["pointer"]) or abs(float(c.get("ratio") or 1.0) - 1.0) < 0.01
        ):
            c["verdict"] = "different"
    important = [
        c
        for c in checks
        if c["risk"] in ("critical", "high")
        and c.get("verdict") != "no_data"
        and c["pointer"] not in identity
        and c["pointer"] not in constants
    ]
    different = [c for c in important if c.get("verdict") == "different"]
    pairs = max([int(c.get("pairs") or 0) for c in checks], default=0)
    different_share = len(different) / len(important) if important else 0.0
    studio = f"/rounds/{round_id}/mapping/{mapping_id}"
    evidence: list[dict[str, Any]] = []

    def ev(kind: str, en: str, th: str, link: str | None = None, **numbers: Any) -> int:
        evidence.append(
            {"id": len(evidence) + 1, "kind": kind, "text": _text(en, th), "numbers": numbers, "link": link}
        )
        return len(evidence)

    if other_code:
        sim_pct = round(float(other.get("similarity") or 1.0) * 100)
        ev(
            "other_customer",
            f"The layout matches {other_code}'s approved format ({sim_pct}%)",
            f"โครงสร้างไฟล์ตรงกับรูปแบบที่อนุมัติแล้วของ {other_code} ({sim_pct}%)",
            f"/rounds/{round_id}",
            customer=other_code,
            similarity=other.get("similarity"),
        )
    if drift and drift != "identical":
        ev(
            "drift",
            f"Layout vs this customer's approved format: {drift}",
            f"โครงสร้างเทียบกับรูปแบบที่อนุมัติของลูกค้ารายนี้: {drift}",
            f"/rounds/{round_id}",
            drift=drift,
        )
    if important:
        same = sum(1 for c in important if c.get("verdict") == "same")
        ev(
            "data_checks",
            f"On {pairs} SKUs present last round, {same} of {len(important)} important fields keep their values and {len(different)} do not",
            f"บน {pairs} SKU ที่มีในรอบก่อน ฟิลด์สำคัญ {same} จาก {len(important)} ค่าเท่าเดิม และ {len(different)} ฟิลด์ค่าไม่ตรง",
            f"{studio}?tab=changes",
            pairs=pairs,
            same=same,
            different=len(different),
            of=len(important),
        )
    if sandbox:
        ev(
            "sandbox",
            f"Against the approved mapping: {sandbox.get('changed_records', 0)} records change, {sandbox.get('lost', 0)} lost, {sandbox.get('gained', 0)} gained, {sandbox.get('changed_prices', 0)} prices differ",
            f"เทียบกับ mapping ที่ใช้อยู่: เปลี่ยน {sandbox.get('changed_records', 0)} รายการ หาย {sandbox.get('lost', 0)} เพิ่ม {sandbox.get('gained', 0)} ราคาต่าง {sandbox.get('changed_prices', 0)}",
            f"{studio}?tab=compare",
            **{k: sandbox.get(k) for k in ("changed_records", "lost", "gained", "changed_prices")},
        )
    learned = policy.get("learning") or {}
    if learned.get("used"):
        n = len(learned["used"])
        ev(
            "learned",
            f"{n} fields mapped from what you taught FORMA before",
            f"{n} ฟิลด์จับคู่จากสิ่งที่คุณเคยสอน FORMA ไว้",
            f"{studio}?tab=fields",
            used=learned["used"],
        )
    if rows_out:
        ev(
            "rows",
            f"{rows_out} records produced",
            f"สร้างได้ {rows_out} รายการ",
            f"/rounds/{round_id}",
            rows_out=rows_out,
        )
    if errors:
        ev(
            "errors",
            f"{errors} engine or validation errors",
            f"ข้อผิดพลาดของ engine/การตรวจ {errors} จุด",
            f"{studio}?tab=findings",
            errors=errors,
        )
    if llm_fallback:
        ev("ai", "AI was unavailable; the deterministic draft is in use", "AI ใช้งานไม่ได้ ใช้ร่างจากกฎแทน", None)
    if questions:
        ev(
            "questions",
            f"{len(questions)} fields below the confidence bar",
            f"{len(questions)} ฟิลด์ยังไม่ถึงเกณฑ์ความมั่นใจ",
            f"/review?round={round_id}",
            count=len(questions),
        )

    rules: list[str] = []
    conclusion: dict[str, str]
    outcome: str
    confidence: float
    row_shift = next((r for r in policy.get("reasons", []) if str(r).startswith("row count changed")), None)
    vat = next((c for c in checks if c.get("verdict") in ("vat_added", "vat_removed")), None)
    scaled = next((c for c in checks if c.get("verdict") == "scaled"), None)
    enough = pairs >= int(cfg["min_pairs"])

    if (
        other_code
        and enough
        and (
            different_share >= float(cfg["different_share"])
            or (rows_out and sandbox.get("lost", 0) / rows_out >= 0.5)
        )
    ):
        rules.append("wrong_customer")
        conclusion = _text(
            f"This file is probably {other_code}'s, not {customer_code}'s",
            f"ไฟล์นี้น่าจะเป็นของ {other_code} ไม่ใช่ {customer_code}",
        )
        outcome = "assign_customer"
        confidence = 0.7 + 0.15 * float(other.get("similarity") or 1.0) + 0.15 * different_share
    elif decision == "block" and (drift == "unrelated_file" or summary.get("drift") == "unrelated_file"):
        rules.append("blocked_unrelated")
        conclusion = _text(
            f"The layout matches nothing approved for {customer_code}"
            + (f"; it does match {other_code}" if other_code else ""),
            f"โครงสร้างไม่ตรงกับรูปแบบใดที่อนุมัติไว้ของ {customer_code}"
            + (f" แต่ตรงกับของ {other_code}" if other_code else ""),
        )
        outcome = "assign_customer" if other_code else "reject_file"
        confidence = 0.8 + (0.1 if other_code else 0.0)
    elif decision == "block":
        rules.append("blocked")
        top = str((policy.get("reasons") or ["blocked by policy"])[0])
        conclusion = _text(f"The policy blocks this mapping: {top}", f"กฎระงับการจับคู่นี้: {top}")
        outcome = "fix_mapping"
        confidence = 0.85
    elif errors and decision != "auto_approve":
        rules.append("engine_errors")
        conclusion = _text(
            f"{errors} errors stop records from being produced", f"ข้อผิดพลาด {errors} จุดทำให้สร้างบางรายการไม่ได้"
        )
        outcome = "fix_mapping"
        confidence = 0.9
    elif vat is not None:
        rules.append("vat_shift")
        up = vat["verdict"] == "vat_added"
        lbl = _label(schema, vat["pointer"])
        conclusion = _text(
            f"{lbl['en']} is 7% {'higher' if up else 'lower'} than last round on {vat.get('pairs')} SKUs — VAT is now {'included' if up else 'excluded'}?",
            f"{lbl['th']} สูง{'ขึ้น' if up else 'ลง'} 7% จากรอบก่อนบน {vat.get('pairs')} SKU — ราคา{'รวม' if up else 'ไม่รวม'} VAT แล้ว?",
        )
        outcome = "answer"
        confidence = 0.75 + 0.15 * min(1.0, int(vat.get("pairs") or 0) / 30)
    elif scaled is not None:
        rules.append("scaled")
        lbl = _label(schema, scaled["pointer"])
        conclusion = _text(
            f"{lbl['en']} is ×{scaled.get('ratio')} of last round's — a unit, pack or currency change?",
            f"{lbl['th']} เป็น ×{scaled.get('ratio')} ของรอบก่อน — หน่วย แพ็ก หรือสกุลเงินเปลี่ยน?",
        )
        outcome = "fix_mapping"
        confidence = 0.7
    elif different and enough:
        rules.append("meaning_changed")
        c0 = different[0]
        lbl = _label(schema, c0["pointer"])
        share = round(float(c0.get("equal_share") or 0) * 100)
        conclusion = _text(
            f"{lbl['en']} no longer means the same thing: only {share}% of {c0.get('pairs')} SKUs keep their value",
            f"{lbl['th']} ไม่ได้หมายถึงสิ่งเดิมแล้ว: มีแค่ {share}% จาก {c0.get('pairs')} SKU ที่ค่าเท่าเดิม",
        )
        outcome = "fix_mapping"
        confidence = 0.65 + 0.2 * (1 - float(c0.get("equal_share") or 0))
    elif decision == "auto_approve":
        rules.append("clean")
        conclusion = _text("Nothing to decide: every check passed", "ไม่มีอะไรต้องตัดสิน ผ่านทุกการตรวจ")
        outcome = "accept"
        confidence = 0.95
    elif (
        drift in ("columns_renamed", "header_moved", "columns_added", "columns_removed", "types_changed")
        and important
        and enough
        and all(c.get("verdict") == "same" for c in important)
        and not sandbox.get("lost")
    ):
        # the approved mapping may produce other values on the new layout (the sandbox counts them), but
        # the proposal reproduces last round's published values on the same SKUs: the layout moved, not the data
        rules.append("rename_only")
        conclusion = _text(
            f"The layout changed ({drift}) but the values are identical on {pairs} SKUs",
            f"โครงสร้างเปลี่ยน ({drift}) แต่ค่าเหมือนเดิมบน {pairs} SKU",
        )
        outcome = "accept"
        confidence = 0.85 + 0.1 * min(1.0, pairs / 30)
    elif other_code and not summary.get("has_previous_approval"):
        rules.append("first_time_shared_layout")
        conclusion = _text(
            f"First file from {customer_code}; its layout is the same as {other_code}'s (the same ERP?) — confirm the sender, then {len(questions)} fields need a confirmation",
            f"ไฟล์แรกของ {customer_code} โครงสร้างเหมือนของ {other_code} (ERP เดียวกัน?) — ยืนยันผู้ส่ง แล้วมี {len(questions)} ฟิลด์ที่ต้องยืนยัน",
        )
        outcome = "answer" if questions else "accept"
        confidence = 0.6
    elif other_code:
        rules.append("other_customer_unconfirmed")
        conclusion = _text(
            f"The layout matches {other_code}'s format but the data could not be compared; confirm the sender",
            f"โครงสร้างตรงกับของ {other_code} แต่ยังเทียบข้อมูลไม่ได้ ต้องยืนยันผู้ส่ง",
        )
        outcome = "assign_customer"
        confidence = 0.55
    elif not summary.get("has_previous_approval"):
        rules.append("first_time")
        conclusion = _text(
            f"First file from {customer_code}; {len(questions)} fields need a confirmation",
            f"ไฟล์แรกของ {customer_code} มี {len(questions)} ฟิลด์ที่ต้องยืนยัน",
        )
        outcome = "answer" if questions else "accept"
        confidence = 0.8 if questions else 0.7
    elif llm_fallback:
        rules.append("ai_unavailable")
        conclusion = _text(
            f"Mapped without AI; {len(questions)} fields need a confirmation",
            f"จับคู่โดยไม่มี AI มี {len(questions)} ฟิลด์ที่ต้องยืนยัน",
        )
        outcome = "answer" if questions else "accept"
        confidence = 0.8
    elif row_shift:
        rules.append("row_shift")
        conclusion = _text(f"The record count moved: {row_shift}", f"จำนวนรายการเปลี่ยน: {row_shift}")
        outcome = "check_data"
        confidence = 0.7
    else:
        rules.append("review")
        top = str((policy.get("reasons") or ["needs a look"])[0])
        conclusion = _text(f"Needs a look: {top}", f"ต้องดู: {top}")
        outcome = "answer" if questions else "fix_mapping"
        confidence = 0.5
    if llm_fallback and "ai_unavailable" not in rules:
        confidence -= 0.15
    if not enough and rules[0] in ("wrong_customer", "rename_only", "meaning_changed"):
        confidence -= 0.15

    must: list[dict[str, Any]] = []
    leaving = outcome in ("assign_customer", "reject_file")
    if vat is not None and not leaving:
        up = vat["verdict"] == "vat_added"
        must.append(
            {
                "id": vat["pointer"],
                "kind": "boolean",
                "question": _text("Do this round's prices include VAT?", "ราคารอบนี้รวม VAT แล้วหรือยัง?"),
                "why": _text(
                    f"Values moved by exactly 7% on {vat.get('pairs')} SKUs",
                    f"ค่าเปลี่ยนไป 7% พอดีบน {vat.get('pairs')} SKU",
                ),
                "options": [True, False],
                "default": up,
                "default_reason": _text(
                    "Thai retail prices are usually VAT-inclusive (ADR-007)",
                    "ราคาขายปลีกไทยมักรวม VAT (ADR-007)",
                ),
                "evidence_ids": [e["id"] for e in evidence if e["kind"] == "data_checks"],
            }
        )
    if not leaving:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        for q in sorted(
            questions, key=lambda q: (q.get("kind") == "confirm", order.get(q["field"].get("risk"), 9))
        ):
            if any(m["id"] == q["id"] for m in must):
                continue
            must.append(
                {
                    "id": q["id"],
                    "kind": q["kind"],
                    "question": _question(q),
                    "why": _text(
                        "Below the confidence bar for its risk", "ความมั่นใจต่ำกว่าเกณฑ์ของระดับความเสี่ยงนี้"
                    ),
                    "options": [True, False]
                    if q["kind"] == "boolean"
                    else ["confirm"]
                    if q["kind"] == "confirm"
                    else [],
                    "default": None,
                    "evidence_ids": [e["id"] for e in evidence if e["kind"] == "questions"],
                }
            )
    cap = int(cfg["max_must_decide"])
    more = max(0, len(must) - cap)
    suppressed = _suppress(review, checks)
    if more:
        suppressed["groups"].append(
            {
                "reason": "more_questions",
                "text": _text(f"{more} more field confirmations", f"อีก {more} ฟิลด์ที่ต้องยืนยัน"),
                "pointers": [m["id"] for m in must[cap:]],
            }
        )
        suppressed["count"] += more
    if open_questions and not leaving:
        suppressed["groups"].append(
            {
                "reason": "open_questions",
                "text": _text(
                    f"{len(open_questions)} notes from the mapper",
                    f"หมายเหตุจากตัวจับคู่ {len(open_questions)} ข้อ",
                ),
                "pointers": [],
            }
        )
    out = {
        "subject": {"kind": "mapping", "id": mapping_id, "round_id": round_id},
        "stage": "mapping",
        "conclusion": conclusion,
        "outcome": outcome,
        "confidence": _clamp(confidence),
        "word": word(_clamp(confidence)),
        "must_decide": must[:cap],
        "evidence": evidence[:8],
        "suppressed": suppressed,
        "rules_fired": rules,
        "policy_decision": decision,
        "rules_version": RULES_VERSION,
        "narrative": None,
    }
    out["digest"] = stable_hash({k: v for k, v in out.items() if k != "narrative"}, 32)
    return out


# --------------------------------------------------------------------------------------------
# data stage
# --------------------------------------------------------------------------------------------
def for_dataset(
    schema: TargetSchema,
    cfg: dict[str, Any],
    *,
    dataset_id: str,
    round_id: str,
    rows: int,
    invalid_records: int,
    error_findings: int,
    anomalies: dict[str, Any],
    publish: dict[str, Any] | None,
    status: str,
) -> dict[str, Any]:
    """The verdict of a dataset version from its anomaly report, findings and publish decision."""
    editor = f"/datasets/{dataset_id}"
    evidence: list[dict[str, Any]] = []

    def ev(kind: str, en: str, th: str, link: str | None = None, **numbers: Any) -> None:
        evidence.append(
            {"id": len(evidence) + 1, "kind": kind, "text": _text(en, th), "numbers": numbers, "link": link}
        )

    items = anomalies.get("items") or []
    dataset_level = anomalies.get("dataset") or []
    high = int(anomalies.get("high") or 0)
    by_kind = anomalies.get("by_kind") or {}
    ev(
        "rows",
        f"{rows} records; {anomalies.get('normal_pct', 100)}% look normal",
        f"{rows} รายการ ดูปกติ {anomalies.get('normal_pct', 100)}%",
        f"{editor}?tab=diff",
        rows=rows,
    )
    if invalid_records:
        ev(
            "contract",
            f"{invalid_records} records break the target format",
            f"{invalid_records} รายการไม่ตรง Target format",
            f"{editor}?tab=findings",
            invalid=invalid_records,
        )
    if error_findings:
        ev(
            "errors",
            f"{error_findings} engine errors (duplicates, broken links)",
            f"ข้อผิดพลาดของ engine {error_findings} จุด",
            f"{editor}?tab=findings",
            errors=error_findings,
        )
    for d in dataset_level:
        ev(
            "dataset_anomaly",
            str(d.get("message")),
            str(d.get("message")),
            f"{editor}?tab=diff",
            anomaly=d.get("kind"),
        )
    if by_kind:
        top = ", ".join(f"{k} ×{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1])[:3])
        ev("anomalies", f"Anomalies: {top}", f"ความผิดปกติ: {top}", f"{editor}?tab=diff", **by_kind)
    if publish:
        ev(
            "publish",
            f"Publish check: {publish.get('decision')}",
            f"ผลตรวจก่อนออก JSON: {publish.get('decision')}",
            None,
            decision=publish.get("decision"),
        )

    rules: list[str] = []
    must: list[dict[str, Any]] = []
    jumps = [
        a for a in items if a.get("kind") == "price_jump" and a.get("value") is not None and a.get("expected")
    ]
    signs = [1 if float(a["value"]) > float(a["expected"]) else -1 for a in jumps]
    removed = next((d for d in dataset_level if d.get("kind") == "skus_removed"), None)
    if status in ("approved", "published"):
        rules.append("published" if status == "published" else "approved")
        conclusion = _text(
            "Approved" + (" and exported" if status == "published" else ""),
            "อนุมัติแล้ว" + (" และส่งออกแล้ว" if status == "published" else ""),
        )
        outcome, confidence = "accept", 0.98
    elif invalid_records:
        rules.append("contract_broken")
        conclusion = _text(
            f"{invalid_records} records break the target format and must be fixed or removed",
            f"{invalid_records} รายการไม่ตรง Target format ต้องแก้หรือลบก่อน",
        )
        outcome, confidence = "fix_data", 0.95
    elif removed and len(jumps) >= int(cfg["min_group"]) and by_kind.get("price_jump", 0) >= rows * 0.5:
        rules.append("catalogue_replaced")
        conclusion = _text(
            "Most of the catalogue changed — a new price list, or the wrong file?",
            "แคตตาล็อกส่วนใหญ่เปลี่ยน — เป็น price list ใหม่ หรือไฟล์ผิด?",
        )
        outcome, confidence = "check_data", 0.7
    elif len(jumps) >= int(cfg["min_group"]) and abs(sum(signs)) >= 0.8 * len(signs):
        rules.append("price_move")
        pcts = [abs(float(a.get("score") or 0)) for a in jumps]
        med = _mode(pcts) or 0.0
        up = sum(signs) > 0
        conclusion = _text(
            f"{len(jumps)} prices {'rose' if up else 'fell'} by about {med:.0f}% — a price change this round?",
            f"ราคา {len(jumps)} รายการ{'ขึ้น' if up else 'ลง'}ราว {med:.0f}% — มีการปรับราคารอบนี้?",
        )
        outcome, confidence = "check_data", 0.75
        must.append(
            {
                "id": "acknowledge:price_jump",
                "kind": "boolean",
                "question": _text(
                    f"Is a {'rise' if up else 'drop'} of about {med:.0f}% on {len(jumps)} products expected?",
                    f"ราคา{'ขึ้น' if up else 'ลง'}ราว {med:.0f}% บน {len(jumps)} สินค้า เป็นเรื่องที่คาดไว้ไหม?",
                ),
                "why": _text(
                    "High-severity anomalies need an acknowledgement before approval (ADR-022)",
                    "ความผิดปกติระดับสูงต้องได้รับการรับทราบก่อนอนุมัติ (ADR-022)",
                ),
                "options": [True, False],
                "default": None,
                "evidence_ids": [e["id"] for e in evidence if e["kind"] == "anomalies"],
            }
        )
    elif high or error_findings:
        rules.append("anomalies")
        top = ", ".join(k for k, _ in sorted(by_kind.items(), key=lambda kv: -kv[1])[:2]) or "engine errors"
        conclusion = _text(
            f"{high} records look wrong ({top}); {error_findings} engine errors",
            f"{high} รายการดูผิดปกติ ({top}) และข้อผิดพลาดของ engine {error_findings} จุด",
        )
        outcome, confidence = "check_data", 0.7
    elif publish and publish.get("decision") == "review":
        rules.append("held")
        top = str((publish.get("reasons") or ["a person approves every dataset"])[0])
        conclusion = _text(f"Held for a person: {top}", f"รอคนตรวจ: {top}")
        outcome, confidence = "accept", 0.8
    else:
        rules.append("publishable")
        conclusion = _text("Ready: the data passed every check", "พร้อม: ข้อมูลผ่านทุกการตรวจ")
        outcome, confidence = "accept", 0.95
    suppressed = {"count": 0, "groups": []}
    low = int(anomalies.get("medium") or 0) + int(anomalies.get("low") or 0)
    if low:
        suppressed = {
            "count": low,
            "groups": [
                {
                    "reason": "minor_anomalies",
                    "text": _text(f"{low} medium/low anomalies", f"ความผิดปกติระดับกลาง/ต่ำ {low} จุด"),
                    "pointers": [],
                }
            ],
        }
    out = {
        "subject": {"kind": "dataset", "id": dataset_id, "round_id": round_id},
        "stage": "data",
        "conclusion": conclusion,
        "outcome": outcome,
        "confidence": _clamp(confidence),
        "word": word(_clamp(confidence)),
        "must_decide": must[: int(cfg["max_must_decide"])],
        "evidence": evidence[:8],
        "suppressed": suppressed,
        "rules_fired": rules,
        "policy_decision": (publish or {}).get("decision"),
        "rules_version": RULES_VERSION,
        "narrative": None,
    }
    out["digest"] = stable_hash({k: v for k, v in out.items() if k != "narrative"}, 32)
    return out
