from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from packages.business.service import BusinessService
from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.business_models import Appointment, BusinessOrder, CatalogItem, Contact, Lead, Quote


class UnifiedBusinessProvider(BaseProvider):
    """Business plugins backed by the same service used by the HTTP API."""

    name = "operly_business"
    capabilities = (
        CapabilityDefinition(
            "business.summary",
            "business_summary",
            "Read a compact summary of the tenant's current business state.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("analytics:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "crm.create_contact",
            "crm_create_contact",
            "Create a CRM contact in this tenant.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "company": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("crm:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "crm.create_lead",
            "crm_create_lead",
            "Create a CRM lead in this tenant.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "value": {"type": "number"},
                    "contact_id": {"type": "string"},
                    "stage": {"type": "string", "enum": ["new", "qualified", "proposal", "won", "lost"]},
                    "assigned_to": {"type": "string"},
                    "next_action": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("crm:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "crm.search_leads",
            "crm_search_leads",
            "Find actionable or stale sales leads in this tenant.",
            {
                "type": "object",
                "properties": {
                    "stale_days": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("crm:read",),
            approval_policy=ApprovalPolicy.AUTO,
        ),
        CapabilityDefinition(
            "crm.update_lead",
            "crm_update_lead",
            "Update the next action for an existing CRM lead.",
            {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "next_action": {"type": "string"},
                },
                "required": ["lead_id", "next_action"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("crm:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "crm.update_stage",
            "crm_update_stage",
            "Move an existing CRM lead to a valid sales stage.",
            {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "stage": {"type": "string", "enum": ["new", "qualified", "proposal", "won", "lost"]},
                },
                "required": ["lead_id", "stage"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("crm:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "catalog.create_item",
            "catalog_create_item",
            "Create a product or service in the business catalog.",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "item_type": {"type": "string", "enum": ["product", "service"]},
                    "sku": {"type": "string"},
                    "price": {"type": "number"},
                    "cost": {"type": "number"},
                    "stock_qty": {"type": "integer"},
                    "reorder_level": {"type": "integer"},
                },
                "required": ["name", "item_type"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("catalog:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "inventory.adjust",
            "inventory_adjust",
            "Adjust inventory for an existing product and record the movement.",
            {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "quantity_change": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["item_id", "quantity_change"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("inventory:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "orders.create",
            "orders_create",
            "Create a draft internal business order. This never charges a payment method.",
            {
                "type": "object",
                "properties": {
                    "contact_id": {"type": "string"},
                    "total": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("orders:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "quotes.create",
            "quotes_create",
            "Create a draft quote. This does not send it externally.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "contact_id": {"type": "string"},
                    "total": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("quotes:write",),
            reversible=True,
        ),
        CapabilityDefinition(
            "calendar.create_internal_event",
            "calendar_create_internal_event",
            "Create an internal Operly appointment without calling an external calendar provider.",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "starts_at": {"type": "string"},
                    "ends_at": {"type": "string"},
                    "contact_id": {"type": "string"},
                    "assigned_to": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["title", "starts_at"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="medium",
            permissions=("calendar:write",),
            reversible=True,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        actor = str(context.actor_id or "OPERLY")
        try:
            if capability_name == "business.summary":
                evidence = await BusinessService.summary(context.db, context.tenant_id)
                return CapabilityResult(True, False, evidence)

            if capability_name == "crm.create_contact":
                row = await BusinessService.create_contact(
                    context.db,
                    context.tenant_id,
                    name=str(arguments["name"]),
                    email=arguments.get("email"),
                    phone=arguments.get("phone"),
                    company=arguments.get("company"),
                    notes=str(arguments.get("notes") or ""),
                    source="operly_agent",
                    actor=actor,
                )
                return CapabilityResult(True, True, {"contact_id": row.id, "name": row.name}, row.id)

            if capability_name == "crm.create_lead":
                row = await BusinessService.create_lead(
                    context.db,
                    context.tenant_id,
                    title=str(arguments["title"]),
                    value=float(arguments.get("value", 0)),
                    contact_id=arguments.get("contact_id"),
                    stage=str(arguments.get("stage") or "new"),
                    assigned_to=arguments.get("assigned_to"),
                    next_action=arguments.get("next_action"),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"lead_id": row.id, "title": row.title, "stage": row.stage, "value": row.value}, row.id)

            if capability_name == "crm.search_leads":
                stale_days = max(0, int(arguments.get("stale_days", 3)))
                rows = await BusinessService.search_leads(
                    context.db,
                    context.tenant_id,
                    stale_days=stale_days,
                    limit=int(arguments.get("limit", 20)),
                )
                return CapabilityResult(
                    True,
                    False,
                    {
                        "leads": [
                            {
                                "id": row.id,
                                "title": row.title,
                                "stage": row.stage,
                                "value": row.value,
                                "next_action": row.next_action,
                                "created_at": row.created_at.isoformat(),
                            }
                            for row in rows
                        ],
                        "stale_days": stale_days,
                    },
                )

            if capability_name == "crm.update_lead":
                row = await BusinessService.update_lead_next_action(
                    context.db,
                    context.tenant_id,
                    str(arguments["lead_id"]),
                    str(arguments["next_action"]),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"lead_id": row.id, "next_action": row.next_action}, row.id)

            if capability_name == "crm.update_stage":
                row = await BusinessService.update_lead_stage(
                    context.db,
                    context.tenant_id,
                    str(arguments["lead_id"]),
                    str(arguments["stage"]),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"lead_id": row.id, "stage": row.stage}, row.id)

            if capability_name == "catalog.create_item":
                row = await BusinessService.create_catalog_item(
                    context.db,
                    context.tenant_id,
                    name=str(arguments["name"]),
                    item_type=str(arguments["item_type"]),
                    sku=arguments.get("sku"),
                    price=float(arguments.get("price", 0)),
                    cost=float(arguments.get("cost", 0)),
                    stock_qty=int(arguments.get("stock_qty", 0)),
                    reorder_level=int(arguments.get("reorder_level", 0)),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"item_id": row.id, "name": row.name, "item_type": row.item_type}, row.id)

            if capability_name == "inventory.adjust":
                row = await BusinessService.adjust_inventory(
                    context.db,
                    context.tenant_id,
                    str(arguments["item_id"]),
                    int(arguments["quantity_change"]),
                    reason=str(arguments.get("reason") or "adjustment"),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"item_id": row.id, "stock_qty": row.stock_qty}, row.id)

            if capability_name == "orders.create":
                row = await BusinessService.create_order(
                    context.db,
                    context.tenant_id,
                    contact_id=arguments.get("contact_id"),
                    total=float(arguments.get("total", 0)),
                    notes=str(arguments.get("notes") or ""),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"order_id": row.id, "status": row.status, "total": row.total}, row.id)

            if capability_name == "quotes.create":
                row = await BusinessService.create_quote(
                    context.db,
                    context.tenant_id,
                    title=str(arguments["title"]),
                    contact_id=arguments.get("contact_id"),
                    total=float(arguments.get("total", 0)),
                    notes=str(arguments.get("notes") or ""),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"quote_id": row.id, "title": row.title, "status": row.status}, row.id)

            if capability_name == "calendar.create_internal_event":
                starts_at = datetime.fromisoformat(str(arguments["starts_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
                ends_at = None
                if arguments.get("ends_at"):
                    ends_at = datetime.fromisoformat(str(arguments["ends_at"]).replace("Z", "+00:00")).replace(tzinfo=None)
                row = await BusinessService.schedule_appointment(
                    context.db,
                    context.tenant_id,
                    title=str(arguments["title"]),
                    starts_at=starts_at,
                    ends_at=ends_at,
                    contact_id=arguments.get("contact_id"),
                    assigned_to=arguments.get("assigned_to"),
                    notes=str(arguments.get("notes") or ""),
                    actor=actor,
                )
                return CapabilityResult(True, True, {"appointment_id": row.id, "starts_at": row.starts_at.isoformat()}, row.id)

        except (LookupError, ValueError, TypeError) as error:
            return CapabilityResult(False, False, {"reason": str(error)})

        return CapabilityResult(False, False, {"reason": "unsupported_business_capability"})

    async def verify(self, context, capability_name, arguments, result):
        if not result.success:
            return CapabilityResult(False, result.changed, result.evidence, result.external_reference)
        if capability_name in {"business.summary", "crm.search_leads"}:
            return CapabilityResult(True, False, {"observation_available": True, **result.evidence})

        model = None
        if capability_name == "crm.create_contact":
            model = Contact
        elif capability_name in {"crm.create_lead", "crm.update_lead", "crm.update_stage"}:
            model = Lead
        elif capability_name in {"catalog.create_item", "inventory.adjust"}:
            model = CatalogItem
        elif capability_name == "orders.create":
            model = BusinessOrder
        elif capability_name == "quotes.create":
            model = Quote
        elif capability_name == "calendar.create_internal_event":
            model = Appointment

        if model is None or not result.external_reference:
            return CapabilityResult(False, result.changed, {"reason": "verification_target_missing"})
        row = await context.db.scalar(
            select(model).where(model.id == result.external_reference, model.tenant_id == context.tenant_id)
        )
        return CapabilityResult(
            row is not None,
            result.changed,
            {"record_exists": row is not None, **result.evidence},
            result.external_reference,
        )
