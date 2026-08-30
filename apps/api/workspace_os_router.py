import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.business_models import (
    Appointment,
    BusinessDocument,
    BusinessOrder,
    CatalogItem,
    Contact,
    InventoryMovement,
    Lead,
    Quote,
    TeamMember,
)
from packages.database.business_suite_models import (
    Expense,
    Fulfillment,
    Invoice,
    MarketingCampaign,
    PurchaseOrder,
    ReturnRecord,
    Supplier,
    SupportTicket,
)
from packages.database.connector_models import TenantConnector
from packages.database.models import AppUser, Task, TenantMember
from packages.database.workspace_module_models import WorkspaceModule
from packages.database.workspace_security_models import WorkspaceRole, WorkspaceRolePermission
from packages.security.permissions import (
    DEFAULT_ROLE_AUTHORITY,
    KNOWN_PERMISSIONS,
    normalize_role_key,
    resolve_workspace_permissions,
    validate_permissions,
)
from packages.workspace_modules.catalog import MODULE_CATALOG, module_manifest

router = APIRouter(prefix="/api/workspace-os", tags=["workspace-os"])


class WorkspaceSettingsInput(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    logo_url: str | None = Field(default=None, max_length=1000)


class ModuleStateInput(BaseModel):
    enabled: bool
    configuration: dict[str, Any] = Field(default_factory=dict)


class MemberInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(default="employee", min_length=1, max_length=30)


class MemberRoleInput(BaseModel):
    role: str = Field(min_length=1, max_length=30)


class RolePermissionsInput(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    permissions: list[str] = Field(default_factory=list)


class InventoryAdjustmentInput(BaseModel):
    quantity_change: int
    reason: str = Field(default="adjustment", min_length=1, max_length=200)


@dataclass(frozen=True)
class RecordConfig:
    model: type
    module: str
    read_permission: str
    write_permission: str
    fields: tuple[str, ...]
    required: tuple[str, ...] = ()
    search_fields: tuple[str, ...] = ()
    references: tuple[tuple[str, type], ...] = ()
    default_sort: str = "created_at"
    mutable: bool = True


ENTITY_REGISTRY: dict[str, RecordConfig] = {
    "contacts": RecordConfig(
        Contact,
        "crm",
        "crm:read",
        "crm:write",
        ("name", "email", "phone", "company", "source", "status", "notes"),
        required=("name",),
        search_fields=("name", "email", "phone", "company", "status"),
    ),
    "leads": RecordConfig(
        Lead,
        "crm",
        "crm:read",
        "crm:write",
        ("contact_id", "title", "stage", "value", "assigned_to", "next_action", "last_contacted_at", "next_action_at"),
        required=("title",),
        search_fields=("title", "stage", "assigned_to"),
        references=(("contact_id", Contact),),
    ),
    "catalog": RecordConfig(
        CatalogItem,
        "catalog",
        "catalog:read",
        "catalog:write",
        ("name", "item_type", "sku", "price", "cost", "stock_qty", "reorder_level", "active"),
        required=("name",),
        search_fields=("name", "sku", "item_type"),
    ),
    "orders": RecordConfig(
        BusinessOrder,
        "sales",
        "orders:read",
        "orders:write",
        ("contact_id", "status", "total", "notes"),
        search_fields=("status", "notes"),
        references=(("contact_id", Contact),),
    ),
    "quotes": RecordConfig(
        Quote,
        "sales",
        "quotes:read",
        "quotes:write",
        ("contact_id", "title", "status", "total", "valid_until", "notes"),
        required=("title",),
        search_fields=("title", "status", "notes"),
        references=(("contact_id", Contact),),
    ),
    "appointments": RecordConfig(
        Appointment,
        "scheduling",
        "appointments:read",
        "appointments:write",
        ("contact_id", "title", "starts_at", "ends_at", "status", "assigned_to", "notes"),
        required=("title", "starts_at"),
        search_fields=("title", "status", "assigned_to"),
        references=(("contact_id", Contact),),
        default_sort="starts_at",
    ),
    "tasks": RecordConfig(
        Task,
        "tasks",
        "tasks:read",
        "tasks:write",
        ("title", "status", "due_at"),
        required=("title",),
        search_fields=("title", "status"),
    ),
    "team": RecordConfig(
        TeamMember,
        "team",
        "team:read",
        "team:write",
        ("name", "email", "role", "active"),
        required=("name",),
        search_fields=("name", "email", "role"),
    ),
    "documents": RecordConfig(
        BusinessDocument,
        "documents",
        "documents:read",
        "documents:write",
        ("title", "document_type", "content", "status"),
        required=("title",),
        search_fields=("title", "document_type", "status"),
    ),
    "suppliers": RecordConfig(
        Supplier,
        "suppliers",
        "suppliers:read",
        "suppliers:write",
        ("name", "email", "phone", "website", "status", "lead_time_days", "minimum_order_value", "currency", "notes"),
        required=("name",),
        search_fields=("name", "email", "status"),
    ),
    "purchase-orders": RecordConfig(
        PurchaseOrder,
        "suppliers",
        "suppliers:read",
        "suppliers:write",
        ("supplier_id", "reference", "status", "currency", "subtotal", "shipping_cost", "total", "expected_at", "received_at", "notes"),
        required=("reference",),
        search_fields=("reference", "status"),
        references=(("supplier_id", Supplier),),
    ),
    "invoices": RecordConfig(
        Invoice,
        "finance",
        "finance:read",
        "finance:write",
        ("contact_id", "order_id", "number", "status", "currency", "subtotal", "tax", "total", "due_at", "paid_at", "notes"),
        required=("number",),
        search_fields=("number", "status", "notes"),
        references=(("contact_id", Contact), ("order_id", BusinessOrder)),
    ),
    "expenses": RecordConfig(
        Expense,
        "finance",
        "finance:read",
        "finance:write",
        ("vendor", "category", "amount", "currency", "status", "incurred_at", "notes"),
        required=("vendor",),
        search_fields=("vendor", "category", "status"),
        default_sort="incurred_at",
    ),
    "fulfillments": RecordConfig(
        Fulfillment,
        "fulfillment",
        "fulfillment:read",
        "fulfillment:write",
        ("order_id", "supplier_id", "method", "status", "carrier", "tracking_number", "fulfillment_cost", "shipped_at", "delivered_at", "notes"),
        required=("order_id",),
        search_fields=("status", "carrier", "tracking_number", "method"),
        references=(("order_id", BusinessOrder), ("supplier_id", Supplier)),
    ),
    "returns": RecordConfig(
        ReturnRecord,
        "fulfillment",
        "fulfillment:read",
        "fulfillment:write",
        ("order_id", "fulfillment_id", "status", "reason", "refund_amount", "currency", "received_back", "notes"),
        required=("order_id",),
        search_fields=("status", "reason"),
        references=(("order_id", BusinessOrder), ("fulfillment_id", Fulfillment)),
    ),
    "tickets": RecordConfig(
        SupportTicket,
        "support",
        "support:read",
        "support:write",
        ("contact_id", "subject", "status", "priority", "channel", "assigned_to", "description", "resolution", "resolved_at"),
        required=("subject",),
        search_fields=("subject", "status", "priority", "channel", "assigned_to"),
        references=(("contact_id", Contact),),
        default_sort="opened_at",
    ),
    "campaigns": RecordConfig(
        MarketingCampaign,
        "marketing",
        "marketing:read",
        "marketing:write",
        ("name", "channel", "status", "budget", "spent", "attributed_revenue", "starts_at", "ends_at", "notes"),
        required=("name",),
        search_fields=("name", "channel", "status"),
    ),
    "integrations": RecordConfig(
        TenantConnector,
        "integrations",
        "integrations:read",
        "integrations:manage",
        ("connector_type", "provider", "display_name", "status", "enabled", "provider_account_id", "granted_scopes_json", "configuration_json", "health_status", "last_health_check", "last_error"),
        required=("connector_type", "provider", "display_name"),
        search_fields=("provider", "display_name", "status", "health_status"),
        mutable=False,
    ),
}


async def _permissions(db: AsyncSession, auth: AuthContext) -> set[str]:
    permissions = await resolve_workspace_permissions(
        db,
        tenant_id=auth.tenant.id,
        role=auth.role,
    )
    if auth.role == "owner":
        permissions |= set(DEFAULT_ROLE_AUTHORITY["owner"])
    return permissions


async def _require_permission(
    db: AsyncSession,
    auth: AuthContext,
    permission: str,
) -> set[str]:
    permissions = await _permissions(db, auth)
    if auth.role != "owner" and permission not in permissions:
        raise HTTPException(status_code=403, detail="Workspace permission denied")
    return permissions


async def _module_row(
    db: AsyncSession,
    tenant_id: str,
    key: str,
) -> WorkspaceModule | None:
    return await db.scalar(
        select(WorkspaceModule).where(
            WorkspaceModule.tenant_id == tenant_id,
            WorkspaceModule.module_key == key,
        )
    )


async def _module_enabled(db: AsyncSession, tenant_id: str, key: str) -> bool:
    manifest = module_manifest(key)
    if manifest.get("locked"):
        return True
    row = await _module_row(db, tenant_id, key)
    if row is None:
        return bool(manifest.get("default_enabled"))
    return bool(row.enabled)


async def _require_module(
    db: AsyncSession,
    auth: AuthContext,
    key: str,
    permission: str,
) -> set[str]:
    permissions = await _require_permission(db, auth, permission)
    if not await _module_enabled(db, auth.tenant.id, key):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODULE_DISABLED",
                "message": f"Enable the {module_manifest(key)['name']} module first.",
            },
        )
    return permissions


