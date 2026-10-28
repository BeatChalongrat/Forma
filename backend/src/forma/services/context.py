"""Shared service context: settings, registry, storage, file store, policy, LLM client."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from forma.ai.llm import LLMClient
from forma.ai.policy import PolicyEngine
from forma.core.settings import Settings, get_settings
from forma.db.models import DatasetVersion
from forma.ingestion.store import FileStore
from forma.schema_registry import SchemaRegistry
from forma.storage import Storage, make_storage

DATASETS_PREFIX = "datasets"
EXPORTS_PREFIX = "exports"


@dataclass
class ServiceContext:
    settings: Settings
    registry: SchemaRegistry
    storage: Storage
    files: FileStore
    policy: PolicyEngine
    llm: LLMClient

    # ---- storage keys (ADR-012): services never build filesystem paths ------------------------
    @staticmethod
    def dataset_key(customer_id: str, version_id: str) -> str:
        return f"{DATASETS_PREFIX}/{customer_id}/{version_id}.jsonl"

    @staticmethod
    def export_key(customer_id: str, name: str) -> str:
        return f"{EXPORTS_PREFIX}/{customer_id}/{name}"

    def records_path(self, dv: DatasetVersion) -> Path:
        """Local file for a dataset version (the object itself on local storage, a cached copy on S3)."""
        if not dv.records_key:
            raise FileNotFoundError(f"dataset version {dv.id} has no records (purged?)")
        return self.storage.local_path(dv.records_key)

    @property
    def tmp_dir(self) -> Path:
        d = self.settings.data_path / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_context() -> ServiceContext:
    s = get_settings()
    storage = make_storage(s)
    return ServiceContext(
        settings=s,
        registry=SchemaRegistry(s.resolve(s.target_schema_dir)),
        storage=storage,
        files=FileStore(storage),
        policy=PolicyEngine.load(s.resolve(s.policies_dir)),
        llm=LLMClient.from_settings(s),
    )
