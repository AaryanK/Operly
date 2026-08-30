from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
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
    Expense,
    Invoice,
    InvoiceItem,
    Payment,
    Project,
    ResearchProject,
    Supplier,
    SupportTicket,
)
from packages.database.models import Task
from packages.database.workspace_module_models import WorkspaceModule
from packages.security.permissions import DEFAULT_ROLE_AUTHORITY, resolve_workspace_permissions
from packages.workspace_modules.catalog import module_manifest


router = APIRouter(prefix="/api/workspace-simple", tags=["workspace-simple"])


class SaleLineInput(BaseModel):
    catalog_item_id: str | None = None
    description: str | None = Field(default=None, max_length=300)
    quantity: int = Field(default=1, ge=1, le=100_000)
    unit_price: float | None = Field(default=None, ge=0)


class SaleInput(BaseModel):
    contact_id: str | None = None
    items: list[SaleLineInput] = Field(min_length=1, max_length=100)
    payment_method: str = Field(default="cash", min_length=1, max_length=60)
    due_days: int = Field(default=30, ge=0, le=365)
    notes: str = Field(default="", max_length=10_000)


class SimpleInvoiceInput(BaseModel):
    contact_id: str | None = None
    description: str = Field(min_length=1, max_length=300)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    due_days: int = Field(default=30, ge=0, le=365)
    notes: str = Field(default="", max_length=10_000)


class RecordPaymentInput(BaseModel):
    invoice_id: str
    amount: float = Field(gt=0)
    method: str = Field(default="cash", min_length=1, max_length=60)
    reference: str | None = Field(default=None, max_length=200)
    notes: str = Field(default="", max_length=10_000)


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


async def _module_enabled(db: AsyncSession, tenant_id: str, module_key: str) -> bool:
    manifest = module_manifest(module_key)
    if manifest.get("locked"):
        return True
    row = await db.scalar(
        select(WorkspaceModule).where(
            WorkspaceModule.tenant_id == tenant_id,
            WorkspaceModule.module_key == module_key,
        )
    )
    if row is None:
        return bool(manifest.get("default_enabled"))
    return bool(row.enabled)


async def _require_module(
    db: AsyncSession,
    auth: AuthContext,
    module_key: str,
    permission: str,
) -> set[str]:
    permissions = await _require_permission(db, auth, permission)
    if not await _module_enabled(db, auth.tenant.id, module_key):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODULE_DISABLED",
                "message": f"Enable the {module_manifest(module_key)['name']} tool first.",
            },
        )
    return permissions


def _activity(
    db: AsyncSession,
    auth: AuthContext,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str | None,
    summary: str,
) -> None:
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


def _money(value: float) -> str:
    return f"${value:,.2f}"


async def _invoice_paid_total(db: AsyncSession, tenant_id: str, invoice_id: str) -> float:
    return float(
        await db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.tenant_id == tenant_id,
                Payment.invoice_id == invoice_id,
                Payment.direction == "incoming",
                Payment.status == "completed",
            )
        )
        or 0
    )


async def _sync_invoice(db: AsyncSession, invoice: Invoice) -> None:
    paid = await _invoice_paid_total(db, invoice.tenant_id, invoice.id)
    if invoice.total > 0 and paid >= float(invoice.total):
        invoice.status = "paid"
        invoice.paid_at = invoice.paid_at or datetime.utcnow()
    elif paid > 0:
        invoice.status = "partial"
        invoice.paid_at = None
    elif invoice.status in {"paid", "partial"}:
        invoice.status = "due"
        invoice.paid_at = None


