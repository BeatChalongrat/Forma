"""Who is calling (ADR-013). One value object for both auth modes; services only ever see this."""

from __future__ import annotations

from dataclasses import dataclass, field

ROLES: tuple[str, ...] = ("importer", "reviewer", "approver", "admin")
# Hierarchical: a role implies every role to its left (admin ⊇ approver ⊇ reviewer ⊇ importer).
_RANK = {r: i for i, r in enumerate(ROLES)}


def normalize_roles(values: object) -> frozenset[str]:
    """Token claims come as a list, a comma/space separated string, or nothing."""
    if values is None:
        return frozenset()
    if isinstance(values, str):
        items = [v for v in values.replace(",", " ").split() if v]
    elif isinstance(values, list | tuple | set | frozenset):
        items = [str(v) for v in values]
    else:
        return frozenset()
    return frozenset(v.strip().lower() for v in items if v.strip().lower() in _RANK)


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    customers: frozenset[str] | None = None  # None = every customer
    name: str | None = None
    mode: str = "dev"  # dev | oidc
    request_id: str | None = None

    @property
    def actor(self) -> str:
        return self.name or self.subject

    def has_role(self, role: str) -> bool:
        need = _RANK[role]
        return any(_RANK[r] >= need for r in self.roles)

    def can_see(self, customer_id: str | None) -> bool:
        return self.customers is None or customer_id is None or customer_id in self.customers

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "name": self.actor,
            "roles": sorted(self.roles),
            "effective_roles": [r for r in ROLES if self.has_role(r)],
            "customers": sorted(self.customers) if self.customers is not None else None,
            "mode": self.mode,
        }


SYSTEM = Principal(subject="system", roles=frozenset(ROLES), mode="system")
