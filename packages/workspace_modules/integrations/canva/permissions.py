from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.canva.provider import (
    CANVA_SCOPE_BY_CAPABILITY,
    WorkspaceCanvaProvider,
    _canva_connectors,
)
from packages.workspace_modules.integrations.common import connector_scopes


class AvailableWorkspaceCanvaProvider(WorkspaceCanvaProvider):
    """Require the exact Canva OAuth scope for the selected deterministic tool."""

    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        if not context.workspace_id:
            return False
        if capability.id == "canva.connection.status":
            return True
        required = CANVA_SCOPE_BY_CAPABILITY.get(capability.id)
        if required is None:
            return False
        rows = await _canva_connectors(db, context.workspace_id)
        return any(required.issubset(connector_scopes(row)) for row in rows)
