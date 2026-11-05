"""Runtime configuration edited in the web app (ADR-017): AI provider, approval policy, default target
format, target schemas registered in the UI.

Environment variables and the files under ``policies/`` and ``target_schema/`` stay the baseline; what an
admin saves here is layered on top and recorded in the audit log. Every write bumps one ``revision`` row,
and each process reloads its policy, schema registry and LLM client when it sees a new revision
(``ServiceContext``), so a change applies to the API and every worker without a restart.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from forma.ai.policy import DEFAULT_POLICY, _merge, file_policy, validate_policy
from forma.core import secrets
from forma.core.errors import NotFound, ValidationFailed
from forma.core.ids import new_id
from forma.core.settings import Settings
from forma.db.models import AppSetting, LLMCall, StoredSchema, utcnow
from forma.db.session import session_scope
from forma.schema_registry import SchemaRegistry, TargetSchema
from forma.services.rounds import audit

if TYPE_CHECKING:
    from forma.ai.llm import LLMClient

REVISION_KEY = "revision"
AI_KEY = "ai"
POLICY_KEY = "policy"
DEFAULTS_KEY = "defaults"
PROVIDERS = ("mock", "openai", "openai_compatible", "anthropic")
FALLBACK_TARGET_SCHEMA = "product"
MAX_BACKUP_KEYS = 4


def personal_preferences(session: Session, subject: str, mode: str) -> dict[str, Any]:
    from forma.core.hashing import stable_hash
    from forma.services.workspace import Preferences

    row = session.get(AppSetting, "user:" + stable_hash([mode, subject], 56))
    return Preferences.model_validate(row.value if row else {}).model_dump()


def save_preferences(
    session: Session, subject: str, mode: str, value: dict[str, Any], actor: str
) -> dict[str, Any]:
    from forma.core.hashing import stable_hash
    from forma.services.workspace import Preferences

    clean = Preferences.model_validate(value).model_dump()
    key = "user:" + stable_hash([mode, subject], 56)
    _put(session, key, clean, actor)
    audit(session, "preferences.update", "user", subject, actor=actor, fields=list(clean))
    return clean


@dataclass
class ConfigSnapshot:
    revision: int = 0
    ai: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] | None = None
    defaults: dict[str, Any] = field(default_factory=dict)
    schemas: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------------------------
# revision + snapshot (what ServiceContext reloads from)
# --------------------------------------------------------------------------------------------
def revision(session: Session) -> int:
    row = session.get(AppSetting, REVISION_KEY)
    return int((row.value or {}).get("n", 0)) if row else 0


def read_revision() -> int | None:
    """Current revision, or ``None`` before the tables exist (fresh database, CLI without a DB)."""
    try:
        with session_scope() as s:
            return revision(s)
    except (OperationalError, ProgrammingError):
        return None


def snapshot() -> ConfigSnapshot:
    try:
        with session_scope() as s:
            rows = {r.key: dict(r.value or {}) for r in s.scalars(select(AppSetting))}
            schemas = [
                copy.deepcopy(r.document)
                for r in s.scalars(
                    select(StoredSchema).order_by(StoredSchema.schema_id, StoredSchema.version)
                )
            ]
    except (OperationalError, ProgrammingError):
        return ConfigSnapshot()
    return ConfigSnapshot(
        revision=int(rows.get(REVISION_KEY, {}).get("n", 0)),
        ai=rows.get(AI_KEY, {}),
        policy=rows[POLICY_KEY].get("policy") if POLICY_KEY in rows else None,
        defaults=rows.get(DEFAULTS_KEY, {}),
        schemas=schemas,
    )


def _put(session: Session, key: str, value: dict[str, Any] | None, actor: str) -> None:
    row = session.get(AppSetting, key)
    if value is None:
        if row is not None:
            session.delete(row)
    elif row is None:
        session.add(AppSetting(key=key, value=value, updated_by=actor))
    else:
        row.value, row.updated_by = value, actor
    rev = session.get(AppSetting, REVISION_KEY)
    n = int((rev.value or {}).get("n", 0)) + 1 if rev else 1
    if rev is None:
        session.add(AppSetting(key=REVISION_KEY, value={"n": n}, updated_by=actor))
    else:
        rev.value, rev.updated_by = {"n": n}, actor
    session.flush()


def _row(session: Session, key: str) -> AppSetting | None:
    return session.get(AppSetting, key)


def _stamp(row: AppSetting | None) -> dict[str, Any]:
    return {"updated_by": row.updated_by, "updated_at": row.updated_at.isoformat()} if row else {}


# --------------------------------------------------------------------------------------------
# AI provider
# --------------------------------------------------------------------------------------------
def llm_settings(base: Settings, ai: dict[str, Any]) -> Settings:
    """``Settings`` with the web app's AI choices applied (the API key decrypted for this process only)."""
    if base.llm_mock_only:
        return base.model_copy(
            update={"llm_provider": "mock", "llm_model": "mock", "llm_api_key": None, "llm_base_url": None}
        )
    update: dict[str, Any] = {}
    for name, attr in (
        ("provider", "llm_provider"),
        ("model", "llm_model"),
        ("base_url", "llm_base_url"),
        ("timeout_s", "llm_timeout_s"),
        ("max_retries", "llm_max_retries"),
    ):
        if name in ai:
            update[attr] = ai[name]
    if ai.get("api_key_enc"):
        update["llm_api_key"] = secrets.decrypt(base, ai["api_key_enc"])
    return base.model_copy(update=update) if update else base


