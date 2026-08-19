from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.business_models import (
    ActivityEvent,
    Appointment,
    BusinessOrder,
    CatalogItem,
    Contact,
    InventoryMovement,
    Lead,
    Quote,
)


VALID_LEAD_STAGES = {"new", "qualified", "proposal", "won", "lost"}
VALID_ITEM_TYPES = {"product", "service"}


class BusinessService:
    """Canonical business-domain implementation used by both HTTP and AI plugins.

    The service never commits. HTTP routers, ActionService, and other callers own
    transaction boundaries so the same implementation can safely participate in
    larger workflows.
    """

    @staticmethod
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
                actor=actor[:200] or "OPERLY",
            )
        )

    @staticmethod
    async def summary(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
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

    @staticmethod
    async def list_contacts(db: AsyncSession, tenant_id: str) -> list[Contact]:
        return list(
            (
                await db.scalars(
                    select(Contact)
                    .where(Contact.tenant_id == tenant_id)
                    .order_by(desc(Contact.created_at))
                )
            ).all()
        )

    @staticmethod
    async def create_contact(
        db: AsyncSession,
        tenant_id: str,
        *,
        name: str,
        email: str | None = None,
        phone: str | None = None,
        company: str | None = None,
        source: str = "manual",
        notes: str = "",
        actor: str = "OPERLY",
    ) -> Contact:
        name = name.strip()
        if not name:
            raise ValueError("Contact name is required")
        row = Contact(
            tenant_id=tenant_id,
            name=name[:200],
            email=(email or "").strip()[:320] or None,
            phone=(phone or "").strip()[:80] or None,
            company=(company or "").strip()[:200] or None,
            source=(source or "manual").strip()[:80] or "manual",
            notes=(notes or "")[:10000],
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "contact",
            row.id,
            f"Contact created: {row.name}",
            actor,
        )
        return row

    @staticmethod
    async def list_leads(db: AsyncSession, tenant_id: str) -> list[tuple[Lead, Contact | None]]:
        rows = (
            await db.execute(
                select(Lead, Contact)
                .outerjoin(Contact, Lead.contact_id == Contact.id)
                .where(Lead.tenant_id == tenant_id)
                .order_by(desc(Lead.created_at))
            )
        ).all()
        return list(rows)

    @staticmethod
    async def search_leads(
        db: AsyncSession,
        tenant_id: str,
        *,
        stale_days: int = 3,
        limit: int = 20,
    ) -> list[Lead]:
        from datetime import timedelta

        stale_days = max(0, stale_days)
        limit = max(1, min(limit, 50))
        return list(
            (
                await db.scalars(
                    select(Lead)
                    .where(
                        Lead.tenant_id == tenant_id,
                        Lead.stage.not_in(["won", "lost"]),
                        Lead.created_at <= datetime.utcnow() - timedelta(days=stale_days),
                    )
                    .order_by(Lead.value.desc(), Lead.created_at)
                    .limit(limit)
                )
            ).all()
        )

    @staticmethod
    async def create_lead(
        db: AsyncSession,
        tenant_id: str,
        *,
        title: str,
        contact_id: str | None = None,
        stage: str = "new",
        value: float = 0,
        assigned_to: str | None = None,
        next_action: str | None = None,
        actor: str = "OPERLY",
    ) -> Lead:
        title = title.strip()
        if not title:
            raise ValueError("Lead title is required")
        stage = stage.strip().lower()
        if stage not in VALID_LEAD_STAGES:
            raise ValueError("Invalid lead stage")
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == tenant_id,
                )
            )
            if contact is None:
                raise LookupError("Contact not found in this workspace")
        row = Lead(
            tenant_id=tenant_id,
            contact_id=contact_id,
            title=title[:300],
            stage=stage,
            value=max(float(value), 0),
            assigned_to=(assigned_to or "").strip()[:200] or None,
            next_action=(next_action or "").strip()[:2000] or None,
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "lead",
            row.id,
            f"Lead created: {row.title}",
            actor,
        )
        return row

    @staticmethod
    async def update_lead_stage(
        db: AsyncSession,
        tenant_id: str,
        lead_id: str,
        stage: str,
        *,
        actor: str = "OPERLY",
    ) -> Lead:
        stage = stage.strip().lower()
        if stage not in VALID_LEAD_STAGES:
            raise ValueError("Invalid lead stage")
        row = await db.scalar(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
        if row is None:
            raise LookupError("Lead not found")
        row.stage = stage
        row.stage_changed_at = datetime.utcnow()
        await BusinessService.log_event(
            db,
            tenant_id,
            "updated",
            "lead",
            row.id,
            f"Lead moved to {stage}: {row.title}",
            actor,
        )
        return row

    @staticmethod
    async def update_lead_next_action(
        db: AsyncSession,
        tenant_id: str,
        lead_id: str,
        next_action: str,
        *,
        actor: str = "OPERLY",
    ) -> Lead:
        row = await db.scalar(
            select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
        )
        if row is None:
            raise LookupError("Lead not found")
        row.next_action = next_action.strip()[:2000]
        row.last_activity_at = datetime.utcnow()
        await BusinessService.log_event(
            db,
            tenant_id,
            "updated",
            "lead",
            row.id,
            f"Lead next action updated: {row.title}",
            actor,
        )
        return row

    @staticmethod
    async def list_catalog(db: AsyncSession, tenant_id: str) -> list[CatalogItem]:
        return list(
            (
                await db.scalars(
                    select(CatalogItem)
                    .where(CatalogItem.tenant_id == tenant_id)
                    .order_by(desc(CatalogItem.created_at))
                )
            ).all()
        )

    @staticmethod
    async def create_catalog_item(
        db: AsyncSession,
        tenant_id: str,
        *,
        name: str,
        item_type: str = "product",
        sku: str | None = None,
        price: float = 0,
        cost: float = 0,
        stock_qty: int = 0,
        reorder_level: int = 0,
        actor: str = "OPERLY",
    ) -> CatalogItem:
        name = name.strip()
        item_type = item_type.strip().lower()
        if not name:
            raise ValueError("Catalog item name is required")
        if item_type not in VALID_ITEM_TYPES:
            raise ValueError("item_type must be product or service")
        if item_type == "service":
            stock_qty = 0
            reorder_level = 0
        row = CatalogItem(
            tenant_id=tenant_id,
            name=name[:250],
            item_type=item_type,
            sku=(sku or "").strip()[:100] or None,
            price=max(float(price), 0),
            cost=max(float(cost), 0),
            stock_qty=int(stock_qty),
            reorder_level=max(int(reorder_level), 0),
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "catalog_item",
            row.id,
            f"{row.item_type.title()} created: {row.name}",
            actor,
        )
        return row

    @staticmethod
    async def adjust_inventory(
        db: AsyncSession,
        tenant_id: str,
        item_id: str,
        quantity_change: int,
        *,
        reason: str = "adjustment",
        actor: str = "OPERLY",
    ) -> CatalogItem:
        quantity_change = int(quantity_change)
        if quantity_change == 0:
            raise ValueError("quantity_change cannot be zero")
        if abs(quantity_change) > 100_000:
            raise ValueError("Inventory adjustment is too large")
        row = await db.scalar(
            select(CatalogItem).where(
                CatalogItem.id == item_id,
                CatalogItem.tenant_id == tenant_id,
            )
        )
        if row is None:
            raise LookupError("Item not found")
        if row.item_type != "product":
            raise ValueError("Services do not have inventory")
        row.stock_qty += quantity_change
        db.add(
            InventoryMovement(
                tenant_id=tenant_id,
                item_id=row.id,
                quantity_change=quantity_change,
                reason=(reason or "adjustment")[:200],
            )
        )
        await BusinessService.log_event(
            db,
            tenant_id,
            "inventory_adjusted",
            "catalog_item",
            row.id,
            f"Inventory changed by {quantity_change}: {row.name}",
            actor,
        )
        return row

    @staticmethod
    async def create_order(
        db: AsyncSession,
        tenant_id: str,
        *,
        contact_id: str | None = None,
        status: str = "draft",
        total: float = 0,
        notes: str = "",
        actor: str = "OPERLY",
    ) -> BusinessOrder:
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            )
            if contact is None:
                raise LookupError("Contact not found in this workspace")
        row = BusinessOrder(
            tenant_id=tenant_id,
            contact_id=contact_id,
            status=(status or "draft")[:50],
            total=max(float(total), 0),
            notes=(notes or "")[:5000],
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "order",
            row.id,
            f"Order created for {row.total:.2f}",
            actor,
        )
        return row

    @staticmethod
    async def create_quote(
        db: AsyncSession,
        tenant_id: str,
        *,
        title: str,
        contact_id: str | None = None,
        status: str = "draft",
        total: float = 0,
        valid_until: datetime | None = None,
        notes: str = "",
        actor: str = "OPERLY",
    ) -> Quote:
        title = title.strip()
        if not title:
            raise ValueError("Quote title is required")
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            )
            if contact is None:
                raise LookupError("Contact not found in this workspace")
        row = Quote(
            tenant_id=tenant_id,
            contact_id=contact_id,
            title=title[:300],
            status=(status or "draft")[:50],
            total=max(float(total), 0),
            valid_until=valid_until,
            notes=(notes or "")[:5000],
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "quote",
            row.id,
            f"Quote created: {row.title}",
            actor,
        )
        return row

    @staticmethod
    async def schedule_appointment(
        db: AsyncSession,
        tenant_id: str,
        *,
        title: str,
        starts_at: datetime,
        ends_at: datetime | None = None,
        contact_id: str | None = None,
        assigned_to: str | None = None,
        notes: str = "",
        actor: str = "OPERLY",
    ) -> Appointment:
        title = title.strip()
        if not title:
            raise ValueError("Appointment title is required")
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            )
            if contact is None:
                raise LookupError("Contact not found in this workspace")
        row = Appointment(
            tenant_id=tenant_id,
            contact_id=contact_id,
            title=title[:300],
            starts_at=starts_at,
            ends_at=ends_at,
            assigned_to=(assigned_to or "").strip()[:200] or None,
            notes=(notes or "")[:5000],
            status="scheduled",
        )
        db.add(row)
        await db.flush()
        await BusinessService.log_event(
            db,
            tenant_id,
            "created",
            "appointment",
            row.id,
            f"Appointment scheduled: {row.title}",
            actor,
        )
        return row