@router.get("/search")
async def universal_search(
    q: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    needle = q.strip()
    if not needle:
        return {"items": []}

    permissions = await _permissions(db, auth)
    tenant_id = auth.tenant.id
    items: list[dict[str, Any]] = []

    async def allowed(module_key: str, permission: str) -> bool:
        return (
            (auth.role == "owner" or permission in permissions)
            and await _module_enabled(db, tenant_id, module_key)
        )

    async def add_rows(
        *,
        module_key: str,
        permission: str,
        model: type,
        where: Any,
        entity: str,
        destination: str,
        title_field: str,
        subtitle_fields: tuple[str, ...] = (),
        type_label: str,
    ) -> None:
        if len(items) >= limit or not await allowed(module_key, permission):
            return
        rows = (
            await db.scalars(
                select(model)
                .where(model.tenant_id == tenant_id, where)
                .limit(min(5, limit - len(items)))
            )
        ).all()
        for row in rows:
            subtitle = " · ".join(
                str(getattr(row, field, "") or "")
                for field in subtitle_fields
                if getattr(row, field, None)
            )
            items.append(
                {
                    "id": row.id,
                    "entity": entity,
                    "destination": destination,
                    "type": type_label,
                    "title": str(getattr(row, title_field, "") or type_label),
                    "subtitle": subtitle,
                }
            )

    pattern = f"%{needle}%"
    await add_rows(
        module_key="crm", permission="crm:read", model=Contact,
        where=or_(Contact.name.ilike(pattern), Contact.email.ilike(pattern), Contact.phone.ilike(pattern), Contact.company.ilike(pattern)),
        entity="contacts", destination="customers", title_field="name", subtitle_fields=("company", "email", "phone"), type_label="Customer",
    )
    await add_rows(
        module_key="crm", permission="crm:read", model=BusinessAccount,
        where=or_(BusinessAccount.name.ilike(pattern), BusinessAccount.email.ilike(pattern), BusinessAccount.phone.ilike(pattern), BusinessAccount.industry.ilike(pattern)),
        entity="organizations", destination="customers", title_field="name", subtitle_fields=("industry", "email"), type_label="Organization",
    )
    await add_rows(
        module_key="crm", permission="crm:read", model=Lead,
        where=or_(Lead.title.ilike(pattern), Lead.stage.ilike(pattern), Lead.assigned_to.ilike(pattern)),
        entity="leads", destination="customers", title_field="title", subtitle_fields=("stage", "assigned_to"), type_label="Opportunity",
    )
    await add_rows(
        module_key="catalog", permission="catalog:read", model=CatalogItem,
        where=or_(CatalogItem.name.ilike(pattern), CatalogItem.sku.ilike(pattern), CatalogItem.item_type.ilike(pattern)),
        entity="catalog", destination="products", title_field="name", subtitle_fields=("sku", "item_type"), type_label="Product / service",
    )
    await add_rows(
        module_key="finance", permission="finance:read", model=Invoice,
        where=or_(Invoice.number.ilike(pattern), Invoice.status.ilike(pattern), Invoice.notes.ilike(pattern)),
        entity="invoices", destination="money", title_field="number", subtitle_fields=("status",), type_label="Invoice",
    )
    await add_rows(
        module_key="projects", permission="projects:read", model=Project,
        where=or_(Project.name.ilike(pattern), Project.code.ilike(pattern), Project.status.ilike(pattern), Project.owner.ilike(pattern)),
        entity="projects", destination="work", title_field="name", subtitle_fields=("code", "status", "owner"), type_label="Project",
    )
    await add_rows(
        module_key="tasks", permission="tasks:read", model=Task,
        where=or_(Task.title.ilike(pattern), Task.status.ilike(pattern)),
        entity="tasks", destination="work", title_field="title", subtitle_fields=("status",), type_label="Task",
    )
    await add_rows(
        module_key="scheduling", permission="appointments:read", model=Appointment,
        where=or_(Appointment.title.ilike(pattern), Appointment.status.ilike(pattern), Appointment.assigned_to.ilike(pattern)),
        entity="appointments", destination="work", title_field="title", subtitle_fields=("status", "assigned_to"), type_label="Appointment",
    )
    await add_rows(
        module_key="suppliers", permission="suppliers:read", model=Supplier,
        where=or_(Supplier.name.ilike(pattern), Supplier.email.ilike(pattern), Supplier.status.ilike(pattern)),
        entity="suppliers", destination="products", title_field="name", subtitle_fields=("status", "email"), type_label="Supplier",
    )
    await add_rows(
        module_key="research", permission="research:read", model=ResearchProject,
        where=or_(ResearchProject.title.ilike(pattern), ResearchProject.code.ilike(pattern), ResearchProject.field.ilike(pattern), ResearchProject.status.ilike(pattern)),
        entity="research-projects", destination="more", title_field="title", subtitle_fields=("code", "field", "status"), type_label="Research project",
    )
    return {"items": items[:limit]}


@router.get("/attention")
async def attention(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    permissions = await _require_permission(db, auth, "workspace:read")
    tenant_id = auth.tenant.id
    now = datetime.utcnow()
    soon = now + timedelta(hours=24)
    items: list[dict[str, Any]] = []
    today: list[dict[str, Any]] = []

    async def can(module_key: str, permission: str) -> bool:
        return (
            (auth.role == "owner" or permission in permissions)
            and await _module_enabled(db, tenant_id, module_key)
        )

    if await can("finance", "finance:read"):
        overdue_count = int(
            await db.scalar(
                select(func.count(Invoice.id)).where(
                    Invoice.tenant_id == tenant_id,
                    Invoice.status.in_(["due", "overdue", "partial"]),
                    Invoice.due_at.is_not(None),
                    Invoice.due_at < now,
                )
            )
            or 0
        )
        overdue_value = float(
            await db.scalar(
                select(func.coalesce(func.sum(Invoice.total), 0)).where(
                    Invoice.tenant_id == tenant_id,
                    Invoice.status.in_(["due", "overdue", "partial"]),
                    Invoice.due_at.is_not(None),
                    Invoice.due_at < now,
                )
            )
            or 0
        )
        if overdue_count:
            items.append({
                "key": "overdue-invoices", "tone": "urgent", "destination": "money", "entity": "invoices",
                "title": f"{overdue_count} overdue invoice{'s' if overdue_count != 1 else ''}",
                "detail": f"{_money(overdue_value)} needs attention", "count": overdue_count,
            })

    if await can("inventory", "inventory:read"):
        low_stock = int(
            await db.scalar(
                select(func.count(CatalogItem.id)).where(
                    CatalogItem.tenant_id == tenant_id,
                    CatalogItem.active.is_(True),
                    CatalogItem.item_type == "product",
                    CatalogItem.stock_qty <= CatalogItem.reorder_level,
                )
            )
            or 0
        )
        if low_stock:
            items.append({
                "key": "low-stock", "tone": "warning", "destination": "products", "entity": "catalog",
                "title": f"{low_stock} product{'s are' if low_stock != 1 else ' is'} low on stock",
                "detail": "Review stock and reorder levels", "count": low_stock,
            })

    if await can("tasks", "tasks:read"):
        due_tasks = (
            await db.scalars(
                select(Task)
                .where(
                    Task.tenant_id == tenant_id,
                    Task.status.notin_(["done", "completed", "cancelled"]),
                    Task.due_at.is_not(None),
                    Task.due_at <= soon,
                )
                .order_by(Task.due_at.asc())
                .limit(6)
            )
        ).all()
        overdue_tasks = sum(1 for task in due_tasks if task.due_at and task.due_at < now)
        if overdue_tasks:
            items.append({
                "key": "overdue-tasks", "tone": "urgent", "destination": "work", "entity": "tasks",
                "title": f"{overdue_tasks} task{'s are' if overdue_tasks != 1 else ' is'} overdue",
                "detail": "Open Work to finish or reschedule them", "count": overdue_tasks,
            })
        for task in due_tasks:
            today.append({
                "kind": "task", "entity": "tasks", "id": task.id, "title": task.title,
                "when": task.due_at.isoformat() if task.due_at else None, "destination": "work",
            })

    if await can("scheduling", "appointments:read"):
        appointments = (
            await db.scalars(
                select(Appointment)
                .where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.status != "cancelled",
                    Appointment.starts_at >= now,
                    Appointment.starts_at <= soon,
                )
                .order_by(Appointment.starts_at.asc())
                .limit(6)
            )
        ).all()
        for appointment in appointments:
            today.append({
                "kind": "appointment", "entity": "appointments", "id": appointment.id,
                "title": appointment.title, "when": appointment.starts_at.isoformat(), "destination": "work",
            })

    if await can("crm", "crm:read"):
        follow_ups = int(
            await db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.tenant_id == tenant_id,
                    Lead.stage.notin_(["won", "lost"]),
                    Lead.next_action_at.is_not(None),
                    Lead.next_action_at <= now,
                )
            )
            or 0
        )
        if follow_ups:
            items.append({
                "key": "follow-ups", "tone": "normal", "destination": "customers", "entity": "leads",
                "title": f"{follow_ups} customer follow-up{'s' if follow_ups != 1 else ''} due",
                "detail": "Keep opportunities moving", "count": follow_ups,
            })

    if await can("support", "support:read"):
        urgent_tickets = int(
            await db.scalar(
                select(func.count(SupportTicket.id)).where(
                    SupportTicket.tenant_id == tenant_id,
                    SupportTicket.status.notin_(["resolved", "closed"]),
                    SupportTicket.priority.in_(["high", "urgent"]),
                )
            )
            or 0
        )
        if urgent_tickets:
            items.append({
                "key": "support", "tone": "warning", "destination": "customers", "entity": "tickets",
                "title": f"{urgent_tickets} high-priority support ticket{'s' if urgent_tickets != 1 else ''}",
                "detail": "Customers are waiting for a response", "count": urgent_tickets,
            })

    today.sort(key=lambda item: item.get("when") or "")
    return {"items": items[:8], "today": today[:10]}


