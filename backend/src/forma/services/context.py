"""Shared service context: settings, registry, storage, file store, policy, LLM client.

The registry, policy and LLM client follow the configuration saved in the web app (ADR-017): each
access checks the config revision at most every ``FORMA_CONFIG_REFRESH_S`` seconds and rebuilds those
three when it changed, so the API and every worker pick up an edit without a restart.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
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
    storage: Storage
    files: FileStore
    _registry: SchemaRegistry | None = None
    _policy: PolicyEngine | None = None
    _llm: LLMClient | None = None
    _revision: int | None = None
    _checked: float = 0.0
    _pinned: set[str] = field(default_factory=set)  # parts a test or caller set explicitly
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # ---- configuration that follows the web app ------------------------------------------------
    @property
    def registry(self) -> SchemaRegistry:
        self.sync()
        assert self._registry is not None
        return self._registry

    @registry.setter
    def registry(self, value: SchemaRegistry) -> None:
        self._registry = value
        self._pinned.add("registry")

    @property
    def policy(self) -> PolicyEngine:
        self.sync()
        assert self._policy is not None
        return self._policy

    @policy.setter
    def policy(self, value: PolicyEngine) -> None:
        self._policy = value
        self._pinned.add("policy")

    @property
    def llm(self) -> LLMClient:
        self.sync()
        assert self._llm is not None
        return self._llm

    @llm.setter
    def llm(self, value: LLMClient) -> None:
        self._llm = value
        self._pinned.add("llm")

    def sync(self, *, force: bool = False) -> None:
        """Rebuild registry/policy/LLM client when the saved configuration changed."""
        now = time.monotonic()
        loaded = self._registry is not None and self._policy is not None and self._llm is not None
        if loaded and not force and now - self._checked < self.settings.config_refresh_s:
            return
        from forma.services import config

        with self._lock:
            self._checked = now
            rev = config.read_revision()
            if loaded and rev == self._revision:
                return
            snap = config.snapshot() if rev else config.ConfigSnapshot()
            s = self.settings
            if "registry" not in self._pinned:
                registry = SchemaRegistry(s.resolve(s.target_schema_dir))
                for doc in snap.schemas:
                    registry.add(doc)
                self._registry = registry
            if "policy" not in self._pinned:
                self._policy = PolicyEngine.load(s.resolve(s.policies_dir), snap.policy)
            if "llm" not in self._pinned:
                self._llm = config.build_client(s, snap.ai)
            self._revision = rev

    def refresh(self) -> None:
        """Apply a change this process just saved immediately (other processes follow within the TTL)."""
        self.sync(force=True)

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
            from forma.core.errors import NotFound

            raise NotFound(f"dataset version {dv.id} has no records (purged by retention)")
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
    return ServiceContext(settings=s, storage=storage, files=FileStore(storage))
