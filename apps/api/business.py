from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.business_models import (
    ActivityEvent,
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

router = APIRouter(prefix="/api/business", tags=["business"])


class ContactInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    source: str = "manual"
    notes: str = ""


class LeadInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    contact_id: str | None = None
    stage: str = "new"
    value: float = 0
    assigned_to: str | None = None
    next_action: str | None = None


class StageInput(BaseModel):
    stage: str


class CatalogInput(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    item_type: str = "product"
    sku: str | None = None
    price: float = 0
    cost: float = 0
    stock_qty: int = 0
    reorder_level: int = 0


class InventoryInput(BaseModel):
    quantity_change: int
    reason: str = "adjustment"


class OrderInput(BaseModel):
    contact_id: str | None = None
    status: str = "draft"
    total: float = 0
    notes: str = ""


class StatusInput(BaseModel):
    status: str


class QuoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    contact_id: str | None = None
    status: str = "draft"
    total: float = 0
    valid_until: datetime | None = None
    notes: str = ""


class AppointmentInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    contact_id: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    assigned_to: str | None = None
    notes: str = ""


class TeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    role: str = "employee"


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    document_type: str = "note"
    content: str = ""


async def log_event(
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    entity_type: str,
    entity_id: str | None,
    summary: str,
    actor: str,
) -> None:
    db.add(
        ActivityEvent(
            tenant_id=tenant_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary,
            actor=actor,
        )
    )


@router.get("/summary")
async def summary(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = auth.tenant.id

    async def count(model, *conditions):
        return (
            await db.scalar(
                select(func.count(model.id)).where(
                    model.tenant_id == tenant_id,
                    *conditions,
                )
            )
        ) or 0

    low_stock = await db.scalar(
        select(func.count(CatalogItem.id)).where(
            CatalogItem.tenant_id == tenant_id,
            CatalogItem.item_type == "product",
            CatalogItem.stock_qty <= CatalogItem.reorder_level,
            CatalogItem.active.is_(True),
        )
    )

    pipeline_value = await db.scalar(
        select(func.coalesce(func.sum(Lead.value), 0)).where(
            Lead.tenant_id == tenant_id,
            Lead.stage.not_in(["won", "lost"]),
        )
    )

    return {
        "contacts": await count(Contact),
        "open_leads": await count(Lead, Lead.stage.not_in(["won", "lost"])),
        "catalog_items": await count(CatalogItem, CatalogItem.active.is_(True)),
        "low_stock": low_stock or 0,
        "open_orders": await count(
            BusinessOrder,
            BusinessOrder.status.not_in(["completed", "cancelled"]),
        ),
        "upcoming_appointments": await count(
            Appointment,
            Appointment.status == "scheduled",
            Appointment.starts_at >= datetime.utcnow(),
        ),
        "pipeline_value": float(pipeline_value or 0),
    }


@router.get("/contacts")
async def contacts(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Contact)
            .where(Contact.tenant_id == auth.tenant.id)
            .order_by(desc(Contact.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "email": row.email,
            "phone": row.phone,
            "company": row.company,
            "source": row.source,
            "status": row.status,
            "notes": row.notes,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/contacts")
async def create_contact(
    payload: ContactInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = Contact(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "contact",
        row.id,
        f"Contact created: {row.name}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.get("/leads")
async def leads(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Lead)
            .where(Lead.tenant_id == auth.tenant.id)
            .order_by(desc(Lead.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "contact_id": row.contact_id,
            "title": row.title,
            "stage": row.stage,
            "value": row.value,
            "assigned_to": row.assigned_to,
            "next_action": row.next_action,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/leads")
async def create_lead(
    payload: LeadInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if payload.contact_id:
        contact = await db.scalar(
            select(Contact).where(
                Contact.id == payload.contact_id,
                Contact.tenant_id == auth.tenant.id,
            )
        )
        if contact is None:
            raise HTTPException(status_code=400, detail="Invalid contact")

    row = Lead(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "lead",
        row.id,
        f"Lead created: {row.title}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.patch("/leads/{lead_id}/stage")
async def update_lead_stage(
    lead_id: str,
    payload: StageInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Lead not found")

    row.stage = payload.stage
    await log_event(
        db,
        auth.tenant.id,
        "updated",
        "lead",
        row.id,
        f"Lead moved to {payload.stage}: {row.title}",
        auth.user.display_name,
    )
    await db.commit()
    return {"ok": True}


@router.get("/catalog")
async def catalog(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(CatalogItem)
            .where(CatalogItem.tenant_id == auth.tenant.id)
            .order_by(desc(CatalogItem.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "item_type": row.item_type,
            "sku": row.sku,
            "price": row.price,
            "cost": row.cost,
            "stock_qty": row.stock_qty,
            "reorder_level": row.reorder_level,
            "active": row.active,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/catalog")
async def create_catalog_item(
    payload: CatalogInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if payload.item_type not in {"product", "service"}:
        raise HTTPException(status_code=400, detail="Invalid item type")

    row = CatalogItem(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "catalog_item",
        row.id,
        f"{row.item_type.title()} created: {row.name}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.post("/catalog/{item_id}/inventory")
async def adjust_inventory(
    item_id: str,
    payload: InventoryInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(CatalogItem).where(
            CatalogItem.id == item_id,
            CatalogItem.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if row.item_type != "product":
        raise HTTPException(status_code=400, detail="Services have no inventory")

    row.stock_qty += payload.quantity_change
    db.add(
        InventoryMovement(
            tenant_id=auth.tenant.id,
            item_id=row.id,
            quantity_change=payload.quantity_change,
            reason=payload.reason,
        )
    )
    await log_event(
        db,
        auth.tenant.id,
        "inventory_adjusted",
        "catalog_item",
        row.id,
        f"Inventory changed by {payload.quantity_change}: {row.name}",
        auth.user.display_name,
    )
    await db.commit()
    return {"ok": True, "stock_qty": row.stock_qty}


@router.get("/orders")
async def orders(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(BusinessOrder)
            .where(BusinessOrder.tenant_id == auth.tenant.id)
            .order_by(desc(BusinessOrder.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "contact_id": row.contact_id,
            "status": row.status,
            "total": row.total,
            "notes": row.notes,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/orders")
async def create_order(
    payload: OrderInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = BusinessOrder(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "order",
        row.id,
        f"Order created for {row.total:.2f}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.patch("/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    payload: StatusInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(BusinessOrder).where(
            BusinessOrder.id == order_id,
            BusinessOrder.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    row.status = payload.status
    await db.commit()
    return {"ok": True}


@router.get("/quotes")
async def quotes(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Quote)
            .where(Quote.tenant_id == auth.tenant.id)
            .order_by(desc(Quote.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "contact_id": row.contact_id,
            "title": row.title,
            "status": row.status,
            "total": row.total,
            "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            "notes": row.notes,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/quotes")
async def create_quote(
    payload: QuoteInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = Quote(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "quote",
        row.id,
        f"Quote created: {row.title}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.patch("/quotes/{quote_id}/status")
async def update_quote_status(
    quote_id: str,
    payload: StatusInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(Quote).where(
            Quote.id == quote_id,
            Quote.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    row.status = payload.status
    await db.commit()
    return {"ok": True}


@router.get("/appointments")
async def appointments(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Appointment)
            .where(Appointment.tenant_id == auth.tenant.id)
            .order_by(Appointment.starts_at)
        )
    ).all()
    return [
        {
            "id": row.id,
            "contact_id": row.contact_id,
            "title": row.title,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat() if row.ends_at else None,
            "status": row.status,
            "assigned_to": row.assigned_to,
            "notes": row.notes,
        }
        for row in rows
    ]


@router.post("/appointments")
async def create_appointment(
    payload: AppointmentInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = Appointment(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "appointment",
        row.id,
        f"Appointment scheduled: {row.title}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.patch("/appointments/{appointment_id}/status")
async def update_appointment_status(
    appointment_id: str,
    payload: StatusInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    row.status = payload.status
    await db.commit()
    return {"ok": True}


@router.get("/team")
async def team(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(TeamMember)
            .where(TeamMember.tenant_id == auth.tenant.id)
            .order_by(TeamMember.name)
        )
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "email": row.email,
            "role": row.role,
            "active": row.active,
        }
        for row in rows
    ]


@router.post("/team")
async def create_team_member(
    payload: TeamInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = TeamMember(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "team_member",
        row.id,
        f"Team member added: {row.name}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.get("/documents")
async def documents(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(BusinessDocument)
            .where(BusinessDocument.tenant_id == auth.tenant.id)
            .order_by(desc(BusinessDocument.created_at))
        )
    ).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "document_type": row.document_type,
            "content": row.content,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/documents")
async def create_document(
    payload: DocumentInput,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = BusinessDocument(tenant_id=auth.tenant.id, **payload.model_dump())
    db.add(row)
    await db.flush()
    await log_event(
        db,
        auth.tenant.id,
        "created",
        "document",
        row.id,
        f"Document created: {row.title}",
        auth.user.display_name,
    )
    await db.commit()
    return {"id": row.id}


@router.get("/activity")
async def activity(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(ActivityEvent)
            .where(ActivityEvent.tenant_id == auth.tenant.id)
            .order_by(desc(ActivityEvent.created_at))
            .limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "entity_type": row.entity_type,
            "summary": row.summary,
            "actor": row.actor,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