@router.get("/customers/{contact_id}")
async def customer_snapshot(
    contact_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_module(db, auth, "crm", "crm:read")
    tenant_id = auth.tenant.id
    contact = await db.get(Contact, contact_id)
    if contact is None or contact.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    leads = (
        await db.scalars(
            select(Lead).where(Lead.tenant_id == tenant_id, Lead.contact_id == contact_id)
            .order_by(Lead.created_at.desc()).limit(8)
        )
    ).all()
    orders = (
        await db.scalars(
            select(BusinessOrder).where(BusinessOrder.tenant_id == tenant_id, BusinessOrder.contact_id == contact_id)
            .order_by(BusinessOrder.created_at.desc()).limit(8)
        )
    ).all()
    invoices = (
        await db.scalars(
            select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.contact_id == contact_id)
            .order_by(Invoice.created_at.desc()).limit(8)
        )
    ).all()
    interactions = (
        await db.scalars(
            select(CRMInteraction).where(CRMInteraction.tenant_id == tenant_id, CRMInteraction.contact_id == contact_id)
            .order_by(CRMInteraction.occurred_at.desc()).limit(10)
        )
    ).all()

    lifetime_sales = float(sum(float(order.total or 0) for order in orders if order.status not in {"draft", "cancelled"}))
    outstanding = float(sum(float(invoice.total or 0) for invoice in invoices if invoice.status in {"due", "overdue", "partial"}))
    return {
        "contact": {
            "id": contact.id, "name": contact.name, "email": contact.email, "phone": contact.phone,
            "company": contact.company, "status": contact.status, "source": contact.source, "notes": contact.notes,
            "created_at": contact.created_at.isoformat(),
        },
        "summary": {"lifetime_sales": lifetime_sales, "outstanding": outstanding, "open_opportunities": sum(1 for lead in leads if lead.stage not in {"won", "lost"})},
        "leads": [{"id": row.id, "title": row.title, "stage": row.stage, "value": row.value, "next_action_at": row.next_action_at.isoformat() if row.next_action_at else None} for row in leads],
        "orders": [{"id": row.id, "status": row.status, "total": row.total, "created_at": row.created_at.isoformat()} for row in orders],
        "invoices": [{"id": row.id, "number": row.number, "status": row.status, "total": row.total, "due_at": row.due_at.isoformat() if row.due_at else None} for row in invoices],
        "interactions": [{"id": row.id, "subject": row.subject, "type": row.interaction_type, "channel": row.channel, "occurred_at": row.occurred_at.isoformat()} for row in interactions],
    }


