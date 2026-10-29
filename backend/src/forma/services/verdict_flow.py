"""Verdicts in the pipeline (ADR-036): computed from stored reports when a mapping or dataset version is
created, kept on the round's summary (and the mapping's policy report / the review task) so queues and
rows can show the conclusion without loading anything, recomputed on read when the rules moved on, and
acted on through the existing approval paths — never around them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, ValidationFailed
from forma.db.models import DatasetVersion, ImportRound, MappingVersion, ReviewTask
from forma.services import verdict
from forma.services.context import ServiceContext
from forma.services.rounds import audit

DATA_STAGES = ("transformed", "approved_data", "published")


def _customer_code(session: Session, r: ImportRound) -> str:
    return r.customer.code if r.customer is not None else r.customer_id


def _summary(r: ImportRound) -> dict[str, Any]:
    return dict(r.summary or {})


# --------------------------------------------------------------------------------------------
# compute
# --------------------------------------------------------------------------------------------
def mapping_verdict(
    ctx: ServiceContext, session: Session, m: MappingVersion, r: ImportRound
) -> dict[str, Any]:
    from forma.services import review_questions

    schema = ctx.registry.get_ref(m.target_schema)
    questions = review_questions.questions(ctx, m)["questions"] if m.status == "proposed" else []
    v = verdict.for_mapping(
        schema,
        verdict.settings(ctx.policy.policy),
        policy={k: val for k, val in (m.policy or {}).items() if k != "verdict"},
        open_questions=m.open_questions or [],
        questions=questions,
        summary=_summary(r),
        round_id=r.id,
        mapping_id=m.id,
        customer_code=_customer_code(session, r),
    )
    return {**v, "round_status": r.status, "mapping_status": m.status}


def dataset_verdict(
    ctx: ServiceContext,
    session: Session,
    dv: DatasetVersion,
    r: ImportRound,
    *,
    anomalies: dict[str, Any] | None = None,
    publish: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from forma.services import datasets as ds
    from forma.services.mapping import get_mapping

    schema = ctx.registry.get_ref(get_mapping(session, dv.mapping_version_id).target_schema)
    settled = dv.status in ("approved", "published")
    rep = anomalies if anomalies is not None else ({} if settled else ds.anomaly_report(ctx, session, dv))
    errors = sum(
        1 for f in dv.findings or [] if f.get("severity") == "error" and f.get("kind") != "validation"
    )
    v = verdict.for_dataset(
        schema,
        verdict.settings(ctx.policy.policy),
        dataset_id=dv.id,
        round_id=r.id,
        rows=dv.row_count,
        invalid_records=ds.invalid_records(dv),
        error_findings=errors,
        anomalies=rep,
        publish=publish if publish is not None else _summary(r).get("publish"),
        status=dv.status,
    )
    return {**v, "round_status": r.status, "dataset_status": dv.status}


# --------------------------------------------------------------------------------------------
# attach at creation time (the version is still being built: not a mutation of history)
# --------------------------------------------------------------------------------------------
def attach_to_mapping(
    ctx: ServiceContext, session: Session, m: MappingVersion, r: ImportRound, task: ReviewTask | None = None
) -> dict[str, Any]:
    v = mapping_verdict(ctx, session, m, r)
    m.policy = {**(m.policy or {}), "verdict": v}
    if task is not None:
        task.details = {**(task.details or {}), "verdict": _brief(v)}
    r.summary = {**_summary(r), "verdict": v}
    return v


def attach_to_dataset(
    ctx: ServiceContext,
    session: Session,
    dv: DatasetVersion,
    r: ImportRound,
    *,
    anomalies: dict[str, Any] | None = None,
    publish: dict[str, Any] | None = None,
) -> dict[str, Any]:
    v = dataset_verdict(ctx, session, dv, r, anomalies=anomalies, publish=publish)
    r.summary = {**_summary(r), "verdict": v}
    return v


def _brief(v: dict[str, Any]) -> dict[str, Any]:
    """What a queue row needs: no evidence payloads."""
    return {
        k: v[k] for k in ("conclusion", "outcome", "confidence", "word", "rules_fired", "digest", "stage")
    }


# --------------------------------------------------------------------------------------------
# read (recomputed when stale)
# --------------------------------------------------------------------------------------------
def _latest_dataset(session: Session, r: ImportRound) -> DatasetVersion | None:
    return session.scalar(
        select(DatasetVersion).where(DatasetVersion.round_id == r.id).order_by(DatasetVersion.version.desc())
    )


def current(ctx: ServiceContext, session: Session, r: ImportRound) -> dict[str, Any] | None:
    """The verdict of the round's current stage; None while the round is still being processed."""
    stored = _summary(r).get("verdict")
    dv = _latest_dataset(session, r) if r.status in DATA_STAGES else None
    if dv is not None:
        fresh = (
            stored
            and stored.get("stage") == "data"
            and stored.get("subject", {}).get("id") == dv.id
            and stored.get("rules_version") == verdict.RULES_VERSION
            and stored.get("round_status") == r.status
        )
        return stored if fresh else attach_to_dataset(ctx, session, dv, r)
    mid = _summary(r).get("mapping_version_id")
    m = session.get(MappingVersion, mid) if mid else None
    if m is None:
        return None
    fresh = (
        stored
        and stored.get("stage") == "mapping"
        and stored.get("subject", {}).get("id") == m.id
        and stored.get("rules_version") == verdict.RULES_VERSION
        and stored.get("round_status") == r.status
        and stored.get("mapping_status") == m.status
    )
    return stored if fresh else attach_to_mapping(ctx, session, m, r)


