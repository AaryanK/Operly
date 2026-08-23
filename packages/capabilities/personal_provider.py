from __future__ import annotations

import re
from types import SimpleNamespace

from sqlalchemy import func, select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.account_connector_models import AccountConnector
from packages.database.models import Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.security.temporal_context import resolve_temporal_context


class PersonalRuntimeProvider(BaseProvider):
    """Account-scoped capabilities for the authenticated human's private Operly surface.

    Personal AI is not a tenant and never receives blanket cross-tenant authority. It
    may inspect every workspace the human belongs to and may *delegate execution* into
    exactly one of those workspaces. Delegated execution goes back through the normal
    PluginAgentHarness, permission resolver, connector availability checks and Action
    firewall, so using a capability from a private DM does not bypass workspace policy.
    """

    name = "operly_personal_runtime"
    capabilities = (
        CapabilityDefinition(
            "runtime.context",
            "runtime_context",
            "Read application-controlled time and request scope for relative dates/times.",
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
            "List every Operly workspace the authenticated human belongs to, including role.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
        CapabilityDefinition(
            "account.create_workspace",
            "account_create_workspace",
            "Create a new workspace owned by the authenticated human. Use when they ask to create a server/workspace.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "timezone": {"type": "string", "maxLength": 100},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            category="account",
        ),
        CapabilityDefinition(
            "account.update_workspace",
            "account_update_workspace",
            "Update a workspace name or timezone when the current human has workspace settings authority. This never changes membership or connector ownership.",
            {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "timezone": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                "required": ["workspace"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
            category="account",
        ),
        CapabilityDefinition(
            "account.list_personal_connectors",
            "account_list_personal_connectors",
            "List the authenticated human's personal connectors and granted capability scopes. Never returns credentials or tokens.",
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
            "Return bounded high-level counts for the human's authorized workspaces.",
            {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Optional workspace name or slug; omit for all."}
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
        CapabilityDefinition(
            "account.workspace_capabilities",
            "account_workspace_capabilities",
            "Inspect the canonical plugin capability registry for workspaces the human belongs to. Returns resolved permissions and live availability. Use this instead of guessing what Operly can do.",
            {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Optional workspace name or slug; omit for all."}
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
        CapabilityDefinition(
            "account.workspace_execute",
            "account_workspace_execute",
            "Execute one canonical Operly plugin capability inside a workspace the human belongs to. First inspect account.workspace_capabilities when the capability ID is uncertain. This tool never bypasses role permissions, connector scopes, approvals, verification, or audit. If the underlying action requires approval, return that pending state rather than claiming it happened.",
            {
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "minLength": 1, "description": "Workspace name, slug, or exact ID."},
                    "capability_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "arguments": {"type": "object", "description": "Arguments for the selected capability."},
                },
                "required": ["workspace", "capability_id", "arguments"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("workspace:read",),
            approval_policy=ApprovalPolicy.AUTO,
            category="account",
        ),
    )

    @staticmethod
    def _workspace_matches(memberships, requested: str):
        needle = " ".join(str(requested or "").lower().split())
        if not needle:
            return memberships
        return [
            (member, tenant)
            for member, tenant in memberships
            if needle == str(tenant.id).lower()
            or needle == str(tenant.slug or "").lower()
            or needle == " ".join(tenant.name.lower().split())
            or needle in " ".join(tenant.name.lower().split())
        ]

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
        return slug or "workspace"

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
            {
                "id": tenant.id,
                "name": tenant.name,
                "slug": tenant.slug,
                "role": member.role,
                "timezone": tenant.timezone,
            }
            for member, tenant in memberships
        ]

        if capability_name == "account.list_workspaces":
            return CapabilityResult(True, False, {"workspaces": rows, "count": len(rows)})

        if capability_name == "account.create_workspace":
            name = " ".join(str(arguments.get("name") or "").replace("\x00", "").split()).strip()[:200]
            if not name:
                return CapabilityResult(False, False, {"reason": "workspace_name_required"})
            base = self._slug(name)
            slug = base
            suffix = 2
            while await context.db.scalar(select(Tenant.id).where(Tenant.slug == slug)):
                slug = f"{base[:72]}-{suffix}"
                suffix += 1
            tenant = Tenant(
                name=name,
                slug=slug,
                timezone=(" ".join(str(arguments.get("timezone") or "UTC").split()) or "UTC")[:100],
            )
            context.db.add(tenant)
            await context.db.flush()
            context.db.add(TenantMember(tenant_id=tenant.id, user_id=context.actor_id, role="owner"))
            await context.db.flush()
            return CapabilityResult(
                True,
                True,
                {"workspace_id": tenant.id, "name": tenant.name, "slug": tenant.slug, "role": "owner"},
                tenant.id,
            )

        if capability_name == "account.list_personal_connectors":
            connectors = (
                await context.db.scalars(
                    select(AccountConnector)
                    .where(AccountConnector.user_id == context.actor_id)
                    .order_by(AccountConnector.created_at)
                )
            ).all()
            return CapabilityResult(
                True,
                False,
                {
                    "connectors": [
                        {
                            "id": row.id,
                            "provider": row.provider,
                            "display_name": row.display_name,
                            "account": row.provider_account_id,
                            "status": row.status,
                            "enabled": row.enabled,
                            "health": row.health_status,
                            "ownership": "personal",
                        }
                        for row in connectors
                    ]
                },
            )

        requested = str(arguments.get("workspace") or "")
        selected = self._workspace_matches(memberships, requested)

        if capability_name == "account.update_workspace":
            if len(selected) != 1:
                return CapabilityResult(
                    False,
                    False,
                    {"reason": "workspace_ambiguous_or_missing", "matches": [tenant.name for _, tenant in selected[:10]]},
                )
            member, tenant = selected[0]
            permissions = await resolve_workspace_permissions(context.db, tenant_id=tenant.id, role=member.role)
            if member.role != "owner" and "workspace:settings:manage" not in permissions:
                return CapabilityResult(False, False, {"reason": "workspace_settings_permission_denied"})
            changed = False
            if arguments.get("name") is not None:
                name = " ".join(str(arguments.get("name") or "").replace("\x00", "").split()).strip()[:200]
                if not name:
                    return CapabilityResult(False, False, {"reason": "workspace_name_required"})
                if name != tenant.name:
                    tenant.name = name
                    changed = True
            if arguments.get("timezone") is not None:
                timezone = " ".join(str(arguments.get("timezone") or "").replace("\x00", "").split()).strip()[:100]
                if not timezone:
                    return CapabilityResult(False, False, {"reason": "workspace_timezone_required"})
                if timezone != tenant.timezone:
                    tenant.timezone = timezone
                    changed = True
            await context.db.flush()
            return CapabilityResult(
                True,
                changed,
                {"workspace_id": tenant.id, "name": tenant.name, "timezone": tenant.timezone, "role": member.role},
                tenant.id,
            )

        if capability_name == "account.workspace_capabilities":
            from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext

            harness = PluginAgentHarness()
            workspace_reports = []
            for member, tenant in selected[:20]:
                permissions = await resolve_workspace_permissions(context.db, tenant_id=tenant.id, role=member.role)
                plugin_context = PluginInvocationContext(
                    tenant_id=tenant.id,
                    user_id=context.actor_id,
                    role=member.role,
                    objective="inspect workspace capabilities",
                    channel="web",
                    metadata={"is_direct": True, "shared_surface": False},
                )
                registry = await harness.registry_for(plugin_context)
                capability_ids = [definition.id for definition in registry.definitions() if not definition.id.startswith("account.")]
                described = registry.describe(
                    tenant.id,
                    capability_ids,
                    authority=permissions,
                    include_schema=False,
                )
                authorized = [item for item in described if item.get("authorized") is not False]
                authorized.sort(key=lambda item: (str(item.get("category") or ""), str(item.get("id") or "")))
                available_count = sum(1 for item in authorized if bool((item.get("availability") or {}).get("available")))
                workspace_reports.append(
                    {
                        "workspace": tenant.name,
                        "workspace_id": tenant.id,
                        "role": member.role,
                        "permissions": sorted(permissions),
                        "capability_count": len(authorized),
                        "available_count": available_count,
                        "unavailable_count": len(authorized) - available_count,
                        "capabilities": authorized,
                    }
                )
            return CapabilityResult(True, False, {"workspaces": workspace_reports, "count": len(workspace_reports)})

        if capability_name == "account.workspace_execute":
            if len(selected) != 1:
                return CapabilityResult(
                    False,
                    False,
                    {"reason": "workspace_ambiguous_or_missing", "matches": [tenant.name for _, tenant in selected[:10]]},
                )
            member, tenant = selected[0]
            capability_id = str(arguments.get("capability_id") or "").strip()
            supplied_arguments = arguments.get("arguments")
            if not capability_id or not isinstance(supplied_arguments, dict):
                return CapabilityResult(False, False, {"reason": "capability_id_and_arguments_required"})
            if capability_id.startswith("account."):
                return CapabilityResult(False, False, {"reason": "recursive_account_capability_not_allowed"})

            from packages.capabilities.agent_harness import PluginAgentHarness, PluginInvocationContext

            permissions = await resolve_workspace_permissions(context.db, tenant_id=tenant.id, role=member.role)
            metadata = dict((invocation.get("metadata") or {}))
            metadata.update({"is_direct": True, "shared_surface": False, "personal_delegate": True})
            plugin_context = PluginInvocationContext(
                tenant_id=tenant.id,
                user_id=context.actor_id,
                role=member.role,
                objective=str(metadata.get("objective") or f"Personal AI request: {capability_id}")[:2000],
                channel=str(invocation.get("channel") or "web"),
                metadata=metadata,
            )
            harness = PluginAgentHarness()
            registry = await harness.registry_for(plugin_context)
            try:
                definition = registry.definition(capability_id)
            except LookupError:
                return CapabilityResult(False, False, {"reason": "capability_not_registered", "capability_id": capability_id})
            described = registry.describe(
                tenant.id,
                [capability_id],
                authority=permissions,
                include_schema=False,
            )
            item = described[0] if described else {}
            availability = item.get("availability") or {}
            if item.get("authorized") is False or not PluginAgentHarness.capability_authorized(capability_id, permissions, plugin_context):
                return CapabilityResult(False, False, {"reason": "permission_denied", "capability_id": capability_id})
            if availability.get("available") is not True:
                return CapabilityResult(
                    False,
                    False,
                    {"reason": availability.get("reason") or "capability_unavailable", "capability_id": capability_id, "availability": availability},
                )
            view = await harness.session_view_for(plugin_context, authority=permissions, registry=registry)
            view.expose([definition.id])
            payload = await harness.invoke(definition.id, dict(supplied_arguments), plugin_context)
            success = bool(payload.get("ok"))
            changed = bool(payload.get("changed"))
            return CapabilityResult(
                success,
                changed,
                {
                    "workspace": tenant.name,
                    "workspace_id": tenant.id,
                    "capability_id": definition.id,
                    "result": payload,
                },
                str(payload.get("external_reference") or "") or None,
            )

        from packages.database.business_models import BusinessOrder, Contact, Lead
        from packages.database.models import Task

        overviews = []
        for member, tenant in selected[:20]:
            permissions = await resolve_workspace_permissions(context.db, tenant_id=tenant.id, role=member.role)
            item = {"workspace": tenant.name, "role": member.role}
            if member.role == "owner" or "crm:read" in permissions:
                item["contacts"] = int(await context.db.scalar(select(func.count(Contact.id)).where(Contact.tenant_id == tenant.id)) or 0)
                item["leads"] = int(await context.db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == tenant.id)) or 0)
            if member.role == "owner" or "orders:write" in permissions:
                item["orders"] = int(await context.db.scalar(select(func.count(BusinessOrder.id)).where(BusinessOrder.tenant_id == tenant.id)) or 0)
            if member.role == "owner" or "tasks:read" in permissions:
                item["open_tasks"] = int(
                    await context.db.scalar(select(func.count(Task.id)).where(Task.tenant_id == tenant.id, Task.status != "done")) or 0
                )
            overviews.append(item)
        return CapabilityResult(True, False, {"workspaces": overviews, "count": len(overviews)})

    async def verify(self, context, capability_name, arguments, result):
        return CapabilityResult(result.success, result.changed, result.evidence, result.external_reference)
