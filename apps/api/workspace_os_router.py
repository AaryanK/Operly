import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import (
    AccountAuthContext,
    AuthContext,
    get_account_auth_context,
    get_auth_context,
    get_db,
)
from packages.database.business_models import (
    ActivityEvent,
    Appointment,
    BusinessDocument,
    BusinessOrder,
    CatalogItem,
    Contact,
    InventoryMovement,
    Lead,
    OrderItem,
    Quote,
    TeamMember,
)
from packages.database.business_suite_models import (
    Asset,
    AuditRecord,
    Budget,
    BusinessAccount,
    CRMInteraction,
    Expense,
    Experiment,
    FinancialAccount,
    Fulfillment,
    GrantRecord,
    IncidentRecord,
    InventoryTransfer,
    Invoice,
    InvoiceItem,
    LeaveRequest,
    LedgerEntry,
    MaintenanceRecord,
    MarketingCampaign,
    MarketingContent,
    Milestone,
    Payment,
    Project,
    PurchaseOrder,
    PurchaseOrderItem,
    QuoteItem,
    ResearchDataset,
    ResearchProject,
    ResearchSample,
    ReturnRecord,
    RiskRecord,
    SalesContract,
    Subscription,
    Supplier,
    SupportTicket,
    TimeEntry,
    Warehouse,
    WorkOrder,
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
from packages.security.workspace_invitations import (
    WorkspaceInvitationError,
    WorkspaceInvitationService,
)
from packages.workspace_modules.catalog import (
    MODULE_CATALOG,
    WORKSPACE_PRESETS,
    module_manifest,
    preset_manifest,
)

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


class InvitationCreateInput(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    role: str = Field(default="employee", min_length=1, max_length=30)
    ttl_days: int = Field(default=7, ge=1, le=30)


class InvitationAcceptInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


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
    "organizations": RecordConfig(
        BusinessAccount, "crm", "crm:read", "crm:write",
        ("name", "account_type", "industry", "website", "email", "phone", "status", "owner", "billing_address", "shipping_address", "notes"),
        required=("name",), search_fields=("name", "industry", "email", "phone", "status", "owner"),
    ),
    "contacts": RecordConfig(
        Contact, "crm", "crm:read", "crm:write",
        ("name", "email", "phone", "company", "source", "status", "notes"),
        required=("name",), search_fields=("name", "email", "phone", "company", "status"),
    ),
    "leads": RecordConfig(
        Lead, "crm", "crm:read", "crm:write",
        ("contact_id", "title", "stage", "value", "assigned_to", "next_action", "last_contacted_at", "next_action_at"),
        required=("title",), search_fields=("title", "stage", "assigned_to"), references=(("contact_id", Contact),),
    ),
    "interactions": RecordConfig(
        CRMInteraction, "crm", "crm:read", "crm:write",
        ("contact_id", "account_id", "lead_id", "interaction_type", "channel", "subject", "body", "owner", "occurred_at", "next_action_at"),
        required=("subject",), search_fields=("subject", "interaction_type", "channel", "owner"),
        references=(("contact_id", Contact), ("account_id", BusinessAccount), ("lead_id", Lead)), default_sort="occurred_at",
    ),
    "catalog": RecordConfig(
        CatalogItem, "catalog", "catalog:read", "catalog:write",
        ("name", "item_type", "sku", "price", "cost", "stock_qty", "reorder_level", "active"),
        required=("name",), search_fields=("name", "sku", "item_type"),
    ),
    "warehouses": RecordConfig(
        Warehouse, "inventory", "inventory:read", "inventory:write",
        ("name", "code", "location", "active", "notes"), required=("name", "code"), search_fields=("name", "code", "location"),
    ),
    "stock-transfers": RecordConfig(
        InventoryTransfer, "inventory", "inventory:read", "inventory:write",
        ("item_id", "from_warehouse_id", "to_warehouse_id", "quantity", "status", "requested_at", "completed_at", "notes"),
        required=("item_id", "quantity"), search_fields=("status", "notes"),
        references=(("item_id", CatalogItem), ("from_warehouse_id", Warehouse), ("to_warehouse_id", Warehouse)), default_sort="requested_at",
    ),
    "quotes": RecordConfig(
        Quote, "sales", "quotes:read", "quotes:write",
        ("contact_id", "title", "status", "total", "valid_until", "notes"),
        required=("title",), search_fields=("title", "status", "notes"), references=(("contact_id", Contact),),
    ),
    "quote-items": RecordConfig(
        QuoteItem, "sales", "quotes:read", "quotes:write",
        ("quote_id", "catalog_item_id", "description", "quantity", "unit_price", "discount", "tax_rate"),
        required=("quote_id", "description"), search_fields=("description",), references=(("quote_id", Quote), ("catalog_item_id", CatalogItem)),
    ),
    "orders": RecordConfig(
        BusinessOrder, "sales", "orders:read", "orders:write",
        ("contact_id", "status", "total", "notes"), search_fields=("status", "notes"), references=(("contact_id", Contact),),
    ),
    "order-items": RecordConfig(
        OrderItem, "sales", "orders:read", "orders:write",
        ("order_id", "catalog_item_id", "description", "quantity", "unit_price"),
        required=("order_id", "description"), search_fields=("description",), references=(("order_id", BusinessOrder), ("catalog_item_id", CatalogItem)),
    ),
    "contracts": RecordConfig(
        SalesContract, "sales", "orders:read", "orders:write",
        ("contact_id", "account_id", "title", "status", "value", "currency", "starts_at", "ends_at", "renewal_at", "terms"),
        required=("title",), search_fields=("title", "status"), references=(("contact_id", Contact), ("account_id", BusinessAccount)),
    ),
    "subscriptions": RecordConfig(
        Subscription, "sales", "orders:read", "orders:write",
        ("contact_id", "account_id", "catalog_item_id", "status", "quantity", "unit_price", "currency", "billing_interval", "started_at", "next_billing_at", "cancelled_at", "notes"),
        search_fields=("status", "billing_interval"), references=(("contact_id", Contact), ("account_id", BusinessAccount), ("catalog_item_id", CatalogItem)), default_sort="started_at",
    ),
    "invoices": RecordConfig(
        Invoice, "finance", "finance:read", "finance:write",
        ("contact_id", "order_id", "number", "status", "currency", "subtotal", "tax", "total", "due_at", "paid_at", "notes"),
        required=("number",), search_fields=("number", "status", "notes"), references=(("contact_id", Contact), ("order_id", BusinessOrder)),
    ),
    "invoice-items": RecordConfig(
        InvoiceItem, "finance", "finance:read", "finance:write",
        ("invoice_id", "catalog_item_id", "description", "quantity", "unit_price", "discount", "tax_rate"),
        required=("invoice_id", "description"), search_fields=("description",), references=(("invoice_id", Invoice), ("catalog_item_id", CatalogItem)),
    ),
    "payments": RecordConfig(
        Payment, "finance", "finance:read", "finance:write",
        ("contact_id", "invoice_id", "order_id", "direction", "method", "provider", "reference", "status", "amount", "currency", "paid_at", "notes"),
        search_fields=("method", "provider", "reference", "status"), references=(("contact_id", Contact), ("invoice_id", Invoice), ("order_id", BusinessOrder)), default_sort="paid_at",
    ),
    "expenses": RecordConfig(
        Expense, "finance", "finance:read", "finance:write",
        ("vendor", "category", "amount", "currency", "status", "incurred_at", "notes"),
        required=("vendor",), search_fields=("vendor", "category", "status"), default_sort="incurred_at",
    ),
    "financial-accounts": RecordConfig(
        FinancialAccount, "finance", "finance:read", "finance:write",
        ("code", "name", "account_type", "currency", "opening_balance", "active", "notes"), required=("name",), search_fields=("code", "name", "account_type"),
    ),
    "ledger": RecordConfig(
        LedgerEntry, "finance", "finance:read", "finance:write",
        ("financial_account_id", "counterparty", "category", "debit", "credit", "currency", "occurred_at", "source_type", "source_id", "memo"),
        search_fields=("counterparty", "category", "source_type", "memo"), references=(("financial_account_id", FinancialAccount),), default_sort="occurred_at",
    ),
    "budgets": RecordConfig(
        Budget, "finance", "finance:read", "finance:write",
        ("name", "category", "period_start", "period_end", "amount", "spent", "currency", "status", "notes"),
        required=("name", "period_start", "period_end"), search_fields=("name", "category", "status"), default_sort="period_start",
    ),
    "suppliers": RecordConfig(
        Supplier, "suppliers", "suppliers:read", "suppliers:write",
        ("name", "email", "phone", "website", "status", "lead_time_days", "minimum_order_value", "currency", "notes"),
        required=("name",), search_fields=("name", "email", "status"),
    ),
    "purchase-orders": RecordConfig(
        PurchaseOrder, "suppliers", "suppliers:read", "suppliers:write",
        ("supplier_id", "reference", "status", "currency", "subtotal", "shipping_cost", "total", "expected_at", "received_at", "notes"),
        required=("reference",), search_fields=("reference", "status"), references=(("supplier_id", Supplier),),
    ),
    "purchase-order-items": RecordConfig(
        PurchaseOrderItem, "suppliers", "suppliers:read", "suppliers:write",
        ("purchase_order_id", "catalog_item_id", "description", "quantity", "unit_cost", "tax_rate", "received_quantity"),
        required=("purchase_order_id", "description"), search_fields=("description",), references=(("purchase_order_id", PurchaseOrder), ("catalog_item_id", CatalogItem)),
    ),
    "fulfillments": RecordConfig(
        Fulfillment, "fulfillment", "fulfillment:read", "fulfillment:write",
        ("order_id", "supplier_id", "method", "status", "carrier", "tracking_number", "fulfillment_cost", "shipped_at", "delivered_at", "notes"),
        required=("order_id",), search_fields=("status", "carrier", "tracking_number", "method"), references=(("order_id", BusinessOrder), ("supplier_id", Supplier)),
    ),
    "returns": RecordConfig(
        ReturnRecord, "fulfillment", "fulfillment:read", "fulfillment:write",
        ("order_id", "fulfillment_id", "status", "reason", "refund_amount", "currency", "received_back", "notes"),
        required=("order_id",), search_fields=("status", "reason"), references=(("order_id", BusinessOrder), ("fulfillment_id", Fulfillment)),
    ),
    "projects": RecordConfig(
        Project, "projects", "projects:read", "projects:write",
        ("contact_id", "code", "name", "project_type", "status", "owner", "starts_at", "due_at", "budget", "spent", "description"),
        required=("name",), search_fields=("code", "name", "project_type", "status", "owner"), references=(("contact_id", Contact),),
    ),
    "milestones": RecordConfig(
        Milestone, "projects", "projects:read", "projects:write",
        ("project_id", "title", "status", "owner", "due_at", "completed_at", "description"),
        required=("project_id", "title"), search_fields=("title", "status", "owner"), references=(("project_id", Project),),
    ),
    "time-entries": RecordConfig(
        TimeEntry, "projects", "projects:read", "projects:write",
        ("project_id", "team_member_id", "work_date", "hours", "billable", "hourly_rate", "description"),
        search_fields=("description",), references=(("project_id", Project), ("team_member_id", TeamMember)), default_sort="work_date",
    ),
    "assets": RecordConfig(
        Asset, "operations", "operations:read", "operations:write",
        ("tag", "name", "asset_type", "status", "serial_number", "location", "owner", "acquired_at", "acquisition_cost", "next_maintenance_at", "notes"),
        required=("name",), search_fields=("tag", "name", "asset_type", "status", "serial_number", "location"),
    ),
    "maintenance": RecordConfig(
        MaintenanceRecord, "operations", "operations:read", "operations:write",
        ("asset_id", "title", "status", "scheduled_at", "completed_at", "cost", "vendor", "notes"),
        required=("asset_id", "title"), search_fields=("title", "status", "vendor"), references=(("asset_id", Asset),), default_sort="scheduled_at",
    ),
    "work-orders": RecordConfig(
        WorkOrder, "operations", "operations:read", "operations:write",
        ("project_id", "contact_id", "asset_id", "reference", "title", "status", "priority", "assigned_to", "scheduled_start", "scheduled_end", "estimated_cost", "actual_cost", "description"),
        required=("reference", "title"), search_fields=("reference", "title", "status", "priority", "assigned_to"),
        references=(("project_id", Project), ("contact_id", Contact), ("asset_id", Asset)), default_sort="scheduled_start",
    ),
    "tickets": RecordConfig(
        SupportTicket, "support", "support:read", "support:write",
        ("contact_id", "subject", "status", "priority", "channel", "assigned_to", "description", "resolution", "resolved_at"),
        required=("subject",), search_fields=("subject", "status", "priority", "channel", "assigned_to"), references=(("contact_id", Contact),), default_sort="opened_at",
    ),
    "appointments": RecordConfig(
        Appointment, "scheduling", "appointments:read", "appointments:write",
        ("contact_id", "title", "starts_at", "ends_at", "status", "assigned_to", "notes"),
        required=("title", "starts_at"), search_fields=("title", "status", "assigned_to"), references=(("contact_id", Contact),), default_sort="starts_at",
    ),
    "tasks": RecordConfig(
        Task, "tasks", "tasks:read", "tasks:write",
        ("title", "status", "due_at"), required=("title",), search_fields=("title", "status"),
    ),
    "team": RecordConfig(
        TeamMember, "team", "team:read", "team:write",
        ("name", "email", "role", "active"), required=("name",), search_fields=("name", "email", "role"),
    ),
    "leave-requests": RecordConfig(
        LeaveRequest, "team", "team:read", "team:write",
        ("team_member_id", "leave_type", "status", "starts_at", "ends_at", "reason", "approved_by"),
        required=("team_member_id", "starts_at", "ends_at"), search_fields=("leave_type", "status", "approved_by"), references=(("team_member_id", TeamMember),), default_sort="starts_at",
    ),
    "documents": RecordConfig(
        BusinessDocument, "documents", "documents:read", "documents:write",
        ("title", "document_type", "content", "status"), required=("title",), search_fields=("title", "document_type", "status"),
    ),
    "campaigns": RecordConfig(
        MarketingCampaign, "marketing", "marketing:read", "marketing:write",
        ("name", "channel", "status", "budget", "spent", "attributed_revenue", "starts_at", "ends_at", "notes"),
        required=("name",), search_fields=("name", "channel", "status"),
    ),
    "marketing-content": RecordConfig(
        MarketingContent, "marketing", "marketing:read", "marketing:write",
        ("campaign_id", "title", "content_type", "channel", "status", "body", "publish_at", "external_url", "notes"),
        required=("title",), search_fields=("title", "content_type", "channel", "status"), references=(("campaign_id", MarketingCampaign),), default_sort="publish_at",
    ),
    "risks": RecordConfig(
        RiskRecord, "compliance", "compliance:read", "compliance:write",
        ("title", "category", "likelihood", "impact", "status", "owner", "mitigation", "due_at"),
        required=("title",), search_fields=("title", "category", "status", "owner"),
    ),
    "incidents": RecordConfig(
        IncidentRecord, "compliance", "compliance:read", "compliance:write",
        ("title", "incident_type", "severity", "status", "occurred_at", "reported_by", "owner", "description", "resolution"),
        required=("title",), search_fields=("title", "incident_type", "severity", "status", "owner"), default_sort="occurred_at",
    ),
    "audits": RecordConfig(
        AuditRecord, "compliance", "compliance:read", "compliance:write",
        ("title", "audit_type", "status", "owner", "scheduled_at", "completed_at", "score", "findings", "corrective_actions"),
        required=("title",), search_fields=("title", "audit_type", "status", "owner"), default_sort="scheduled_at",
    ),
    "research-projects": RecordConfig(
        ResearchProject, "research", "research:read", "research:write",
        ("code", "title", "field", "status", "principal_investigator", "starts_at", "ends_at", "ethics_status", "funding_source", "objective"),
        required=("title",), search_fields=("code", "title", "field", "status", "principal_investigator", "ethics_status"),
    ),
    "experiments": RecordConfig(
        Experiment, "research", "research:read", "research:write",
        ("research_project_id", "name", "status", "owner", "started_at", "completed_at", "hypothesis", "protocol", "result_summary"),
        required=("research_project_id", "name"), search_fields=("name", "status", "owner"), references=(("research_project_id", ResearchProject),),
    ),
    "samples": RecordConfig(
        ResearchSample, "research", "research:read", "research:write",
        ("research_project_id", "experiment_id", "sample_code", "sample_type", "status", "storage_location", "collected_at", "metadata_json", "notes"),
        required=("research_project_id", "sample_code"), search_fields=("sample_code", "sample_type", "status", "storage_location"), references=(("research_project_id", ResearchProject), ("experiment_id", Experiment)),
    ),
    "datasets": RecordConfig(
        ResearchDataset, "research", "research:read", "research:write",
        ("research_project_id", "experiment_id", "name", "version", "status", "storage_uri", "license", "checksum", "description"),
        required=("research_project_id", "name"), search_fields=("name", "version", "status", "license"), references=(("research_project_id", ResearchProject), ("experiment_id", Experiment)),
    ),
    "grants": RecordConfig(
        GrantRecord, "grants", "grants:read", "grants:write",
        ("project_id", "research_project_id", "funder", "program", "reference", "status", "amount", "currency", "submitted_at", "awarded_at", "starts_at", "ends_at", "notes"),
        required=("funder",), search_fields=("funder", "program", "reference", "status"), references=(("project_id", Project), ("research_project_id", ResearchProject)),
    ),
    "integrations": RecordConfig(
        TenantConnector, "integrations", "integrations:read", "integrations:manage",
        ("connector_type", "provider", "display_name", "status", "enabled", "provider_account_id", "granted_scopes_json", "configuration_json", "health_status", "last_health_check", "last_error"),
        required=("connector_type", "provider", "display_name"), search_fields=("provider", "display_name", "status", "health_status"), mutable=False,
    ),
}


async def _permissions(db: AsyncSession, auth: AuthContext) -> set[str]:
    permissions = await resolve_workspace_permissions(db, tenant_id=auth.tenant.id, role=auth.role)
    if auth.role == "owner":
        permissions |= set(DEFAULT_ROLE_AUTHORITY["owner"])
    return permissions


async def _require_permission(db: AsyncSession, auth: AuthContext, permission: str) -> set[str]:
    permissions = await _permissions(db, auth)
    if auth.role != "owner" and permission not in permissions:
        raise HTTPException(status_code=403, detail="Workspace permission denied")
    return permissions


async def _role_exists(db: AsyncSession, tenant_id: str, role_key: str) -> bool:
    if role_key in DEFAULT_ROLE_AUTHORITY:
        return True
    return bool(await db.scalar(select(WorkspaceRole.id).where(WorkspaceRole.tenant_id == tenant_id, WorkspaceRole.key == role_key)))


async def _module_row(db: AsyncSession, tenant_id: str, key: str) -> WorkspaceModule | None:
    return await db.scalar(select(WorkspaceModule).where(WorkspaceModule.tenant_id == tenant_id, WorkspaceModule.module_key == key))


async def _module_enabled(db: AsyncSession, tenant_id: str, key: str) -> bool:
    manifest = module_manifest(key)
    if manifest.get("locked"):
        return True
    row = await _module_row(db, tenant_id, key)
    return bool(manifest.get("default_enabled")) if row is None else bool(row.enabled)


async def _require_module(db: AsyncSession, auth: AuthContext, key: str, permission: str) -> set[str]:
    permissions = await _require_permission(db, auth, permission)
    if not await _module_enabled(db, auth.tenant.id, key):
        raise HTTPException(status_code=409, detail={"code": "MODULE_DISABLED", "message": f"Enable the {module_manifest(key)['name']} module first."})
    return permissions


def _clean_text(value: Any, *, max_length: int | None = None) -> str:
    raw = str(value or "").replace("\x00", "")
    if max_length is None:
        return raw.strip()
    normalized = " ".join(raw.split()).strip()
    return normalized[:max_length]


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
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
    return value.isoformat() if isinstance(value, datetime) else value


def _serialize_record(row: Any, config: RecordConfig) -> dict[str, Any]:
    keys = ["id", *config.fields]
    for system_key in ("created_at", "updated_at", "opened_at", "stage_changed_at"):
        if hasattr(row, system_key) and system_key not in keys:
            keys.append(system_key)
    return {key: _serialize_value(getattr(row, key, None)) for key in keys}


def _payload_values(config: RecordConfig, payload: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    unknown = sorted(set(payload) - set(config.fields))
    if unknown:
        raise HTTPException(status_code=422, detail="Unsupported fields: " + ", ".join(unknown))
    values = {field: _coerce(config.model, field, value) for field, value in payload.items()}
    if not partial:
        missing = [field for field in config.required if field not in values or values[field] in {None, ""}]
        if missing:
            raise HTTPException(status_code=422, detail="Required fields: " + ", ".join(missing))
    return values


async def _validate_references(db: AsyncSession, auth: AuthContext, config: RecordConfig, values: dict[str, Any]) -> None:
    for field, target_model in config.references:
        target_id = values.get(field)
        if not target_id:
            continue
        target = await db.get(target_model, target_id)
        if target is None or getattr(target, "tenant_id", None) != auth.tenant.id:
            raise HTTPException(status_code=422, detail=f"Invalid workspace reference for {field}")


async def _record_for_workspace(db: AsyncSession, auth: AuthContext, config: RecordConfig, record_id: str):
    row = await db.get(config.model, record_id)
    if row is None or getattr(row, "tenant_id", None) != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Record not found")
    return row


def _activity(db: AsyncSession, auth: AuthContext, verb: str, entity: str, record_id: str | None, summary: str) -> None:
    db.add(ActivityEvent(
        tenant_id=auth.tenant.id,
        event_type=f"record.{verb}",
        entity_type=entity,
        entity_id=record_id,
        summary=summary,
        actor=auth.user.display_name or auth.user.email,
    ))


async def _upsert_module(db: AsyncSession, auth: AuthContext, key: str, *, enabled: bool, configuration: dict[str, Any] | None = None) -> WorkspaceModule:
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
        row.configuration_json = json.dumps(configuration, separators=(",", ":"), sort_keys=True)
    return row


async def _enable_with_dependencies(db: AsyncSession, auth: AuthContext, key: str) -> None:
    manifest = module_manifest(key)
    for dependency in manifest.get("dependencies", []):
        await _enable_with_dependencies(db, auth, dependency)
    await _upsert_module(db, auth, key, enabled=True)


async def _recalculate_parent(db: AsyncSession, tenant_id: str, model: type, parent_id: str | None) -> None:
    if not parent_id:
        return
    if model is OrderItem:
        parent = await db.get(BusinessOrder, parent_id)
        if parent and parent.tenant_id == tenant_id:
            rows = (await db.scalars(select(OrderItem).where(OrderItem.tenant_id == tenant_id, OrderItem.order_id == parent_id))).all()
            parent.total = round(sum(float(row.quantity or 0) * float(row.unit_price or 0) for row in rows), 2)
    elif model is QuoteItem:
        parent = await db.get(Quote, parent_id)
        if parent and parent.tenant_id == tenant_id:
            rows = (await db.scalars(select(QuoteItem).where(QuoteItem.tenant_id == tenant_id, QuoteItem.quote_id == parent_id))).all()
            total = 0.0
            for row in rows:
                base = float(row.quantity or 0) * float(row.unit_price or 0)
                net = max(0.0, base - float(row.discount or 0))
                total += net + net * float(row.tax_rate or 0) / 100.0
            parent.total = round(total, 2)
    elif model is PurchaseOrderItem:
        parent = await db.get(PurchaseOrder, parent_id)
        if parent and parent.tenant_id == tenant_id:
            rows = (await db.scalars(select(PurchaseOrderItem).where(PurchaseOrderItem.tenant_id == tenant_id, PurchaseOrderItem.purchase_order_id == parent_id))).all()
            subtotal = sum(float(row.quantity or 0) * float(row.unit_cost or 0) for row in rows)
            tax = sum(float(row.quantity or 0) * float(row.unit_cost or 0) * float(row.tax_rate or 0) / 100.0 for row in rows)
            parent.subtotal = round(subtotal, 2)
            parent.total = round(subtotal + tax + float(parent.shipping_cost or 0), 2)
    elif model is InvoiceItem:
        parent = await db.get(Invoice, parent_id)
        if parent and parent.tenant_id == tenant_id:
            rows = (await db.scalars(select(InvoiceItem).where(InvoiceItem.tenant_id == tenant_id, InvoiceItem.invoice_id == parent_id))).all()
            subtotal = 0.0
            tax = 0.0
            for row in rows:
                base = float(row.quantity or 0) * float(row.unit_price or 0)
                net = max(0.0, base - float(row.discount or 0))
                subtotal += net
                tax += net * float(row.tax_rate or 0) / 100.0
            parent.subtotal = round(subtotal, 2)
            parent.tax = round(tax, 2)
            parent.total = round(subtotal + tax, 2)


async def _sync_invoice_payment_state(db: AsyncSession, tenant_id: str, invoice_id: str | None) -> None:
    if not invoice_id:
        return
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None or invoice.tenant_id != tenant_id:
        return
    paid = float(await db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.tenant_id == tenant_id,
            Payment.invoice_id == invoice_id,
            Payment.direction == "incoming",
            Payment.status == "completed",
        )
    ) or 0)
    if invoice.total > 0 and paid >= float(invoice.total):
        invoice.status = "paid"
        invoice.paid_at = invoice.paid_at or datetime.utcnow()
    elif invoice.status == "paid" and paid < float(invoice.total):
        invoice.status = "due"
        invoice.paid_at = None


