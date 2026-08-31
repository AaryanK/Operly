from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Tenant
from packages.database.workspace_module_models import WorkspaceModule
from packages.kernel.contracts import CapabilityExecutionResult, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.catalog import MODULE_CATALOG


PROVIDER_ID = "operly.workspace_system"


def _object(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def workspace_system_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        CapabilitySpec(
            id="workspace.describe",
            version="1.0.0",
            display_name="Describe workspace",
            description="Return trusted workspace identity, role, mode, and timezone.",
            provider_id=PROVIDER_ID,
            scopes=frozenset({"workspace"}),
            input_schema=_object({}),
            output_schema=_object(
                {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "timezone": {"type": "string"},
                    "role": {"type": "string"},
                    "mode": {"type": "string"},
                    "minimum_context": {"type": "object"},
                },
                required=["id", "name", "timezone", "role", "mode", "minimum_context"],
            ),
            permissions=("workspace:read",),
            aliases=("workspace info", "workspace details", "describe workspace"),
            tags=frozenset({"workspace", "identity", "read"}),
            resource_scope="workspace",
        ),
        CapabilitySpec(
            id="workspace.modules.list",
            version="1.0.0",
            display_name="List workspace modules",
            description="List Workspace OS modules visible to the current role and their activation state.",
            provider_id=PROVIDER_ID,
            scopes=frozenset({"workspace"}),
            input_schema=_object({}),
            output_schema=_object(
                {
                    "modules": {
                        "type": "array",
                        "items": _object(
                            {
                                "key": {"type": "string"},
                                "name": {"type": "string"},
                                "category": {"type": "string"},
                                "enabled": {"type": "boolean"},
                                "state": {"type": "string"},
                            },
                            required=["key", "name", "category", "enabled", "state"],
                        ),
                    }
                },
                required=["modules"],
            ),
            permissions=("workspace:read",),
            aliases=("list modules", "workspace apps", "workspace capabilities"),
            tags=frozenset({"workspace", "modules", "read"}),
            resource_scope="workspace",
        ),
    )


class WorkspaceSystemProvider:
    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        del db, capability
        return bool(context.workspace_id)

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del arguments
        if not context.workspace_id:
            raise PermissionError("Workspace capability requires workspace authority")
        if capability.id == "workspace.describe":
            workspace = await db.get(Tenant, context.workspace_id)
            if workspace is None:
                raise LookupError("Workspace is unavailable")
            return CapabilityExecutionResult(
                value={
                    "id": workspace.id,
                    "name": workspace.name,
                    "timezone": workspace.timezone,
                    "role": context.role,
                    "mode": context.workspace_mode,
                    "minimum_context": minimum_context,
                },
                resource_type="workspace",
                resource_id=workspace.id,
            )
        if capability.id == "workspace.modules.list":
            rows = (
                await db.scalars(
                    select(WorkspaceModule).where(WorkspaceModule.tenant_id == context.workspace_id)
                )
            ).all()
            persisted = {row.module_key: row for row in rows}
            modules: list[dict[str, Any]] = []
            for key, manifest in MODULE_CATALOG.items():
                required = str(manifest.get("required_permission") or "workspace:read")
                if not context.can(required):
                    continue
                row = persisted.get(key)
                enabled = row.enabled if row is not None else bool(manifest.get("default_enabled", False))
                modules.append(
                    {
                        "key": key,
                        "name": str(manifest.get("name") or key),
                        "category": str(manifest.get("category") or "other"),
                        "enabled": enabled,
                        "state": row.state if row is not None else ("active" if enabled else "disabled"),
                    }
                )
            return CapabilityExecutionResult(
                value={"modules": modules},
                resource_type="workspace",
                resource_id=context.workspace_id,
            )
        raise LookupError(f"Workspace system capability is not implemented: {capability.id}")
