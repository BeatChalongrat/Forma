"""Send a file back to the AI once the AI answers again (ADR-077).

When the AI fails, times out or gives an answer FORMA rejects, the round gets FORMA's own draft and
goes to a person (ADR-013, ADR-015). That stays. What was missing: nothing sent the file back to the
AI when the provider recovered, so after an outage a person had to find those files and run them
again by hand. This module does it, under rules that keep people's work and the queue safe:

- Only the round's current proposal, only a fallback draft, and only while nobody has acted on its
  card: the task is open (not answered, edited, approved, rejected or parked for the customer).
- Only after the provider answers a probe, and only while no fresh AI work waits in the queue:
  catching up fills idle capacity and never delays a new upload.
- A bounded number of times per round, waiting longer each time. An outage gets
  ``ai_retry_max_attempts``; an answer FORMA rejected (invalid output, a refusal, the workflow's own
  attempt cap) gets one, because asking again rarely changes it.
- The job and its claim on the round are written in one transaction (the outbox). While the claim
  runs, people's actions on the old card are refused with a reason instead of racing the new
  proposal. The old proposal is superseded and its card dismissed in the transaction that writes the
  new one, and only if the round is still the way the claim found it.
- Nothing is approved here. The new proposal meets the same policy as any other; when the policy
  approves it, the rest of the pipeline (transform, and publish if the round asked for it) is queued.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from forma.core.errors import Conflict
from forma.core.logging import get_logger
from forma.core.settings import Settings
from forma.db.models import REVIEW_UNRESOLVED, ImportRound, Job, MappingVersion, ReviewTask
from forma.services.context import ServiceContext

log = get_logger(__name__)

KEY = "ai_retry"  # the claim, kept in ImportRound.summary
JOB_KIND = "retry_ai_mapping"
ACTOR = "system:ai_retry"
AI_KINDS = ("run_pipeline", "process_batch_item", "propose_mapping")  # work that waits for the AI
LIVE = ("queued", "running")
RETRY_STATUSES = ("review", "blocked")
GROWTH = 3  # each retry waits this many times longer than the one before

_UNAVAILABLE = re.compile(
    r"HTTP (?:429|5\d\d)|circuit|timed out|timeout|deadline|capacity|unavailable|connect|busy|already running",
    re.IGNORECASE,
)
_REJECTED = re.compile(
    r"invalid JSON|cut off|declined|refusal|failed validation|malformed|no answer content|empty answer"
    r"|no completion choices|attempt/time budget exhausted",
    re.IGNORECASE,
)


class Overtaken(Exception):
    """The round changed while the AI worked; the new proposal is thrown away."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(d: dt.datetime) -> dt.datetime:
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


# --------------------------------------------------------------------------------------------
# what a fallback is, and how often it may be retried
# --------------------------------------------------------------------------------------------
def failure(mv: MappingVersion) -> str | None:
    """Why the AI did not write this proposal, or None when it is not a fallback draft."""
    policy = mv.policy or {}
    if policy.get("source") != "heuristic" or not (policy.get("checks") or {}).get("llm_fallback"):
        return None
    for entry in mv.agent_log or []:
        for note in entry.get("notes") or []:
            if isinstance(note, str) and note.startswith("llm_failed"):
                return note.removeprefix("llm_failed:").strip()
        if entry.get("agent") == "llm" and entry.get("error"):
            return str(entry["error"])
    return "unknown"


def kind_of(reason: str) -> str:
    """``unavailable`` (the AI could not be reached or ran out of time) or ``rejected`` (it answered and
    FORMA refused the answer). Anything unrecognised counts as unavailable."""
    text = reason.replace("every AI route failed", "")
    if _UNAVAILABLE.search(text):
        return "unavailable"
    return "rejected" if _REJECTED.search(text) else "unavailable"


def allowed(settings: Settings, reason: str) -> int:
    n = settings.ai_retry_max_attempts
    return min(n, 1) if kind_of(reason) == "rejected" else n


def claim_of(r: ImportRound) -> dict[str, Any]:
    return dict((r.summary or {}).get(KEY) or {})


def spent(claim: dict[str, Any], mapping_id: str) -> int:
    """Retries already spent on this line of fallback drafts. A fallback that no retry of FORMA's
    produced (a person ran the round again, say) starts the count over."""
    if mapping_id in (claim.get("replaces"), claim.get("produced")):
        return int(claim.get("attempts") or 0)
    return 0


