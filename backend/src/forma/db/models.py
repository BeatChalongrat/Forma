"""Persistence model (ADR-005). Everything is versioned and append-only where it matters:
source files are immutable, mappings and datasets get new versions, edits are change sets.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from forma.core.ids import new_id


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class UTCDateTime(TypeDecorator[dt.datetime]):
    """Timezone-aware UTC on every dialect (ADR-011). SQLite has no tz: naive values are *declared* UTC on
    read, and naive inputs are treated as UTC on write, so comparisons never mix aware/naive."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value

    def process_result_value(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value


# JSON on SQLite, JSONB on Postgres (indexable, binary) — one declaration, no dialect branches elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType, dt.datetime: UTCDateTime}


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cus"))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    erp_type: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # target format new rounds of this customer use ("catalog" = latest, "catalog@2" = pinned)
    default_target_schema: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    rounds: Mapped[list[ImportRound]] = relationship(back_populates="customer")


class ImportRound(Base):
    """One delivery from a customer (every ~14 days). Holds one or more source files."""

    __tablename__ = "import_rounds"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rnd"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    # uploaded → profiled → proposed → review | auto_approved → transformed → published | blocked | failed
    target_schema: Mapped[str] = mapped_column(String(64), default="product")
    notes: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="rounds")
    files: Mapped[list[SourceFile]] = relationship(back_populates="round", cascade="all, delete-orphan")


class SourceFile(Base):
    __tablename__ = "source_files"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("fil"))
    round_id: Mapped[str] = mapped_column(ForeignKey("import_rounds.id"), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    original_name: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # excel | csv
    storage_key: Mapped[str] = mapped_column(Text)  # ADR-012: storage key, never a path
    status: Mapped[str] = mapped_column(String(32), default="stored")  # stored | profiled | error | ignored
    duplicate_of: Mapped[str | None] = mapped_column(String(40))
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    drift: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    round: Mapped[ImportRound] = relationship(back_populates="files")


class Batch(Base):
    """A bulk upload (ADR-027): many files at once, each matched to its customer by format and processed
    into one JSON of its own."""

    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("bch"))
    label: Mapped[str | None] = mapped_column(String(255))
    work_date: Mapped[dt.date | None] = mapped_column(Date, index=True)
    expected_files: Mapped[int | None] = mapped_column(Integer)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    items: Mapped[list[BatchItem]] = relationship(back_populates="batch", order_by="BatchItem.created_at")


class BatchItem(Base):
    """One file of a bulk upload. Stored before its customer is known; becomes a round once matched."""

    __tablename__ = "batch_items"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("bit"))
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(Text)
    # received → (identified) needs_customer | attached → the round's own status takes over; failed
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    match: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType
    )  # known (exact / same columns / similar) | unknown
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True)
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)

    batch: Mapped[Batch] = relationship(back_populates="items")


class MappingVersion(Base):
    __tablename__ = "mapping_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("map"))
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(32), default="proposed", index=True
    )  # proposed | approved | rejected | superseded
    target_schema: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict[str, Any]] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(32), default="system")  # system | ai | human
    model: Mapped[str | None] = mapped_column(String(128))
    fingerprint_strict: Mapped[str | None] = mapped_column(String(64), index=True)
    fingerprint_loose: Mapped[str | None] = mapped_column(String(64), index=True)
    policy: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    agent_log: Mapped[list[Any] | None] = mapped_column(JSONType)
    open_questions: Mapped[list[Any] | None] = mapped_column(JSONType)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


Index("ix_mapping_customer_fp", MappingVersion.customer_id, MappingVersion.fingerprint_strict)
Index("ix_mapping_customer_status", MappingVersion.customer_id, MappingVersion.status)


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rev"))
    round_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    mapping_version_id: Mapped[str | None] = mapped_column(String(40))
    dataset_version_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(32))  # mapping | drift | data | blocked
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="open", index=True
    )  # open | approved | rejected | dismissed
    reasons: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    resolver: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)


Index("ix_review_status_priority", ReviewTask.status, ReviewTask.priority, ReviewTask.created_at)


