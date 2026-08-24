from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import AppUser, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Application-resolved security principal for one Operly operation."""

    workspace_id: str
    user_id: str | None
    membership_id: str | None
    role: str
    permissions: frozenset[str]
    channel: str
    surface: SurfaceKind = SurfaceKind.UNKNOWN
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self.membership_id is not None

    def can(self, permission: str) -> bool:
        return self.role == "owner" or permission in self.permissions


class ExecutionContextError(PermissionError):
    pass


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
    Surface is a first-class application value; missing or invalid surface values are
    UNKNOWN and therefore fail closed for personal/private capability and context
    access. Membership in the selected workspace is revalidated on every execution
    boundary.
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
        surface=SurfaceKind.coerce(surface),
        conversation_id=conversation_id,
        metadata=dict(metadata or {}),
    )