def next_at(settings: Settings, mv: MappingVersion, attempts: int) -> dt.datetime:
    return _aware(mv.created_at) + dt.timedelta(seconds=settings.ai_retry_after_s * GROWTH**attempts)


def untouched(session: Session, mapping_id: str) -> bool:
    statuses = list(
        session.scalars(
            select(ReviewTask.status).where(
                ReviewTask.mapping_version_id == mapping_id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
            )
        )
    )
    return bool(statuses) and all(s == "open" for s in statuses)


def _job_live(session: Session, job_id: str | None) -> bool:
    return bool(job_id) and session.scalar(select(Job.status).where(Job.id == job_id)) in LIVE


def _round_busy(session: Session, round_id: str) -> bool:
    return bool(
        session.scalar(
            select(func.count()).select_from(Job).where(Job.round_id == round_id, Job.status.in_(LIVE))
        )
    )


def backlog(session: Session) -> int:
    """Fresh AI work waiting for a worker. Retries wait until there is none."""
    return int(
        session.scalar(
            select(func.count()).select_from(Job).where(Job.status == "queued", Job.kind.in_(AI_KINDS))
        )
        or 0
    )


def in_flight(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Job).where(Job.kind == JOB_KIND, Job.status.in_(LIVE))
        )
        or 0
    )


def due(session: Session, settings: Settings, now: dt.datetime | None = None) -> list[tuple[str, str]]:
    """(round id, fallback mapping id) of every round that should go back to the AI now, oldest first."""
    now = now or _now()
    since = now - dt.timedelta(hours=settings.ai_retry_window_h)
    out: list[tuple[str, str]] = []
    # FORMA's own drafts are written by "system"; the AI's by "ai" and people's by "human"
    candidates = session.scalars(
        select(MappingVersion)
        .where(
            MappingVersion.status == "proposed",
            MappingVersion.created_by == "system",
            MappingVersion.round_id.is_not(None),
            MappingVersion.created_at >= since,
        )
        .order_by(MappingVersion.created_at)
    )
    for mv in candidates:
        reason = failure(mv)
        if reason is None or mv.round_id is None:
            continue
        r = session.get(ImportRound, mv.round_id)
        if (
            r is None
            or r.status not in RETRY_STATUSES
            or (r.summary or {}).get("mapping_version_id") != mv.id
        ):
            continue
        claim = claim_of(r)
        if claim.get("state") == "running":
            continue
        attempts = spent(claim, mv.id)
        if attempts >= allowed(settings, reason) or now < next_at(settings, mv, attempts):
            continue
        if not untouched(session, mv.id) or _round_busy(session, r.id):
            continue
        out.append((r.id, mv.id))
    return out


# --------------------------------------------------------------------------------------------
# the sweep: run from the job recovery loop
# --------------------------------------------------------------------------------------------
def release_stale(session: Session, settings: Settings, now: dt.datetime | None = None) -> list[str]:
    """A claim whose job ended without finishing it (failed for good, cancelled) frees the round: the
    attempt counts, and the next one waits as if it had failed."""
    now = now or _now()
    since = now - dt.timedelta(hours=settings.ai_retry_window_h)
    ended = session.execute(
        select(Job.id, Job.round_id).where(
            Job.kind == JOB_KIND, Job.status.not_in(LIVE), Job.created_at >= since, Job.round_id.is_not(None)
        )
    ).all()
    released = []
    for job_id, round_id in ended:
        r = session.get(ImportRound, round_id)
        claim = claim_of(r) if r is not None else {}
        if r is None or claim.get("state") != "running" or claim.get("job_id") != job_id:
            continue
        r.summary = {**(r.summary or {}), KEY: {**claim, "state": "waiting", "finished_at": now.isoformat()}}
        released.append(r.id)
    return released


