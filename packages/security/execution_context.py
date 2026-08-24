from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.security.surfaces import SurfaceKind, surface_from_legacy_metadata


class ScopeKind(StrEnum):
    """Trusted authority namespace for one Operly operation."""

    PERSONAL = "personal"
    WORKSPACE = "workspace"


# Personal authority is intentionally explicit rather than treating the account owner
# like a workspace owner. Resource/connector resolvers still decide which account-owned
# objects are available; these permissions only describe the operations the private
# account surface may request through governed providers.
PERSONAL_EXECUTION_PERMISSIONS = frozenset(
    {
        "workspace:read",
        "tasks:read",
        "tasks:write",
        "model:invoke",
        "context:human:read",
        "context:human:write",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Application-resolved security principal for one Operly operation.

    ``workspace_id`` is populated only for workspace authority. A Personal operation
    keeps it ``None``; an optional workspace focus is stored separately and never
    becomes authority merely because the UI or conversation selected it.
    """

    workspace_id: str | None
    user_id: str | None
    membership_id: str | None
    role: str
    permissions: frozenset[str]
    channel: str
    surface: SurfaceKind = SurfaceKind.UNKNOWN
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scope_kind: ScopeKind = ScopeKind.WORKSPACE
    focus_workspace_id: str | None = None

    @property
    def is_member(self) -> bool:
        return self.membership_id is not None

    @property
    def is_personal(self) -> bool:
        return self.scope_kind is ScopeKind.PERSONAL

    @property
    def is_workspace(self) -> bool:
        return self.scope_kind is ScopeKind.WORKSPACE

    @property
    def scope_id(self) -> str | None:
        return self.user_id if self.is_personal else self.workspace_id

    def can(self, permission: str) -> bool:
        # Workspace owner remains the existing root-authority shortcut. Personal
        # authority is allowlisted explicitly above so adding a future workspace
        # permission cannot silently widen a person's private execution authority.
        return (self.is_workspace and self.role == "owner") or permission in self.permissions


class ExecutionContextError(PermissionError):
    pass


def _surface_kind(
    *,
    channel: str,
    surface: SurfaceKind | str | None,
    metadata: dict[str, Any] | None,
) -> SurfaceKind:
    value = SurfaceKind.coerce(surface)
    if value is SurfaceKind.UNKNOWN:
        value = surface_from_legacy_metadata(channel, metadata)
    return value


async def resolve_personal_execution_context(
    db: AsyncSession,
    *,
    user_id: str,
    channel: str,
    surface: SurfaceKind | str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    focus_workspace_id: str | None = None,
    permissions: set[str] | frozenset[str] | None = None,
) -> ExecutionContext:
    """Resolve the authenticated person's private authority without inventing a tenant.

    ``focus_workspace_id`` is only a disambiguation/context hint. When present it is
    revalidated against current membership, but it never populates ``workspace_id``
    and never grants workspace permissions. Any actual workspace operation must still
    acquire a separate workspace ``ExecutionContext`` through
    :func:`resolve_execution_context`.
    """

    user = await db.get(AppUser, user_id)
    if user is None or not user.active:
        raise ExecutionContextError("Operly user is unavailable")

    resolved_focus = None
    if focus_workspace_id:
        workspace = await db.get(Tenant, focus_workspace_id)
        membership = (
            await db.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == focus_workspace_id,
                    TenantMember.user_id == user_id,
                )
            )
            if workspace is not None
            else None
        )
        if workspace is None or membership is None:
            raise ExecutionContextError("Workspace focus is unavailable")
        resolved_focus = workspace.id

    return ExecutionContext(
        workspace_id=None,
        user_id=user.id,
        membership_id=None,
        role="personal_owner",
        permissions=frozenset(
            PERSONAL_EXECUTION_PERMISSIONS if permissions is None else permissions
        ),
        channel=str(channel or "unknown"),
        surface=_surface_kind(channel=channel, surface=surface, metadata=metadata),
        conversation_id=conversation_id,
        metadata=dict(metadata or {}),
        scope_kind=ScopeKind.PERSONAL,
        focus_workspace_id=resolved_focus,
    )


async def resolve_execution_context(
    db: AsyncSession,
    *,
    workspace_id: str,
    user_id: str | None,
    channel: str,
    surface: SurfaceKind | str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    require_membership: bool = True,
) -> ExecutionContext:
    """Resolve workspace membership and permissions from trusted database state.

    Role and permission values are never accepted from the model or request payload.
    Surface is a first-class application value. During ingress migration, an absent
    explicit surface may be recovered only through the conservative legacy bridge;
    missing/invalid web metadata remains UNKNOWN and therefore fails closed for
    personal/private capability and context access. Membership in the selected
    workspace is revalidated on every execution boundary.
    """
    workspace = await db.get(Tenant, workspace_id)
    if workspace is None:
        raise ExecutionContextError("Workspace is unavailable")

    user = await db.get(AppUser, user_id) if user_id else None
    if user_id and (user is None or not user.active):
        raise ExecutionContextError("Operly user is unavailable")

    membership = None
    if user_id:
        membership = await db.scalar(
            select(TenantMember).where(
                TenantMember.tenant_id == workspace_id,
                TenantMember.user_id == user_id,
            )
        )

    if require_membership and membership is None:
        raise ExecutionContextError("User is not a member of this workspace")

    role = membership.role if membership is not None else "guest"
    permissions = (
        await resolve_workspace_permissions(
            db,
            tenant_id=workspace_id,
            role=role,
        )
        if membership is not None
        else set()
    )

    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id,
        membership_id=membership.id if membership is not None else None,
        role=role,
        permissions=frozenset(permissions),
        channel=str(channel or "unknown"),
        surface=_surface_kind(channel=channel, surface=surface, metadata=metadata),
        conversation_id=conversation_id,
        metadata=dict(metadata or {}),
        scope_kind=ScopeKind.WORKSPACE,
        focus_workspace_id=workspace_id,
    )
