"""Automatic publishing (ADR-027): after a round's data is created, the policy decides whether it is approved
and exported as JSON without a person. Only rounds that ask for it (bulk uploads) are considered; every
decision, either way, is stored on the round with its reasons and written to the audit log."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.db.models import DatasetVersion, ExportArtifact, ImportRound
from forma.services import datasets as ds
from forma.services.context import ServiceContext
from forma.services.mapping import get_mapping
from forma.services.rounds import audit, get_round

ACTOR = "policy"
FORMAT = "json"


def latest_export(session: Session, round_id: str, fmt: str = FORMAT) -> ExportArtifact | None:
    return session.scalar(
        select(ExportArtifact)
        .join(DatasetVersion, DatasetVersion.id == ExportArtifact.dataset_version_id)
        .where(DatasetVersion.round_id == round_id, ExportArtifact.fmt == fmt)
        .order_by(ExportArtifact.created_at.desc())
    )


def auto_publish(ctx: ServiceContext, session: Session, round_id: str) -> dict[str, Any]:
    """Decide for the round's newest dataset; approve and export it when the policy says ``publish``."""
    r = get_round(session, round_id)
    dv = session.scalar(
        select(DatasetVersion).where(DatasetVersion.round_id == r.id).order_by(DatasetVersion.version.desc())
    )
    if dv is None:
        return {"decision": "review", "reasons": ["no data has been created yet"]}
    if dv.status in ("approved", "published"):
        art = latest_export(session, r.id) or ds.export_dataset(ctx, session, dv.id, FORMAT, ACTOR)
        approved = {
            "decision": "publish",
            "reasons": ["the data was approved by a person"],
            "export_id": art.id,
        }
        r.summary = {**(r.summary or {}), "publish": approved}
        return approved
    mv = get_mapping(session, dv.mapping_version_id)
    anomalies = ds.anomaly_report(ctx, session, dv)
    errors = sum(
        1 for f in dv.findings or [] if f.get("severity") == "error" and f.get("kind") != "validation"
    )
    decision = ctx.policy.evaluate_publish(
        mapping_approved_by=mv.approved_by if mv.status == "approved" else None,
        rows=dv.row_count,
        invalid_records=ds.invalid_records(dv),
        error_findings=errors,
        high_anomalies=int(anomalies["high"]),
    )
    result: dict[str, Any] = {
        "decision": decision.decision,
        "reasons": decision.reasons,
        "checks": decision.checks,
    }
    if decision.decision == "publish":
        ds.approve_dataset(
            session,
            dv.id,
            ACTOR,
            ctx=ctx,
            acknowledge_anomalies=bool(anomalies["high"]),  # only within policy.publish.max_high_anomalies
            comment="published automatically: " + "; ".join(decision.reasons),
        )
        art = ds.export_dataset(ctx, session, dv.id, FORMAT, ACTOR)
        result["export_id"] = art.id
    r.summary = {**(r.summary or {}), "publish": result}
    audit(
        session,
        "round.publish_decision",
        "round",
        r.id,
        actor=ACTOR,
        decision=decision.decision,
        reasons=decision.reasons,
    )
    return result


def after_manual_approval(
    ctx: ServiceContext, session: Session, round_: ImportRound
) -> dict[str, Any] | None:
    """A person approved the data of a round that asked to publish automatically: export its JSON too."""
    if not (round_.summary or {}).get("auto_publish"):
        return None
    return auto_publish(ctx, session, round_.id)