def _clean_text(value: Any, *, max_length: int | None = None) -> str:
    raw = str(value or "").replace("\x00", "")
    if max_length is None:
        # Text columns are user content (SOPs, notes, descriptions). Preserve
        # paragraph and line structure instead of treating them like identifiers.
        return raw.strip()
    normalized = " ".join(raw.split()).strip()
    return normalized[:max_length]


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"Invalid datetime: {value}") from error


def _coerce(model: type, field: str, value: Any) -> Any:
    column = model.__table__.columns[field]
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, DateTime):
        return _parse_datetime(value)
    if isinstance(column_type, Boolean):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise HTTPException(status_code=422, detail=f"Invalid boolean for {field}")
    if isinstance(column_type, Integer):
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=f"Invalid integer for {field}") from error
    if isinstance(column_type, Float):
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=f"Invalid number for {field}") from error
    if isinstance(column_type, Text):
        return _clean_text(value)
    if isinstance(column_type, String):
        return _clean_text(value, max_length=column_type.length)
    return value


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_record(row: Any, config: RecordConfig) -> dict[str, Any]:
    keys = ["id", *config.fields]
    for system_key in ("created_at", "updated_at", "opened_at", "stage_changed_at"):
        if hasattr(row, system_key) and system_key not in keys:
            keys.append(system_key)
    return {key: _serialize_value(getattr(row, key, None)) for key in keys}


