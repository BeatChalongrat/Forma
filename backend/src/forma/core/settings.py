from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_FIELDS = {
    "llm_api_key",
    "database_url",
    "redis_url",
    "s3_secret_key",
    "auth_client_secret",
    "secret_key",
}


class Settings(BaseSettings):
    """Runtime configuration. All values can be overridden with FORMA_* env vars or a .env file.

    Every field carries a one-line description; `forma config show` and `.env.production.example`
    are generated from this class so documentation cannot drift from the code.
    """

    model_config = SettingsConfigDict(env_prefix="FORMA_", env_file=".env", extra="ignore")

    pilot_mode: bool = Field(
        default=False,
        description="Require explicit human dataset approval before export; enable pilot question feedback.",
    )

    env: str = Field(
        default="dev", description="dev | prod. prod requires migrations and disables create_all."
    )
    # Repo-relative data root. Resolved at runtime so the app runs from any cwd.
    data_dir: Path = Field(default=Path("../data"), description="Runtime data root (local storage, SQLite).")
    database_url: str = Field(
        default="sqlite:///../data/forma.db",
        description="SQLAlchemy URL. SQLite for dev; postgresql+psycopg://user:pass@host/db in production.",
    )
    target_schema_dir: Path = Field(default=Path("../target_schema"), description="Target JSON Schema dir.")
    policies_dir: Path = Field(default=Path("../policies"), description="approval.yaml / retention.yaml dir.")
    alembic_dir: Path | None = Field(
        default=None, description="Migration scripts dir (auto: backend/alembic or ./alembic)."
    )

    # LLM (provider-agnostic). provider: mock | openai | anthropic | openai_compatible
    llm_provider: str = Field(default="mock", description="mock | openai | openai_compatible | anthropic.")
    llm_mock_only: bool = Field(
        default=False,
        description=(
            "Local reproduction safety lock: force offline mock, "
            "ignore saved AI routes and deny provider HTTP."
        ),
    )
    llm_model: str = Field(default="gpt-4.1-mini", description="Model name sent to the provider.")
    llm_api_key: str | None = Field(default=None, description="Provider API key (secret).")
    llm_base_url: str | None = Field(default=None, description="Gateway base URL for openai_compatible.")
    llm_timeout_s: float = Field(default=90.0, description="Per-call HTTP timeout in seconds.")
    llm_task_budget_s: float = Field(
        default=600.0,
        gt=0,
        description="Total AI task wall-clock budget across tools, provider retries and repairs, in seconds.",
    )
    llm_max_output_tokens: int = Field(
        default=16000, ge=256, description="Maximum output tokens per structured provider response."
    )
    llm_max_retries: int = Field(default=2, description="Structured-output retries on invalid JSON.")
    llm_transport_retries: int = Field(default=3, description="Attempts per provider call on timeouts/5xx.")
    llm_backoff_s: float = Field(default=1.0, description="Base backoff between transport retries (doubles).")
    llm_breaker_failures: int = Field(
        default=5, description="Consecutive provider failures that open the circuit."
    )
    llm_breaker_reset_s: float = Field(
        default=120.0, description="Seconds the circuit stays open before a probe."
    )
    llm_rate_limit_per_min: int = Field(
        default=30, description="Max LLM-calling API requests per actor per minute."
    )
    llm_max_sample_rows: int = Field(default=5, description="Max sample rows sent to a model per call.")
    llm_max_sample_chars: int = Field(
        default=200, description="Max characters per sample cell sent to a model."
    )
    llm_inspect_rounds: int = Field(
        default=2,
        description="Turns in which a mapping agent may ask read-only questions about the file (ADR-058).",
    )
    llm_self_check_rounds: int = Field(
        default=2,
        description="Times a mapping agent sees its mapping run on the first rows and may fix it (ADR-058).",
    )
    llm_self_check_rows: int = Field(
        default=50, description="Source rows the engine runs for the agent's self-check (ADR-058)."
    )
    mapping_lab_enabled: bool = Field(
        default=True, description="Allow mapping agents to use the isolated runner when configured."
    )
    mapping_lab_sockets: list[str] = Field(
        default_factory=list, description="Isolated runner socket pool; empty uses code_sandbox_socket"
    )
    mapping_lab_turns: int = Field(
        default=3, ge=0, le=6, description="Maximum isolated Python experiments per mapping proposal."
    )
    knowledge_policy: str = Field(
        default="legacy",
        pattern="^(legacy|strict)$",
        description=(
            "strict recalls only reviewed real-data knowledge; legacy preserves unclassified dev data."
        ),
    )

    # Engine
    mapping_workflow: str = Field(
        default="graph",
        pattern="^(legacy|graph)$",
        description="Mapping orchestration: graph (three roles) or legacy (comparison/rollback).",
    )
    mapping_workflow_calls: int = Field(
        default=18,
        ge=3,
        le=60,
        description="Durable provider-attempt ceiling per mapping workflow.",
    )
    mapping_workflow_repairs: int = Field(
        default=1,
        ge=0,
        le=2,
        description="Maximum mapper revisions requested by the semantic reviewer.",
    )

    chunk_rows: int = Field(default=200_000, description="Rows per engine chunk.")
    sample_rows: int = Field(default=25, description="Sample rows kept per column profile.")
    preview_rows: int = Field(default=200, description="Rows in Mapping Studio previews.")

    # Policy
    auto_approve_min_confidence: float = Field(default=0.95, description="Fallback auto-approve threshold.")

    # Jobs
    job_backend: str = Field(
        default="thread", description="thread | process | arq (Redis-backed, `forma worker`)."
    )
    workers: int = Field(default=4, description="Worker threads/processes per node.")
    report_workers: int = Field(default=1, ge=1, le=4, description="Separate document worker concurrency.")
    assistant_workers: int = Field(
        default=2, ge=1, le=8, description="Independent assistant queue concurrency per worker container."
    )
    assistant_run_max_calls: int = Field(
        default=48,
        ge=1,
        le=100,
        description="Persistent model request budget per assistant message, including retries.",
    )
    assistant_step_attempts: int = Field(
        default=3, ge=1, le=5, description="Maximum attempts of each unfinished assistant step."
    )
    assistant_run_max_cost_usd: float = Field(
        default=2.0,
        gt=0,
        description="Pause new calls after recorded estimated spend; not a provider billing cap.",
    )
    assistant_run_timeout_s: int = Field(
        default=1800, ge=30, description="Assistant message lifetime, including queue wait and recovery."
    )
    report_queue_max: int = Field(
        default=20, ge=1, le=100, description="Bounded pending/running document jobs."
    )
    worker_queue: str = Field(
        default="mapping",
        pattern="^(mapping|reports|assistant)$",
        description="ARQ queue selected by this worker: mapping, reports or assistant.",
    )
    report_sandbox_socket: str | None = Field(default=None, description="Isolated document renderer socket.")
    report_timeout_s: float = Field(
        default=240, ge=30, le=300, description="Document renderer time limit in seconds."
    )
    file_workers: int = Field(default=4, description="Files profiled concurrently inside one round.")
    queue_max: int = Field(default=500, description="Max queued+running jobs before the API answers 429.")
    job_stale_s: int = Field(
        default=1800, description="Legacy jobs without leases are stale after this many seconds."
    )
    job_timeout_s: int = Field(default=1800, ge=1, description="Hard execution limit per ARQ child process.")
    job_lease_s: int = Field(
        default=90, ge=3, description="Worker ownership lease, renewed every third of this interval."
    )
    job_recovery_interval_s: float = Field(
        default=15, gt=0, description="Seconds between stale recovery and queue reconciliation."
    )
    job_dispatch_retry_s: float = Field(
        default=30, gt=0, description="Redispatch durable queued rows after this interval."
    )
    llm_shared_concurrency: int = Field(
        default=4, ge=1, description="Shared simultaneous provider HTTP calls per credential and endpoint."
    )
    llm_shared_rpm: int = Field(
        default=60,
        ge=1,
        description="Shared provider requests per rolling minute; separate from per-user API limits.",
    )
    llm_shared_limiter: str = Field(
        default="auto",
        pattern="^(auto|redis|local)$",
        description="auto uses Redis for ARQ, otherwise process-local limits.",
    )
    job_max_attempts: int = Field(default=3, description="Attempts before a stale job is marked failed.")
    redis_url: str = Field(
        default="redis://localhost:6379/0", description="Redis URL for the arq backend (secret)."
    )
    max_upload_mb: int = Field(default=200, description="Largest accepted upload per file, in MB.")

    retention_interval_h: float = Field(
        default=0.0, description="Hours between scheduled retention runs (0 = off)."
    )
    retention_apply: bool = Field(
        default=False, description="Scheduled retention deletes (true) or only plans (false)."
    )
    # Storage
    storage: str = Field(default="local", description="local | s3 (S3 or MinIO via boto3).")
    s3_bucket: str = Field(default="forma", description="S3 bucket name.")
    s3_prefix: str = Field(default="", description="Key prefix inside the bucket.")
    s3_endpoint_url: str | None = Field(default=None, description="Custom endpoint (MinIO), empty for AWS.")
    s3_public_endpoint_url: str | None = Field(
        default=None, description="Endpoint browsers can reach, used only to pre-sign download URLs (MinIO)."
    )
    s3_region: str = Field(default="us-east-1", description="S3 region.")
    s3_access_key: str | None = Field(default=None, description="S3 access key id (falls back to AWS chain).")
    s3_secret_key: str | None = Field(default=None, description="S3 secret key (secret).")
    s3_presign_s: int = Field(default=900, description="Pre-signed download URL lifetime in seconds.")

    # Auth
    auth: str = Field(default="dev", description="dev (X-Forma-User header, no verification) | oidc.")
    auth_issuer: str | None = Field(default=None, description="OIDC issuer URL (discovery at /.well-known).")
    auth_audience: str | None = Field(default=None, description="Expected `aud` claim.")
    auth_jwks_url: str | None = Field(default=None, description="JWKS URL (derived from issuer when empty).")
    auth_client_id: str | None = Field(default=None, description="Public client id for the SPA (PKCE).")
    auth_client_secret: str | None = Field(default=None, description="Optional confidential client secret.")
    auth_role_claim: str = Field(default="roles", description="Token claim holding FORMA roles.")
    auth_customers_claim: str = Field(default="customers", description="Token claim restricting customers.")
    auth_dev_default_role: str = Field(default="admin", description="Role for unknown users in dev mode.")
    auth_allow_dev_in_prod: bool = Field(
        default=False,
        description="Local smoke tests of the prod stack only: let FORMA_AUTH=dev run with FORMA_ENV=prod.",
    )
    secret_key: str | None = Field(
        default=None,
        description="Encrypts secrets saved in the web app (AI API key). Required in prod to save them; "
        "changing it makes saved secrets unreadable until they are entered again.",
    )
    code_sandbox_socket: str | None = Field(
        default=None,
        description="Unix socket of the assistant's code sandbox (ADR-048). Empty: it cannot run code.",
    )
    code_sandbox_timeout_s: float = Field(
        default=60.0, description="Longest one code run may take, in seconds."
    )
    code_sandbox_max_input_mb: int = Field(
        default=200, description="Most data one code run may receive, in MB."
    )
    assistant_max_image_mb: int = Field(
        default=5, description="Largest image accepted in an assistant chat, in MB."
    )
    assistant_max_file_mb: int = Field(
        default=25, description="Largest data file accepted in an assistant chat, in MB."
    )
    sandbox_max_rows: int = Field(
        default=50_000,
        description="Rows per source the mapping sandbox runs the new and the approved mapping on (0 = all).",
    )
    config_refresh_s: float = Field(
        default=5.0, description="How often each process checks for settings changed in the web app."
    )

    # Observability
    otel_endpoint: str | None = Field(
        default=None, description="OTLP gRPC/HTTP endpoint; unset = tracing off."
    )
    otel_service_name: str = Field(default="forma-api", description="OpenTelemetry service.name.")
    log_format: str = Field(default="console", description="console | json.")
    log_level: str = Field(default="INFO", description="Python log level.")

    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://127.0.0.1:5173"],
        description="Allowed browser origins (JSON list).",
    )

    @property
    def root(self) -> Path:
        """Repository root, resolved relative to this file (backend/src/forma/core -> repo)."""
        return Path(__file__).resolve().parents[4]

    def resolve(self, p: Path) -> Path:
        """Relative paths are relative to ``backend/`` in a source checkout; in an installed image (no
        checkout around site-packages) they are relative to the working directory."""
        if p.is_absolute():
            return p
        base = self.root / "backend"
        if not (base / "pyproject.toml").exists():
            base = Path.cwd()
        return (base / p).resolve()

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

    @property
    def is_prod(self) -> bool:
        return self.env.lower() in ("prod", "production")

    def effective(self, *, mask_secrets: bool = True) -> dict[str, Any]:
        """Effective configuration as plain values; secrets masked unless asked otherwise."""
        out: dict[str, Any] = {}
        for name in type(self).model_fields:
            v = getattr(self, name)
            if isinstance(v, Path):
                v = str(v)
            if mask_secrets and name in SECRET_FIELDS and v:
                v = _mask(str(v))
            out[name] = v
        return out

    @classmethod
    def describe(cls) -> list[tuple[str, str, str]]:
        """(ENV_NAME, default, description) for every field — drives `.env.production.example`."""
        import json

        from pydantic_core import PydanticUndefined

        rows = []
        for name, f in cls.model_fields.items():
            default: Any = f.default
            if default is PydanticUndefined and f.default_factory is not None:
                default = f.default_factory()  # type: ignore[call-arg]
            if isinstance(default, Path):
                default = default.as_posix()  # OS-independent, so the generated example never drifts
            elif isinstance(default, list):
                default = json.dumps(default)  # a list field is read back as JSON, so write it as JSON
            elif default is None:
                default = ""
            rows.append((f"FORMA_{name.upper()}", str(default), f.description or ""))
        return rows


def _mask(v: str) -> str:
    if "://" in v and "@" in v:
        head, _, tail = v.rpartition("@")
        scheme, _, creds = head.partition("://")
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{tail}"
    return v[:3] + "***" if len(v) > 6 else "***"


@lru_cache
def get_settings() -> Settings:
    return Settings()
