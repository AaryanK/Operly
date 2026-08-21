from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.models import Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.security.temporal_context import resolve_temporal_context


class PersonalRuntimeProvider(BaseProvider):
    """Personal/account capabilities exposed only in private user surfaces by the harness."""

    name = "operly_personal_runtime"
    capabilities = (
        CapabilityDefinition(
            "runtime.context",
            "runtime_context",
            "Read the application-controlled current time context and request scope. Use this for relative dates/times such as today, tomorrow, tonight, or next Monday.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="runtime",
        ),
        CapabilityDefinition(
            "account.list_workspaces",
            "account_list_workspaces",
            "List every Operly workspace the current authenticated human belongs to, including role. This is a personal-account capability for DMs/private surfaces, not a shared-workspace capability.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
        CapabilityDefinition(
            "account.workspace_overview",
            "account_workspace_overview",
            "Return bounded high-level counts for the current human's authorized workspaces. Use in a private DM for cross-workspace questions; each workspace is independently permission-checked before disclosure.",
            {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Optional workspace name; omit for every authorized workspace."}
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
    )

    async def execute(self, context, capability_name, arguments):
        if not context.actor_id:
            return CapabilityResult(False, False, {"reason": "authenticated_user_required"})

        invocation = context.invocation or {}
        if capability_name == "runtime.context":
            temporal = invocation.get("temporal_context")
            if not isinstance(temporal, dict):
                temporal = (
                    await resolve_temporal_context(
                        context.db,
                        user_id=context.actor_id,
                        tenant_id=context.tenant_id,
                    )
                ).as_dict()
            return CapabilityResult(
                True,
                False,
                {
                    "time": temporal,
                    "origin": invocation.get("channel") or "unknown",
                    "is_direct": bool((invocation.get("metadata") or {}).get("is_direct")),
                    "current_workspace_id": context.tenant_id,
                },
            )

        memberships = (
            await context.db.execute(
                select(TenantMember, Tenant)
                .join(Tenant, Tenant.id == TenantMember.tenant_id)
                .where(TenantMember.user_id == context.actor_id)
                .order_by(Tenant.name)
            )
        ).all()
        rows = [
            {"id": tenant.id, "name": tenant.name, "role": member.role, "timezone": tenant.timezone}
            for member, tenant in memberships
        ]
        if capability_name == "account.list_workspaces":
            return CapabilityResult(True, False, {"workspaces": rows, "count": len(rows)})

        requested = " ".join(str(arguments.get("workspace") or "").lower().split())
        selected = [
            (member, tenant)
            for member, tenant in memberships
            if not requested
            or requested in " ".join(tenant.name.lower().split())
            or requested == str(tenant.slug or "").lower()
        ]
        from packages.database.business_models import Contact, Lead, Order
        from packages.database.models import Task
        from sqlalchemy import func

        overviews = []
        for member, tenant in selected[:20]:
            permissions = await resolve_workspace_permissions(
                context.db, tenant_id=tenant.id, role=member.role
            )
            item = {"workspace": tenant.name, "role": member.role}
            if member.role == "owner" or "crm:read" in permissions:
                item["contacts"] = int(
                    await context.db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == tenant.id)) or 0
                )
                item["leads"] = int(
                    await context.db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == tenant.id)) or 0
                )
            if member.role == "owner" or "orders:write" in permissions:
                item["orders"] = int(
                    await context.db.scalar(select(func.count(Order.id)).where(Order.tenant_id == tenant.id)) or 0
                )
            if member.role == "owner" or "tasks:read" in permissions:
                item["open_tasks"] = int(
                    await context.db.scalar(
                        select(func.count(Task.id)).where(Task.tenant_id == tenant.id, Task.status != "done")
                    ) or 0
                )
            overviews.append(item)
        return CapabilityResult(True, False, {"workspaces": overviews, "count": len(overviews)})

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(result.success, False, result.evidence, result.external_reference)