@router.post("/sales", status_code=201)
async def complete_sale(
    payload: SaleInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    permissions = await _require_module(db, auth, "sales", "orders:write")
    tenant_id = auth.tenant.id
    if payload.contact_id:
        contact = await db.get(Contact, payload.contact_id)
        if contact is None or contact.tenant_id != tenant_id:
            raise HTTPException(status_code=422, detail="Customer does not belong to this workspace")

    pay_later = payload.payment_method.strip().lower() in {"pay_later", "invoice", "later"}
    if pay_later:
        if auth.role != "owner" and "finance:write" not in permissions:
            raise HTTPException(status_code=403, detail="Finance permission is required to sell on account")
        if not await _module_enabled(db, tenant_id, "finance"):
            raise HTTPException(status_code=409, detail="Enable Money/Finance before selling on account")

    inventory_enabled = await _module_enabled(db, tenant_id, "inventory")
    can_adjust_inventory = auth.role == "owner" or "inventory:write" in permissions

    order = BusinessOrder(
        tenant_id=tenant_id,
        contact_id=payload.contact_id,
        status="confirmed" if pay_later else "completed",
        total=0,
        notes=payload.notes.strip(),
    )
    db.add(order)
    await db.flush()

    total = 0.0
    invoice_lines: list[tuple[str | None, str, int, float]] = []
    for line in payload.items:
        item: CatalogItem | None = None
        if line.catalog_item_id:
            item = await db.get(CatalogItem, line.catalog_item_id)
            if item is None or item.tenant_id != tenant_id:
                raise HTTPException(status_code=422, detail="A selected item does not belong to this workspace")
        description = (line.description or (item.name if item else "")).strip()
        if not description:
            raise HTTPException(status_code=422, detail="Every sale line needs a description or catalog item")
        unit_price = float(line.unit_price if line.unit_price is not None else (item.price if item else 0))
        line_total = round(unit_price * line.quantity, 2)
        total += line_total
        db.add(
            OrderItem(
                tenant_id=tenant_id,
                order_id=order.id,
                catalog_item_id=item.id if item else None,
                description=description,
                quantity=line.quantity,
                unit_price=unit_price,
            )
        )
        invoice_lines.append((item.id if item else None, description, line.quantity, unit_price))

        if item and inventory_enabled and item.item_type == "product":
            if not can_adjust_inventory:
                raise HTTPException(status_code=403, detail="Inventory permission is required to sell stocked products")
            if item.stock_qty < line.quantity:
                raise HTTPException(status_code=409, detail=f"Not enough stock for {item.name}")
            item.stock_qty -= line.quantity
            db.add(
                InventoryMovement(
                    tenant_id=tenant_id,
                    item_id=item.id,
                    quantity_change=-line.quantity,
                    reason=f"sale:{order.id}",
                )
            )

    order.total = round(total, 2)
    invoice: Invoice | None = None
    payment: Payment | None = None

    if pay_later:
        invoice = Invoice(
            tenant_id=tenant_id,
            contact_id=payload.contact_id,
            order_id=order.id,
            number=f"INV-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}",
            status="due",
            currency="USD",
            subtotal=order.total,
            tax=0,
            total=order.total,
            due_at=datetime.utcnow() + timedelta(days=payload.due_days),
            notes=payload.notes.strip(),
        )
        db.add(invoice)
        await db.flush()
        for catalog_item_id, description, quantity, unit_price in invoice_lines:
            db.add(
                InvoiceItem(
                    tenant_id=tenant_id,
                    invoice_id=invoice.id,
                    catalog_item_id=catalog_item_id,
                    description=description,
                    quantity=quantity,
                    unit_price=unit_price,
                    discount=0,
                    tax_rate=0,
                )
            )
    elif order.total > 0 and await _module_enabled(db, tenant_id, "finance"):
        if auth.role != "owner" and "finance:write" not in permissions:
            raise HTTPException(status_code=403, detail="Finance permission is required to record payment")
        payment = Payment(
            tenant_id=tenant_id,
            contact_id=payload.contact_id,
            order_id=order.id,
            direction="incoming",
            method=payload.payment_method.strip().lower(),
            provider=None,
            reference=None,
            status="completed",
            amount=order.total,
            currency="USD",
            paid_at=datetime.utcnow(),
            notes=payload.notes.strip(),
        )
        db.add(payment)

    _activity(
        db,
        auth,
        event_type="workflow.sale.completed",
        entity_type="orders",
        entity_id=order.id,
        summary=f"Completed sale for {_money(order.total)}" if not pay_later else f"Created {_money(order.total)} sale on account",
    )
    await db.commit()
    return {
        "ok": True,
        "order_id": order.id,
        "total": order.total,
        "invoice_id": invoice.id if invoice else None,
        "invoice_number": invoice.number if invoice else None,
        "payment_id": payment.id if payment else None,
    }


@router.post("/invoices", status_code=201)
async def create_simple_invoice(
    payload: SimpleInvoiceInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_module(db, auth, "finance", "finance:write")
    if payload.contact_id:
        contact = await db.get(Contact, payload.contact_id)
        if contact is None or contact.tenant_id != auth.tenant.id:
            raise HTTPException(status_code=422, detail="Customer does not belong to this workspace")
    invoice = Invoice(
        tenant_id=auth.tenant.id,
        contact_id=payload.contact_id,
        order_id=None,
        number=f"INV-{datetime.utcnow():%Y%m%d}-{uuid4().hex[:6].upper()}",
        status="due",
        currency=payload.currency.upper(),
        subtotal=payload.amount,
        tax=0,
        total=payload.amount,
        due_at=datetime.utcnow() + timedelta(days=payload.due_days),
        notes=payload.notes.strip(),
    )
    db.add(invoice)
    await db.flush()
    db.add(
        InvoiceItem(
            tenant_id=auth.tenant.id,
            invoice_id=invoice.id,
            catalog_item_id=None,
            description=payload.description.strip(),
            quantity=1,
            unit_price=payload.amount,
            discount=0,
            tax_rate=0,
        )
    )
    _activity(
        db,
        auth,
        event_type="workflow.invoice.created",
        entity_type="invoices",
        entity_id=invoice.id,
        summary=f"Created invoice {invoice.number} for {_money(invoice.total)}",
    )
    await db.commit()
    return {"ok": True, "invoice_id": invoice.id, "number": invoice.number, "total": invoice.total}


@router.post("/payments", status_code=201)
async def record_payment(
    payload: RecordPaymentInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await _require_module(db, auth, "finance", "finance:write")
    invoice = await db.get(Invoice, payload.invoice_id)
    if invoice is None or invoice.tenant_id != auth.tenant.id:
        raise HTTPException(status_code=404, detail="Invoice not found")
    payment = Payment(
        tenant_id=auth.tenant.id,
        contact_id=invoice.contact_id,
        invoice_id=invoice.id,
        order_id=invoice.order_id,
        direction="incoming",
        method=payload.method.strip().lower(),
        provider=None,
        reference=payload.reference.strip() if payload.reference else None,
        status="completed",
        amount=payload.amount,
        currency=invoice.currency,
        paid_at=datetime.utcnow(),
        notes=payload.notes.strip(),
    )
    db.add(payment)
    await db.flush()
    await _sync_invoice(db, invoice)
    _activity(
        db,
        auth,
        event_type="workflow.payment.recorded",
        entity_type="payments",
        entity_id=payment.id,
        summary=f"Recorded {_money(payment.amount)} payment for {invoice.number}",
    )
    await db.commit()
    return {"ok": True, "payment_id": payment.id, "invoice_id": invoice.id, "invoice_status": invoice.status}