def build_client(base: Settings, ai: dict[str, Any]) -> LLMClient:
    """The LLM client with its backup keys (ADR-025), in the order the admin set. A backup whose key
    cannot be read is skipped. With no usable primary, the first usable backup becomes the primary."""
    from forma.ai.llm import LLMClient, MockProvider, Route, make_provider

    eff = llm_settings(base, ai)
    client = LLMClient.from_settings(eff)
    if base.llm_mock_only:
        return client
    client.primary_vision = _vision(ai.get("vision"), eff.llm_provider, eff.llm_base_url, eff.llm_model)
    routes: list[Route] = []
    for b in ai.get("backups") or []:
        key = secrets.decrypt(base, b["api_key_enc"]) if b.get("api_key_enc") else None
        provider = make_provider(b["provider"], key, b.get("base_url"), eff.llm_timeout_s)
        if not isinstance(provider, MockProvider):
            vision = _vision(b.get("vision"), b["provider"], b.get("base_url"), b["model"])
            routes.append(Route(b["id"], provider, b["model"], vision))
    if client.is_mock and routes:
        first = routes.pop(0)
        client = LLMClient(
            first.provider,
            first.model,
            eff.llm_max_retries,
            primary_name=first.name,
            primary_vision=first.vision,
        )
    client.fallbacks = routes
    return client


def _route_id(provider: str, base_url: str | None, model: str) -> str:
    return f"{provider}|{base_url or ''}|{model}"


def _vision(result: dict[str, Any] | None, provider: str, base_url: str | None, model: str) -> bool | None:
    """The saved image test (ADR-049), for exactly this model: changing any of the three voids it."""
    if not result or result.get("route") != _route_id(provider, base_url, model):
        return None
    return bool(result.get("ok"))


def _backup_view(base: Settings, b: dict[str, Any], usable: set[str]) -> dict[str, Any]:
    from forma.ai.breaker import is_open

    key = secrets.decrypt(base, b["api_key_enc"]) if b.get("api_key_enc") else None
    return {
        "id": b["id"],
        "provider": b["provider"],
        "model": b["model"],
        "base_url": b.get("base_url"),
        "api_key_hint": secrets.hint(key),
        "usable": b["id"] in usable,
        "circuit_open": is_open(b["id"]),
        "warning": None if key or not b.get("api_key_enc") else "the key cannot be decrypted: add it again",
        "vision": _vision(b.get("vision"), b["provider"], b.get("base_url"), b["model"]),
        "added_by": b.get("added_by"),
        "added_at": b.get("added_at"),
    }


