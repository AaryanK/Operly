"""Account-owned workspace provisioning through the canonical Operly Kernel.

No direct REST relay, synthetic workspace token, model-derived user ID, or
implicit access to newly created Workspace tools. The Kernel owns approval,
idempotency, tracing and final transaction commit.
"""
from __future__ import annotations

import os
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Tenant, TenantMember
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext

PROVIDER_ID = "operly.personal.workspace_provision"


def personal_workspace_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        CapabilitySpec(
            id="workspace.list_owned",
            version="1.0.0",
            display_name="List my Operly workspaces",
            description="Read workspaces the authenticated Personal user actually belongs to. Does not grant Workspace execution.",
            provider_id=PROVIDER_ID,
            scopes=frozenset({"personal"}),
            resource_scope="personal",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"workspaces": {"type": "array", "items": {
                    "type": "object", "properties": {
                        "id": {"type": "string"}, "name": {"type": "string"},
                        "timezone": {"type": "string"}, "role": {"type": "string"},
                    },
                    "required": ["id", "name", "timezone", "role"],
                    "additionalProperties": False,
                }}},
                "required": ["workspaces"], "additionalProperties": False,
            },
            permissions=("workspace:read",),
            tags=frozenset({"personal", "workspace", "list", "read"}),
        ),
        CapabilitySpec(
            id="workspace.create",
            version="1.0.0",
            display_name="Create Operly workspace",
            description="Provision one new workspace owned by the authenticated Personal user; exact human approval required. Never grants existing Workspace permissions or tools.",
            provider_id=PROVIDER_ID,
            scopes=frozenset({"personal"}),
            resource_scope="personal",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "timezone": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                "required": ["name"], "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"},
                    "timezone": {"type": "string"}, "role": {"type": "string"},
                    "current": {"type": "boolean"},
                },
                "required": ["id", "name", "timezone", "role", "current"],
                "additionalProperties": False,
            },
            permissions=("workspace:create",),
            risk=CapabilityRisk.HIGH,
            approval_required=True,
            reversible=False,
            emits=("workspace.created",),
            tags=frozenset({"personal", "workspace", "create", "provision", "approval"}),
        ),
    )


class PersonalWorkspaceProvisionProvider:
    async def is_available(
        self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec,
    ) -> bool:
        del db
        if not context.is_personal or not context.user_id:
            return False
        if capability.id == "workspace.create":
            return os.getenv("OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED") == "1"
        return capability.id == "workspace.list_owned"

    async def execute(
        self, db: AsyncSession, *, context: ExecutionContext,
        capability: CapabilitySpec, arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        if not context.is_personal or not context.user_id or context.workspace_id is not None:
            raise PermissionError("A signed-in Personal user is required")
        if capability.id == "workspace.list_owned":
            records = (await db.execute(
                select(Tenant, TenantMember.role)
                .join(TenantMember, TenantMember.tenant_id == Tenant.id)
                .where(TenantMember.user_id == context.user_id)
                .order_by(Tenant.created_at.desc()).limit(100)
            )).all()
            return CapabilityExecutionResult(value={"workspaces": [
                {"id": tenant.id, "name": tenant.name, "timezone": tenant.timezone,
                 "role": role}
                for tenant, role in records
            ]})
        if capability.id != "workspace.create":
            raise LookupError("Unsupported Personal workspace capability")
        if os.getenv("OPERLY_PERSONAL_MCP_WORKSPACE_CREATE_ENABLED") != "1":
            raise PermissionError("Workspace creation via MCP is disabled")
        name = " ".join(str(arguments["name"]).replace("\x00", "").split()).strip()
        timezone = " ".join(str(arguments.get("timezone") or "UTC").replace("\x00", "").split()).strip() or "UTC"
        if not name or len(name) > 200 or len(timezone) > 100:
            raise ValueError("Invalid workspace name or timezone")
        tenant = Tenant(name=name, timezone=timezone, slug=None)
        db.add(tenant)
        await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=context.user_id, role="owner"))
        await db.flush()
        return CapabilityExecutionResult(
            value={"id": tenant.id, "name": tenant.name, "timezone": tenant.timezone,
                   "role": "owner", "current": False},
            resource_type="workspace",
            resource_id=tenant.id,
            event_payload={"workspace_id": tenant.id},
        )
