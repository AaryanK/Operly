from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.workspace_os_router import ENTITY_REGISTRY, _module_enabled
from packages.kernel.contracts import CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.tools.business import WorkspaceBusinessProvider
from packages.workspace_modules.tools.controls import WorkspaceControlProvider
from packages.workspace_modules.tools.records import WorkspaceOSProvider


class AvailableWorkspaceOSProvider(WorkspaceOSProvider):
    async def is_available(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec) -> bool:
        if not context.workspace_id:
            return False
        operation = self._operations.get(capability.id)
        if operation is None:
            return False
        config = ENTITY_REGISTRY.get(operation.entity)
        if config is None:
            return False
        return await _module_enabled(db, context.workspace_id, config.module)


class AvailableWorkspaceControlProvider(WorkspaceControlProvider):
    async def is_available(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec) -> bool:
        if not context.workspace_id:
            return False
        if capability.id.startswith("workspace.inventory."):
            return await _module_enabled(db, context.workspace_id, "inventory")
        return True


class AvailableWorkspaceBusinessProvider(WorkspaceBusinessProvider):
    _MODULE_BY_CAPABILITY = {
        "workspace.customer.snapshot": "crm",
        "workspace.sales.complete": "sales",
        "workspace.finance.invoice.create_simple": "finance",
        "workspace.finance.payment.record": "finance",
    }

    async def is_available(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec) -> bool:
        if not context.workspace_id:
            return False
        module = self._MODULE_BY_CAPABILITY.get(capability.id)
        if module is None:
            return True
        return await _module_enabled(db, context.workspace_id, module)