# --------------------------------------------------------------------------------------------
# act (through the existing approval paths; the policy still decides)
# --------------------------------------------------------------------------------------------
def accept(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    r: ImportRound,
    digest: str,
    decisions: dict[str, Any],
    comment: str | None = None,
) -> dict[str, Any]:
    from forma.services import datasets as ds
    from forma.services import mapping as ms
    from forma.services import review_questions

    v = current(ctx, session, r)
    if v is None:
        raise Conflict("This file has no verdict yet; it is still being processed")
    if digest != v["digest"]:
        raise Conflict(
            "The file changed since this verdict was shown. Reload and look again.",
            details={"digest": v["digest"]},
        )
    allowed = {m["id"] for m in v["must_decide"]}
    if set(decisions) - allowed:
        raise ValidationFailed("A decision refers to something this verdict did not ask")
    outcome = v["outcome"]
    changed = {
        k: val
        for k, val in decisions.items()
        if val != next(m.get("default") for m in v["must_decide"] if m["id"] == k)
    }
    result: dict[str, Any]
    if v["stage"] == "mapping":
        if outcome == "accept":
            m = ms.approve_mapping(
                session,
                v["subject"]["id"],
                p.actor,
                comment or f"accepted verdict {','.join(v['rules_fired'])}",
            )
            result = {
                "did": "approve_mapping",
                "mapping_id": m.id,
                "auto_publish": bool(_summary(r).get("auto_publish")),
            }
        elif outcome == "answer":
            answers = {k: val for k, val in decisions.items() if val is not None and val != "unknown"}
            if not answers:
                raise ValidationFailed("Choose at least one answer")
            m = ms.get_mapping(session, v["subject"]["id"])
            _doc, _remaining, expected = review_questions.answer_document(ctx, m, answers, p.actor)
            new = review_questions.apply_answers(ctx, session, m, answers, expected, p.actor)
            result = {"did": "apply_answers", "mapping_id": new.id}
        else:
            raise Conflict(
                f"'{outcome}' is done on its own page, not with one click",
                details={
                    "outcome": outcome,
                    "links": [e.get("link") for e in v["evidence"] if e.get("link")],
                },
            )
    else:
        dv_id = v["subject"]["id"]
        if outcome in ("accept", "check_data"):
            ack = any(k.startswith("acknowledge:") and val is True for k, val in decisions.items())
            if outcome == "check_data" and not ack:
                raise Conflict(
                    "Acknowledge the anomalies or open the Data Editor to look at them",
                    details={"outcome": outcome},
                )
            dv = ds.approve_dataset(
                session,
                dv_id,
                p.actor,
                ctx=ctx,
                acknowledge_anomalies=ack,
                comment=comment or f"accepted verdict {','.join(v['rules_fired'])}",
            )
            result = {
                "did": "approve_dataset",
                "dataset_id": dv.id,
                "auto_publish": bool(_summary(r).get("auto_publish")),
            }
        else:
            raise Conflict(
                f"'{outcome}' is done in the Data Editor, not with one click",
                details={
                    "outcome": outcome,
                    "links": [e.get("link") for e in v["evidence"] if e.get("link")],
                },
            )
    audit(
        session,
        "verdict.accept",
        "round",
        r.id,
        actor=p.actor,
        stage=v["stage"],
        outcome=outcome,
        rules_fired=v["rules_fired"],
        confidence=v["confidence"],
        digest=v["digest"],
        decisions=decisions,
        changed=changed,
        **result,
    )
    return {**result, "verdict": v}