def ai_view(session: Session, base: Settings) -> dict[str, Any]:
    """What the Settings page shows: effective values, where each comes from, never the key itself."""
    from forma.ai.breaker import is_open
    from forma.ai.llm import model_catalogue

    row = _row(session, AI_KEY)
    ai = dict(row.value or {}) if row else {}
    if base.llm_mock_only:
        ai = {}  # Do not decrypt/display restored production credentials or backup routes.
    eff = llm_settings(base, ai)
    client = build_client(base, ai)
    key_source = "ui" if ai.get("api_key_enc") else "env" if base.llm_api_key else None
    active = client.provider.name if not client.is_mock else "mock"
    usable = {r.name for r in client.routes} if not client.is_mock else set()
    warning = (
        "Offline reproduction mode: mock only; saved AI settings and backup keys are ignored"
        if base.llm_mock_only
        else None
    )
    if client.primary_name != "primary":
        warning = "the primary provider has no usable key: the first backup key answers"
    elif eff.llm_provider != "mock" and client.is_mock:
        warning = f"{eff.llm_provider} is selected but has no API key (or gateway URL): AI runs offline"
    elif ai.get("api_key_enc") and eff.llm_api_key is None:
        warning = "the saved API key cannot be decrypted (FORMA_SECRET_KEY changed): enter it again"
    return {
        "provider": eff.llm_provider,
        "model": eff.llm_model,
        "base_url": eff.llm_base_url,
        "timeout_s": eff.llm_timeout_s,
        "max_retries": eff.llm_max_retries,
        "api_key_set": bool(eff.llm_api_key),
        "api_key_hint": secrets.hint(eff.llm_api_key),
        "api_key_source": key_source,
        "sources": {
            k: ("ui" if k in ai else "server")
            for k in ("provider", "model", "base_url", "timeout_s", "max_retries")
        },
        "active_provider": active,
        "active_model": client.model,
        "active_route": client.primary_name if not client.is_mock else None,
        "vision": client.primary_vision,
        "supports_vision": client.supports_vision,
        "offline": client.is_mock,
        "mock_only": base.llm_mock_only,
        "warning": warning,
        "providers": list(PROVIDERS),
        "models": model_catalogue(),
        "circuit_open": is_open("primary"),
        "backups": [_backup_view(base, b, usable) for b in ai.get("backups") or []],
        "max_backups": MAX_BACKUP_KEYS,
        **_stamp(row),
    }


def update_ai(session: Session, base: Settings, patch: dict[str, Any], actor: str) -> None:
    """Partial update. ``api_key``: a string replaces it, ``""``/``None`` removes the saved one (the
    server's FORMA_LLM_API_KEY applies again); a field set to ``None`` goes back to the server value."""
    row = _row(session, AI_KEY)
    ai = dict(row.value or {}) if row else {}
    changed: list[str] = []
    for k in ("provider", "model", "base_url", "timeout_s", "max_retries"):
        if k not in patch:
            continue
        v = patch[k]
        if v is None or v == "":
            ai.pop(k, None)
        elif k == "provider" and v not in PROVIDERS:
            raise ValidationFailed(f"provider must be one of {list(PROVIDERS)}")
        elif k == "timeout_s" and (not isinstance(v, int | float) or not 1 <= v <= 600):
            raise ValidationFailed("timeout_s must be between 1 and 600 seconds")
        elif k == "max_retries" and (not isinstance(v, int) or not 0 <= v <= 10):
            raise ValidationFailed("max_retries must be a whole number from 0 to 10")
        elif k == "base_url" and not str(v).startswith(("http://", "https://")):
            raise ValidationFailed("base_url must start with http:// or https://")
        else:
            ai[k] = v.strip() if isinstance(v, str) else v
        changed.append(k)
    if "api_key" in patch:
        key = patch["api_key"]
        if key:
            ai["api_key_enc"] = secrets.encrypt(base, str(key).strip())
        else:
            ai.pop("api_key_enc", None)
        changed.append("api_key")
    if not changed:
        return
    _put(session, AI_KEY, ai or None, actor)
    audit(session, "settings.ai.update", "settings", AI_KEY, actor=actor, changed=changed)  # never the key


# --------------------------------------------------------------------------------------------
# approval policy
# --------------------------------------------------------------------------------------------
def policy_view(session: Session, base: Settings) -> dict[str, Any]:
    row = _row(session, POLICY_KEY)
    baseline = file_policy(base.resolve(base.policies_dir))
    override = (row.value or {}).get("policy") if row else None
    return {
        "effective": _merge(_merge(DEFAULT_POLICY, baseline), override or {}),
        "file": _merge(DEFAULT_POLICY, baseline),
        "source": "ui" if override else "file",
        **_stamp(row),
    }


def update_policy(session: Session, base: Settings, policy: dict[str, Any], actor: str) -> None:
    """Save a complete policy (as the Settings form sends it). Rejected with reasons when unusable."""
    problems = validate_policy(policy)
    if problems:
        raise ValidationFailed("policy is not valid", details={"problems": problems})
    before = policy_view(session, base)["effective"]
    _put(session, POLICY_KEY, {"policy": policy}, actor)
    audit(session, "settings.policy.update", "settings", POLICY_KEY, actor=actor, before=before, after=policy)


def reset_policy(session: Session, base: Settings, actor: str) -> None:
    before = policy_view(session, base)["effective"]
    _put(session, POLICY_KEY, None, actor)
    audit(session, "settings.policy.reset", "settings", POLICY_KEY, actor=actor, before=before)


