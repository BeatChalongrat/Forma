"""Persistence model (ADR-005). Everything is versioned and append-only where it matters:
source files are immutable, mappings and datasets get new versions, edits are change sets.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from forma.core.ids import new_id


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cus"))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    erp_type: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

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
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="stored")  # stored | profiled | error | ignored
    duplicate_of: Mapped[str | None] = mapped_column(String(40))
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fingerprint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    drift: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    round: Mapped[ImportRound] = relationship(back_populates="files")


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
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(32), default="system")  # system | ai | human
    model: Mapped[str | None] = mapped_column(String(128))
    fingerprint_strict: Mapped[str | None] = mapped_column(String(64), index=True)
    fingerprint_loose: Mapped[str | None] = mapped_column(String(64), index=True)
    policy: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    agent_log: Mapped[list[Any] | None] = mapped_column(JSON)
    open_questions: Mapped[list[Any] | None] = mapped_column(JSON)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_mapping_customer_fp", MappingVersion.customer_id, MappingVersion.fingerprint_strict)


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
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolver: Mapped[str | None] = mapped_column(String(128))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


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
    records_path: Mapped[str] = mapped_column(Text)  # JSONL on disk
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    findings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(128), default="system")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeSet(Base):
    """A reviewed batch of edits applied to a dataset version → produces a new dataset version."""

    __tablename__ = "change_sets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("chg"))
    dataset_version_id: Mapped[str] = mapped_column(String(40), index=True)
    result_version_id: Mapped[str | None] = mapped_column(String(40))
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # one_time | persistent | mapping | copilot
    operations: Mapped[list[Any]] = mapped_column(JSON, default=list)
    impact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    author: Mapped[str] = mapped_column(String(128), default="operator")
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="applied")  # previewed | applied | reverted
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Override(Base):
    """Persistent override rule: re-applied automatically on every future import of this customer."""

    __tablename__ = "overrides"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ovr"))
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(255))
    where: Mapped[dict[str, Any]] = mapped_column(
        JSON
    )  # {"identity": "SKU"} or DSL boolean expr over target pointers
    value: Mapped[Any] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    author: Mapped[str] = mapped_column(String(128), default="operator")
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExportArtifact(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("exp"))
    dataset_version_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    fmt: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(Text)
    row_count: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True
    )  # queued | running | done | failed
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("aud"))
    actor: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(40), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
