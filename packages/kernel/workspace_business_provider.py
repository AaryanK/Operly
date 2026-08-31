from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext
from apps.api.workspace_os_router import _module_enabled
from packages.database.business_models import (
    ActivityEvent,
    Appointment,
    BusinessOrder,
    CatalogItem,
    Contact,
    InventoryMovement,
    Lead,
    OrderItem,
)
from packages.database.business_suite_models import (
    BusinessAccount,
    CRMInteraction,
    Invoice,
    InvoiceItem,
    Payment,
    Project,
    ResearchProject,
    Supplier,
    SupportTicket,
)
from packages.database.models import AppUser, Task, Tenant
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext


PROVIDER_ID = "operly.workspace_business"


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


def _array(item: dict[str, Any], *, max_items: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "array", "items": item}
    if max_items is not None:
        result["maxItems"] = max_items
    return result


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
        tags=frozenset(("workspace", "business", "deterministic", *tags)),
        resource_scope="workspace",
    )


def workspace_business_capabilities() -> tuple[CapabilitySpec, ...]:
    search_item = _object(
        {
            "id": {"type": "string"},
            "entity": {"type": "string"},
            "destination": {"type": "string"},
            "type": {"type": "string"},
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
        },
        required=["id", "entity", "destination", "type", "title", "subtitle"],
    )
    sale_line = _object(
        {
            "catalog_item_id": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"], "maxLength": 300},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 100000},
            "unit_price": {"type": ["number", "null"], "minimum": 0},
        },
        required=["quantity"],
    )
    return (
        _capability(
            "workspace.search",
            "Search workspace",
            "Search across permission-visible customers, opportunities, products, invoices, projects, tasks, appointments, suppliers, and research projects.",
            permission="workspace:read",
            input_schema=_object(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 120},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                required=["query"],
            ),
            output_schema=_object({"items": _array(search_item)}, required=["items"]),
            tags=("search", "discovery", "read"),
        ),
        _capability(
            "workspace.attention.list",
            "List workspace attention items",
            "Return deterministic overdue, low-stock, follow-up, support, task, and appointment signals that need attention.",
            permission="workspace:read",
            output_schema=_object(
                {
                    "items": _array(_object({}, additional=True)),
                    "today": _array(_object({}, additional=True)),
                },
                required=["items", "today"],
            ),
            tags=("attention", "priorities", "read"),
        ),
        _capability(
            "workspace.customer.snapshot",
            "Read customer snapshot",
            "Return one customer's contact data, opportunities, orders, invoices, interactions, and deterministic commercial summary.",
            permission="crm:read",
            input_schema=_object({"contact_id": {"type": "string", "minLength": 1, "maxLength": 80}}, required=["contact_id"]),
            output_schema=_object({}, additional=True),
            tags=("crm", "customer", "context", "read"),
        ),
        _capability(
            "workspace.sales.complete",
            "Complete sale",
            "Atomically create an order, line items, inventory movements, and either a payment or invoice as one deterministic business transaction.",
            permission="orders:write",
            input_schema=_object(
                {
                    "contact_id": {"type": ["string", "null"], "maxLength": 80},
                    "items": {"type": "array", "items": sale_line, "minItems": 1, "maxItems": 100},
                    "payment_method": {"type": "string", "minLength": 1, "maxLength": 60},
                    "due_days": {"type": "integer", "minimum": 0, "maximum": 365},
                    "notes": {"type": "string", "maxLength": 10000},
                },
                required=["items"],
            ),
            output_schema=_object(
                {
                    "order_id": {"type": "string"},
                    "total": {"type": "number"},
                    "invoice_id": {"type": ["string", "null"]},
                    "invoice_number": {"type": ["string", "null"]},
                    "payment_id": {"type": ["string", "null"]},
                },
                required=["order_id", "total", "invoice_id", "invoice_number", "payment_id"],
            ),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=False,
            emits=("sale.completed",),
            tags=("sales", "finance", "inventory", "workflow", "write"),
        ),
        _capability(
            "workspace.finance.invoice.create_simple",
            "Create simple invoice",
            "Atomically create a due invoice and its line item for an amount and description.",
            permission="finance:write",
            input_schema=_object(
                {
                    "contact_id": {"type": ["string", "null"], "maxLength": 80},
                    "description": {"type": "string", "minLength": 1, "maxLength": 300},
                    "amount": {"type": "number", "minimum": 0.01},
                    "currency": {"type": "string", "minLength": 3, "maxLength": 3},
                    "due_days": {"type": "integer", "minimum": 0, "maximum": 365},
                    "notes": {"type": "string", "maxLength": 10000},
                },
                required=["description", "amount"],
            ),
            output_schema=_object(
                {
                    "invoice_id": {"type": "string"},
                    "number": {"type": "string"},
                    "total": {"type": "number"},
                },
                required=["invoice_id", "number", "total"],
            ),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=False,
            emits=("invoice.created",),
            tags=("finance", "invoice", "workflow", "write"),
        ),
        _capability(
            "workspace.finance.payment.record",
            "Record invoice payment",
            "Record an incoming payment against an invoice and deterministically synchronize invoice payment status.",
            permission="finance:write",
            input_schema=_object(
                {
                    "invoice_id": {"type": "string", "minLength": 1, "maxLength": 80},
                    "amount": {"type": "number", "minimum": 0.01},
                    "method": {"type": "string", "minLength": 1, "maxLength": 60},
                    "reference": {"type": ["string", "null"], "maxLength": 200},
                    "notes": {"type": "string", "maxLength": 10000},
                },
                required=["invoice_id", "amount"],
            ),
            output_schema=_object(
                {
                    "payment_id": {"type": "string"},
                    "invoice_id": {"type": "string"},
                    "invoice_status": {"type": "string"},
                },
                required=["payment_id", "invoice_id", "invoice_status"],
            ),
            risk=CapabilityRisk.MEDIUM,
            approval=True,
            reversible=False,
            emits=("payment.recorded",),
            tags=("finance", "payment", "workflow", "write"),
        ),
    )