def stage(session: Session, settings: Settings, round_id: str, mapping_id: str) -> str | None:
    """Claim one round and stage its retry job in the same transaction; None when it no longer
    qualifies (someone acted on it since ``due`` looked)."""
    from forma.jobs import runner
    from forma.services.round_lifecycle import is_closed, lock_round

    r = lock_round(session, round_id, require_open=False)
    mv = session.get(MappingVersion, mapping_id)
    if (
        is_closed(r)
        or mv is None
        or mv.status != "proposed"
        or r.status not in RETRY_STATUSES
        or (r.summary or {}).get("mapping_version_id") != mapping_id
    ):
        return None
    reason = failure(mv)
    claim = claim_of(r)
    attempts = spent(claim, mapping_id)
    if (
        reason is None
        or claim.get("state") == "running"
        or attempts >= allowed(settings, reason)
        or not untouched(session, mapping_id)
        or _round_busy(session, round_id)
    ):
        return None
    attempt = attempts + 1
    job_id = runner.stage_job(
        session,
        JOB_KIND,
        {"round_id": round_id, "replaces": mapping_id, "attempt": attempt, "actor": ACTOR},
        round_id=round_id,
    )
    r.summary = {
        **(r.summary or {}),
        KEY: {
            "state": "running",
            "job_id": job_id,
            "attempts": attempt,
            "allowed": allowed(settings, reason),
            "replaces": mapping_id,
            "reason": reason[:300],
            "kind": kind_of(reason),
            "started_at": _now().isoformat(),
        },
    }
    from forma.services.rounds import audit

    audit(
        session,
        "mapping.ai_retry",
        "round",
        round_id,
        actor=ACTOR,
        replaces=mapping_id,
        attempt=attempt,
        reason=reason[:300],
    )
    return job_id


_LAST_SWEEP = 0.0
_SWEEP_LOCK = threading.Lock()


def maybe_sweep(ctx: ServiceContext, *, clock: Callable[[], float] = time.monotonic) -> dict[str, Any] | None:
    """The recovery loop's hook: at most one sweep per ``ai_retry_interval_s`` in this process."""
    global _LAST_SWEEP
    settings = ctx.settings
    with _SWEEP_LOCK:
        if clock() - _LAST_SWEEP < settings.ai_retry_interval_s:
            return None
        _LAST_SWEEP = clock()
    return sweep(ctx)


def provider_answers(ctx: ServiceContext) -> bool:
    """One tiny call to the primary route and each backup key: does any of them answer?"""
    from forma.services.config import probe_all

    out = probe_all(ctx.llm)
    return bool(out.get("ok")) or any(b.get("ok") for b in out.get("backups") or [])


def sweep(
    ctx: ServiceContext,
    *,
    now: dt.datetime | None = None,
    probe: Callable[[ServiceContext], bool] = provider_answers,
) -> dict[str, Any]:
    """Find rounds to send back to the AI; when the AI answers and the queue is free, stage them."""
    from forma.db.session import session_scope
    from forma.jobs import runner

    settings = ctx.settings
    if not settings.ai_retry_enabled or settings.ai_retry_max_attempts <= 0 or ctx.llm.is_mock:
        return {"state": "off"}
    with session_scope() as s:
        released = release_stale(s, settings, now)
        found = due(s, settings, now)
        waiting = backlog(s)
        room = settings.ai_retry_parallel - in_flight(s)
    result: dict[str, Any] = {"due": len(found), "released": released}
    if not found:
        return {**result, "state": "nothing_due"}
    if waiting:
        return {**result, "state": "queue_busy", "queued": waiting}
    if room <= 0:
        return {**result, "state": "retries_running"}
    if not probe(ctx):
        log.info("ai retry: provider still unavailable", due=len(found))
        return {**result, "state": "provider_unavailable"}
    staged = []
    for round_id, mapping_id in found[:room]:
        try:
            with session_scope() as s:
                job_id = stage(s, settings, round_id, mapping_id)
        except Exception as e:  # a full queue or a busy row: the next sweep tries again
            log.warning("ai retry not staged", round_id=round_id, error=str(e)[:200])
            continue
        if job_id:
            staged.append(job_id)
    runner.deliver_jobs(staged)
    if staged:
        log.info("ai retry: files sent back to the AI", jobs=len(staged), due=len(found))
    return {**result, "state": "staged", "jobs": staged}


