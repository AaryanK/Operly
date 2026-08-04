from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from packages.business_brain.registry import ToolRegistry
from packages.business_brain.operations_tools import register_operations_tools
from packages.business_brain.types import ToolContext
from packages.database.business_models import (
    Appointment,
    BusinessOrder,
    CatalogItem,
    Contact,
    Lead,
    Quote,
)
from packages.database.db import session_scope
from packages.database.models import (
    Approval,
    Memory,
    Message,
    ScheduledJob,
    Task,
)


def string_arg(args: dict[str, Any], key: str, maximum: int = 500) -> str:
    value = str(args.get(key, "")).strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value[:maximum]


async def create_task(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    title = string_arg(args, "title")

    async with session_scope() as db:
        row = Task(
            tenant_id=context.tenant_id,
            title=title,
            status="open",
        )
        db.add(row)
        await db.flush()
        return {"ok": True, "task_id": row.id, "title": row.title}


async def list_tasks(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Task)
                .where(
                    Task.tenant_id == context.tenant_id,
                    Task.status == "open",
                )
                .order_by(desc(Task.created_at))
                .limit(25)
            )
        ).all()

    return {
        "ok": True,
        "tasks": [{"id": row.id, "title": row.title} for row in rows],
    }


async def complete_task(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    task_id = string_arg(args, "task_id", 80)

    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Task).where(
                    Task.tenant_id == context.tenant_id,
                    Task.id.like(f"{task_id}%"),
                )
            )
        ).all()

        if len(rows) != 1:
            return {
                "ok": False,
                "error": "Provide one unambiguous task ID or ID prefix",
            }

        rows[0].status = "completed"
        return {"ok": True, "task_id": rows[0].id}


async def remember_fact(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    fact = string_arg(args, "fact", 10_000)

    async with session_scope() as db:
        row = Memory(
            tenant_id=context.tenant_id,
            kind="fact",
            content=fact,
        )
        db.add(row)
        await db.flush()
        return {"ok": True, "memory_id": row.id}


async def search_memory(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    query = string_arg(args, "query", 200)
    pattern = f"%{query}%"

    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Memory)
                .where(
                    Memory.tenant_id == context.tenant_id,
                    Memory.content.ilike(pattern),
                )
                .order_by(desc(Memory.created_at))
                .limit(20)
            )
        ).all()

    return {
        "ok": True,
        "matches": [
            {"id": row.id, "kind": row.kind, "content": row.content[:1000]}
            for row in rows
        ],
    }


