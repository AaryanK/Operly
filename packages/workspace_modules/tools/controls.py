from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext
from apps.api.workspace_os_router import (
    _activity,
    _clean_text,
    _enable_with_dependencies,
    _module_enabled,
    _module_row,
    _upsert_module,
)
from packages.database.business_models import ActivityEvent, CatalogItem, InventoryMovement
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.workspace_security_models import WorkspaceRole, WorkspaceRolePermission
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.security.permissions import (
    DEFAULT_ROLE_AUTHORITY,
    KNOWN_PERMISSIONS,
    normalize_role_key,
    resolve_workspace_permissions,
    validate_permissions,
)
from packages.security.workspace_invitations import WorkspaceInvitationError, WorkspaceInvitationService
from packages.workspace_modules.catalog import MODULE_CATALOG, WORKSPACE_PRESETS, module_manifest, preset_manifest


PROVIDER_ID = "operly.workspace_control"


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


def _array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


def _capability(
    capability_id: str,
    name: str,
    description: str,
    *,
    permission: str,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
    approval: bool = False,
    reversible: bool = False,
    emits: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema or _object({}),
        output_schema=output_schema or _object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        emits=emits,
        tags=frozenset(("workspace", "deterministic", *tags)),
        resource_scope="workspace",
    )