async def _validate_references(
    db: AsyncSession,
    auth: AuthContext,
    config: RecordConfig,
    values: dict[str, Any],
) -> None:
    for field, target_model in config.references:
        target_id = values.get(field)
        if not target_id:
            continue
        target = await db.get(target_model, target_id)
        if target is None or getattr(target, "tenant_id", None) != auth.tenant.id:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid workspace reference for {field}",
            )


def _payload_values(
    config: RecordConfig,
    payload: dict[str, Any],
    *,
    partial: bool,
) -> dict[str, Any]:
    unknown = sorted(set(payload) - set(config.fields))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Unsupported fields: " + ", ".join(unknown),
        )
    values = {
        field: _coerce(config.model, field, value)
        for field, value in payload.items()
    }
    if not partial:
        missing = [
            field
            for field in config.required
            if field not in values or values[field] in {None, ""}
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Required fields: " + ", ".join(missing),
            )
    return values


async def _record_for_workspace(
    db: AsyncSession,
    auth: AuthContext,
    config: RecordConfig,
    record_id: str,
):
    row = await db.get(config.model, record_id)
    if row is None or getattr(row, "tenant_id", None) != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Record not found")
    return row


def _require_mutable(config: RecordConfig) -> None:
    if not config.mutable:
        raise HTTPException(
            status_code=405,
            detail="This record type is managed through its dedicated integration flow",
        )