# --------------------------------------------------------------------------------------------
# target formats
# --------------------------------------------------------------------------------------------
def default_target_schema(session: Session) -> str:
    row = _row(session, DEFAULTS_KEY)
    return (
        str((row.value or {}).get("target_schema") or FALLBACK_TARGET_SCHEMA)
        if row
        else FALLBACK_TARGET_SCHEMA
    )


def set_default_target_schema(session: Session, registry: SchemaRegistry, ref: str, actor: str) -> None:
    resolve_target_schema(session, registry, ref, None)  # 422 when it does not exist
    row = _row(session, DEFAULTS_KEY)
    _put(session, DEFAULTS_KEY, {**(dict(row.value or {}) if row else {}), "target_schema": ref}, actor)
    audit(session, "settings.default_schema", "settings", DEFAULTS_KEY, actor=actor, target_schema=ref)


def store_schema(session: Session, schema: TargetSchema, actor: str) -> None:
    if session.get(StoredSchema, (schema.schema_id, schema.version)) is not None:
        raise ValidationFailed(f"{schema.ref} already exists; versions are append-only")
    session.add(
        StoredSchema(
            schema_id=schema.schema_id, version=schema.version, document=schema.document, created_by=actor
        )
    )
    _put(session, f"schema:{schema.schema_id}", {"latest": schema.version}, actor)
    audit(session, "schema.register", "schema", schema.ref, actor=actor, fields=len(schema.leaf_fields()))


def stored_refs(session: Session) -> set[str]:
    return {f"{r.schema_id}@{r.version}" for r in session.scalars(select(StoredSchema))}


def resolve_target_schema(
    session: Session, registry: SchemaRegistry, explicit: str | None, customer_default: str | None
) -> str:
    """Round target format: what the request names, else the customer's, else the global default."""
    ref = explicit or customer_default or default_target_schema(session)
    try:
        registry.get_ref(ref)
    except NotFound as e:
        raise ValidationFailed(f"target format {ref!r} does not exist", details={"target_schema": ref}) from e
    return ref


# --------------------------------------------------------------------------------------------
# one-step connect: paste a key, FORMA finds the provider, checks the key, then saves it
# --------------------------------------------------------------------------------------------
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-4.1-mini"}


def detect_provider(api_key: str) -> str | None:
    """Anthropic keys start with ``sk-ant-``, OpenAI keys with ``sk-``; anything else is a gateway."""
    k = api_key.strip()
    if k.startswith("sk-ant-"):
        return "anthropic"
    if k.startswith("sk-"):
        return "openai"
    return None


def _model_for(provider: str, current: str | None) -> str:
    """Keep the chosen model when it belongs to the provider, else that provider's default."""
    if current and provider == "openai_compatible":
        return current
    if current and (current.startswith("claude")) == (provider == "anthropic"):
        return current
    return DEFAULT_MODELS.get(provider, current or DEFAULT_MODELS["openai"])


def probe(client: Any) -> dict[str, Any]:
    """One tiny call straight to the provider (no retries, no circuit breaker): is this key usable?"""
    if client.is_mock:
        return {"ok": False, "offline": True, "message": "AI is offline: no provider key"}
    return _probe(client.provider, client.model, client.temperature)


def probe_all(client: Any) -> dict[str, Any]:
    """The primary's probe, plus one per backup key (``backups``), each straight to its provider."""
    out = probe(client)
    if client.is_mock:
        return out
    out["route"] = client.primary_name
    out["backups"] = [
        {"id": r.name, **_probe(r.provider, r.model, client.temperature)} for r in client.fallbacks
    ]
    return out


def _probe(provider: Any, model: str, temperature: float = 0.0) -> dict[str, Any]:
    import time

    from forma.ai.llm import _parse_json

    t0 = time.perf_counter()
    try:
        text, usage = provider.complete_json(
            system="You are a connectivity check.",
            user='Return {"ok": true}.',
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            model=model,
            temperature=temperature,
        )
        ok = bool(_parse_json(text).get("ok"))
    except Exception as e:  # any provider/transport failure is the answer
        return {"ok": False, "offline": False, "message": str(e)[:300], "model": model}
    return {
        "ok": ok,
        "offline": False,
        "message": "connected" if ok else "the provider answered, but not with the expected JSON",
        "model": model,
        "provider": provider.name,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cost_usd": usage.cost_usd,
        },
    }


