"""API request/response models (thin; domain objects are serialised by `serialize.py`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CustomerIn(BaseModel):
    code: str
    name: str
    erp_type: str | None = None
    notes: str | None = None


class RoundIn(BaseModel):
    customer_id: str
    label: str | None = None
    target_schema: str = "product"


class Decision(BaseModel):
    actor: str = "operator"
    comment: str | None = None


class MappingEditIn(BaseModel):
    document: dict[str, Any]
    actor: str = "operator"


class MappingPreviewIn(BaseModel):
    document: dict[str, Any]
    limit: int = Field(default=200, le=2000)


class SourcePreviewIn(BaseModel):
    source: dict[str, Any]
    limit: int = Field(default=100, le=1000)


class ChangesIn(BaseModel):
    operations: list[dict[str, Any]]
    kind: str = "one_time"  # one_time | persistent | copilot
    actor: str = "operator"
    note: str | None = None


class CopilotIn(BaseModel):
    instruction: str
    actor: str = "operator"


class SchemaIn(BaseModel):
    document: dict[str, Any]


class ProposeIn(BaseModel):
    use_llm: bool | None = None
    actor: str = "operator"
    sync: bool = False


class BatchRunIn(BaseModel):
    round_ids: list[str] = Field(min_length=1, max_length=2000)
    use_llm: bool | None = None
    actor: str = "operator"
    force: bool = False
    sync: bool = False
