"""Authentication, authorisation and tenancy (ADR-013)."""

from forma.auth.deps import (
    authenticated_principal,
    current_principal,
    ensure_all_customers,
    ensure_auth_is_safe,
    ensure_customer,
    llm_rate_limited,
    require_role,
)
from forma.auth.principal import ROLES, SYSTEM, Principal

__all__ = [
    "ROLES",
    "SYSTEM",
    "Principal",
    "authenticated_principal",
    "current_principal",
    "ensure_all_customers",
    "ensure_auth_is_safe",
    "ensure_customer",
    "llm_rate_limited",
    "require_role",
]