async def _upsert_module(
    db: AsyncSession,
    auth: AuthContext,
    key: str,
    *,
    enabled: bool,
    configuration: dict[str, Any] | None = None,
) -> WorkspaceModule:
    now = datetime.utcnow()
    row = await _module_row(db, auth.tenant.id, key)
    if row is None:
        row = WorkspaceModule(tenant_id=auth.tenant.id, module_key=key)
        db.add(row)
    row.enabled = enabled
    row.state = "active" if enabled else "disabled"
    row.activated_by_user_id = auth.user.id if enabled else row.activated_by_user_id
    row.activated_at = now if enabled else row.activated_at
    row.disabled_at = None if enabled else now
    if configuration is not None:
        row.configuration_json = json.dumps(
            configuration,
            separators=(",", ":"),
            sort_keys=True,
        )
    return row


@router.get("/context")
async def workspace_context(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    permissions = await _permissions(db, auth)
    modules = []
    for key, manifest in MODULE_CATALOG.items():
        row = await _module_row(db, auth.tenant.id, key)
        enabled = (
            True
            if manifest.get("locked")
            else (
                row.enabled
                if row is not None
                else bool(manifest.get("default_enabled"))
            )
        )
        modules.append(
            {
                "key": key,
                **manifest,
                "enabled": enabled,
                "state": (
                    row.state
                    if row is not None
                    else ("active" if enabled else "disabled")
                ),
                "configuration": (
                    json.loads(row.configuration_json or "{}")
                    if row is not None
                    else {}
                ),
                "can_read": (
                    auth.role == "owner"
                    or manifest["required_permission"] in permissions
                ),
                "can_write": bool(manifest.get("write_permission"))
                and (
                    auth.role == "owner"
                    or manifest.get("write_permission") in permissions
                ),
                "can_manage": (
                    auth.role == "owner"
                    or "workspace:modules:manage" in permissions
                ),
            }
        )
    return {
        "workspace": {
            "id": auth.tenant.id,
            "name": auth.tenant.name,
            "slug": auth.tenant.slug,
            "timezone": auth.tenant.timezone,
            "logo_url": auth.tenant.logo_url,
        },
        "user": {
            "id": auth.user.id,
            "email": auth.user.email,
            "display_name": auth.user.display_name,
        },
        "role": auth.role,
        "permissions": sorted(permissions),
        "modules": modules,
    }


@router.patch("/settings")
async def update_workspace_settings(
    payload: WorkspaceSettingsInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:settings:manage")
    if payload.name is not None:
        name = _clean_text(payload.name, max_length=200)
        if not name:
            raise HTTPException(status_code=422, detail="Workspace name is required")
        auth.tenant.name = name
    if payload.timezone is not None:
        timezone = _clean_text(payload.timezone, max_length=100)
        if not timezone:
            raise HTTPException(status_code=422, detail="Timezone is required")
        auth.tenant.timezone = timezone
    if payload.logo_url is not None:
        auth.tenant.logo_url = payload.logo_url.strip()[:1000] or None
    await db.commit()
    return {
        "ok": True,
        "workspace": {
            "id": auth.tenant.id,
            "name": auth.tenant.name,
            "timezone": auth.tenant.timezone,
            "logo_url": auth.tenant.logo_url,
        },
    }


@router.get("/modules")
async def list_modules(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return (await workspace_context(auth=auth, db=db))["modules"]


@router.put("/modules/{module_key}")
async def set_module_state(
    module_key: str,
    payload: ModuleStateInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:modules:manage")
    try:
        manifest = module_manifest(module_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    key = manifest["key"]
    if manifest.get("locked") and not payload.enabled:
        raise HTTPException(
            status_code=409,
            detail="Core workspace modules cannot be disabled",
        )

    if payload.enabled:
        for dependency in manifest.get("dependencies", []):
            await _upsert_module(db, auth, dependency, enabled=True)
    else:
        blocking = []
        for candidate_key, candidate in MODULE_CATALOG.items():
            if (
                key in candidate.get("dependencies", [])
                and await _module_enabled(db, auth.tenant.id, candidate_key)
            ):
                blocking.append(candidate["name"])
        if blocking:
            raise HTTPException(
                status_code=409,
                detail="Disable dependent modules first: "
                + ", ".join(sorted(blocking)),
            )

    row = await _upsert_module(
        db,
        auth,
        key,
        enabled=payload.enabled,
        configuration=payload.configuration,
    )
    await db.commit()
    return {
        "ok": True,
        "key": key,
        "enabled": row.enabled,
        "state": row.state,
        "configuration": json.loads(row.configuration_json or "{}"),
    }


@router.get("/members")
async def members(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:read")
    rows = (
        await db.execute(
            select(TenantMember, AppUser)
            .join(AppUser, AppUser.id == TenantMember.user_id)
            .where(TenantMember.tenant_id == auth.tenant.id)
            .order_by(AppUser.display_name, AppUser.email)
        )
    ).all()
    return [
        {
            "user_id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "role": membership.role,
        }
        for membership, user in rows
    ]


async def _role_exists(db: AsyncSession, tenant_id: str, role_key: str) -> bool:
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


@router.post("/members", status_code=201)
async def add_member(
    payload: MemberInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:members:manage")
    role_key = normalize_role_key(payload.role)
    if role_key == "owner" and auth.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only an owner can assign the owner role",
        )
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    email = payload.email.strip().lower()
    user = await db.scalar(select(AppUser).where(func.lower(AppUser.email) == email))
    if user is None or not user.active:
        raise HTTPException(
            status_code=404,
            detail="That email does not have an active Operly account yet",
        )
    membership = TenantMember(
        tenant_id=auth.tenant.id,
        user_id=user.id,
        role=role_key,
    )
    db.add(membership)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="User is already a workspace member",
        ) from error
    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "role": role_key,
    }


@router.patch("/members/{user_id}")
async def update_member_role(
    user_id: str,
    payload: MemberRoleInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:members:manage")
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == auth.tenant.id,
            TenantMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    role_key = normalize_role_key(payload.role)
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    if (
        membership.role == "owner" or role_key == "owner"
    ) and auth.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only an owner can change workspace ownership",
        )
    if membership.role == "owner" and role_key != "owner":
        owner_count = await db.scalar(
            select(func.count(TenantMember.id)).where(
                TenantMember.tenant_id == auth.tenant.id,
                TenantMember.role == "owner",
            )
        )
        if int(owner_count or 0) <= 1:
            raise HTTPException(
                status_code=409,
                detail="A workspace must keep at least one owner",
            )
    membership.role = role_key
    await db.commit()
    return {"ok": True, "user_id": user_id, "role": role_key}


@router.delete("/members/{user_id}")
async def remove_member(
    user_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:members:manage")
    membership = await db.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == auth.tenant.id,
            TenantMember.user_id == user_id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if membership.role == "owner":
        if auth.role != "owner":
            raise HTTPException(
                status_code=403,
                detail="Only an owner can remove an owner",
            )
        owner_count = await db.scalar(
            select(func.count(TenantMember.id)).where(
                TenantMember.tenant_id == auth.tenant.id,
                TenantMember.role == "owner",
            )
        )
        if int(owner_count or 0) <= 1:
            raise HTTPException(
                status_code=409,
                detail="A workspace must keep at least one owner",
            )
    await db.delete(membership)
    await db.commit()
    return {"ok": True}


@router.get("/roles")
async def roles(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:read")
    custom = (
        await db.scalars(
            select(WorkspaceRole)
            .where(WorkspaceRole.tenant_id == auth.tenant.id)
            .order_by(WorkspaceRole.name)
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
                "permissions": sorted(
                    await resolve_workspace_permissions(
                        db,
                        tenant_id=auth.tenant.id,
                        role=key,
                    )
                ),
            }
        )
    return {
        "roles": result,
        "known_permissions": sorted(KNOWN_PERMISSIONS),
    }


@router.put("/roles/{role_key}")
async def set_role_permissions(
    role_key: str,
    payload: RolePermissionsInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:roles:manage")
    key = normalize_role_key(role_key)
    if key == "owner" and auth.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Only an owner can edit the owner role",
        )
    permissions = validate_permissions(payload.permissions)
    row = await db.scalar(
        select(WorkspaceRole).where(
            WorkspaceRole.tenant_id == auth.tenant.id,
            WorkspaceRole.key == key,
        )
    )
    if row is None:
        row = WorkspaceRole(
            tenant_id=auth.tenant.id,
            key=key,
            name=_clean_text(
                payload.name or key.replace("-", " ").title(),
                max_length=120,
            ),
            is_system=key in DEFAULT_ROLE_AUTHORITY,
        )
        db.add(row)
        await db.flush()
    elif payload.name:
        row.name = _clean_text(payload.name, max_length=120)
    await db.execute(
        delete(WorkspaceRolePermission).where(
            WorkspaceRolePermission.role_id == row.id
        )
    )
    db.add_all(
        WorkspaceRolePermission(role_id=row.id, permission=permission)
        for permission in sorted(permissions)
    )
    await db.commit()
    return {
        "key": row.key,
        "name": row.name,
        "permissions": sorted(permissions),
    }


@router.get("/summary")
async def summary(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_permission(db, auth, "workspace:read")
    tenant_id = auth.tenant.id

    async def count(model):
        return int(
            await db.scalar(
                select(func.count(model.id)).where(model.tenant_id == tenant_id)
            )
            or 0
        )

    lead_value = float(
        await db.scalar(
            select(func.coalesce(func.sum(Lead.value), 0)).where(
                Lead.tenant_id == tenant_id,
                Lead.stage.notin_(["won", "lost"]),
            )
        )
        or 0
    )
    sales_total = float(
        await db.scalar(
            select(func.coalesce(func.sum(BusinessOrder.total), 0)).where(
                BusinessOrder.tenant_id == tenant_id,
                BusinessOrder.status.notin_(["cancelled", "draft"]),
            )
        )
        or 0
    )
    invoice_total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Invoice.total), 0)).where(
                Invoice.tenant_id == tenant_id,
                Invoice.status.notin_(["void", "draft"]),
            )
        )
        or 0
    )
    expense_total = float(
        await db.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0)).where(
                Expense.tenant_id == tenant_id,
                Expense.status != "void",
            )
        )
        or 0
    )
    low_stock = int(
        await db.scalar(
            select(func.count(CatalogItem.id)).where(
                CatalogItem.tenant_id == tenant_id,
                CatalogItem.active.is_(True),
                CatalogItem.stock_qty <= CatalogItem.reorder_level,
            )
        )
        or 0
    )
    return {
        "contacts": await count(Contact),
        "open_leads": int(
            await db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id,
                    Lead.stage.notin_(["won", "lost"]),
                )
            )
            or 0
        ),
        "pipeline_value": lead_value,
        "orders": await count(BusinessOrder),
        "sales_total": sales_total,
        "invoice_total": invoice_total,
        "expenses_total": expense_total,
        "net_operating": invoice_total - expense_total,
        "products": await count(CatalogItem),
        "low_stock": low_stock,
        "open_tickets": int(
            await db.scalar(
                select(func.count(SupportTicket.id)).where(
                    SupportTicket.tenant_id == tenant_id,
                    SupportTicket.status.notin_(["resolved", "closed"]),
                )
            )
            or 0
        ),
        "upcoming_appointments": int(
            await db.scalar(
                select(func.count(Appointment.id)).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.starts_at >= datetime.utcnow(),
                    Appointment.status != "cancelled",
                )
            )
            or 0
        ),
    }