class WorkspaceBusinessProvider:
    def __init__(self) -> None:
        self._handlers = {
            "workspace.search": self._search,
            "workspace.attention.list": self._attention,
            "workspace.customer.snapshot": self._customer_snapshot,
            "workspace.sales.complete": self._complete_sale,
            "workspace.finance.invoice.create_simple": self._create_invoice,
            "workspace.finance.payment.record": self._record_payment,
        }

    async def _auth(self, db: AsyncSession, context: ExecutionContext) -> AuthContext:
        if not context.workspace_id or not context.user_id:
            raise PermissionError("Workspace business capability requires a workspace member")
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
            raise LookupError(f"Workspace business capability is not implemented: {capability.id}")
        auth = await self._auth(db, context)
        return await handler(db, auth, context, arguments)

    def _activity(self, db: AsyncSession, auth: AuthContext, *, event_type: str, entity_type: str, entity_id: str | None, summary: str) -> None:
        db.add(
            ActivityEvent(
                tenant_id=auth.tenant.id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                summary=summary,
                actor=auth.user.display_name or auth.user.email,
            )
        )

    async def _allowed(self, db: AsyncSession, context: ExecutionContext, module: str, permission: str) -> bool:
        return context.can(permission) and await _module_enabled(db, context.workspace_id, module)

    async def _search(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        needle = str(arguments["query"]).strip()
        limit = max(1, min(int(arguments.get("limit") or 20), 50))
        pattern = f"%{needle}%"
        items: list[dict[str, Any]] = []

        async def add_rows(*, module: str, permission: str, model: type, where: Any, entity: str, destination: str, title_field: str, subtitle_fields: tuple[str, ...], type_label: str) -> None:
            if len(items) >= limit or not await self._allowed(db, context, module, permission):
                return
            rows = (
                await db.scalars(
                    select(model).where(model.tenant_id == auth.tenant.id, where).limit(min(5, limit - len(items)))
                )
            ).all()
            for row in rows:
                subtitle = " · ".join(str(getattr(row, field, "") or "") for field in subtitle_fields if getattr(row, field, None))
                items.append({
                    "id": row.id,
                    "entity": entity,
                    "destination": destination,
                    "type": type_label,
                    "title": str(getattr(row, title_field, "") or type_label),
                    "subtitle": subtitle,
                })

        await add_rows(module="crm", permission="crm:read", model=Contact, where=or_(Contact.name.ilike(pattern), Contact.email.ilike(pattern), Contact.phone.ilike(pattern), Contact.company.ilike(pattern)), entity="contacts", destination="customers", title_field="name", subtitle_fields=("company", "email", "phone"), type_label="Customer")
        await add_rows(module="crm", permission="crm:read", model=BusinessAccount, where=or_(BusinessAccount.name.ilike(pattern), BusinessAccount.email.ilike(pattern), BusinessAccount.phone.ilike(pattern), BusinessAccount.industry.ilike(pattern)), entity="organizations", destination="customers", title_field="name", subtitle_fields=("industry", "email"), type_label="Organization")
        await add_rows(module="crm", permission="crm:read", model=Lead, where=or_(Lead.title.ilike(pattern), Lead.stage.ilike(pattern), Lead.assigned_to.ilike(pattern)), entity="leads", destination="customers", title_field="title", subtitle_fields=("stage", "assigned_to"), type_label="Opportunity")
        await add_rows(module="catalog", permission="catalog:read", model=CatalogItem, where=or_(CatalogItem.name.ilike(pattern), CatalogItem.sku.ilike(pattern), CatalogItem.item_type.ilike(pattern)), entity="catalog", destination="products", title_field="name", subtitle_fields=("sku", "item_type"), type_label="Product / service")
        await add_rows(module="finance", permission="finance:read", model=Invoice, where=or_(Invoice.number.ilike(pattern), Invoice.status.ilike(pattern), Invoice.notes.ilike(pattern)), entity="invoices", destination="money", title_field="number", subtitle_fields=("status",), type_label="Invoice")
        await add_rows(module="projects", permission="projects:read", model=Project, where=or_(Project.name.ilike(pattern), Project.code.ilike(pattern), Project.status.ilike(pattern), Project.owner.ilike(pattern)), entity="projects", destination="work", title_field="name", subtitle_fields=("code", "status", "owner"), type_label="Project")
        await add_rows(module="tasks", permission="tasks:read", model=Task, where=or_(Task.title.ilike(pattern), Task.status.ilike(pattern)), entity="tasks", destination="work", title_field="title", subtitle_fields=("status",), type_label="Task")
        await add_rows(module="scheduling", permission="appointments:read", model=Appointment, where=or_(Appointment.title.ilike(pattern), Appointment.status.ilike(pattern), Appointment.assigned_to.ilike(pattern)), entity="appointments", destination="work", title_field="title", subtitle_fields=("status", "assigned_to"), type_label="Appointment")
        await add_rows(module="suppliers", permission="suppliers:read", model=Supplier, where=or_(Supplier.name.ilike(pattern), Supplier.email.ilike(pattern), Supplier.status.ilike(pattern)), entity="suppliers", destination="products", title_field="name", subtitle_fields=("status", "email"), type_label="Supplier")
        await add_rows(module="research", permission="research:read", model=ResearchProject, where=or_(ResearchProject.title.ilike(pattern), ResearchProject.code.ilike(pattern), ResearchProject.field.ilike(pattern), ResearchProject.status.ilike(pattern)), entity="research-projects", destination="more", title_field="title", subtitle_fields=("code", "field", "status"), type_label="Research project")
        return CapabilityExecutionResult(value={"items": items[:limit]}, resource_type="workspace", resource_id=auth.tenant.id)

    async def _attention(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        del arguments
        tenant_id = auth.tenant.id
        now = datetime.utcnow()
        soon = now + timedelta(hours=24)
        items: list[dict[str, Any]] = []
        today: list[dict[str, Any]] = []

        if await self._allowed(db, context, "finance", "finance:read"):
            overdue_count = int(await db.scalar(select(func.count(Invoice.id)).where(Invoice.tenant_id == tenant_id, Invoice.status.in_(["due", "overdue", "partial"]), Invoice.due_at.is_not(None), Invoice.due_at < now)) or 0)
            overdue_value = float(await db.scalar(select(func.coalesce(func.sum(Invoice.total), 0)).where(Invoice.tenant_id == tenant_id, Invoice.status.in_(["due", "overdue", "partial"]), Invoice.due_at.is_not(None), Invoice.due_at < now)) or 0)
            if overdue_count:
                items.append({"key": "overdue-invoices", "tone": "urgent", "entity": "invoices", "title": f"{overdue_count} overdue invoices", "detail": f"${overdue_value:,.2f} needs attention", "count": overdue_count})

        if await self._allowed(db, context, "inventory", "inventory:read"):
            low_stock = int(await db.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.tenant_id == tenant_id, CatalogItem.active.is_(True), CatalogItem.item_type == "product", CatalogItem.stock_qty <= CatalogItem.reorder_level)) or 0)
            if low_stock:
                items.append({"key": "low-stock", "tone": "warning", "entity": "catalog", "title": f"{low_stock} products are low on stock", "detail": "Review stock and reorder levels", "count": low_stock})

        if await self._allowed(db, context, "tasks", "tasks:read"):
            due_tasks = (await db.scalars(select(Task).where(Task.tenant_id == tenant_id, Task.status.notin_(["done", "completed", "cancelled"]), Task.due_at.is_not(None), Task.due_at <= soon).order_by(Task.due_at.asc()).limit(6))).all()
            overdue_tasks = sum(1 for task in due_tasks if task.due_at and task.due_at < now)
            if overdue_tasks:
                items.append({"key": "overdue-tasks", "tone": "urgent", "entity": "tasks", "title": f"{overdue_tasks} tasks are overdue", "detail": "Finish or reschedule them", "count": overdue_tasks})
            today.extend({"kind": "task", "entity": "tasks", "id": task.id, "title": task.title, "when": task.due_at.isoformat() if task.due_at else None} for task in due_tasks)

        if await self._allowed(db, context, "scheduling", "appointments:read"):
            appointments = (await db.scalars(select(Appointment).where(Appointment.tenant_id == tenant_id, Appointment.status != "cancelled", Appointment.starts_at >= now, Appointment.starts_at <= soon).order_by(Appointment.starts_at.asc()).limit(6))).all()
            today.extend({"kind": "appointment", "entity": "appointments", "id": row.id, "title": row.title, "when": row.starts_at.isoformat()} for row in appointments)

        if await self._allowed(db, context, "crm", "crm:read"):
            follow_ups = int(await db.scalar(select(func.count(Lead.id)).where(Lead.tenant_id == tenant_id, Lead.stage.notin_(["won", "lost"]), Lead.next_action_at.is_not(None), Lead.next_action_at <= now)) or 0)
            if follow_ups:
                items.append({"key": "follow-ups", "tone": "normal", "entity": "leads", "title": f"{follow_ups} customer follow-ups due", "detail": "Keep opportunities moving", "count": follow_ups})

        if await self._allowed(db, context, "support", "support:read"):
            urgent = int(await db.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.tenant_id == tenant_id, SupportTicket.status.notin_(["resolved", "closed"]), SupportTicket.priority.in_(["high", "urgent"]))) or 0)
            if urgent:
                items.append({"key": "support", "tone": "warning", "entity": "tickets", "title": f"{urgent} high-priority support tickets", "detail": "Customers are waiting", "count": urgent})

        today.sort(key=lambda item: item.get("when") or "")
        return CapabilityExecutionResult(value={"items": items[:8], "today": today[:10]}, resource_type="workspace", resource_id=tenant_id)

    async def _customer_snapshot(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not await self._allowed(db, context, "crm", "crm:read"):
            raise PermissionError("CRM module is disabled or unavailable")
        contact_id = str(arguments["contact_id"])
        contact = await db.get(Contact, contact_id)
        if contact is None or contact.tenant_id != auth.tenant.id:
            raise ValueError("Customer not found")
        leads = (await db.scalars(select(Lead).where(Lead.tenant_id == auth.tenant.id, Lead.contact_id == contact_id).order_by(Lead.created_at.desc()).limit(8))).all()
        orders = (await db.scalars(select(BusinessOrder).where(BusinessOrder.tenant_id == auth.tenant.id, BusinessOrder.contact_id == contact_id).order_by(BusinessOrder.created_at.desc()).limit(8))).all()
        invoices = (await db.scalars(select(Invoice).where(Invoice.tenant_id == auth.tenant.id, Invoice.contact_id == contact_id).order_by(Invoice.created_at.desc()).limit(8))).all()
        interactions = (await db.scalars(select(CRMInteraction).where(CRMInteraction.tenant_id == auth.tenant.id, CRMInteraction.contact_id == contact_id).order_by(CRMInteraction.occurred_at.desc()).limit(10))).all()
        lifetime_sales = float(sum(float(row.total or 0) for row in orders if row.status not in {"draft", "cancelled"}))
        outstanding = float(sum(float(row.total or 0) for row in invoices if row.status in {"due", "overdue", "partial"}))
        return CapabilityExecutionResult(
            value={
                "contact": {"id": contact.id, "name": contact.name, "email": contact.email, "phone": contact.phone, "company": contact.company, "status": contact.status, "source": contact.source, "notes": contact.notes},
                "summary": {"lifetime_sales": lifetime_sales, "outstanding": outstanding, "open_opportunities": sum(1 for row in leads if row.stage not in {"won", "lost"})},
                "leads": [{"id": row.id, "title": row.title, "stage": row.stage, "value": row.value, "next_action_at": row.next_action_at.isoformat() if row.next_action_at else None} for row in leads],
                "orders": [{"id": row.id, "status": row.status, "total": row.total, "created_at": row.created_at.isoformat()} for row in orders],
                "invoices": [{"id": row.id, "number": row.number, "status": row.status, "total": row.total, "due_at": row.due_at.isoformat() if row.due_at else None} for row in invoices],
                "interactions": [{"id": row.id, "subject": row.subject, "type": row.interaction_type, "channel": row.channel, "occurred_at": row.occurred_at.isoformat()} for row in interactions],
            },
            resource_type="contact",
            resource_id=contact.id,
        )

    async def _complete_sale(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        tenant_id = auth.tenant.id
        if not await _module_enabled(db, tenant_id, "sales"):
            raise PermissionError("Sales module is disabled")
        contact_id = arguments.get("contact_id")
        if contact_id:
            contact = await db.get(Contact, str(contact_id))
            if contact is None or contact.tenant_id != tenant_id:
                raise ValueError("Customer does not belong to this workspace")

        payment_method = str(arguments.get("payment_method") or "cash").strip().lower()
        pay_later = payment_method in {"pay_later", "invoice", "later"}
        if pay_later and (not context.can("finance:write") or not await _module_enabled(db, tenant_id, "finance")):
            raise PermissionError("Finance authority is required to sell on account")
        inventory_enabled = await _module_enabled(db, tenant_id, "inventory")

        order = BusinessOrder(tenant_id=tenant_id, contact_id=contact_id, status="confirmed" if pay_later else "completed", total=0, notes=str(arguments.get("notes") or "").strip())
        db.add(order)
        await db.flush()
        total = 0.0
        invoice_lines: list[tuple[str | None, str, int, float]] = []
        for raw in list(arguments["items"]):
            item = await db.get(CatalogItem, str(raw["catalog_item_id"])) if raw.get("catalog_item_id") else None
            if item is not None and item.tenant_id != tenant_id:
                raise ValueError("A selected item does not belong to this workspace")
            description = str(raw.get("description") or (item.name if item else "")).strip()
            if not description:
                raise ValueError("Every sale line needs a description or catalog item")
            quantity = int(raw.get("quantity") or 1)
            unit_price = float(raw["unit_price"] if raw.get("unit_price") is not None else (item.price if item else 0))
            total += round(unit_price * quantity, 2)
            db.add(OrderItem(tenant_id=tenant_id, order_id=order.id, catalog_item_id=item.id if item else None, description=description, quantity=quantity, unit_price=unit_price))
            invoice_lines.append((item.id if item else None, description, quantity, unit_price))
            if item and inventory_enabled and item.item_type == "product":
                if not context.can("inventory:write"):
                    raise PermissionError("Inventory authority is required to sell stocked products")
                if item.stock_qty < quantity:
                    raise ValueError(f"Not enough stock for {item.name}")
                item.stock_qty -= quantity
                db.add(InventoryMovement(tenant_id=tenant_id, item_id=item.id, quantity_change=-quantity, reason=f"sale:{order.id}"))

        order.total = round(total, 2)
        invoice: Invoice | None = None
        payment: Payment | None = None
        notes = str(arguments.get("notes") or "").strip()
        if pay_later:
            invoice = Invoice(tenant_id=tenant_id, contact_id=contact_id, order_id=order.id, number=f"INV-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}", status="due", currency="USD", subtotal=order.total, tax=0, total=order.total, due_at=datetime.utcnow() + timedelta(days=int(arguments.get("due_days") or 30)), notes=notes)
            db.add(invoice)
            await db.flush()
            for catalog_item_id, description, quantity, unit_price in invoice_lines:
                db.add(InvoiceItem(tenant_id=tenant_id, invoice_id=invoice.id, catalog_item_id=catalog_item_id, description=description, quantity=quantity, unit_price=unit_price, discount=0, tax_rate=0))
        elif order.total > 0 and await _module_enabled(db, tenant_id, "finance"):
            if not context.can("finance:write"):
                raise PermissionError("Finance authority is required to record payment")
            payment = Payment(tenant_id=tenant_id, contact_id=contact_id, order_id=order.id, direction="incoming", method=payment_method, provider=None, reference=None, status="completed", amount=order.total, currency="USD", paid_at=datetime.utcnow(), notes=notes)
            db.add(payment)
            await db.flush()

        self._activity(db, auth, event_type="workflow.sale.completed", entity_type="orders", entity_id=order.id, summary=f"Completed sale for ${order.total:,.2f}")
        await db.flush()
        return CapabilityExecutionResult(
            value={"order_id": order.id, "total": order.total, "invoice_id": invoice.id if invoice else None, "invoice_number": invoice.number if invoice else None, "payment_id": payment.id if payment else None},
            resource_type="order",
            resource_id=order.id,
            event_payload={"order_id": order.id, "total": order.total, "invoice_id": invoice.id if invoice else None, "payment_id": payment.id if payment else None},
        )

    async def _create_invoice(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not await _module_enabled(db, auth.tenant.id, "finance"):
            raise PermissionError("Finance module is disabled")
        contact_id = arguments.get("contact_id")
        if contact_id:
            contact = await db.get(Contact, str(contact_id))
            if contact is None or contact.tenant_id != auth.tenant.id:
                raise ValueError("Customer does not belong to this workspace")
        amount = float(arguments["amount"])
        invoice = Invoice(tenant_id=auth.tenant.id, contact_id=contact_id, order_id=None, number=f"INV-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}", status="due", currency=str(arguments.get("currency") or "USD").upper(), subtotal=amount, tax=0, total=amount, due_at=datetime.utcnow() + timedelta(days=int(arguments.get("due_days") or 30)), notes=str(arguments.get("notes") or "").strip())
        db.add(invoice)
        await db.flush()
        db.add(InvoiceItem(tenant_id=auth.tenant.id, invoice_id=invoice.id, catalog_item_id=None, description=str(arguments["description"]).strip(), quantity=1, unit_price=amount, discount=0, tax_rate=0))
        self._activity(db, auth, event_type="workflow.invoice.created", entity_type="invoices", entity_id=invoice.id, summary=f"Created invoice {invoice.number} for ${invoice.total:,.2f}")
        await db.flush()
        return CapabilityExecutionResult(value={"invoice_id": invoice.id, "number": invoice.number, "total": invoice.total}, resource_type="invoice", resource_id=invoice.id, event_payload={"invoice_id": invoice.id, "number": invoice.number, "total": invoice.total})

    async def _record_payment(self, db: AsyncSession, auth: AuthContext, context: ExecutionContext, arguments: dict[str, Any]) -> CapabilityExecutionResult:
        if not await _module_enabled(db, auth.tenant.id, "finance"):
            raise PermissionError("Finance module is disabled")
        invoice = await db.get(Invoice, str(arguments["invoice_id"]))
        if invoice is None or invoice.tenant_id != auth.tenant.id:
            raise ValueError("Invoice not found")
        payment = Payment(tenant_id=auth.tenant.id, contact_id=invoice.contact_id, invoice_id=invoice.id, order_id=invoice.order_id, direction="incoming", method=str(arguments.get("method") or "cash").strip().lower(), provider=None, reference=str(arguments.get("reference") or "").strip() or None, status="completed", amount=float(arguments["amount"]), currency=invoice.currency, paid_at=datetime.utcnow(), notes=str(arguments.get("notes") or "").strip())
        db.add(payment)
        await db.flush()
        paid = float(await db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.tenant_id == auth.tenant.id, Payment.invoice_id == invoice.id, Payment.direction == "incoming", Payment.status == "completed")) or 0)
        if invoice.total > 0 and paid >= float(invoice.total):
            invoice.status = "paid"
            invoice.paid_at = invoice.paid_at or datetime.utcnow()
        elif paid > 0:
            invoice.status = "partial"
            invoice.paid_at = None
        elif invoice.status in {"paid", "partial"}:
            invoice.status = "due"
            invoice.paid_at = None
        self._activity(db, auth, event_type="workflow.payment.recorded", entity_type="payments", entity_id=payment.id, summary=f"Recorded ${payment.amount:,.2f} payment for {invoice.number}")
        await db.flush()
        return CapabilityExecutionResult(value={"payment_id": payment.id, "invoice_id": invoice.id, "invoice_status": invoice.status}, resource_type="payment", resource_id=payment.id, event_payload={"payment_id": payment.id, "invoice_id": invoice.id, "invoice_status": invoice.status})
