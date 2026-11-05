"""Query the selected provider's model catalogue without storing keys or changing configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from forma.core.errors import ValidationFailed
from forma.core.settings import Settings
from forma.db.models import AppSetting
from forma.services import config


def endpoint(provider: str, url: str | None) -> str:
    defaults = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
    value = (url or defaults.get(provider, "")).strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValidationFailed("Enter a base endpoint URL without credentials, query or fragment")
    if value.endswith(("/chat/completions", "/messages", "/models")):
        raise ValidationFailed("Enter the base URL, not the /chat/completions, /messages or /models path")
    if provider == "anthropic" and value.endswith("/v1"):
        value = value[:-3]
    return value


def discover(
    session: Session, base: Settings, provider: str, base_url: str | None, api_key: str | None
) -> dict[str, Any]:
    if base.llm_mock_only:
        raise ValidationFailed("Offline reproduction mode: provider model discovery is disabled")
    if provider not in ("openai", "anthropic", "openai_compatible"):
        raise ValidationFailed("Select a provider first")
    url = endpoint(provider, base_url)
    row = session.get(AppSetting, config.AI_KEY)
    eff = config.llm_settings(base, dict(row.value or {}) if row else {})
    key = (api_key or "").strip()
    if not key:
        if provider != eff.llm_provider or url != endpoint(eff.llm_provider, eff.llm_base_url):
            raise ValidationFailed("Enter the API key again when changing provider or endpoint")
        key = eff.llm_api_key or ""
    if not key and provider != "openai_compatible":
        raise ValidationFailed("Enter an API key first")
    headers = (
        {"x-api-key": key, "anthropic-version": "2023-06-01"}
        if provider == "anthropic"
        else ({"Authorization": "Bearer " + key} if key else {})
    )
    path = url + ("/v1/models" if provider == "anthropic" else "/models")
    models: set[str] = set()
    more = False
    params: dict[str, Any] = {"limit": 100} if provider == "anthropic" else {}
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for _ in range(3):
                with client.stream("GET", path, headers=headers, params=params) as response:
                    if response.status_code != 200:
                        return {
                            "models": [],
                            "source": "unavailable",
                            "message": f"Model listing returned HTTP {response.status_code}. You can enter a model manually and test it.",
                            "truncated": False,
                        }
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 2_000_000:
                            raise ValueError("Catalogue too large")
                    import json

                    data = json.loads(content)
                entries = data.get("data", [])
                models.update(
                    m["id"]
                    for m in entries
                    if isinstance(m, dict) and isinstance(m.get("id"), str) and 0 < len(m["id"]) <= 200
                )
                more = bool(data.get("has_more"))
                if not more or provider != "anthropic" or not data.get("last_id"):
                    break
                params["after_id"] = data["last_id"]
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return {
            "models": [],
            "source": "unavailable",
            "message": "Could not read the model list. Enter the model name manually and test the connection.",
            "truncated": False,
        }
    return {
        "models": sorted(models),
        "source": "provider",
        "truncated": more,
        "message": "Models reported by this endpoint. Connect tests whether the chosen model can return the JSON FORMA needs.",
    }