def workspace_control_capabilities() -> tuple[CapabilitySpec, ...]:
    member = _object(
        {
            "user_id": {"type": "string"},
            "display_name": {"type": "string"},
            "email": {"type": "string"},
            "role": {"type": "string"},
        },
        required=["user_id", "display_name", "email", "role"],
    )
    role = _object(
        {
            "key": {"type": "string"},
            "name": {"type": "string"},
            "system": {"type": "boolean"},
            "permissions": _array({"type": "string"}),
        },
        required=["key", "name", "system", "permissions"],
    )
    invitation = _object(
        {
            "id": {"type": "string"},
            "target_email": {"type": ["string", "null"]},
            "role": {"type": "string"},
            "status": {"type": "string"},
            "source": {"type": "string"},
            "expires_at": {"type": "string"},
            "accepted_at": {"type": ["string", "null"]},
            "created_at": {"type": "string"},
        },
        required=["id", "target_email", "role", "status", "source", "expires_at", "accepted_at", "created_at"],
    )
    movement = _object(
        {
            "id": {"type": "string"},
            "item_id": {"type": "string"},
            "quantity_change": {"type": "integer"},
            "reason": {"type": "string"},
            "created_at": {"type": "string"},
        },
        required=["id", "item_id", "quantity_change", "reason", "created_at"],
    )
    return (
        _capability(
            "workspace.summary.read",
            "Read workspace operating summary",
            "Return deterministic operating counts and totals for the authorized workspace.",
            permission="workspace:read",
            output_schema=_object({}, additional=True),
            tags=("summary", "analytics", "read"),
        ),
        _capability(
            "workspace.activity.list",
            "List workspace activity",
            "List deterministic business activity events for the authorized workspace.",
            permission="workspace:read",
            input_schema=_object({"limit": {"type": "integer", "minimum": 1, "maximum": 200}}),
            output_schema=_object({"events": _array(_object({}, additional=True))}, required=["events"]),
            tags=("activity", "audit", "read"),
        ),
        _capability(
            "workspace.settings.update",
            "Update workspace settings",
            "Update workspace name, timezone, or logo through the governed execution path.",
            permission="workspace:settings:manage",
            input_schema=_object(
                {
                    "name": {"type": ["string", "null"], "maxLength": 200},
                    "timezone": {"type": ["string", "null"], "maxLength": 100},
                    "logo_url": {"type": ["string", "null"], "maxLength": 1000},
                }
            ),
            output_schema=_object({"workspace": _object({}, additional=True)}, required=["workspace"]),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("workspace.settings.updated",),
            tags=("settings", "write"),
        ),
        _capability(
            "workspace.modules.set",
            "Set workspace module state",
            "Enable or disable a Workspace OS module while preserving dependency rules.",
            permission="workspace:modules:manage",
            input_schema=_object(
                {
                    "module_key": {"type": "string", "minLength": 1, "maxLength": 60},
                    "enabled": {"type": "boolean"},
                    "configuration": {"type": "object"},
                },
                required=["module_key", "enabled"],
            ),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("workspace.module.updated",),
            tags=("modules", "write"),
        ),
        _capability(
            "workspace.presets.list",
            "List workspace presets",
            "List deterministic Workspace OS module packs available to this workspace.",
            permission="workspace:read",
            output_schema=_object({"presets": _array(_object({}, additional=True))}, required=["presets"]),
            tags=("modules", "presets", "read"),
        ),
        _capability(
            "workspace.presets.apply",
            "Apply workspace preset",
            "Enable the module set belonging to a Workspace OS preset.",
            permission="workspace:modules:manage",
            input_schema=_object({"preset_key": {"type": "string", "minLength": 1, "maxLength": 80}}, required=["preset_key"]),
            output_schema=_object({}, additional=True),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("workspace.preset.applied",),
            tags=("modules", "presets", "write"),
        ),
        _capability(
            "workspace.members.list",
            "List workspace members",
            "List users and roles in the authorized workspace.",
            permission="workspace:read",
            output_schema=_object({"members": _array(member)}, required=["members"]),
            tags=("members", "read"),
        ),
        _capability(
            "workspace.members.add",
            "Add workspace member",
            "Add an existing active Operly user to the workspace with a specific role.",
            permission="workspace:members:manage",
            input_schema=_object(
                {
                    "email": {"type": "string", "minLength": 3, "maxLength": 320},
                    "role": {"type": "string", "minLength": 1, "maxLength": 30},
                },
                required=["email"],
            ),
            output_schema=member,
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=True,
            emits=("workspace.member.added",),
            tags=("members", "write", "access"),
        ),
        _capability(
            "workspace.members.role.update",
            "Update workspace member role",
            "Change a workspace member's role while preserving ownership invariants.",
            permission="workspace:members:manage",
            input_schema=_object(
                {
                    "user_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "role": {"type": "string", "minLength": 1, "maxLength": 30},
                },
                required=["user_id", "role"],
            ),
            output_schema=_object({"user_id": {"type": "string"}, "role": {"type": "string"}}, required=["user_id", "role"]),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=True,
            emits=("workspace.member.role_updated",),
            tags=("members", "roles", "write", "access"),
        ),
        _capability(
            "workspace.members.remove",
            "Remove workspace member",
            "Remove a workspace member while preserving the last-owner invariant.",
            permission="workspace:members:manage",
            input_schema=_object({"user_id": {"type": "string", "minLength": 1, "maxLength": 80}}, required=["user_id"]),
            output_schema=_object({"ok": {"type": "boolean"}, "user_id": {"type": "string"}}, required=["ok", "user_id"]),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("workspace.member.removed",),
            tags=("members", "delete", "access"),
        ),
        _capability(
            "workspace.roles.list",
            "List workspace roles",
            "List system and custom workspace roles with their effective permission sets.",
            permission="workspace:read",
            output_schema=_object(
                {"roles": _array(role), "known_permissions": _array({"type": "string"})},
                required=["roles", "known_permissions"],
            ),
            tags=("roles", "permissions", "read"),
        ),
        _capability(
            "workspace.roles.permissions.set",
            "Set workspace role permissions",
            "Create or update a workspace role's explicit permission set.",
            permission="workspace:roles:manage",
            input_schema=_object(
                {
                    "role_key": {"type": "string", "minLength": 1, "maxLength": 30},
                    "name": {"type": ["string", "null"], "maxLength": 120},
                    "permissions": _array({"type": "string"}),
                },
                required=["role_key", "permissions"],
            ),
            output_schema=role,
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workspace.role.permissions_updated",),
            tags=("roles", "permissions", "write", "access"),
        ),
        _capability(
            "workspace.invitations.list",
            "List workspace invitations",
            "List workspace invitations and their lifecycle state.",
            permission="workspace:members:manage",
            output_schema=_object({"invitations": _array(invitation)}, required=["invitations"]),
            tags=("members", "invitations", "read"),
        ),
        _capability(
            "workspace.invitations.create",
            "Create workspace invitation",
            "Create a time-bounded workspace invitation after approval.",
            permission="workspace:members:manage",
            input_schema=_object(
                {
                    "email": {"type": ["string", "null"], "maxLength": 320},
                    "role": {"type": "string", "minLength": 1, "maxLength": 30},
                    "ttl_days": {"type": "integer", "minimum": 1, "maximum": 30},
                }
            ),
            output_schema=_object(
                {
                    "id": {"type": "string"},
                    "role": {"type": "string"},
                    "target_email": {"type": ["string", "null"]},
                    "expires_at": {"type": "string"},
                    "token": {"type": "string"},
                },
                required=["id", "role", "target_email", "expires_at", "token"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=True,
            emits=("workspace.invitation.created",),
            tags=("members", "invitations", "write", "access"),
        ),
        _capability(
            "workspace.invitations.revoke",
            "Revoke workspace invitation",
            "Revoke an existing workspace invitation.",
            permission="workspace:members:manage",
            input_schema=_object({"invitation_id": {"type": "string", "minLength": 1, "maxLength": 80}}, required=["invitation_id"]),
            output_schema=_object({"id": {"type": "string"}, "status": {"type": "string"}}, required=["id", "status"]),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=False,
            emits=("workspace.invitation.revoked",),
            tags=("members", "invitations", "write", "access"),
        ),
        _capability(
            "workspace.inventory.movements.list",
            "List inventory movements",
            "List stock adjustments for one catalog item in the authorized workspace.",
            permission="inventory:read",
            input_schema=_object(
                {
                    "item_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                required=["item_id"],
            ),
            output_schema=_object({"movements": _array(movement)}, required=["movements"]),
            tags=("inventory", "read"),
        ),
        _capability(
            "workspace.inventory.adjust",
            "Adjust inventory stock",
            "Apply a deterministic stock adjustment and record its movement.",
            permission="inventory:write",
            input_schema=_object(
                {
                    "item_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "quantity_change": {"type": "integer"},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                required=["item_id", "quantity_change"],
            ),
            output_schema=_object(
                {
                    "item_id": {"type": "string"},
                    "stock_qty": {"type": "integer"},
                    "movement_id": {"type": "string"},
                },
                required=["item_id", "stock_qty", "movement_id"],
            ),
            risk=CapabilityRisk.LOW,
            reversible=True,
            emits=("inventory.stock.adjusted",),
            tags=("inventory", "write"),
        ),
    )


class WorkspaceControlProvider:
    def __init__(self) -> None:
        self._handlers = {
            "workspace.summary.read": self._summary,
            "workspace.activity.list": self._activity_list,
            "workspace.settings.update": self._settings_update,
            "workspace.modules.set": self._module_set,
            "workspace.presets.list": self._presets_list,
            "workspace.presets.apply": self._preset_apply,
            "workspace.members.list": self._members_list,
            "workspace.members.add": self._member_add,
            "workspace.members.role.update": self._member_role_update,
            "workspace.members.remove": self._member_remove,
            "workspace.roles.list": self._roles_list,
            "workspace.roles.permissions.set": self._role_permissions_set,
            "workspace.invitations.list": self._invitations_list,
            "workspace.invitations.create": self._invitation_create,
            "workspace.invitations.revoke": self._invitation_revoke,
            "workspace.inventory.movements.list": self._inventory_movements,
            "workspace.inventory.adjust": self._inventory_adjust,
        }

    async def _auth(self, db: AsyncSession, context: ExecutionContext) -> AuthContext:
        if not context.workspace_id or not context.user_id:
            raise PermissionError("Workspace control capability requires a workspace member")
        tenant = await db.get(Tenant, context.workspace_id)
        user = await db.get(AppUser, context.user_id)
        if tenant is None or user is None:
            raise PermissionError("Workspace authority is unavailable")
        return AuthContext(user=user, tenant=tenant, role=context.role)

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        handler = self._handlers.get(capability.id)
        if handler is None:
            raise LookupError(f"Workspace control capability is not implemented: {capability.id}")
        auth = await self._auth(db, context)
        return await handler(db, auth, arguments)

    async def _summary(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del arguments
        tenant_id = auth.tenant.id

        async def count(model, *criteria) -> int:
            statement = select(func.count(model.id)).where(model.tenant_id == tenant_id)
            for criterion in criteria:
                statement = statement.where(criterion)
            return int(await db.scalar(statement) or 0)

        return CapabilityExecutionResult(
            value={
                "catalog_items": await count(CatalogItem),
                "low_stock": await count(CatalogItem, CatalogItem.active.is_(True), CatalogItem.stock_qty <= CatalogItem.reorder_level),
                "members": int(await db.scalar(select(func.count(TenantMember.id)).where(TenantMember.tenant_id == tenant_id)) or 0),
                "modules_enabled": sum(1 for key in MODULE_CATALOG if await _module_enabled(db, tenant_id, key)),
                "modules_total": len(MODULE_CATALOG),
                "generated_at": datetime.utcnow().isoformat(),
            },
            resource_type="workspace",
            resource_id=tenant_id,
        )

    async def _activity_list(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        limit = max(1, min(int(arguments.get("limit") or 50), 200))
        rows = (
            await db.scalars(
                select(ActivityEvent)
                .where(ActivityEvent.tenant_id == auth.tenant.id)
                .order_by(ActivityEvent.created_at.desc())
                .limit(limit)
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "events": [
                    {
                        "id": row.id,
                        "event_type": row.event_type,
                        "entity_type": row.entity_type,
                        "entity_id": row.entity_id,
                        "summary": row.summary,
                        "actor": row.actor,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            },
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _settings_update(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not arguments:
            raise ValueError("At least one workspace setting must be supplied")
        if "name" in arguments and arguments["name"] is not None:
            name = _clean_text(arguments["name"], max_length=200)
            if not name:
                raise ValueError("Workspace name is required")
            auth.tenant.name = name
        if "timezone" in arguments and arguments["timezone"] is not None:
            timezone = _clean_text(arguments["timezone"], max_length=100)
            if not timezone:
                raise ValueError("Timezone is required")
            auth.tenant.timezone = timezone
        if "logo_url" in arguments:
            auth.tenant.logo_url = str(arguments.get("logo_url") or "").strip()[:1000] or None
        _activity(db, auth, "updated", "workspace", auth.tenant.id, "Updated workspace settings")
        await db.flush()
        return CapabilityExecutionResult(
            value={
                "workspace": {
                    "id": auth.tenant.id,
                    "name": auth.tenant.name,
                    "timezone": auth.tenant.timezone,
                    "logo_url": auth.tenant.logo_url,
                }
            },
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _module_set(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        key = str(arguments["module_key"]).strip().lower()
        try:
            manifest = module_manifest(key)
        except KeyError as error:
            raise ValueError("Unknown workspace module") from error
        enabled = bool(arguments["enabled"])
        configuration = dict(arguments.get("configuration") or {})
        if manifest.get("locked") and not enabled:
            raise ValueError("Core workspace modules cannot be disabled")
        if enabled:
            await _enable_with_dependencies(db, auth, key)
        else:
            blockers = []
            for candidate_key, candidate in MODULE_CATALOG.items():
                if key in candidate.get("dependencies", []) and await _module_enabled(db, auth.tenant.id, candidate_key):
                    blockers.append(str(candidate.get("name") or candidate_key))
            if blockers:
                raise ValueError("Disable dependent modules first: " + ", ".join(sorted(blockers)))
            await _upsert_module(db, auth, key, enabled=False, configuration=configuration)
        row = await _module_row(db, auth.tenant.id, key)
        if row is not None and enabled and configuration:
            import json
            row.configuration_json = json.dumps(configuration, separators=(",", ":"), sort_keys=True)
        _activity(db, auth, "updated", "workspace_module", row.id if row else None, f"{'Enabled' if enabled else 'Disabled'} {manifest['name']}")
        await db.flush()
        return CapabilityExecutionResult(
            value={"key": key, "enabled": enabled, "state": row.state if row else ("active" if enabled else "disabled")},
            resource_type="workspace_module",
            resource_id=row.id if row else key,
        )

    async def _presets_list(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del db, arguments
        return CapabilityExecutionResult(
            value={"presets": [preset_manifest(key) for key in WORKSPACE_PRESETS]},
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _preset_apply(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        key = str(arguments["preset_key"]).strip()
        try:
            preset = preset_manifest(key)
        except KeyError as error:
            raise ValueError("Unknown workspace preset") from error
        for module_key in preset["modules"]:
            await _enable_with_dependencies(db, auth, module_key)
        _activity(db, auth, "updated", "workspace_preset", None, f"Applied {preset['name']} workspace pack")
        await db.flush()
        return CapabilityExecutionResult(
            value={"preset": preset, "modules": list(preset["modules"])},
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _members_list(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del arguments
        rows = (
            await db.execute(
                select(TenantMember, AppUser)
                .join(AppUser, AppUser.id == TenantMember.user_id)
                .where(TenantMember.tenant_id == auth.tenant.id)
                .order_by(AppUser.display_name, AppUser.email)
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "members": [
                    {
                        "user_id": user.id,
                        "display_name": user.display_name or "",
                        "email": user.email,
                        "role": membership.role,
                    }
                    for membership, user in rows
                ]
            },
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _role_exists(self, db: AsyncSession, tenant_id: str, role_key: str) -> bool:
        if role_key in DEFAULT_ROLE_AUTHORITY:
            return True
        return bool(
            await db.scalar(
                select(WorkspaceRole.id).where(
                    WorkspaceRole.tenant_id == tenant_id,
                    WorkspaceRole.key == role_key,
                )
            )
        )

    async def _member_add(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        role_key = normalize_role_key(str(arguments.get("role") or "employee"))
        if role_key == "owner" and auth.role != "owner":
            raise PermissionError("Only an owner can assign the owner role")
        if not await self._role_exists(db, auth.tenant.id, role_key):
            raise ValueError("Workspace role not found")
        email = str(arguments["email"]).strip().lower()
        user = await db.scalar(select(AppUser).where(func.lower(AppUser.email) == email))
        if user is None or not user.active:
            raise ValueError("That email does not have an active Operly account")
        membership = TenantMember(tenant_id=auth.tenant.id, user_id=user.id, role=role_key)
        db.add(membership)
        try:
            await db.flush()
        except IntegrityError as error:
            raise ValueError("User is already a workspace member") from error
        _activity(db, auth, "created", "workspace_member", user.id, f"Added {user.display_name or user.email} as {role_key}")
        return CapabilityExecutionResult(
            value={"user_id": user.id, "display_name": user.display_name or "", "email": user.email, "role": role_key},
            resource_type="workspace_member",
            resource_id=user.id,
        )

    async def _member_role_update(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        user_id = str(arguments["user_id"])
        membership = await db.scalar(
            select(TenantMember).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.user_id == user_id)
        )
        if membership is None:
            raise ValueError("Workspace member not found")
        role_key = normalize_role_key(str(arguments["role"]))
        if not await self._role_exists(db, auth.tenant.id, role_key):
            raise ValueError("Workspace role not found")
        if (membership.role == "owner" or role_key == "owner") and auth.role != "owner":
            raise PermissionError("Only an owner can change workspace ownership")
        if membership.role == "owner" and role_key != "owner":
            owner_count = int(
                await db.scalar(
                    select(func.count(TenantMember.id)).where(
                        TenantMember.tenant_id == auth.tenant.id,
                        TenantMember.role == "owner",
                    )
                )
                or 0
            )
            if owner_count <= 1:
                raise ValueError("A workspace must keep at least one owner")
        membership.role = role_key
        _activity(db, auth, "updated", "workspace_member", user_id, f"Changed workspace member role to {role_key}")
        await db.flush()
        return CapabilityExecutionResult(
            value={"user_id": user_id, "role": role_key},
            resource_type="workspace_member",
            resource_id=user_id,
        )

    async def _member_remove(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        user_id = str(arguments["user_id"])
        membership = await db.scalar(
            select(TenantMember).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.user_id == user_id)
        )
        if membership is None:
            raise ValueError("Workspace member not found")
        if membership.role == "owner":
            if auth.role != "owner":
                raise PermissionError("Only an owner can remove an owner")
            owner_count = int(
                await db.scalar(
                    select(func.count(TenantMember.id)).where(
                        TenantMember.tenant_id == auth.tenant.id,
                        TenantMember.role == "owner",
                    )
                )
                or 0
            )
            if owner_count <= 1:
                raise ValueError("A workspace must keep at least one owner")
        await db.delete(membership)
        _activity(db, auth, "deleted", "workspace_member", user_id, "Removed a workspace member")
        await db.flush()
        return CapabilityExecutionResult(
            value={"ok": True, "user_id": user_id},
            resource_type="workspace_member",
            resource_id=user_id,
        )

    async def _roles_list(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del arguments
        custom = (
            await db.scalars(
                select(WorkspaceRole).where(WorkspaceRole.tenant_id == auth.tenant.id).order_by(WorkspaceRole.name)
            )
        ).all()
        custom_by_key = {row.key: row for row in custom}
        result = []
        for key in sorted(set(DEFAULT_ROLE_AUTHORITY) | set(custom_by_key)):
            row = custom_by_key.get(key)
            result.append(
                {
                    "key": key,
                    "name": row.name if row else key.replace("-", " ").title(),
                    "system": key in DEFAULT_ROLE_AUTHORITY,
                    "permissions": sorted(await resolve_workspace_permissions(db, tenant_id=auth.tenant.id, role=key)),
                }
            )
        return CapabilityExecutionResult(
            value={"roles": result, "known_permissions": sorted(KNOWN_PERMISSIONS)},
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _role_permissions_set(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        key = normalize_role_key(str(arguments["role_key"]))
        if key == "owner" and auth.role != "owner":
            raise PermissionError("Only an owner can edit the owner role")
        permissions = validate_permissions(list(arguments.get("permissions") or []))
        row = await db.scalar(
            select(WorkspaceRole).where(WorkspaceRole.tenant_id == auth.tenant.id, WorkspaceRole.key == key)
        )
        if row is None:
            row = WorkspaceRole(
                tenant_id=auth.tenant.id,
                key=key,
                name=_clean_text(arguments.get("name") or key.replace("-", " ").title(), max_length=120),
                is_system=key in DEFAULT_ROLE_AUTHORITY,
            )
            db.add(row)
            await db.flush()
        elif arguments.get("name"):
            row.name = _clean_text(arguments["name"], max_length=120)
        await db.execute(delete(WorkspaceRolePermission).where(WorkspaceRolePermission.role_id == row.id))
        db.add_all(WorkspaceRolePermission(role_id=row.id, permission=permission) for permission in sorted(permissions))
        _activity(db, auth, "updated", "workspace_role", row.id, f"Updated {row.name} permissions")
        await db.flush()
        return CapabilityExecutionResult(
            value={"key": row.key, "name": row.name, "system": bool(row.is_system), "permissions": sorted(permissions)},
            resource_type="workspace_role",
            resource_id=row.id,
        )

    async def _invitations_list(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del arguments
        rows = await WorkspaceInvitationService.list_for_workspace(db, tenant_id=auth.tenant.id)
        return CapabilityExecutionResult(
            value={
                "invitations": [
                    {
                        "id": row.id,
                        "target_email": row.target_email,
                        "role": row.role,
                        "status": row.status,
                        "source": row.source,
                        "expires_at": row.expires_at.isoformat(),
                        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            },
            resource_type="workspace",
            resource_id=auth.tenant.id,
        )

    async def _invitation_create(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        role_key = normalize_role_key(str(arguments.get("role") or "employee"))
        if role_key == "owner" and auth.role != "owner":
            raise PermissionError("Only an owner can invite another owner")
        if not await self._role_exists(db, auth.tenant.id, role_key):
            raise ValueError("Workspace role not found")
        target_email = str(arguments.get("email") or "").strip() or None
        try:
            row, token = await WorkspaceInvitationService.create(
                db,
                tenant_id=auth.tenant.id,
                role=role_key,
                invited_by_user_id=auth.user.id,
                target_email=target_email,
                source="kernel",
                ttl_days=int(arguments.get("ttl_days") or 7),
            )
        except WorkspaceInvitationError as error:
            raise ValueError(str(error)) from error
        _activity(db, auth, "created", "workspace_invitation", row.id, f"Created a {role_key} workspace invitation")
        return CapabilityExecutionResult(
            value={
                "id": row.id,
                "role": row.role,
                "target_email": row.target_email,
                "expires_at": row.expires_at.isoformat(),
                "token": token,
            },
            resource_type="workspace_invitation",
            resource_id=row.id,
            event_payload={"invitation_id": row.id, "role": row.role, "targeted": bool(row.target_email)},
        )

    async def _invitation_revoke(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        try:
            row = await WorkspaceInvitationService.revoke(
                db,
                tenant_id=auth.tenant.id,
                invitation_id=str(arguments["invitation_id"]),
            )
        except WorkspaceInvitationError as error:
            raise ValueError(str(error)) from error
        _activity(db, auth, "updated", "workspace_invitation", row.id, "Revoked a workspace invitation")
        return CapabilityExecutionResult(
            value={"id": row.id, "status": row.status},
            resource_type="workspace_invitation",
            resource_id=row.id,
        )

    async def _inventory_movements(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not await _module_enabled(db, auth.tenant.id, "inventory"):
            raise PermissionError("Inventory module is disabled")
        item_id = str(arguments["item_id"])
        item = await db.get(CatalogItem, item_id)
        if item is None or item.tenant_id != auth.tenant.id:
            raise ValueError("Catalog item not found")
        limit = max(1, min(int(arguments.get("limit") or 100), 500))
        rows = (
            await db.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.tenant_id == auth.tenant.id, InventoryMovement.item_id == item_id)
                .order_by(InventoryMovement.created_at.desc())
                .limit(limit)
            )
        ).all()
        return CapabilityExecutionResult(
            value={
                "movements": [
                    {
                        "id": row.id,
                        "item_id": row.item_id,
                        "quantity_change": row.quantity_change,
                        "reason": row.reason,
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in rows
                ]
            },
            resource_type="catalog_item",
            resource_id=item_id,
        )

    async def _inventory_adjust(self, db: AsyncSession, auth: AuthContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not await _module_enabled(db, auth.tenant.id, "inventory"):
            raise PermissionError("Inventory module is disabled")
        item_id = str(arguments["item_id"])
        item = await db.get(CatalogItem, item_id)
        if item is None or item.tenant_id != auth.tenant.id:
            raise ValueError("Catalog item not found")
        quantity_change = int(arguments["quantity_change"])
        reason = _clean_text(arguments.get("reason") or "adjustment", max_length=200)
        item.stock_qty += quantity_change
        movement = InventoryMovement(
            tenant_id=auth.tenant.id,
            item_id=item.id,
            quantity_change=quantity_change,
            reason=reason,
        )
        db.add(movement)
        await db.flush()
        _activity(db, auth, "updated", "inventory", item.id, f"Adjusted {item.name} stock by {quantity_change:+d}")
        return CapabilityExecutionResult(
            value={"item_id": item.id, "stock_qty": item.stock_qty, "movement_id": movement.id},
            resource_type="catalog_item",
            resource_id=item.id,
            event_payload={"item_id": item.id, "quantity_change": quantity_change, "movement_id": movement.id},
        )
