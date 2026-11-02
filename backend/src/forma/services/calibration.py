"""Policy calibration from outcomes (ADR-037): how often FORMA was right, and what the policy could allow.

Every approval, rejection, correction and later edit is an outcome of an earlier decision. This module
turns them into (1) a report — precision per verdict rule and per decision kind — and (2) *proposals*:
concrete, reversible policy changes with the evidence behind them and the reviews they would have saved.
Nothing is applied here on its own: an admin applies a proposal (audited), the policy still decides
(rule 6), and the next report shows whether it was right.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.ai.policy import DEFAULT_POLICY, _merge, validate_policy
from forma.core.errors import NotFound, ValidationFailed
from forma.core.hashing import stable_hash
from forma.db.models import (
    AuditLog,
    ChangeSet,
    Correction,
    Customer,
    DatasetVersion,
    ImportRound,
    MappingVersion,
    ReviewTask,
)
from forma.services.context import ServiceContext
from forma.services.rounds import audit

OUTCOMES = (
    "accepted_unchanged",
    "corrected",
    "rejected",
    "auto_ok",
    "auto_then_edited",
    "auto_then_rolled_back",
)
RIGHT = ("accepted_unchanged", "auto_ok")
MIN_EVIDENCE = 3


# --------------------------------------------------------------------------------------------
# outcomes: what happened after each decision
# --------------------------------------------------------------------------------------------
def outcomes(session: Session, *, since: dt.datetime | None = None) -> list[dict[str, Any]]:
    """One row per decided mapping version: the decision, the verdict rules, and what a person did next."""
    q = select(MappingVersion).where(
        MappingVersion.status.in_(("approved", "superseded", "rejected", "published"))
    )
    if since is not None:
        q = q.where(MappingVersion.created_at >= since)
    versions = session.scalars(q.order_by(MappingVersion.created_at)).all()
    by_id = {m.id: m for m in versions}
    corrections: dict[str, list[Correction]] = defaultdict(list)
    for c in session.scalars(select(Correction).where(Correction.mapping_version_id.in_(list(by_id)))):
        corrections[c.mapping_version_id or ""].append(c)
    rounds = {
        r.id: r
        for r in session.scalars(
            select(ImportRound).where(ImportRound.id.in_({m.round_id for m in versions if m.round_id}))
        )
    }
    edited_rounds = {
        dv.round_id
        for dv in session.scalars(
            select(DatasetVersion)
            .join(ChangeSet, ChangeSet.dataset_version_id == DatasetVersion.id)
            .where(DatasetVersion.round_id.in_(list(rounds)))
        )
    }
    rolled_back = {
        str((a.details or {}).get("round_id") or "")
        for a in session.scalars(select(AuditLog).where(AuditLog.action == "dataset.rollback"))
    }
    rows: list[dict[str, Any]] = []
    for m in versions:
        pol = m.policy or {}
        rules = list(((pol.get("verdict") or {}).get("rules_fired")) or [])
        decision = "auto" if m.approved_by == "policy" else "review"
        if m.created_by == "human":
            # a human version: its outcome belongs to the proposal it corrected (recorded by learning)
            continue
        own = corrections.get(m.id, [])
        descendants = [
            c
            for x in versions
            if x.parent_id == m.id or _descends(x, m.id, by_id)
            for c in corrections.get(x.id, [])
        ]
        if m.status == "rejected":
            outcome = "rejected"
        elif decision == "auto":
            if m.round_id and m.round_id in rolled_back:
                outcome = "auto_then_rolled_back"
            elif any(x.parent_id == m.id and x.created_by == "human" for x in versions) or (
                m.round_id in edited_rounds
            ):
                outcome = "auto_then_edited"
            else:
                outcome = "auto_ok"
        elif any(c.kind in ("alias", "constant") for c in [*own, *descendants]):
            outcome = "corrected"
        elif m.status in ("approved", "published") or any(c.kind == "accepted" for c in [*own, *descendants]):
            outcome = "accepted_unchanged"
        else:
            continue  # superseded without an approval: no outcome yet
        r = rounds.get(m.round_id or "")
        rows.append(
            {
                "mapping_id": m.id,
                "round_id": m.round_id,
                "customer_id": m.customer_id,
                "decision": decision,
                "policy_decision": pol.get("decision"),
                "rules": rules or ["(no verdict)"],
                "reasons": list(pol.get("reasons") or [])[:3],
                "drift": (r.summary or {}).get("drift") if r else None,
                "row_change": _row_change(pol),
                "low_high": list((pol.get("checks") or {}).get("low_confidence_high") or []),
                "agreement": pol.get("agreement"),
                "corrected_targets": sorted(
                    {c.target for c in [*own, *descendants] if c.kind in ("alias", "constant") and c.target}
                ),
                "outcome": outcome,
                "at": (m.approved_at or m.created_at).isoformat()
                if (m.approved_at or m.created_at)
                else None,
            }
        )
    return rows


def _descends(x: MappingVersion, ancestor_id: str, by_id: dict[str, MappingVersion]) -> bool:
    cur = x
    for _ in range(50):
        if not cur.parent_id:
            return False
        if cur.parent_id == ancestor_id:
            return True
        nxt = by_id.get(cur.parent_id)
        if nxt is None:
            return False
        cur = nxt
    return False


def _row_change(pol: dict[str, Any]) -> float | None:
    for reason in pol.get("reasons") or []:
        s = str(reason)
        if s.startswith("row count changed"):
            try:
                return float(s.split("changed", 1)[1].split("%", 1)[0].strip()) / 100
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------------------------
# report: precision per rule and per decision kind
# --------------------------------------------------------------------------------------------
def report(session: Session, *, months: int = 6) -> dict[str, Any]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=31 * months)
    rows = outcomes(session, since=since)
    per_rule: dict[str, Counter[str]] = defaultdict(Counter)
    per_decision: dict[str, Counter[str]] = defaultdict(Counter)
    per_customer: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        for rule in r["rules"]:
            per_rule[rule][r["outcome"]] += 1
        per_decision[r["decision"]][r["outcome"]] += 1
        per_customer[r["customer_id"]][r["outcome"]] += 1
        per_customer[r["customer_id"]][r["decision"]] += 1

    def summarise(c: Counter[str]) -> dict[str, Any]:
        n = sum(v for k, v in c.items() if k in OUTCOMES)
        right = sum(c[k] for k in RIGHT)
        return {
            "n": n,
            "right": right,
            "precision": round(right / n, 3) if n else None,
            **{k: c[k] for k in OUTCOMES if c[k]},
        }

    codes = {
        c.id: c.code for c in session.scalars(select(Customer).where(Customer.id.in_(list(per_customer))))
    }
    customers = [
        {
            "customer_id": cid,
            "code": codes.get(cid, cid),
            "auto": c["auto"],
            "review": c["review"],
            **summarise(c),
        }
        for cid, c in per_customer.items()
    ]
    customers.sort(key=lambda x: (-x["review"], x["code"]))
    return {
        "since": since.date().isoformat(),
        "decisions": len(rows),
        "per_rule": {
            k: summarise(v) for k, v in sorted(per_rule.items(), key=lambda kv: -sum(kv[1].values()))
        },
        "per_decision": {k: summarise(v) for k, v in per_decision.items()},
        "per_signal": per_signal(rows),
        "audit": audit_outcomes(session, since=since),
        "customers": customers[:50],
    }


SIGNALS = ("agree", "disagree", "model_only")


def per_signal(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """How often a person corrected a field, by what FORMA knew about it at proposal time (ADR-059): the
    matcher and the model agreed, disagreed, or only the model mapped it. The correction rate per signal is
    the evidence a confidence floor or cap stands on."""
    out: dict[str, dict[str, Any]] = {}
    for name in SIGNALS:
        fields = corrected = 0
        for r in rows:
            ag = r.get("agreement") or {}
            pointers = ag.get(name) or []
            hit = set(r.get("corrected_targets") or [])
            fields += len(pointers)
            corrected += sum(1 for ptr in pointers if ptr in hit)
        out[name] = {
            "fields": fields,
            "corrected": corrected,
            "correction_rate": round(corrected / fields, 3) if fields else None,
        }
    return out


def audit_outcomes(session: Session, *, since: dt.datetime | None = None) -> dict[str, int]:
    """Spot checks of automatic approvals (ADR-059): sampled, confirmed as right, replaced by a person's
    version (dismissed), still open. The honest accuracy of automation is confirmed / (confirmed + corrected)."""
    q = select(ReviewTask).where(ReviewTask.kind == "audit")
    if since is not None:
        q = q.where(ReviewTask.created_at >= since)
    tasks = list(session.scalars(q))
    return {
        "sampled": len(tasks),
        "confirmed": sum(1 for t in tasks if t.status == "approved"),
        "corrected": sum(1 for t in tasks if t.status in ("dismissed", "rejected")),
        "open": sum(1 for t in tasks if t.status in ("open", "waiting")),
    }


# --------------------------------------------------------------------------------------------
# proposals: what the policy could allow, with the evidence
# --------------------------------------------------------------------------------------------
def proposals(session: Session, policy: dict[str, Any], *, months: int = 6) -> list[dict[str, Any]]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=31 * months)
    rows = outcomes(session, since=since)
    out: list[dict[str, Any]] = []
    sb = policy.get("sandbox", {})
    aa = policy.get("auto_approve", {})

    # 1) renamed layouts whose values were identical: trust the data check over the sandbox delta
    renamed = [r for r in rows if "rename_only" in r["rules"] and r["decision"] == "review"]
    ok = [r for r in renamed if r["outcome"] == "accepted_unchanged"]
    bad = [r for r in renamed if r["outcome"] in ("corrected", "rejected")]
    if not sb.get("trust_data_checks") and len(ok) >= MIN_EVIDENCE and not bad:
        out.append(
            _proposal(
                "trust_data_checks",
                scope="policy",
                path="sandbox.trust_data_checks",
                value=True,
                current=False,
                title=(
                    "Approve a changed layout automatically when the values are identical",
                    "อนุมัติ layout ที่เปลี่ยนอัตโนมัติเมื่อค่าเหมือนเดิมทุกฟิลด์",
                ),
                why=(
                    f"{len(ok)} reviews of a renamed or moved layout were accepted without any change; none was corrected or rejected",
                    f"{len(ok)} รายการที่ layout เปลี่ยนแต่ค่าเหมือนเดิม ถูกอนุมัติโดยไม่แก้เลย ไม่มีรายการใดถูกแก้หรือปฏิเสธ",
                ),
                saves=len(ok),
                evidence=[r["mapping_id"] for r in ok][:10],
            )
        )

    # 2) high-risk fields just under the confidence bar that people confirmed as read
    thr = float(aa.get("min_field_confidence_high", 0.8))
    near = [r for r in rows if r["decision"] == "review" and r["low_high"] and not r["drift"]]
    confirmed = [r for r in near if r["outcome"] == "accepted_unchanged"]
    corrected = [r for r in near if r["outcome"] == "corrected"]
    if thr > 0.6 and len(confirmed) >= MIN_EVIDENCE + 2 and len(corrected) * 4 < len(confirmed):
        out.append(
            _proposal(
                "lower_high_confidence",
                scope="policy",
                path="auto_approve.min_field_confidence_high",
                value=round(thr - 0.05, 2),
                current=thr,
                title=(
                    "Lower the confidence bar for high-risk fields by 0.05",
                    "ลดเกณฑ์ความมั่นใจของฟิลด์ความเสี่ยงสูงลง 0.05",
                ),
                why=(
                    f"{len(confirmed)} reviews were raised only by a high-risk field below {thr:.2f} and were confirmed as read; {len(corrected)} were corrected",
                    f"{len(confirmed)} รายการเข้า review เพราะฟิลด์ความเสี่ยงสูงต่ำกว่า {thr:.2f} แล้วถูกยืนยันว่าอ่านถูก มี {len(corrected)} รายการที่ถูกแก้",
                ),
                saves=len(confirmed),
                evidence=[r["mapping_id"] for r in confirmed][:10],
            )
        )

    # 3) per customer: row counts that swing but were always fine
    by_customer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if "row_shift" in r["rules"] or r["row_change"] is not None:
            by_customer[r["customer_id"]].append(r)
    codes = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(list(by_customer))))}
    for cid, rs in by_customer.items():
        fine = [r for r in rs if r["outcome"] in RIGHT and r["row_change"] is not None]
        wrong = [r for r in rs if r["outcome"] in ("corrected", "rejected")]
        c = codes.get(cid)
        if c is None or len(fine) < 2 or wrong:
            continue
        current = float(
            ((c.policy_overrides or {}).get("auto_approve") or {}).get("max_row_change_ratio")
            or aa.get("max_row_change_ratio", 0.3)
        )
        observed = max(float(r["row_change"]) for r in fine)
        value = round(min(0.95, observed + 0.1), 2)
        if value <= current:
            continue
        out.append(
            _proposal(
                f"row_change:{cid}",
                scope="customer",
                customer_id=cid,
                customer_code=c.code,
                path="auto_approve.max_row_change_ratio",
                value=value,
                current=current,
                title=(
                    f"Let {c.code}'s record count move up to {value:.0%} without a review",
                    f"ยอมให้จำนวนรายการของ {c.code} เปลี่ยนได้ถึง {value:.0%} โดยไม่ต้อง review",
                ),
                why=(
                    f"{len(fine)} rounds moved by up to {observed:.0%} and were approved as they were",
                    f"{len(fine)} รอบมีจำนวนรายการเปลี่ยนถึง {observed:.0%} และถูกอนุมัติตามเดิม",
                ),
                saves=len(fine),
                evidence=[r["mapping_id"] for r in fine][:10],
            )
        )
    out.extend(confidence_proposals(rows, policy))
    out.extend(anomaly_proposals(session, policy))
    return out


AGREE_MIN_FIELDS = 20  # agreed fields with an outcome before the floor may move
AGREE_TIGHT = 0.02  # corrected at most this often: the floor may rise
AGREE_LOOSE = 0.10  # corrected at least this often: the floor must fall


def confidence_proposals(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    """The agreement floor follows what people did with agreed fields (ADR-059): rarely corrected → it may
    rise by 0.05 (up to 0.95); often corrected → it falls by 0.05 (down to 0.80). Thresholds come from outcomes,
    not from a rule of thumb."""
    sig = per_signal(rows)["agree"]
    floor = float((policy.get("confidence") or {}).get("agree_floor", 0.9))
    rate = sig["correction_rate"]
    if sig["fields"] < AGREE_MIN_FIELDS or rate is None:
        return []
    if rate <= AGREE_TIGHT and floor < 0.95:
        value, title, why = (
            round(min(0.95, floor + 0.05), 2),
            (
                "Trust fields the matcher and the model agree on a little more",
                "เชื่อฟิลด์ที่ matcher กับโมเดลเห็นตรงกันมากขึ้นอีกนิด",
            ),
            (
                f"of {sig['fields']} agreed fields people corrected {sig['corrected']} ({rate:.1%})",
                f"จาก {sig['fields']} ฟิลด์ที่เห็นตรงกัน คนแก้ {sig['corrected']} ฟิลด์ ({rate:.1%})",
            ),
        )
    elif rate >= AGREE_LOOSE and floor > 0.8:
        value, title, why = (
            round(max(0.8, floor - 0.05), 2),
            (
                "Trust fields the matcher and the model agree on less",
                "เชื่อฟิลด์ที่ matcher กับโมเดลเห็นตรงกันน้อยลง",
            ),
            (
                f"of {sig['fields']} agreed fields people corrected {sig['corrected']} ({rate:.1%})",
                f"จาก {sig['fields']} ฟิลด์ที่เห็นตรงกัน คนแก้ {sig['corrected']} ฟิลด์ ({rate:.1%})",
            ),
        )
    else:
        return []
    return [
        _proposal(
            "agree_floor",
            scope="policy",
            path="confidence.agree_floor",
            value=value,
            current=floor,
            title=title,
            why=why,
            saves=0,
            evidence=[r["mapping_id"] for r in rows if (r.get("agreement") or {}).get("agree")][:10],
        )
    ]


def anomaly_proposals(session: Session, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Customers whose price jumps were acknowledged and approved in two or more rounds: raise their own
    threshold to the observed 90th percentile plus five points (ADR-039), never the global one."""
    acknowledged = {
        a.entity_id
        for a in session.scalars(select(AuditLog).where(AuditLog.action == "dataset.approve"))
        if (a.details or {}).get("acknowledged_anomalies")
    }
    if not acknowledged:
        return []
    rounds_of = {
        dv.id: dv.round_id
        for dv in session.scalars(select(DatasetVersion).where(DatasetVersion.id.in_(sorted(acknowledged))))
    }
    rounds = {
        r.id: r
        for r in session.scalars(select(ImportRound).where(ImportRound.id.in_(set(rounds_of.values()))))
    }
    per_customer: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
    for dv_id, rid in rounds_of.items():
        r = rounds.get(rid)
        summ = (r.summary or {}).get("anomaly_summary") if r else None
        if not r or not summ or summ.get("dataset_id") != dv_id or not summ.get("price_jump_pcts"):
            continue
        per_customer[r.customer_id].append((r.id, [float(x) for x in summ["price_jump_pcts"]]))
    out: list[dict[str, Any]] = []
    global_thr = float(policy.get("anomaly", {}).get("max_price_change_pct", 30))
    customers = {
        c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(list(per_customer))))
    }
    for cid, rows in per_customer.items():
        c = customers.get(cid)
        if c is None or len(rows) < 2:
            continue
        pcts = sorted(x for _, xs in rows for x in xs)
        p90 = pcts[min(len(pcts) - 1, int(len(pcts) * 0.9))]
        current = float(
            ((c.policy_overrides or {}).get("anomaly") or {}).get("max_price_change_pct") or global_thr
        )
        value = float(min(80, int(p90) + 5))
        if value <= current:
            continue
        out.append(
            _proposal(
                f"price_move:{cid}",
                scope="customer",
                customer_id=cid,
                customer_code=c.code,
                path="anomaly.max_price_change_pct",
                value=value,
                current=current,
                title=(
                    f"Treat price moves up to {value:.0f}% as normal for {c.code}",
                    f"ถือว่าราคาของ {c.code} ขยับได้ถึง {value:.0f}% เป็นเรื่องปกติ",
                ),
                why=(
                    f"in {len(rows)} rounds a person acknowledged price jumps (90th percentile {p90:.0f}%) and approved the data",
                    f"ใน {len(rows)} รอบ คนรับทราบราคาที่กระโดด (เปอร์เซ็นไทล์ที่ 90 = {p90:.0f}%) แล้วอนุมัติข้อมูล",
                ),
                saves=len(rows),
                evidence=[rid for rid, _ in rows][:10],
            )
        )
    return out