# --------------------------------------------------------------------------------------------
# the job
# --------------------------------------------------------------------------------------------
def run(ctx: ServiceContext, session: Session, payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Propose again with the AI and replace the fallback. The round stays locked from the first read to
    the commit, so nothing changes it in between: people are refused by the claim before they wait for
    the lock (``refuse_while_running``), and closing the round cancels this job, whose commit is fenced.
    Raises ``Overtaken`` (the caller rolls back) when the round is no longer the way the claim found it."""
    from forma.jobs import runner
    from forma.services import mapping as ms
    from forma.services import verdict_flow
    from forma.services.round_lifecycle import is_closed, lock_round
    from forma.services.rounds import audit

    round_id, old_id = payload["round_id"], payload["replaces"]
    r = lock_round(session, round_id, require_open=False)
    claim = claim_of(r)
    if claim.get("job_id") != job_id or claim.get("state") != "running":
        return {"round_id": round_id, "state": "skipped", "why": "the claim on this round is not this job's"}
    old = session.get(MappingVersion, old_id)
    if (
        is_closed(r)
        or old is None
        or old.status != "proposed"
        or (r.summary or {}).get("mapping_version_id") != old_id
    ):
        raise Overtaken("the proposal this retry replaces is no longer the round's current one")

    mv = ms.propose_for_round(ctx, session, round_id, use_llm=True, actor=ACTOR)

    now = _now()
    old.status = "superseded"
    for t in session.scalars(
        select(ReviewTask).where(
            ReviewTask.mapping_version_id == old_id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
        )
    ):
        t.status = "dismissed"
        t.resolver = ACTOR
        t.comment = "replaced by the AI's proposal after the AI answered again"
        t.resolved_at = now

    attempt = int(claim.get("attempts") or payload.get("attempt") or 1)
    again = failure(mv)
    if again is None:
        outcome = "done"
    else:
        limit = allowed(ctx.settings, again)
        outcome = "waiting" if attempt < limit else "gave_up"
        reason = f"AI retry {attempt} of {limit} after the AI was unavailable did not succeed; " + (
            "FORMA will try again later" if outcome == "waiting" else "FORMA stopped retrying"
        )
        mv.policy = {**(mv.policy or {}), "reasons": [*(mv.policy or {}).get("reasons", []), reason]}
        task = session.scalar(
            select(ReviewTask).where(
                ReviewTask.mapping_version_id == mv.id, ReviewTask.status.in_(REVIEW_UNRESOLVED)
            )
        )
        if task is not None:
            task.reasons = [*(task.reasons or []), reason]
        verdict_flow.attach_to_mapping(ctx, session, mv, r, task)
    mv.policy = {
        **(mv.policy or {}),
        KEY: {"replaces": old_id, "attempt": attempt, "outcome": outcome},
    }
    mv.agent_log = [
        *(mv.agent_log or []),
        {"agent": KEY, "replaces": old_id, "attempt": attempt, "outcome": outcome},
    ]
    r.summary = {
        **(r.summary or {}),
        KEY: {**claim, "state": outcome, "produced": mv.id, "finished_at": now.isoformat()},
    }
    follow_up = None
    if mv.status == "approved":
        # the policy approved the AI's proposal: finish what the upload asked for
        follow_up = runner.stage_job(
            session,
            "run_pipeline",
            {
                "round_id": round_id,
                "actor": ACTOR,
                "auto_publish": bool((r.summary or {}).get("auto_publish")),
            },
            round_id=round_id,
            check=False,
        )
    audit(
        session,
        "mapping.ai_retry_done",
        "mapping",
        mv.id,
        actor=ACTOR,
        round_id=round_id,
        replaces=old_id,
        attempt=attempt,
        outcome=outcome,
        status=mv.status,
    )
    return {
        "round_id": round_id,
        "state": outcome,
        "mapping_version_id": mv.id,
        "mapping_status": mv.status,
        "replaces": old_id,
        "attempt": attempt,
        "follow_up_job": follow_up,
    }


def release(session: Session, round_id: str, job_id: str, why: str) -> dict[str, Any]:
    """After ``Overtaken``: free the round without touching anything else on it."""
    from forma.services.rounds import audit, get_round

    r = get_round(session, round_id)
    claim = claim_of(r)
    if claim.get("job_id") == job_id and claim.get("state") == "running":
        r.summary = {
            **(r.summary or {}),
            KEY: {**claim, "state": "overtaken", "finished_at": _now().isoformat(), "why": why},
        }
    audit(session, "mapping.ai_retry_overtaken", "round", round_id, actor=ACTOR, job_id=job_id, why=why)
    return {"round_id": round_id, "state": "overtaken", "why": why}


# --------------------------------------------------------------------------------------------
# people's actions while a retry runs
# --------------------------------------------------------------------------------------------
def refuse_while_running(session: Session, round_id: str | None) -> None:
    """A person's change to a round the AI is reading again would race the new proposal: refuse it,
    and say when to come back."""
    if not round_id:
        return
    r = session.get(ImportRound, round_id)
    claim = claim_of(r) if r is not None else {}
    if claim.get("state") == "running" and _job_live(session, claim.get("job_id")):
        raise Conflict(
            "The AI is reading this file again now that it answers; this card will be replaced in a few "
            "minutes. Reload then.",
            details={"round_id": round_id, "job_id": claim.get("job_id")},
        )
