"""API request/response models (thin; domain objects are serialised by `serialize.py`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CustomerIn(BaseModel):
    code: str
    name: str
    erp_type: str | None = None
    notes: str | None = None


class BatchAssignIn(BaseModel):
    """Give files of a bulk upload that matched no format a customer: an existing one or a new one."""

    item_ids: list[str] = Field(min_length=1, max_length=1000)
    customer_id: str | None = None
    new_customer: CustomerIn | None = None
    use_llm: bool | None = None
    sync: bool = False


class RoundIn(BaseModel):
    customer_id: str
    label: str | None = None
    target_schema: str | None = None  # None → the customer's format, else the default format


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class Decision(BaseModel):
    actor: str = "operator"
    comment: str | None = None
    override_block: bool = False  # approve a policy-blocked mapping (approver role + comment required)
    acknowledge_anomalies: bool = False  # approve data that has high-severity anomalies (ADR-022)


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


class CustomerPatch(BaseModel):
    """Only the fields sent are changed (``model_fields_set``); ``null`` clears an optional one."""

    code: str | None = None
    name: str | None = None
    erp_type: str | None = None
    notes: str | None = None
    active: bool | None = None
    default_target_schema: str | None = None


class AISettingsIn(BaseModel):
    """Only the fields sent are changed. ``null``/"" puts a field back to the server value;
    ``api_key`` replaces the saved key, "" or ``null`` removes it."""

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_s: float | None = None
    max_retries: int | None = None
    api_key: str | None = None


class PolicyIn(BaseModel):
    policy: dict[str, Any]


class DefaultFormatIn(BaseModel):
    target_schema: str


class ConnectAIIn(BaseModel):
    api_key: str
    provider: str | None = None  # only when the key's prefix does not tell (gateways)


class BackupKeyIn(BaseModel):
    api_key: str
    provider: str | None = None  # only when the key's prefix does not tell (gateways)
    model: str | None = None  # default: the provider's default model
    base_url: str | None = None


class BackupKeyPatchIn(BaseModel):
    """Only the fields sent are changed."""

    model: str | None = None
    base_url: str | None = None


class BackupOrderIn(BaseModel):
    ids: list[str] = Field(max_length=16)


class CompareIn(BaseModel):
    document: dict[str, Any] | None = None  # unsaved edits; omitted → the mapping as saved
    limit: int = Field(default=5000, ge=1, le=200_000)


class RelEndIn(BaseModel):
    node: str
    column: str


class RelProbeIn(BaseModel):
    """A connection drawn by hand in the Studio, measured before it is added to the mapping."""

    from_: RelEndIn = Field(alias="from")
    to: RelEndIn


class RelationshipsIn(BaseModel):
    document: dict[str, Any] | None = None  # the Studio's current (possibly unsaved) mapping
    limit: int = Field(default=20_000, ge=1, le=200_000)
    probe: RelProbeIn | None = None