@router.get("/invitation/inspect")
async def inspect_invitation(token: str = Query(..., min_length=20, max_length=500), db: AsyncSession = Depends(get_db)):
    info = await WorkspaceInvitationService.inspect(db, token=token)
    if info is None:
        raise HTTPException(status_code=404, detail="Workspace invitation is invalid or expired")
    return info.as_dict(reveal_email=False)


@router.post("/invitation/accept")
async def accept_invitation(
    payload: InvitationAcceptInput,
    auth: AccountAuthContext = Depends(get_account_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        membership = await WorkspaceInvitationService.accept(db, token=payload.token, user_id=auth.user.id)
    except WorkspaceInvitationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    auth.session.tenant_id = membership.tenant_id
    workspace = await db.get(__import__("packages.database.models", fromlist=["Tenant"]).Tenant, membership.tenant_id)
    await db.commit()
    return {
        "ok": True,
        "workspace_id": membership.tenant_id,
        "workspace_name": workspace.name if workspace else "Workspace",
        "role": membership.role,
        "next": f"/channels/{quote(membership.tenant_id, safe='')}",
    }


@router.get("/context")
async def workspace_context(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    permissions = await _permissions(db, auth)
    modules = []
    for key, manifest in MODULE_CATALOG.items():
        row = await _module_row(db, auth.tenant.id, key)
        enabled = True if manifest.get("locked") else (row.enabled if row is not None else bool(manifest.get("default_enabled")))
        module = module_manifest(key)
        modules.append({
            **module,
            "enabled": enabled,
            "state": row.state if row is not None else ("active" if enabled else "disabled"),
            "configuration": json.loads(row.configuration_json or "{}") if row is not None else {},
            "can_read": auth.role == "owner" or manifest["required_permission"] in permissions,
            "can_write": bool(manifest.get("write_permission")) and (auth.role == "owner" or manifest.get("write_permission") in permissions),
            "can_manage": auth.role == "owner" or "workspace:modules:manage" in permissions,
        })
    return {
        "workspace": {"id": auth.tenant.id, "name": auth.tenant.name, "slug": auth.tenant.slug, "timezone": auth.tenant.timezone, "logo_url": auth.tenant.logo_url},
        "user": {"id": auth.user.id, "email": auth.user.email, "display_name": auth.user.display_name},
        "role": auth.role,
        "permissions": sorted(permissions),
        "modules": modules,
    }


@router.patch("/settings")
async def update_workspace_settings(payload: WorkspaceSettingsInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
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
    _activity(db, auth, "updated", "workspace", auth.tenant.id, "Updated workspace settings")
    await db.commit()
    return {"ok": True, "workspace": {"id": auth.tenant.id, "name": auth.tenant.name, "timezone": auth.tenant.timezone, "logo_url": auth.tenant.logo_url}}


@router.get("/modules")
async def list_modules(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    return (await workspace_context(auth=auth, db=db))["modules"]


@router.put("/modules/{module_key}")
async def set_module_state(module_key: str, payload: ModuleStateInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:modules:manage")
    try:
        manifest = module_manifest(module_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    key = manifest["key"]
    if manifest.get("locked") and not payload.enabled:
        raise HTTPException(status_code=409, detail="Core workspace modules cannot be disabled")
    if payload.enabled:
        await _enable_with_dependencies(db, auth, key)
    else:
        blocking = []
        for candidate_key, candidate in MODULE_CATALOG.items():
            if key in candidate.get("dependencies", []) and await _module_enabled(db, auth.tenant.id, candidate_key):
                blocking.append(candidate["name"])
        if blocking:
            raise HTTPException(status_code=409, detail="Disable dependent modules first: " + ", ".join(sorted(blocking)))
        await _upsert_module(db, auth, key, enabled=False, configuration=payload.configuration)
    row = await _module_row(db, auth.tenant.id, key)
    if row and payload.enabled:
        row.configuration_json = json.dumps(payload.configuration, separators=(",", ":"), sort_keys=True)
    _activity(db, auth, "updated", "workspace_module", row.id if row else None, f"{'Enabled' if payload.enabled else 'Disabled'} {manifest['name']}")
    await db.commit()
    return {"ok": True, "key": key, "enabled": bool(row.enabled if row else payload.enabled), "state": row.state if row else "active", "configuration": json.loads(row.configuration_json or "{}") if row else payload.configuration}


@router.get("/presets")
async def list_presets(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:read")
    return [preset_manifest(key) for key in WORKSPACE_PRESETS]


@router.post("/presets/{preset_key}/apply")
async def apply_preset(preset_key: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:modules:manage")
    try:
        preset = preset_manifest(preset_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    for key in preset["modules"]:
        await _enable_with_dependencies(db, auth, key)
    _activity(db, auth, "updated", "workspace_preset", None, f"Applied {preset['name']} workspace pack")
    await db.commit()
    return {"ok": True, "preset": preset, "modules": preset["modules"]}


@router.get("/members")
async def members(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:read")
    rows = (await db.execute(
        select(TenantMember, AppUser).join(AppUser, AppUser.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == auth.tenant.id).order_by(AppUser.display_name, AppUser.email)
    )).all()
    return [{"user_id": user.id, "display_name": user.display_name, "email": user.email, "role": membership.role} for membership, user in rows]


@router.post("/members", status_code=201)
async def add_member(payload: MemberInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    role_key = normalize_role_key(payload.role)
    if role_key == "owner" and auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can assign the owner role")
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    email = payload.email.strip().lower()
    user = await db.scalar(select(AppUser).where(func.lower(AppUser.email) == email))
    if user is None or not user.active:
        raise HTTPException(status_code=404, detail="That email does not have an active Operly account yet. Create an invite link instead.")
    membership = TenantMember(tenant_id=auth.tenant.id, user_id=user.id, role=role_key)
    db.add(membership)
    _activity(db, auth, "created", "workspace_member", user.id, f"Added {user.display_name or user.email} as {role_key}")
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User is already a workspace member") from error
    return {"user_id": user.id, "display_name": user.display_name, "email": user.email, "role": role_key}


@router.patch("/members/{user_id}")
async def update_member_role(user_id: str, payload: MemberRoleInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    membership = await db.scalar(select(TenantMember).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.user_id == user_id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    role_key = normalize_role_key(payload.role)
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    if (membership.role == "owner" or role_key == "owner") and auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can change workspace ownership")
    if membership.role == "owner" and role_key != "owner":
        owner_count = await db.scalar(select(func.count(TenantMember.id)).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.role == "owner"))
        if int(owner_count or 0) <= 1:
            raise HTTPException(status_code=409, detail="A workspace must keep at least one owner")
    membership.role = role_key
    _activity(db, auth, "updated", "workspace_member", user_id, f"Changed workspace member role to {role_key}")
    await db.commit()
    return {"ok": True, "user_id": user_id, "role": role_key}


@router.delete("/members/{user_id}")
async def remove_member(user_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    membership = await db.scalar(select(TenantMember).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.user_id == user_id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace member not found")
    if membership.role == "owner":
        if auth.role != "owner":
            raise HTTPException(status_code=403, detail="Only an owner can remove an owner")
        owner_count = await db.scalar(select(func.count(TenantMember.id)).where(TenantMember.tenant_id == auth.tenant.id, TenantMember.role == "owner"))
        if int(owner_count or 0) <= 1:
            raise HTTPException(status_code=409, detail="A workspace must keep at least one owner")
    await db.delete(membership)
    _activity(db, auth, "deleted", "workspace_member", user_id, "Removed a workspace member")
    await db.commit()
    return {"ok": True}


@router.get("/invitations")
async def invitations(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    rows = await WorkspaceInvitationService.list_for_workspace(db, tenant_id=auth.tenant.id)
    return [{
        "id": row.id,
        "target_email": row.target_email,
        "role": row.role,
        "status": row.status,
        "source": row.source,
        "expires_at": row.expires_at.isoformat(),
        "accepted_at": row.accepted_at.isoformat() if row.accepted_at else None,
        "created_at": row.created_at.isoformat(),
    } for row in rows]


@router.post("/invitations", status_code=201)
async def create_invitation(payload: InvitationCreateInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    role_key = normalize_role_key(payload.role)
    if role_key == "owner" and auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can invite another owner")
    if not await _role_exists(db, auth.tenant.id, role_key):
        raise HTTPException(status_code=422, detail="Workspace role not found")
    target_email = payload.email.strip() if payload.email and payload.email.strip() else None
    try:
        row, token = await WorkspaceInvitationService.create(
            db,
            tenant_id=auth.tenant.id,
            role=role_key,
            invited_by_user_id=auth.user.id,
            target_email=target_email,
            source="workspace_os",
            ttl_days=payload.ttl_days,
        )
    except (ValueError, WorkspaceInvitationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _activity(db, auth, "created", "workspace_invitation", row.id, f"Created a {role_key} workspace invitation")
    await db.commit()
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return {
        "id": row.id,
        "role": row.role,
        "target_email": row.target_email,
        "expires_at": row.expires_at.isoformat(),
        "invite_url": f"{base}/join#invite={quote(token, safe='')}",
        "token": token,
    }


@router.delete("/invitations/{invitation_id}")
async def revoke_invitation(invitation_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:members:manage")
    try:
        row = await WorkspaceInvitationService.revoke(db, tenant_id=auth.tenant.id, invitation_id=invitation_id)
    except WorkspaceInvitationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    _activity(db, auth, "updated", "workspace_invitation", row.id, "Revoked a workspace invitation")
    await db.commit()
    return {"ok": True, "id": row.id, "status": row.status}


@router.get("/roles")
async def roles(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:read")
    custom = (await db.scalars(select(WorkspaceRole).where(WorkspaceRole.tenant_id == auth.tenant.id).order_by(WorkspaceRole.name))).all()
    custom_by_key = {row.key: row for row in custom}
    result = []
    for key in sorted(set(DEFAULT_ROLE_AUTHORITY) | set(custom_by_key)):
        row = custom_by_key.get(key)
        result.append({
            "key": key,
            "name": row.name if row else key.replace("-", " ").title(),
            "system": key in DEFAULT_ROLE_AUTHORITY,
            "permissions": sorted(await resolve_workspace_permissions(db, tenant_id=auth.tenant.id, role=key)),
        })
    return {"roles": result, "known_permissions": sorted(KNOWN_PERMISSIONS)}


@router.put("/roles/{role_key}")
async def set_role_permissions(role_key: str, payload: RolePermissionsInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:roles:manage")
    key = normalize_role_key(role_key)
    if key == "owner" and auth.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can edit the owner role")
    permissions = validate_permissions(payload.permissions)
    row = await db.scalar(select(WorkspaceRole).where(WorkspaceRole.tenant_id == auth.tenant.id, WorkspaceRole.key == key))
    if row is None:
        row = WorkspaceRole(tenant_id=auth.tenant.id, key=key, name=_clean_text(payload.name or key.replace("-", " ").title(), max_length=120), is_system=key in DEFAULT_ROLE_AUTHORITY)
        db.add(row)
        await db.flush()
    elif payload.name:
        row.name = _clean_text(payload.name, max_length=120)
    await db.execute(delete(WorkspaceRolePermission).where(WorkspaceRolePermission.role_id == row.id))
    db.add_all(WorkspaceRolePermission(role_id=row.id, permission=permission) for permission in sorted(permissions))
    _activity(db, auth, "updated", "workspace_role", row.id, f"Updated {row.name} permissions")
    await db.commit()
    return {"key": row.key, "name": row.name, "permissions": sorted(permissions)}


@router.get("/activity")
async def activity(limit: int = Query(default=40, ge=1, le=200), auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:read")
    rows = (await db.scalars(select(ActivityEvent).where(ActivityEvent.tenant_id == auth.tenant.id).order_by(ActivityEvent.created_at.desc()).limit(limit))).all()
    return [{
        "id": row.id, "event_type": row.event_type, "entity_type": row.entity_type, "entity_id": row.entity_id,
        "summary": row.summary, "actor": row.actor, "created_at": row.created_at.isoformat(),
    } for row in rows]


@router.get("/summary")
async def summary(auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_permission(db, auth, "workspace:read")
    tenant_id = auth.tenant.id

    async def count(model, *criteria) -> int:
        statement = select(func.count(model.id)).where(model.tenant_id == tenant_id)
        for criterion in criteria:
            statement = statement.where(criterion)
        return int(await db.scalar(statement) or 0)

    lead_value = float(await db.scalar(select(func.coalesce(func.sum(Lead.value), 0)).where(Lead.tenant_id == tenant_id, Lead.stage.notin_(["won", "lost"]))) or 0)
    sales_total = float(await db.scalar(select(func.coalesce(func.sum(BusinessOrder.total), 0)).where(BusinessOrder.tenant_id == tenant_id, BusinessOrder.status.notin_(["cancelled", "draft"]))) or 0)
    invoice_total = float(await db.scalar(select(func.coalesce(func.sum(Invoice.total), 0)).where(Invoice.tenant_id == tenant_id, Invoice.status.notin_(["void", "draft"]))) or 0)
    expense_total = float(await db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(Expense.tenant_id == tenant_id, Expense.status != "void")) or 0)
    incoming_payments = float(await db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == tenant_id, Payment.direction == "incoming", Payment.status == "completed")) or 0)
    grant_pipeline = float(await db.scalar(select(func.coalesce(func.sum(GrantRecord.amount), 0)).where(GrantRecord.tenant_id == tenant_id, GrantRecord.status.in_(["prospect", "preparing", "submitted", "under_review", "awarded"]))) or 0)
    return {
        "contacts": await count(Contact),
        "open_leads": await count(Lead, Lead.stage.notin_(["won", "lost"])),
        "pipeline_value": lead_value,
        "orders": await count(BusinessOrder),
        "sales_total": sales_total,
        "invoice_total": invoice_total,
        "expenses_total": expense_total,
        "incoming_payments": incoming_payments,
        "net_operating": incoming_payments - expense_total,
        "overdue_invoices": await count(Invoice, Invoice.status.in_(["due", "overdue"]), Invoice.due_at.is_not(None), Invoice.due_at < datetime.utcnow()),
        "products": await count(CatalogItem),
        "low_stock": await count(CatalogItem, CatalogItem.active.is_(True), CatalogItem.stock_qty <= CatalogItem.reorder_level),
        "open_tickets": await count(SupportTicket, SupportTicket.status.notin_(["resolved", "closed"])),
        "upcoming_appointments": await count(Appointment, Appointment.starts_at >= datetime.utcnow(), Appointment.status != "cancelled"),
        "active_projects": await count(Project, Project.status.in_(["planning", "active"])),
        "open_work_orders": await count(WorkOrder, WorkOrder.status.notin_(["completed", "cancelled"])),
        "maintenance_due": await count(Asset, Asset.next_maintenance_at.is_not(None), Asset.next_maintenance_at <= datetime.utcnow()),
        "open_risks": await count(RiskRecord, RiskRecord.status.notin_(["closed"])),
        "open_incidents": await count(IncidentRecord, IncidentRecord.status.notin_(["resolved", "closed"])),
        "active_research_projects": await count(ResearchProject, ResearchProject.status.in_(["planning", "active"])),
        "grant_pipeline": grant_pipeline,
        "scheduled_content": await count(MarketingContent, MarketingContent.status == "scheduled"),
    }


def _searchable_clause(config: RecordConfig, q: str):
    clauses = []
    for field in config.search_fields:
        column = getattr(config.model, field)
        if isinstance(config.model.__table__.columns[field].type, (String, Text)):
            clauses.append(column.ilike(f"%{q.strip()}%"))
    return or_(*clauses) if clauses else None


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
    criteria = [config.model.tenant_id == auth.tenant.id]
    if q and config.search_fields:
        clause = _searchable_clause(config, q)
        if clause is not None:
            criteria.append(clause)
    if status and hasattr(config.model, "status"):
        criteria.append(config.model.status == status)
    sort_key = sort or config.default_sort
    if not hasattr(config.model, sort_key):
        raise HTTPException(status_code=422, detail="Unsupported sort field")
    sort_column = getattr(config.model, sort_key)
    query = select(config.model).where(*criteria).order_by(sort_column.asc() if direction == "asc" else sort_column.desc()).offset(offset).limit(limit)
    rows = (await db.scalars(query)).all()
    total = int(await db.scalar(select(func.count(config.model.id)).where(*criteria)) or 0)
    return {"items": [_serialize_record(row, config) for row in rows], "total": total, "limit": limit, "offset": offset}


async def _after_record_mutation(db: AsyncSession, auth: AuthContext, model: type, row: Any, old_parent: str | None = None) -> None:
    if model is OrderItem:
        if old_parent and old_parent != row.order_id:
            await _recalculate_parent(db, auth.tenant.id, model, old_parent)
        await _recalculate_parent(db, auth.tenant.id, model, row.order_id)
    elif model is QuoteItem:
        if old_parent and old_parent != row.quote_id:
            await _recalculate_parent(db, auth.tenant.id, model, old_parent)
        await _recalculate_parent(db, auth.tenant.id, model, row.quote_id)
    elif model is PurchaseOrderItem:
        if old_parent and old_parent != row.purchase_order_id:
            await _recalculate_parent(db, auth.tenant.id, model, old_parent)
        await _recalculate_parent(db, auth.tenant.id, model, row.purchase_order_id)
    elif model is InvoiceItem:
        if old_parent and old_parent != row.invoice_id:
            await _recalculate_parent(db, auth.tenant.id, model, old_parent)
        await _recalculate_parent(db, auth.tenant.id, model, row.invoice_id)
    elif model is Payment:
        if old_parent and old_parent != row.invoice_id:
            await _sync_invoice_payment_state(db, auth.tenant.id, old_parent)
        await _sync_invoice_payment_state(db, auth.tenant.id, row.invoice_id)
    elif model is CRMInteraction and row.lead_id:
        lead = await db.get(Lead, row.lead_id)
        if lead and lead.tenant_id == auth.tenant.id:
            lead.last_activity_at = row.occurred_at or datetime.utcnow()
            if row.interaction_type in {"call", "email", "meeting", "message"}:
                lead.last_contacted_at = row.occurred_at or datetime.utcnow()
            if row.next_action_at:
                lead.next_action_at = row.next_action_at


@router.post("/records/{entity}", status_code=201)
async def create_record(entity: str, payload: dict[str, Any] = Body(...), auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    if not config.mutable:
        raise HTTPException(status_code=405, detail="This record type is read-only from the business workspace")
    await _require_module(db, auth, config.module, config.write_permission)
    values = _payload_values(config, payload, partial=False)
    await _validate_references(db, auth, config, values)
    values["tenant_id"] = auth.tenant.id
    if config.model is Task:
        values["owner_user_id"] = None
    row = config.model(**values)
    db.add(row)
    try:
        await db.flush()
        await _after_record_mutation(db, auth, config.model, row)
        _activity(db, auth, "created", entity, row.id, f"Created {entity.replace('-', ' ')} record")
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A record with that unique identifier already exists") from error
    await db.refresh(row)
    return _serialize_record(row, config)


@router.patch("/records/{entity}/{record_id}")
async def update_record(entity: str, record_id: str, payload: dict[str, Any] = Body(...), auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    if not config.mutable:
        raise HTTPException(status_code=405, detail="This record type is read-only from the business workspace")
    await _require_module(db, auth, config.module, config.write_permission)
    row = await _record_for_workspace(db, auth, config, record_id)
    parent_field = {OrderItem: "order_id", QuoteItem: "quote_id", PurchaseOrderItem: "purchase_order_id", InvoiceItem: "invoice_id", Payment: "invoice_id"}.get(config.model)
    old_parent = getattr(row, parent_field, None) if parent_field else None
    values = _payload_values(config, payload, partial=True)
    await _validate_references(db, auth, config, values)
    for key, value in values.items():
        setattr(row, key, value)
    if isinstance(row, Lead) and "stage" in values:
        row.stage_changed_at = datetime.utcnow()
    try:
        await db.flush()
        await _after_record_mutation(db, auth, config.model, row, old_parent)
        _activity(db, auth, "updated", entity, record_id, f"Updated {entity.replace('-', ' ')} record")
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Update conflicts with an existing record") from error
    await db.refresh(row)
    return _serialize_record(row, config)


@router.delete("/records/{entity}/{record_id}")
async def delete_record(entity: str, record_id: str, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    config = ENTITY_REGISTRY.get(entity)
    if config is None:
        raise HTTPException(status_code=404, detail="Unknown business record type")
    if not config.mutable:
        raise HTTPException(status_code=405, detail="This record type is read-only from the business workspace")
    await _require_module(db, auth, config.module, config.write_permission)
    row = await _record_for_workspace(db, auth, config, record_id)
    parent_field = {OrderItem: "order_id", QuoteItem: "quote_id", PurchaseOrderItem: "purchase_order_id", InvoiceItem: "invoice_id", Payment: "invoice_id"}.get(config.model)
    old_parent = getattr(row, parent_field, None) if parent_field else None
    await db.delete(row)
    try:
        await db.flush()
        if config.model in {OrderItem, QuoteItem, PurchaseOrderItem, InvoiceItem}:
            await _recalculate_parent(db, auth.tenant.id, config.model, old_parent)
        elif config.model is Payment:
            await _sync_invoice_payment_state(db, auth.tenant.id, old_parent)
        _activity(db, auth, "deleted", entity, record_id, f"Deleted {entity.replace('-', ' ')} record")
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This record is still referenced by other workspace data") from error
    return {"ok": True}


@router.post("/inventory/{item_id}/adjust")
async def adjust_inventory(item_id: str, payload: InventoryAdjustmentInput, auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_module(db, auth, "inventory", "inventory:write")
    item = await db.get(CatalogItem, item_id)
    if item is None or item.tenant_id != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    item.stock_qty += payload.quantity_change
    movement = InventoryMovement(tenant_id=auth.tenant.id, item_id=item.id, quantity_change=payload.quantity_change, reason=_clean_text(payload.reason, max_length=200))
    db.add(movement)
    await db.flush()
    _activity(db, auth, "updated", "inventory", item.id, f"Adjusted {item.name} stock by {payload.quantity_change:+d}")
    await db.commit()
    return {"ok": True, "item_id": item.id, "stock_qty": item.stock_qty, "movement_id": movement.id}


@router.get("/inventory/{item_id}/movements")
async def inventory_movements(item_id: str, limit: int = Query(default=100, ge=1, le=500), auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)):
    await _require_module(db, auth, "inventory", "inventory:read")
    item = await db.get(CatalogItem, item_id)
    if item is None or item.tenant_id != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    rows = (await db.scalars(select(InventoryMovement).where(InventoryMovement.tenant_id == auth.tenant.id, InventoryMovement.item_id == item_id).order_by(InventoryMovement.created_at.desc()).limit(limit))).all()
    return [{"id": row.id, "quantity_change": row.quantity_change, "reason": row.reason, "created_at": row.created_at.isoformat()} for row in rows]