def _filtered_query(config: RecordConfig, tenant_id: str, q: str | None, status: str | None):
    statement = select(config.model).where(config.model.tenant_id == tenant_id)
    if q and config.search_fields:
        clauses = []
        needle = q.strip()
        if needle:
            for field in config.search_fields:
                column = getattr(config.model, field)
                if isinstance(config.model.__table__.columns[field].type, String):
                    clauses.append(column.ilike(f"%{needle}%"))
        if clauses:
            statement = statement.where(or_(*clauses))
    if status and hasattr(config.model, "status"):
        statement = statement.where(config.model.status == status)
    return statement


@router.get("/records/{entity}")
async def list_records(
    entity: str,
    q: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: str | None = Query(default=None, max_length=80),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    await _require_module(db, auth, config.module, config.read_permission)

    sort_key = sort or config.default_sort
    if not hasattr(config.model, sort_key):
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    sort_column = getattr(config.model, sort_key)

    base_statement = _filtered_query(config, auth.tenant.id, q, status)
    query = (
        base_statement.order_by(
            sort_column.asc() if direction == "asc" else sort_column.desc()
        )
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.scalars(query)).all()

    # Count from the same filtered statement so search pagination remains truthful.
    count_subquery = base_statement.with_only_columns(config.model.id).order_by(None).subquery()
    total = int(await db.scalar(select(func.count()).select_from(count_subquery)) or 0)
    return {
        "items": [_serialize_record(row, config) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/records/{entity}", status_code=201)
async def create_record(
    entity: str,
    payload: dict[str, Any] = Body(...),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    _require_mutable(config)
    await _require_module(db, auth, config.module, config.write_permission)
    values = _payload_values(config, payload, partial=False)
    await _validate_references(db, auth, config, values)
    if config.model is Task:
        values.update({"tenant_id": auth.tenant.id, "owner_user_id": None})
    else:
        values["tenant_id"] = auth.tenant.id
    row = config.model(**values)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A record with that unique identifier already exists",
        ) from error
    await db.refresh(row)
    return _serialize_record(row, config)


@router.patch("/records/{entity}/{record_id}")
async def update_record(
    entity: str,
    record_id: str,
    payload: dict[str, Any] = Body(...),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    _require_mutable(config)
    await _require_module(db, auth, config.module, config.write_permission)
    row = await _record_for_workspace(db, auth, config, record_id)
    values = _payload_values(config, payload, partial=True)
    await _validate_references(db, auth, config, values)
    for key, value in values.items():
        setattr(row, key, value)
    if isinstance(row, Lead) and "stage" in values:
        row.stage_changed_at = datetime.utcnow()
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Update conflicts with an existing record",
        ) from error
    await db.refresh(row)
    return _serialize_record(row, config)


@router.delete("/records/{entity}/{record_id}")
async def delete_record(
    entity: str,
    record_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    _require_mutable(config)
    await _require_module(db, auth, config.module, config.write_permission)
    row = await _record_for_workspace(db, auth, config, record_id)
    await db.delete(row)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This record is still referenced by other workspace data",
        ) from error
    return {"ok": True}


@router.post("/inventory/{item_id}/adjust")
async def adjust_inventory(
    item_id: str,
    payload: InventoryAdjustmentInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_module(db, auth, "inventory", "inventory:write")
    item = await db.get(CatalogItem, item_id)
    if item is None or item.tenant_id != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    item.stock_qty += payload.quantity_change
    movement = InventoryMovement(
        tenant_id=auth.tenant.id,
        item_id=item.id,
        quantity_change=payload.quantity_change,
        reason=_clean_text(payload.reason, max_length=200),
    )
    db.add(movement)
    await db.commit()
    return {
        "ok": True,
        "item_id": item.id,
        "stock_qty": item.stock_qty,
        "movement_id": movement.id,
    }


@router.get("/inventory/{item_id}/movements")
async def inventory_movements(
    item_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_module(db, auth, "inventory", "inventory:read")
    item = await db.get(CatalogItem, item_id)
    if item is None or item.tenant_id != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    rows = (
        await db.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.tenant_id == auth.tenant.id,
                InventoryMovement.item_id == item_id,
            )
            .order_by(InventoryMovement.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": row.id,
            "quantity_change": row.quantity_change,
            "reason": row.reason,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
