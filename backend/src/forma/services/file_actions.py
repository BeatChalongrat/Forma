"""File-level corrections create new deliveries; original files and outputs remain immutable."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.auth import Principal
from forma.core.errors import Conflict, NotFound, ValidationFailed
from forma.db.models import BatchItem, ImportRound, Job, SourceFile
from forma.services import round_lifecycle as life
from forma.services import rounds as rs
from forma.services.context import ServiceContext


def _copy(session: Session, f: SourceFile, r: ImportRound) -> None:
    session.add(
        SourceFile(
            round_id=r.id,
            customer_id=r.customer_id,
            sha256=f.sha256,
            original_name=f.original_name,
            size=f.size,
            kind=f.kind,
            storage_key=f.storage_key,
            duplicate_of=f.id,
        )
    )


def change(
    ctx: ServiceContext,
    session: Session,
    p: Principal,
    r: ImportRound,
    file_id: str,
    action: Literal["replace", "move", "cancel"],
    reason: str,
    *,
    replacement: tuple[str, bytes] | None = None,
    customer_id: str | None = None,
) -> tuple[ImportRound, list[ImportRound]]:
    """Return the destination and deliveries to rerun. Never change source file rows in place."""
    # Serialize competing corrections on Postgres; SQLite serializes the write transaction.
    session.scalar(select(ImportRound).where(ImportRound.id == r.id).with_for_update())
    session.refresh(r, attribute_names=["status", "files"])
    f = next((x for x in r.files if x.id == file_id), None)
    if f is None:
        raise NotFound("File not found in this delivery")
    reason = life._reason(reason)
    if life.is_closed(r):
        raise Conflict("This file belongs to an older delivery. Open the current delivery first.")
    if session.scalar(
        select(Job.id).where(Job.round_id == r.id, Job.status.in_(("queued", "running"))).limit(1)
    ):
        raise Conflict("This file is being processed. Wait until it finishes, then try again.")
    if any(x.status == "purged" or not x.storage_key for x in r.files):
        raise Conflict("Some source files are no longer stored. Upload a complete new delivery.")
    if action == "replace" and replacement is None:
        raise ValidationFailed("Choose the corrected file first")
    if action == "move":
        if not customer_id or customer_id == r.customer_id:
            raise ValidationFailed("Choose a different customer")
        rs.get_customer(session, customer_id)

    detached: ImportRound | None = None
    if len(r.files) == 1:
        if action == "cancel":
            destination, rerun = life.cancel(session, p, r, reason), []
        elif action == "move":
            destination = life.move(session, p, r, customer_id or "", reason)
            rerun = [destination]
        else:
            assert replacement is not None
            destination = life.replace(ctx, session, p, r, [replacement], reason)
            rerun = [destination]
    else:
        # The remaining files form a new version of the original customer's delivery.
        items = list(session.scalars(select(BatchItem).where(BatchItem.round_id == r.id)))
        selected_items = [it for it in items if it.sha256 == f.sha256 and it.file_name == f.original_name]
        life._close(session, p, r, "superseded", reason)
        remaining = life._successor(session, r, r.customer_id, r.label, "superseded", reason)
        for other in r.files:
            if other.id != file_id:
                _copy(session, other, remaining)
        destination = remaining
        rerun = [remaining]
        if action == "replace":
            assert replacement is not None
            rs.add_file(ctx, session, remaining, *replacement)
        else:
            # A moved/cancelled file has its own history, separate from the retained files.
            detached = ImportRound(
                customer_id=customer_id if action == "move" and customer_id else r.customer_id,
                label=f.original_name,
                target_schema=r.target_schema,
                notes=r.notes,
                predecessor_id=r.id,
                summary={k: v for k, v in (r.summary or {}).items() if k in ("batch_id", "auto_publish")},
            )
            session.add(detached)
            session.flush()
            _copy(session, f, detached)
            for it in selected_items:
                it.round_id, it.customer_id = detached.id, detached.customer_id
            if action == "cancel":
                life.cancel(session, p, detached, reason)
            else:
                destination = detached
                rerun.append(detached)
            rs.audit(session, "round.create", "round", detached.id, actor=p.actor, from_round=r.id)
        rs.audit(session, "round.create", "round", remaining.id, actor=p.actor, replaces=r.id)
    session.flush()
    rs.audit(
        session,
        f"file.{action}",
        "round",
        r.id,
        actor=p.actor,
        file_id=file_id,
        file_name=f.original_name,
        reason=reason,
        new_round=destination.id,
        detached_round_id=detached.id if detached else None,
        rerun_rounds=[new.id for new in rerun],
        customer_id=customer_id,
    )
    return destination, rerun
