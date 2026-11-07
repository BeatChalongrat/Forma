"""Accepted questions survive HTTP disconnects and process restarts."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from forma.ai.text_stream import AnswerStream
from forma.ai.workflow_store import workflow_storage
from forma.auth import Principal
from forma.core.errors import Conflict
from forma.db.models import AssistantConversation as Chat
from forma.db.models import AssistantRun, Job, utcnow
from forma.db.models import AssistantTurn as Turn
from forma.db.session import session_scope
from forma.jobs import runner
from forma.jobs.execution import interruptible_sleep
from forma.services import artifacts, attachments, conversations
from forma.services.assistant_runtime import Journal, active
from forma.services.context import ServiceContext

KIND = "assistant_message"


def accept(
    ctx: ServiceContext, s: Session, p: Principal, chat_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    chat = conversations.get_chat(s, p, chat_id)
    s.execute(update(Chat).where(Chat.id == chat.id).values(updated_at=Chat.updated_at))
    s.refresh(chat)
    existing = s.scalar(
        select(Turn).where(Turn.conversation_id == chat_id, Turn.request_id == body["request_id"])
    )
    if existing:
        if existing.question != body["question"]:
            raise Conflict("This request belongs to a different question")
        run = s.get(AssistantRun, existing.id)
        if existing.status not in ("complete", "stopped"):
            other = s.scalar(
                select(Job.id)
                .join(AssistantRun, AssistantRun.job_id == Job.id)
                .where(
                    AssistantRun.conversation_id == chat_id,
                    AssistantRun.turn_id != existing.id,
                    Job.status.in_(("queued", "running")),
                )
                .limit(1)
            )
            if other or chat.archived:
                raise Conflict("Another reply is running, or this chat is archived")
        if run is None and existing.status not in ("complete", "stopped"):
            if chat.busy_until and chat.busy_until > utcnow():
                raise Conflict("A reply is still running")
            jid = runner.stage_job(
                s,
                KIND,
                {"chat_id": chat_id, "turn_id": existing.id, "principal": p.to_dict(), "body": body},
                request_id=p.request_id,
            )
            run = AssistantRun(
                turn_id=existing.id,
                conversation_id=chat_id,
                job_id=jid,
                expires_at=utcnow() + timedelta(seconds=ctx.settings.assistant_run_timeout_s),
            )
            s.add(run)
            existing.status = "pending"
            chat.active_run, chat.busy_until = jid, run.expires_at
        if existing.status == "failed" and run:
            job = s.get(Job, run.job_id)
            if job and job.status in ("failed", "done"):
                # Retry cannot reset the saved per-step or total budget.
                job.status, job.error, job.dispatched_at = "queued", None, None
                existing.status = "pending"
                chat.active_run, chat.busy_until = job.id, run.expires_at
        s.commit()
        if run:
            runner.deliver_jobs([run.job_id])
        return conversations.message_progress(s, p, chat_id, body["request_id"])
    if chat.archived or (chat.busy_until and chat.busy_until > utcnow()):
        raise Conflict("A reply is already running, or this chat is archived")
    if s.scalar(
        select(Job.id)
        .join(AssistantRun, AssistantRun.job_id == Job.id)
        .where(AssistantRun.conversation_id == chat_id, Job.status.in_(("queued", "running")))
        .limit(1)
    ):
        raise Conflict("A saved reply is still running or recovering")
    if source_id := body.get("clarification_turn_id"):
        source = s.get(Turn, source_id)
        if (
            source is None
            or source.conversation_id != chat_id
            or source.status != "complete"
            or not (source.reply or {}).get("clarifications")
        ):
            from forma.core.errors import ValidationFailed

            raise ValidationFailed("These questions no longer belong to an available answer in this chat")
    attachments.check(s, chat_id, body.get("attachment_ids", []))
    if body.get("artifact_id"):
        artifacts.working_on(s, chat_id, body["artifact_id"], body.get("target", ""))
    sequence = (s.scalar(select(func.max(Turn.sequence)).where(Turn.conversation_id == chat_id)) or 0) + 1
    turn = Turn(
        conversation_id=chat_id, request_id=body["request_id"], sequence=sequence, question=body["question"]
    )
    s.add(turn)
    s.flush()
    turn.attachments = attachments.claim(s, chat_id, turn.id, body.get("attachment_ids", []))
    jid = runner.stage_job(
        s,
        KIND,
        {"chat_id": chat_id, "turn_id": turn.id, "principal": p.to_dict(), "body": body},
        request_id=p.request_id,
    )
    expires = utcnow() + timedelta(seconds=ctx.settings.assistant_run_timeout_s)
    s.add(AssistantRun(turn_id=turn.id, conversation_id=chat_id, job_id=jid, expires_at=expires))
    chat.active_run, chat.busy_until, chat.updated_at = jid, expires, utcnow()
    if sequence == 1 and chat.title == "New chat":
        chat.title = body["question"].replace("\n", " ")[:80]
    s.commit()
    runner.deliver_jobs([jid])
    return conversations.message_progress(s, p, chat_id, body["request_id"])


class GraphState(TypedDict, total=False):
    turn_id: str
    finished: bool


@runner.handler(KIND)
def execute(ctx: ServiceContext, payload: dict[str, Any], prog: runner.JobProgress) -> dict[str, Any]:
    raw = payload["principal"]
    p = Principal(
        subject=raw["subject"],
        roles=frozenset(raw["roles"]),
        customers=frozenset(raw["customers"]) if raw["customers"] is not None else None,
        name=raw["name"],
        mode=raw["mode"],
    )
    body = payload["body"]

    def answer(_: GraphState) -> GraphState:
        for _retry in range(ctx.settings.assistant_step_attempts):
            with session_scope() as s:
                conversations.get_chat(s, p, payload["chat_id"])
                turn = s.get(Turn, payload["turn_id"])
                if turn is None or turn.status in ("stopped", "complete"):
                    return {"finished": True}
                run = s.get(AssistantRun, turn.id)
                assert run is not None
                retry_at = run.state.get("retry_at")
                if retry_at:
                    interruptible_sleep(max(0, (datetime.fromisoformat(retry_at) - utcnow()).total_seconds()))
                journal = Journal(s, turn.id, ctx.settings)
                token = active.set(journal)
                from forma.ai.budget import request_guard, task_budget, usage_observer

                guard_token = request_guard.set(journal.reserve_request)
                usage_token = usage_observer.set(journal.record_usage)
                try:
                    with task_budget(max(0, (run.expires_at - utcnow()).total_seconds())):
                        result = conversations.send(
                            ctx,
                            s,
                            p,
                            payload["chat_id"],
                            body["question"],
                            body["request_id"],
                            body.get("language", "th"),
                            body.get("timezone", "Asia/Bangkok"),
                            stream=AnswerStream(lambda event: None) if body.get("_stream", True) else None,
                            attachment_ids=body.get("attachment_ids"),
                            artifact_id=body.get("artifact_id"),
                            target=body.get("target", ""),
                            prepared_job=prog.job_id,
                        )
                    if result["status"] != "failed":
                        return {"finished": True}
                    if result.get("reply", {}).get("reason") in ("recovery_paused", "not_configured"):
                        break
                    run = s.get(AssistantRun, turn.id, populate_existing=True)
                    assert run is not None
                    failures = int(run.state.get("retries", 0)) + 1
                    run.state = {
                        **run.state,
                        "retries": failures,
                        "retry_at": (
                            utcnow()
                            + timedelta(seconds=min(8, 2 ** min(failures - 1, 3)) + random.random() * 0.25)
                        ).isoformat(),
                    }
                    s.commit()
                finally:
                    request_guard.reset(guard_token)
                    usage_observer.reset(usage_token)
                    active.reset(token)
        return {"finished": True}

    # The graph owns stage continuity; the tool journal makes replay inside a node idempotent.
    graph = StateGraph(GraphState)
    graph.add_node("answer", RunnableLambda(answer))
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    with workflow_storage(ctx.settings, "assistant-" + payload["turn_id"]) as (saver, _):
        app = graph.compile(checkpointer=saver)
        config: RunnableConfig = {"configurable": {"thread_id": "assistant-" + payload["turn_id"]}}
        saved = app.get_state(config)
        # A deliberate retry starts a new graph execution, retaining tool receipts and budgets.
        app.invoke(None if saved.next else GraphState(turn_id=payload["turn_id"]), config)
    with session_scope() as s:
        turn = s.get(Turn, payload["turn_id"])
        return {"turn_id": payload["turn_id"], "status": turn.status if turn else "deleted"}
