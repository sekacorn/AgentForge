"""Role-based access control (RBAC).

A small, dependency-free authorization primitive. The orchestrator can be given
a :class:`Principal` and an :class:`AccessController`; sensitive operations
(running agents, using dangerous tools, viewing the audit log) are then checked
against the principal's roles. Enterprises typically map their IdP groups onto
these roles.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from forge.exceptions import AccessDeniedError


class Permission(enum.StrEnum):
    """Discrete capabilities that can be granted to roles."""

    RUN_AGENT = "run_agent"
    USE_TOOL = "use_tool"
    USE_DANGEROUS_TOOL = "use_dangerous_tool"
    VIEW_AUDIT = "view_audit"
    MANAGE_CONFIG = "manage_config"


#: Default role -> permission mapping. Override by passing your own to
#: :class:`AccessController`.
DEFAULT_ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "admin": set(Permission),
    "operator": {
        Permission.RUN_AGENT,
        Permission.USE_TOOL,
        Permission.USE_DANGEROUS_TOOL,
        Permission.VIEW_AUDIT,
    },
    "developer": {Permission.RUN_AGENT, Permission.USE_TOOL},
    "viewer": {Permission.VIEW_AUDIT},
}


@dataclass(frozen=True)
class Principal:
    """An authenticated actor with a set of roles."""

    id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def system(cls) -> Principal:
        """The built-in privileged principal used when RBAC is not configured."""
        return cls(id="system", roles=frozenset({"admin"}))


class AccessController:
    """Resolves a principal's permissions and enforces requirements."""

    def __init__(self, role_permissions: dict[str, set[Permission]] | None = None) -> None:
        self._role_permissions = role_permissions or DEFAULT_ROLE_PERMISSIONS

    def permissions_for(self, principal: Principal) -> set[Permission]:
        granted: set[Permission] = set()
        for role in principal.roles:
            granted |= self._role_permissions.get(role, set())
        return granted

    def can(self, principal: Principal, permission: Permission) -> bool:
        return permission in self.permissions_for(principal)

    def require(self, principal: Principal, permission: Permission) -> None:
        """Raise :class:`AccessDeniedError` unless ``principal`` has ``permission``."""
        if not self.can(principal, permission):
            raise AccessDeniedError(
                f"Principal {principal.id!r} lacks permission {permission.value!r}",
                context={"roles": sorted(principal.roles), "required": permission.value},
            )