def _proposal(pid: str, **kw: Any) -> dict[str, Any]:
    title = kw.pop("title")
    why = kw.pop("why")
    p = {"id": pid, "title": {"en": title[0], "th": title[1]}, "why": {"en": why[0], "th": why[1]}, **kw}
    p["digest"] = stable_hash({k: v for k, v in p.items() if k != "evidence"}, 16)
    return p


# --------------------------------------------------------------------------------------------
# apply: an admin's decision, audited, through the ordinary settings paths
# --------------------------------------------------------------------------------------------
def apply(ctx: ServiceContext, session: Session, proposal_id: str, digest: str, actor: str) -> dict[str, Any]:
    from forma.services import config

    policy = config.policy_view(session, ctx.settings)["effective"]
    p = next((x for x in proposals(session, policy) if x["id"] == proposal_id), None)
    if p is None:
        raise NotFound("This proposal is no longer available; the evidence changed")
    if p["digest"] != digest:
        raise ValidationFailed("The proposal changed since it was shown; reload")
    section, key = p["path"].split(".", 1)
    if p["scope"] == "policy":
        new_policy = _merge(policy, {section: {key: p["value"]}})
        problems = validate_policy(new_policy)
        if problems:
            raise ValidationFailed("policy is not valid", details={"problems": problems})
        config.update_policy(session, ctx.settings, new_policy, actor)
    else:
        c = session.get(Customer, p["customer_id"])
        if c is None:
            raise NotFound("customer not found")
        overrides = _merge(dict(c.policy_overrides or {}), {section: {key: p["value"]}})
        problems = validate_policy(_merge(DEFAULT_POLICY, overrides))
        if problems:
            raise ValidationFailed("policy is not valid", details={"problems": problems})
        c.policy_overrides = overrides
    audit(
        session,
        "policy.calibrate",
        "customer" if p["scope"] == "customer" else "settings",
        p.get("customer_id") or "policy",
        actor=actor,
        proposal=p["id"],
        path=p["path"],
        before=p["current"],
        after=p["value"],
        saves=p["saves"],
    )
    return {"applied": p["id"], "path": p["path"], "value": p["value"], "scope": p["scope"]}