async def search_messages(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    query = string_arg(args, "query", 200)
    pattern = f"%{query}%"

    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == context.tenant_id,
                    Message.content.ilike(pattern),
                )
                .order_by(desc(Message.created_at))
                .limit(20)
            )
        ).all()

    return {
        "ok": True,
        "matches": [
            {
                "author": row.author_name,
                "content": row.content[:900],
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


async def create_contact(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    name = string_arg(args, "name", 200)
    email = str(args.get("email") or "").strip()[:320] or None
    phone = str(args.get("phone") or "").strip()[:80] or None
    company = str(args.get("company") or "").strip()[:200] or None

    async with session_scope() as db:
        row = Contact(
            tenant_id=context.tenant_id,
            name=name,
            email=email,
            phone=phone,
            company=company,
            source=context.channel,
        )
        db.add(row)
        await db.flush()
        return {"ok": True, "contact_id": row.id, "name": row.name}


async def create_lead(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    title = string_arg(args, "title", 300)
    value = max(float(args.get("value", 0)), 0)
    contact_id = str(args.get("contact_id") or "").strip() or None

    async with session_scope() as db:
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == context.tenant_id,
                )
            )
            if contact is None:
                return {"ok": False, "error": "Contact not found in this workspace"}

        row = Lead(
            tenant_id=context.tenant_id,
            contact_id=contact_id,
            title=title,
            value=value,
            stage="new",
        )
        db.add(row)
        await db.flush()
        return {
            "ok": True,
            "lead_id": row.id,
            "title": row.title,
            "value": row.value,
        }


async def update_lead_stage(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    lead_id = string_arg(args, "lead_id", 80)
    stage = string_arg(args, "stage", 30).lower()
    allowed = {"new", "qualified", "proposal", "won", "lost"}

    if stage not in allowed:
        return {"ok": False, "error": "Invalid lead stage"}

    async with session_scope() as db:
        row = await db.scalar(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.tenant_id == context.tenant_id,
            )
        )
        if row is None:
            return {"ok": False, "error": "Lead not found"}

        row.stage = stage
        return {"ok": True, "lead_id": row.id, "stage": row.stage}


async def create_catalog_item(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    name = string_arg(args, "name", 250)
    item_type = str(args.get("item_type", "product")).lower()
    if item_type not in {"product", "service"}:
        return {"ok": False, "error": "item_type must be product or service"}

    price = max(float(args.get("price", 0)), 0)
    stock_qty = int(args.get("stock_qty", 0)) if item_type == "product" else 0
    reorder_level = (
        max(int(args.get("reorder_level", 0)), 0)
        if item_type == "product"
        else 0
    )

    async with session_scope() as db:
        row = CatalogItem(
            tenant_id=context.tenant_id,
            name=name,
            item_type=item_type,
            sku=str(args.get("sku") or "").strip()[:100] or None,
            price=price,
            stock_qty=stock_qty,
            reorder_level=reorder_level,
        )
        db.add(row)
        await db.flush()
        return {"ok": True, "item_id": row.id, "name": row.name}


async def adjust_inventory(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    item_id = string_arg(args, "item_id", 80)
    quantity_change = int(args.get("quantity_change", 0))

    if quantity_change == 0:
        return {"ok": False, "error": "quantity_change cannot be zero"}
    if abs(quantity_change) > 100_000:
        return {"ok": False, "error": "Inventory adjustment is too large"}

    async with session_scope() as db:
        row = await db.scalar(
            select(CatalogItem).where(
                CatalogItem.id == item_id,
                CatalogItem.tenant_id == context.tenant_id,
            )
        )
        if row is None:
            return {"ok": False, "error": "Item not found"}
        if row.item_type != "product":
            return {"ok": False, "error": "Services do not have inventory"}

        row.stock_qty += quantity_change
        return {
            "ok": True,
            "item_id": row.id,
            "stock_qty": row.stock_qty,
        }


async def create_order(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    total = max(float(args.get("total", 0)), 0)
    notes = str(args.get("notes") or "").strip()[:3000]
    contact_id = str(args.get("contact_id") or "").strip() or None

    async with session_scope() as db:
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == context.tenant_id,
                )
            )
            if contact is None:
                return {"ok": False, "error": "Contact not found"}

        row = BusinessOrder(
            tenant_id=context.tenant_id,
            contact_id=contact_id,
            status="draft",
            total=total,
            notes=notes,
        )
        db.add(row)
        await db.flush()
        return {
            "ok": True,
            "order_id": row.id,
            "status": "draft",
            "total": row.total,
        }


async def create_quote(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    title = string_arg(args, "title", 300)
    total = max(float(args.get("total", 0)), 0)
    contact_id = str(args.get("contact_id") or "").strip() or None
    notes = str(args.get("notes") or "").strip()[:5000]

    async with session_scope() as db:
        if contact_id:
            contact = await db.scalar(
                select(Contact).where(
                    Contact.id == contact_id,
                    Contact.tenant_id == context.tenant_id,
                )
            )
            if contact is None:
                return {"ok": False, "error": "Contact not found"}

        row = Quote(
            tenant_id=context.tenant_id,
            contact_id=contact_id,
            title=title,
            total=total,
            status="draft",
            notes=notes,
        )
        db.add(row)
        await db.flush()
        return {
            "ok": True,
            "quote_id": row.id,
            "title": row.title,
            "status": "draft",
        }


async def schedule_appointment(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    title = string_arg(args, "title", 300)
    starts_at_text = string_arg(args, "starts_at", 80)

    try:
        starts_at = datetime.fromisoformat(starts_at_text.replace("Z", "+00:00"))
        starts_at = starts_at.replace(tzinfo=None)
    except ValueError:
        return {
            "ok": False,
            "error": "starts_at must be an ISO-8601 date and time",
        }

    async with session_scope() as db:
        row = Appointment(
            tenant_id=context.tenant_id,
            title=title,
            starts_at=starts_at,
            assigned_to=str(args.get("assigned_to") or "").strip()[:200] or None,
            status="scheduled",
        )
        db.add(row)
        await db.flush()
        return {
            "ok": True,
            "appointment_id": row.id,
            "starts_at": row.starts_at.isoformat(),
        }


async def create_reminder(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    if context.channel != "discord":
        return {
            "ok": False,
            "error": (
                "Direct reminders currently require Discord. "
                "Create a task with a due time for web requests."
            ),
        }

    value = int(args.get("value", 0))
    unit = str(args.get("unit", "minutes")).lower()
    content = string_arg(args, "content", 1000)

    multipliers = {
        "seconds": 1,
        "minutes": 60,
        "hours": 3600,
        "days": 86400,
    }

    if value <= 0 or unit not in multipliers:
        return {"ok": False, "error": "Invalid reminder duration"}

    seconds = value * multipliers[unit]
    if seconds > 365 * 86400:
        return {"ok": False, "error": "Reminder cannot exceed one year"}

    channel_id = context.metadata.get("discord_channel_id")
    user_id = context.metadata.get("discord_user_id")
    guild_id = context.metadata.get("discord_guild_id")

    if channel_id is None or user_id is None:
        return {"ok": False, "error": "Discord delivery metadata is missing"}

    run_at = datetime.utcnow() + timedelta(seconds=seconds)

    async with session_scope() as db:
        row = ScheduledJob(
            tenant_id=context.tenant_id,
            guild_id=int(guild_id) if guild_id is not None else None,
            channel_id=int(channel_id),
            user_id=int(user_id),
            job_type="reminder",
            content=content,
            delivery=str(args.get("delivery", "channel")),
            run_at=run_at,
            status="pending",
        )
        db.add(row)
        await db.flush()
        return {
            "ok": True,
            "job_id": row.id,
            "run_at_utc": row.run_at.isoformat() + "Z",
        }


async def request_approval(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    action = string_arg(args, "action", 100)
    reason = string_arg(args, "reason", 2000)

    async with session_scope() as db:
        row = Approval(
            tenant_id=context.tenant_id,
            requester_id=None,
            action=action,
            payload_json='{"reason": ' + repr(reason).replace("'", '"') + "}",
            status="pending",
        )
        db.add(row)
        await db.flush()
        return {"ok": True, "approval_id": row.id, "status": "pending"}


async def business_summary(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    async with session_scope() as db:
        contacts = await db.scalar(
            select(func.count(Contact.id)).where(
                Contact.tenant_id == context.tenant_id
            )
        )
        leads = await db.scalar(
            select(func.count(Lead.id)).where(
                Lead.tenant_id == context.tenant_id,
                Lead.stage.not_in(["won", "lost"]),
            )
        )
        pipeline = await db.scalar(
            select(func.coalesce(func.sum(Lead.value), 0)).where(
                Lead.tenant_id == context.tenant_id,
                Lead.stage.not_in(["won", "lost"]),
            )
        )
        orders = await db.scalar(
            select(func.count(BusinessOrder.id)).where(
                BusinessOrder.tenant_id == context.tenant_id,
                BusinessOrder.status.not_in(["completed", "cancelled"]),
            )
        )
        low_stock = await db.scalar(
            select(func.count(CatalogItem.id)).where(
                CatalogItem.tenant_id == context.tenant_id,
                CatalogItem.item_type == "product",
                CatalogItem.stock_qty <= CatalogItem.reorder_level,
            )
        )

    return {
        "ok": True,
        "contacts": contacts or 0,
        "open_leads": leads or 0,
        "pipeline_value": float(pipeline or 0),
        "open_orders": orders or 0,
        "low_stock_items": low_stock or 0,
    }


def tool_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(
        tool_schema(
            "create_task",
            "Create an internal business task.",
            {"title": {"type": "string"}},
            ["title"],
        ),
        create_task,
    )
    registry.register(
        tool_schema("list_tasks", "List open business tasks.", {}),
        list_tasks,
    )
    registry.register(
        tool_schema(
            "complete_task",
            "Mark one task complete using its ID or unambiguous ID prefix.",
            {"task_id": {"type": "string"}},
            ["task_id"],
        ),
        complete_task,
    )
    registry.register(
        tool_schema(
            "remember_fact",
            "Store a business fact only when explicitly asked to remember it.",
            {"fact": {"type": "string"}},
            ["fact"],
        ),
        remember_fact,
    )
    registry.register(
        tool_schema(
            "search_memory",
            "Search business memory inside the current tenant.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        search_memory,
    )
    registry.register(
        tool_schema(
            "search_messages",
            "Search stored channel messages inside the current tenant.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        search_messages,
    )
    registry.register(
        tool_schema(
            "create_contact",
            "Create a CRM contact.",
            {
                "name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
            },
            ["name"],
        ),
        create_contact,
    )
    registry.register(
        tool_schema(
            "create_lead",
            "Create a new sales lead.",
            {
                "title": {"type": "string"},
                "value": {"type": "number"},
                "contact_id": {"type": "string"},
            },
            ["title"],
        ),
        create_lead,
    )
    registry.register(
        tool_schema(
            "update_lead_stage",
            "Move a lead to a valid sales stage.",
            {
                "lead_id": {"type": "string"},
                "stage": {
                    "type": "string",
                    "enum": ["new", "qualified", "proposal", "won", "lost"],
                },
            },
            ["lead_id", "stage"],
        ),
        update_lead_stage,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "create_catalog_item",
            "Create a product or service in the business catalog.",
            {
                "name": {"type": "string"},
                "item_type": {
                    "type": "string",
                    "enum": ["product", "service"],
                },
                "sku": {"type": "string"},
                "price": {"type": "number"},
                "stock_qty": {"type": "integer"},
                "reorder_level": {"type": "integer"},
            },
            ["name", "item_type"],
        ),
        create_catalog_item,
    )
    registry.register(
        tool_schema(
            "adjust_inventory",
            "Adjust stock for an existing product. Never use for a service.",
            {
                "item_id": {"type": "string"},
                "quantity_change": {"type": "integer"},
            },
            ["item_id", "quantity_change"],
        ),
        adjust_inventory,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "create_order",
            "Create a draft internal order. This does not charge a payment method.",
            {
                "contact_id": {"type": "string"},
                "total": {"type": "number"},
                "notes": {"type": "string"},
            },
        ),
        create_order,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "create_quote",
            "Create a draft quotation. This does not send it externally.",
            {
                "title": {"type": "string"},
                "contact_id": {"type": "string"},
                "total": {"type": "number"},
                "notes": {"type": "string"},
            },
            ["title"],
        ),
        create_quote,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "schedule_appointment",
            "Schedule an internal appointment in ISO-8601 time.",
            {
                "title": {"type": "string"},
                "starts_at": {"type": "string"},
                "assigned_to": {"type": "string"},
            },
            ["title", "starts_at"],
        ),
        schedule_appointment,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "create_reminder",
            "Create an actual Discord reminder after a duration.",
            {
                "value": {"type": "integer"},
                "unit": {
                    "type": "string",
                    "enum": ["seconds", "minutes", "hours", "days"],
                },
                "content": {"type": "string"},
                "delivery": {
                    "type": "string",
                    "enum": ["channel", "dm"],
                },
            },
            ["value", "unit", "content"],
        ),
        create_reminder,
        risk="medium",
    )
    registry.register(
        tool_schema(
            "request_approval",
            "Create an owner approval request for a consequential action.",
            {
                "action": {"type": "string"},
                "reason": {"type": "string"},
            },
            ["action", "reason"],
        ),
        request_approval,
        risk="high",
    )
    registry.register(
        tool_schema(
            "business_summary",
            "Read a compact business summary for the current tenant.",
            {},
        ),
        business_summary,
    )

    register_operations_tools(registry)

    from packages.business_brain.studio_tools import register_studio_tools
    register_studio_tools(registry)

    return registry