class DatasetVersion(Base):
    """Output of running an approved (or proposed) mapping over a round, plus applied change sets."""

    __tablename__ = "dataset_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dsv"))
    round_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    mapping_version_id: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(32), default="draft", index=True
    )  # draft | approved | published | superseded
    records_key: Mapped[str] = mapped_column(Text)  # ADR-012: JSONL object key
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    findings: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


Index("ix_dataset_round_version", DatasetVersion.round_id, DatasetVersion.version)


class ChangeSet(Base):
    """A reviewed batch of edits applied to a dataset version → produces a new dataset version."""

    __tablename__ = "change_sets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("chg"))
    dataset_version_id: Mapped[str] = mapped_column(String(40), index=True)
    result_version_id: Mapped[str | None] = mapped_column(String(40))
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # one_time | persistent | mapping | copilot
    operations: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    impact: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    author: Mapped[str] = mapped_column(String(128), default="operator")
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="applied")  # previewed | applied | reverted
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class Override(Base):
    """Persistent override rule: re-applied automatically on every future import of this customer."""

    __tablename__ = "overrides"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ovr"))
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(255))
    where: Mapped[dict[str, Any]] = mapped_column(
        JSONType
    )  # {"identity": "SKU"} or DSL boolean expr over target pointers
    value: Mapped[Any] = mapped_column(JSONType)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    author: Mapped[str] = mapped_column(String(128), default="operator")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExportArtifact(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("exp"))
    dataset_version_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    fmt: Mapped[str] = mapped_column(String(16))
    storage_key: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True
    )  # queued | running | done | failed
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String(512))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    batch_id: Mapped[str | None] = mapped_column(String(40), index=True)
    worker: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)


Index("ix_jobs_status_created", Job.status, Job.created_at)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    actor: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


Index("ix_audit_entity_created", AuditLog.entity_id, AuditLog.created_at)


class LLMCall(Base):
    """Token/cost ledger for every agent call."""

    __tablename__ = "llm_calls"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("llm"))
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class Correction(Base):
    """What a human decision taught FORMA (ADR-038): a column alias or a confirmed constant for a
    customer, an approval accepted as proposed, or an alias promoted to everyone. The mapper reads it
    before it guesses."""

    __tablename__ = "corrections"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cor"))
    customer_id: Mapped[str | None] = mapped_column(String(40), index=True)  # None = promoted, for everyone
    kind: Mapped[str] = mapped_column(String(16))  # alias | constant | accepted | promoted
    target: Mapped[str | None] = mapped_column(String(255))  # target pointer
    header: Mapped[str | None] = mapped_column(String(255))  # normalized source header (alias, promoted)
    before: Mapped[Any | None] = mapped_column(JSONType)
    after: Mapped[Any | None] = mapped_column(JSONType)
    actor: Mapped[str] = mapped_column(String(128))
    mapping_version_id: Mapped[str | None] = mapped_column(String(40))
    round_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="confirmed", server_default="confirmed")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (
        Index("ix_corrections_kind_status", "kind", "status"),
        Index("ix_corrections_header_target", "header", "target"),
    )


class AppSetting(Base):
    """Runtime configuration edited in the web app (ADR-017): AI provider, approval policy, defaults.
    Secrets are stored encrypted; the ``revision`` row tells every process when to reload."""

    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class StoredSchema(Base):
    """A target schema registered from the web app. Versions are append-only; the files under
    ``target_schema/`` stay the read-only built-ins."""

    __tablename__ = "target_schemas"
    schema_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("chat"))
    owner_key: Mapped[str] = mapped_column(String(64))
    access_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    round_id: Mapped[str | None] = mapped_column(String(40))
    dataset_id: Mapped[str | None] = mapped_column(String(40))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    legacy_key: Mapped[str | None] = mapped_column(String(64))
    active_run: Mapped[str | None] = mapped_column(String(40))
    busy_until: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    __table_args__ = (
        Index("ix_assistant_owner_access_updated", "owner_key", "access_key", "updated_at"),
        UniqueConstraint("owner_key", "access_key", "legacy_key", name="uq_assistant_legacy"),
    )


class AssistantTurn(Base):
    __tablename__ = "assistant_turns"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("turn"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("assistant_conversations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    reply: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_id", name="uq_assistant_request"),
        UniqueConstraint("conversation_id", "sequence", name="uq_assistant_sequence"),
    )
