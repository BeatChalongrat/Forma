"""Replay completed steps; commit a tool's receipt with its database effects.

The journal is authoritative even if a worker dies before LangGraph saves its node.
An interrupted provider call is a new bounded attempt, never an exact token resume.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import json
from collections.abc import Callable
from contextvars import ContextVar
from decimal import Decimal
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from forma.ai.llm import ImagePart, StructuredResult, Usage
from forma.core.errors import NotFound, ValidationFailed
from forma.db.models import AssistantRun, AssistantTurn, LLMCall, utcnow

PROTOCOL = "assistant-replay-v1"
T = TypeVar("T")
active: ContextVar[Journal | None] = ContextVar("assistant_journal", default=None)


class RecoveryPaused(Exception):
    pass


def encode(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, dt.date):
        return {"$date": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "$type": type(value).__name__,
            "value": encode({f.name: getattr(value, f.name) for f in dataclasses.fields(value)}),
        }
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode()}
    if isinstance(value, tuple):
        return {"$tuple": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {"$dict": {str(k): encode(v) for k, v in value.items()}}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if "$dict" in value:
            return {k: decode(v) for k, v in value["$dict"].items()}
        if "$datetime" in value:
            return dt.datetime.fromisoformat(value["$datetime"])
        if "$date" in value:
            return dt.date.fromisoformat(value["$date"])
        if "$decimal" in value:
            return Decimal(value["$decimal"])
        if "$bytes" in value:
            return base64.b64decode(value["$bytes"])
        if "$tuple" in value:
            return tuple(decode(v) for v in value["$tuple"])
        if "$type" in value:
            return {"ImagePart": ImagePart, "StructuredResult": StructuredResult, "Usage": Usage}[
                value["$type"]
            ](**decode(value["value"]))
        return {k: decode(v) for k, v in value.items()}
    return value


class Journal:
    def __init__(self, session: Session, turn_id: str, settings: Any) -> None:
        self.session, self.turn_id, self.settings, self.index = session, turn_id, settings, 0

    def invoke(self, name: str, arguments: Any, fn: Callable[[], T]) -> T:
        from forma.jobs.activity import emit
        from forma.jobs.execution import check

        check()
        row = self.session.get(AssistantRun, self.turn_id, populate_existing=True)
        if row is None:
            raise RecoveryPaused("Saved work is missing")
        if row.state.get("protocol", PROTOCOL) != PROTOCOL:
            raise RecoveryPaused("The saved workflow version is no longer compatible")
        if "protocol" not in row.state:
            row.state = {**row.state, "protocol": PROTOCOL}
        turn = self.session.get(AssistantTurn, self.turn_id, populate_existing=True)
        if turn is None or turn.status == "stopped":
            from forma.ai.text_stream import StreamCancelled

            raise StreamCancelled()
        key = str(self.index)
        self.index += 1
        fingerprint = hashlib.sha256(
            json.dumps(encode(arguments), sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        entries = dict(row.state.get("steps", {}))
        entry = entries.get(key)
        if entry and (entry["name"], entry["fingerprint"]) != (name, fingerprint):
            raise RecoveryPaused("The workflow changed; start a new message to use the new version")
        if entry and entry.get("done"):
            emit("assistant.step", "replayed", detail={"step": key, "tool": name})
            if entry.get("error"):
                error = entry["error"]
                raise {"validation_failed": ValidationFailed, "not_found": NotFound}[error["code"]](
                    error["message"]
                )
            return decode(entry["result"])  # type: ignore[no-any-return]
        if (
            row.expires_at <= utcnow()
            or row.calls >= self.settings.assistant_run_max_calls
            or row.cost_usd >= self.settings.assistant_run_max_cost_usd
        ):
            raise RecoveryPaused("The saved request reached its time or usage limit")
        attempts = int((entry or {}).get("attempts", 0)) + 1
        if attempts > self.settings.assistant_step_attempts:
            raise RecoveryPaused("This step needs attention after repeated failures")
        entries[key] = {"name": name, "fingerprint": fingerprint, "attempts": attempts, "done": False}
        row.state = {**row.state, "steps": entries}
        row.revision += 1
        self.session.commit()  # reserve attempts BEFORE external work, never refund unknown calls
        emit("assistant.step", "started", detail={"step": key, "tool": name, "attempt": attempts})
        prior = self.session.info.get("assistant_atomic_tool")
        self.session.info["assistant_atomic_tool"] = True
        try:
            result = fn()
            check()
            # Functions may expire ORM objects; reload before replacing the checkpoint.
            row = self.session.get(AssistantRun, self.turn_id)
            assert row is not None
            entries = dict(row.state.get("steps", {}))
            entries[key] = {**entries[key], "done": True, "result": encode(result)}
            row.state = {**row.state, "steps": entries}
            row.revision += 1
            self.session.commit()  # effects + receipt, guarded by the worker fencing token
            emit("assistant.step", "completed", detail={"step": key, "tool": name, "attempt": attempts})
            return result
        except (ValidationFailed, NotFound) as error:
            self.session.rollback()
            row = self.session.get(AssistantRun, self.turn_id, populate_existing=True)
            assert row is not None
            entries = dict(row.state.get("steps", {}))
            entries[key] = {
                **entries[key],
                "done": True,
                "error": {"code": error.code, "message": error.message},
            }
            row.state = {**row.state, "steps": entries}
            row.revision += 1
            self.session.commit()
            raise
        except BaseException:
            self.session.rollback()
            raise
        finally:
            self.session.info["assistant_atomic_tool"] = prior

    def reserve_request(self) -> None:
        from forma.jobs.execution import check

        check()
        row = self.session.get(AssistantRun, self.turn_id, populate_existing=True)
        if (
            row is None
            or row.expires_at <= utcnow()
            or row.calls >= self.settings.assistant_run_max_calls
            or row.cost_usd >= self.settings.assistant_run_max_cost_usd
        ):
            raise RecoveryPaused("Request budget reached")
        row.calls += 1
        row.revision += 1
        self.session.commit()

    def record_usage(self, usage: object) -> None:
        if not isinstance(usage, Usage):
            return
        row = self.session.get(AssistantRun, self.turn_id, populate_existing=True)
        assert row is not None
        row.cost_usd += usage.cost_usd
        from forma.db.models import AssistantConversation

        chat = self.session.get(AssistantConversation, row.conversation_id)
        self.session.add(
            LLMCall(
                round_id=chat.round_id if chat else None,
                agent="workspace_assistant",
                model=usage.model,
                prompt_version="workspace-durable-v1",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
                latency_ms=usage.latency_ms,
                attempts=1,
            )
        )
        self.session.commit()


def step[T](name: str, arguments: Any, fn: Callable[[], T]) -> T:
    journal = active.get()
    return journal.invoke(name, arguments, fn) if journal else fn()


def context_history(
    session: Session, chat_id: str, sequence: int, recent: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Lossless user constraints within a bounded extract; older full text stays searchable.

    Deliberately not a model-invented summary. Preserve exact user language and disclose omissions.
    """
    query = select(AssistantTurn.sequence, AssistantTurn.question).where(
        AssistantTurn.conversation_id == chat_id,
        AssistantTurn.sequence < sequence,
        AssistantTurn.status.in_(("complete", "stopped")),
    )
    from sqlalchemy import func

    total = max(0, (session.scalar(select(func.count()).select_from(query.subquery())) or 0) - 8)
    if not total:
        return recent
    older = session.execute(query.order_by(AssistantTurn.sequence).limit(min(total, 200))).all()
    chunks, remaining = [], 16000
    for row in older:
        text = f"[{row.sequence}] User: {row.question}\n"
        if len(text) > remaining:
            break
        chunks.append(text)
        remaining -= len(text)
    note = (
        "Earlier user messages (historical context, not proof of current facts). Search conversation_history for missing detail.\n"
        + "".join(chunks)
    )
    if len(chunks) < total:
        note += (
            f"\n{total - len(chunks)} earlier messages omitted; retrieve before answering references to them."
        )
    return [{"role": "user", "content": note}, *recent]