def connect_ai(
    session: Session,
    base: Settings,
    api_key: str,
    actor: str,
    provider: str | None = None,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Paste-a-key flow: detect the provider, pick a model, try the key, save it only if it works."""
    from forma.ai.llm import LLMClient

    key = api_key.strip()
    chosen = provider or detect_provider(key)
    if chosen is None or chosen == "mock" or chosen not in PROVIDERS:
        raise ValidationFailed(
            "cannot tell which provider this key belongs to: choose OpenAI, Anthropic or a gateway",
            details={"needs_provider": True},
        )
    row = _row(session, AI_KEY)
    ai = dict(row.value or {}) if row else {}
    from forma.services.model_discovery import endpoint

    model = (model or "").strip() or _model_for(
        chosen, ai.get("model") or (base.llm_model if base.llm_provider == chosen else None)
    )
    url = endpoint(
        chosen,
        base_url if base_url is not None else ai.get("base_url") if ai.get("provider") == chosen else None,
    )
    if not key:
        saved = llm_settings(base, ai)
        if chosen != saved.llm_provider or url != endpoint(saved.llm_provider, saved.llm_base_url):
            raise ValidationFailed("Enter the API key again when changing provider or endpoint")
        key = saved.llm_api_key or ""
        if not key:
            raise ValidationFailed("paste an API key")
    candidate = llm_settings(
        base,
        {
            **{k: v for k, v in ai.items() if k != "api_key_enc"},
            "provider": chosen,
            "model": model,
            "base_url": url,
        },
    )
    candidate = candidate.model_copy(update={"llm_api_key": key})
    result = probe(LLMClient.from_settings(candidate))
    if not result["ok"]:
        import re

        status = re.search(r"HTTP\s+(\d{3})", str(result.get("message", "")))
        detail = f" (HTTP {status.group(1)})" if status else ""
        raise ValidationFailed(
            f"the key did not work{detail}. Check the endpoint, key and model access.",
            details={"provider": chosen, "model": model},
        )
    _ledger(session, "settings.connect", model, result)
    update_ai(session, base, {"provider": chosen, "model": model, "api_key": key, "base_url": url}, actor)
    return result


# --------------------------------------------------------------------------------------------
# backup keys (ADR-025): tried in order when the primary provider fails
# --------------------------------------------------------------------------------------------
def test_vision(session: Session, base: Settings, actor: str) -> dict[str, Any]:
    """Show every connected model a small test picture and keep each answer (ADR-049). An unreachable
    model keeps whatever it had: that says nothing about images."""
    from forma.ai import vision

    ai, backups = _backups(session)
    client = build_client(base, ai)
    if client.is_mock:
        return {"results": [], "message": "AI is offline: no provider key"}
    eff = llm_settings(base, ai)
    now = utcnow().isoformat()
    results: list[dict[str, Any]] = []
    for route in client.routes:
        result = {"id": route.name, **vision.probe(route.provider, route.model, client.temperature)}
        results.append(result)
        if result.get("usage"):
            _ledger(session, "settings.vision_test", route.model, result)
        if result["vision"] is None:
            continue
        saved = {"ok": result["vision"], "tested_at": now}
        if route.name == "primary":
            ai["vision"] = {
                **saved,
                "route": _route_id(eff.llm_provider, eff.llm_base_url, eff.llm_model),
            }
        else:
            b = next(x for x in backups if x["id"] == route.name)
            b["vision"] = {**saved, "route": _route_id(b["provider"], b.get("base_url"), b["model"])}
    _put(session, AI_KEY, {**ai, "backups": backups}, actor)
    audit(
        session,
        "settings.ai.vision_test",
        "settings",
        AI_KEY,
        actor=actor,
        results={r["id"]: r["vision"] for r in results},
    )
    return {"results": results}


def _ledger(session: Session, agent: str, model: str, result: dict[str, Any]) -> None:
    session.add(
        LLMCall(
            agent=agent,
            model=model,
            prompt_version="settings",
            input_tokens=result["usage"]["input_tokens"],
            output_tokens=result["usage"]["output_tokens"],
            cost_usd=result["usage"]["cost_usd"],
            latency_ms=result["latency_ms"],
        )
    )


def add_backup(
    session: Session,
    base: Settings,
    api_key: str,
    actor: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Add a backup key: the provider is recognised from the key, the key is tried with one tiny call
    on the chosen model, and it is saved (encrypted) only when that call works."""
    import datetime as dt

    from forma.ai.llm import MockProvider, make_provider

    key = api_key.strip()
    if not key:
        raise ValidationFailed("paste an API key")
    chosen = provider or detect_provider(key)
    if chosen is None or chosen == "mock" or chosen not in PROVIDERS:
        raise ValidationFailed(
            "cannot tell which provider this key belongs to: choose OpenAI, Anthropic or a gateway",
            details={"needs_provider": True},
        )
    url = (base_url or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        raise ValidationFailed("base_url must start with http:// or https://")
    if chosen == "openai_compatible" and not (url and (model or "").strip()):
        raise ValidationFailed("a gateway backup needs its URL and a model name")
    row = _row(session, AI_KEY)
    ai = dict(row.value or {}) if row else {}
    backups = list(ai.get("backups") or [])
    if len(backups) >= MAX_BACKUP_KEYS:
        raise ValidationFailed(f"at most {MAX_BACKUP_KEYS} backup keys")
    chosen_model = (model or "").strip() or _model_for(chosen, None)
    eff = llm_settings(base, ai)
    candidate = make_provider(chosen, key, url, eff.llm_timeout_s)
    if isinstance(candidate, MockProvider):  # pragma: no cover - a key is always given here
        raise ValidationFailed("this key cannot reach a provider")
    result = _probe(candidate, chosen_model)
    if not result["ok"]:
        raise ValidationFailed(
            f"the key did not work: {result['message']}", details={"provider": chosen, "model": chosen_model}
        )
    _ledger(session, "settings.backup", chosen_model, result)
    entry = {
        "id": new_id("bk"),
        "provider": chosen,
        "model": chosen_model,
        "base_url": url,
        "api_key_enc": secrets.encrypt(base, key),
        "added_by": actor,
        "added_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    _put(session, AI_KEY, {**ai, "backups": [*backups, entry]}, actor)
    audit(
        session,
        "settings.ai.backup.add",
        "settings",
        AI_KEY,
        actor=actor,
        backup=entry["id"],
        provider=chosen,
        model=chosen_model,
    )  # never the key
    return {"id": entry["id"], **result}


def _backups(session: Session) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = _row(session, AI_KEY)
    ai = dict(row.value or {}) if row else {}
    return ai, [dict(b) for b in ai.get("backups") or []]


def update_backup(session: Session, backup_id: str, patch: dict[str, Any], actor: str) -> None:
    """Change a backup's model or gateway URL (the key stays; add the backup again to change it)."""
    ai, backups = _backups(session)
    b = next((x for x in backups if x["id"] == backup_id), None)
    if b is None:
        raise NotFound(f"backup key {backup_id} not found")
    changed: list[str] = []
    if "model" in patch:
        m = (patch["model"] or "").strip()
        if not m:
            raise ValidationFailed("model must not be empty")
        b["model"] = m
        changed.append("model")
    if "base_url" in patch:
        url = (patch["base_url"] or "").strip() or None
        if url and not url.startswith(("http://", "https://")):
            raise ValidationFailed("base_url must start with http:// or https://")
        if b["provider"] == "openai_compatible" and not url:
            raise ValidationFailed("a gateway backup needs its URL")
        b["base_url"] = url
        changed.append("base_url")
    if not changed:
        return
    _put(session, AI_KEY, {**ai, "backups": backups}, actor)
    audit(
        session,
        "settings.ai.backup.update",
        "settings",
        AI_KEY,
        actor=actor,
        backup=backup_id,
        **{k: b[k] for k in changed},
    )


def remove_backup(session: Session, backup_id: str, actor: str) -> None:
    ai, backups = _backups(session)
    left = [b for b in backups if b["id"] != backup_id]
    if len(left) == len(backups):
        raise NotFound(f"backup key {backup_id} not found")
    rest = {k: v for k, v in ai.items() if k != "backups"}
    _put(session, AI_KEY, ({**rest, "backups": left} if left else rest) or None, actor)
    audit(session, "settings.ai.backup.remove", "settings", AI_KEY, actor=actor, backup=backup_id)


def order_backups(session: Session, ids: list[str], actor: str) -> None:
    """Set the order in which backups are tried (every backup id, once)."""
    ai, backups = _backups(session)
    if sorted(ids) != sorted(b["id"] for b in backups):
        raise ValidationFailed("give every backup key id exactly once")
    by_id = {b["id"]: b for b in backups}
    _put(session, AI_KEY, {**ai, "backups": [by_id[i] for i in ids]}, actor)
    audit(session, "settings.ai.backup.order", "settings", AI_KEY, actor=actor, order=ids)
