"""Shared service context: settings, registry, file store, policy, LLM client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from forma.ai.llm import LLMClient
from forma.ai.policy import PolicyEngine
from forma.core.settings import Settings, get_settings
from forma.ingestion.store import FileStore
from forma.schema_registry import SchemaRegistry


@dataclass
class ServiceContext:
    settings: Settings
    registry: SchemaRegistry
    files: FileStore
    policy: PolicyEngine
    llm: LLMClient

    @property
    def datasets_dir(self) -> Path:
        d = self.settings.data_path / "datasets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def exports_dir(self) -> Path:
        d = self.settings.data_path / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_context() -> ServiceContext:
    s = get_settings()
    return ServiceContext(
        settings=s,
        registry=SchemaRegistry(s.resolve(s.target_schema_dir)),
        files=FileStore(s.data_path / "files"),
        policy=PolicyEngine.load(s.resolve(s.policies_dir)),
        llm=LLMClient.from_settings(s),
    )
