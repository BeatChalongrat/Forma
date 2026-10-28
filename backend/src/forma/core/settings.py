from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden with FORMA_* env vars or a .env file."""

    model_config = SettingsConfigDict(env_prefix="FORMA_", env_file=".env", extra="ignore")

    env: str = "dev"
    # Repo-relative data root. Resolved at runtime so the app runs from any cwd.
    data_dir: Path = Field(default=Path("../data"))
    database_url: str = "sqlite:///../data/forma.db"
    target_schema_dir: Path = Field(default=Path("../target_schema"))
    policies_dir: Path = Field(default=Path("../policies"))

    # LLM (provider-agnostic). provider: mock | openai | anthropic | openai_compatible
    llm_provider: str = "mock"
    llm_model: str = "gpt-4.1-mini"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_timeout_s: float = 90.0
    llm_max_retries: int = 2

    # Engine
    chunk_rows: int = 200_000
    sample_rows: int = 25
    preview_rows: int = 200

    # Policy
    auto_approve_min_confidence: float = 0.95

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def root(self) -> Path:
        """Repository root, resolved relative to this file (backend/src/forma/core -> repo)."""
        return Path(__file__).resolve().parents[4]

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (self.root / "backend" / p).resolve()

    @property
    def data_path(self) -> Path:
        d = self.resolve(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
            rel = url.removeprefix("sqlite:///")
            abs_path = self.resolve(Path(rel))
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path.as_posix()}"
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
