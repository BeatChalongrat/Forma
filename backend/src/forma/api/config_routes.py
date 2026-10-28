"""Settings edited in the web app (ADR-017): AI provider and key, approval policy, target formats.

Admins change these here instead of in environment files or the database. Every write is validated,
audited and applied to this process at once (``ctx.refresh``); other API and worker processes follow
within ``FORMA_CONFIG_REFRESH_S``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from forma.api.routes import ctx_dep
from forma.api.schemas import (
    AISettingsIn,
    BackupKeyIn,
    BackupKeyPatchIn,
    BackupOrderIn,
    ConnectAIIn,
    DefaultFormatIn,
    PolicyIn,
    SchemaIn,
)
from forma.auth import Principal, current_principal, llm_rate_limited, require_role
from forma.db.models import LLMCall
from forma.db.session import get_session
from forma.schema_registry import SEMANTICS, SchemaRegistry, schema_findings
from forma.services import config as cfg
from forma.services.context import ServiceContext

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------------------------
# AI provider
# --------------------------------------------------------------------------------------------
@router.get("/settings/ai")
def get_ai(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    return cfg.ai_view(session, ctx.settings)


@router.put("/settings/ai")
def put_ai(
    body: AISettingsIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    cfg.update_ai(session, ctx.settings, patch, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.ai_view(session, ctx.settings)


@router.post("/settings/ai/connect")
def connect_ai(
    body: ConnectAIIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
    _rl: Principal = Depends(llm_rate_limited),
) -> dict[str, Any]:
    """Paste a key and go: the provider is recognised from the key, a model is chosen, the key is tried
    with one tiny call, and it is saved (encrypted) only when that call works."""
    result = cfg.connect_ai(session, ctx.settings, body.api_key, p.actor, body.provider)
    session.commit()
    ctx.refresh()
    return {"test": result, "ai": cfg.ai_view(session, ctx.settings)}


@router.post("/settings/ai/test")
def test_ai(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
    _rl: Principal = Depends(llm_rate_limited),
) -> dict[str, Any]:
    """One tiny call with the saved settings — the primary and every backup key (``backups``); the
    tokens go to the LLM ledger."""
    ctx.refresh()
    result = cfg.probe_all(ctx.llm)
    for r in [result, *result.get("backups", [])]:
        if r.get("usage"):
            session.add(
                LLMCall(
                    agent="settings.test",
                    model=r["model"],
                    prompt_version="settings",
                    input_tokens=r["usage"]["input_tokens"],
                    output_tokens=r["usage"]["output_tokens"],
                    cost_usd=r["usage"]["cost_usd"],
                    latency_ms=r["latency_ms"],
                )
            )
    return result


@router.post("/settings/ai/backups")
def add_backup(
    body: BackupKeyIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
    _rl: Principal = Depends(llm_rate_limited),
) -> dict[str, Any]:
    """Add a backup key (ADR-025): tried with one tiny call, saved encrypted only when it works, and used
    when the primary provider fails."""
    result = cfg.add_backup(
        session,
        ctx.settings,
        body.api_key,
        p.actor,
        provider=body.provider,
        model=body.model,
        base_url=body.base_url,
    )
    session.commit()
    ctx.refresh()
    return {"test": result, "ai": cfg.ai_view(session, ctx.settings)}


@router.patch("/settings/ai/backups/{backup_id}")
def update_backup(
    backup_id: str,
    body: BackupKeyPatchIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    cfg.update_backup(session, backup_id, {k: getattr(body, k) for k in body.model_fields_set}, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.ai_view(session, ctx.settings)


@router.delete("/settings/ai/backups/{backup_id}")
def remove_backup(
    backup_id: str,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    cfg.remove_backup(session, backup_id, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.ai_view(session, ctx.settings)


@router.put("/settings/ai/backups/order")
def order_backups(
    body: BackupOrderIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    cfg.order_backups(session, body.ids, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.ai_view(session, ctx.settings)


# --------------------------------------------------------------------------------------------
# approval policy
# --------------------------------------------------------------------------------------------
@router.get("/settings/policy")
def get_policy(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    return cfg.policy_view(session, ctx.settings)


@router.put("/settings/policy")
def put_policy(
    body: PolicyIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    cfg.update_policy(session, ctx.settings, body.policy, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.policy_view(session, ctx.settings)


@router.delete("/settings/policy")
def reset_policy(
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Back to ``policies/approval.yaml``."""
    cfg.reset_policy(session, ctx.settings, p.actor)
    session.commit()
    ctx.refresh()
    return cfg.policy_view(session, ctx.settings)


# --------------------------------------------------------------------------------------------
# target formats
# --------------------------------------------------------------------------------------------
@router.get("/settings/semantics")
def semantics(p: Principal = Depends(current_principal)) -> dict[str, str]:
    """The x-forma.semantic vocabulary, for writing a new target format."""
    return SEMANTICS


@router.post("/schemas/preview")
def preview_schema(
    body: SchemaIn,
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Validate a target format before registering it: its id and next version, the fields FORMA sees,
    how well they can be mapped, and what changed against the current version."""
    s = ctx.registry.prepare(body.document)
    previous = next(
        (x for x in reversed(ctx.registry.list()) if x.schema_id == s.schema_id and x.version < s.version),
        None,
    )
    return {
        **s.to_summary(),
        "fields": [f.to_dict() for f in s.fields()],
        "findings": schema_findings(s),
        "previous": previous.ref if previous else None,
        "diff": SchemaRegistry.diff(previous, s) if previous else None,
    }


@router.put("/settings/default-format")
def put_default_format(
    body: DefaultFormatIn,
    session: Session = Depends(get_session),
    ctx: ServiceContext = Depends(ctx_dep),
    p: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    """The target format new rounds use when neither the request nor the customer names one."""
    cfg.set_default_target_schema(session, ctx.registry, body.target_schema, p.actor)
    session.commit()
    ctx.refresh()
    return {"target_schema": cfg.default_target_schema(session)}
