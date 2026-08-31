from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.security.execution_context import (
    ExecutionContext,
    ScopeKind,
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class TrustedIngress:
    """Application/connector-owned facts for one inbound interaction.

    Roles and permissions are intentionally absent. An ingress adapter can identify
    the authenticated principal and source space, but authority is always reloaded
    from Operly state and trusted source-platform metadata.
    """

    scope_kind: ScopeKind
    user_id: str | None
    workspace_id: str | None
    channel: str
    surface: SurfaceKind
    conversation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    focus_workspace_id: str | None = None


async def resolve_ingress_context(db: AsyncSession, ingress: TrustedIngress) -> ExecutionContext:
    if ingress.scope_kind is ScopeKind.PERSONAL:
        if not ingress.user_id:
            raise PermissionError("Personal ingress requires an authenticated Operly user")
        return await resolve_personal_execution_context(
            db,
            user_id=ingress.user_id,
            channel=ingress.channel,
            surface=ingress.surface,
            conversation_id=ingress.conversation_id,
            metadata=ingress.metadata,
            focus_workspace_id=ingress.focus_workspace_id,
        )
    if not ingress.workspace_id:
        raise PermissionError("Workspace ingress requires a resolved workspace")
    return await resolve_execution_context(
        db,
        workspace_id=ingress.workspace_id,
        user_id=ingress.user_id,
        channel=ingress.channel,
        surface=ingress.surface,
        conversation_id=ingress.conversation_id,
        metadata=ingress.metadata,
    )
