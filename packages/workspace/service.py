from __future__ import annotations

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Approval, Memory, Message, Task


class WorkspaceService:
    """Canonical workspace primitives shared by HTTP and AI execution paths."""

    @staticmethod
    async def dashboard(db: AsyncSession, tenant_id: str) -> dict:
        message_count = await db.scalar(select(func.count(Message.id)).where(Message.tenant_id == tenant_id))
        open_tasks = await db.scalar(
            select(func.count(Task.id)).where(Task.tenant_id == tenant_id, Task.status == "open")
        )
        memory_count = await db.scalar(select(func.count(Memory.id)).where(Memory.tenant_id == tenant_id))
        pending_approvals = await db.scalar(
            select(func.count(Approval.id)).where(Approval.tenant_id == tenant_id, Approval.status == "pending")
        )
        recent = (
            await db.scalars(
                select(Message)
                .where(Message.tenant_id == tenant_id)
                .order_by(desc(Message.created_at))
                .limit(8)
            )
        ).all()
        return {
            "stats": {
                "messages": message_count or 0,
                "open_tasks": open_tasks or 0,
                "memories": memory_count or 0,
                "pending_approvals": pending_approvals or 0,
            },
            "recent_messages": [
                {
                    "id": row.id,
                    "author_name": row.author_name,
                    "content": row.content,
                    "is_bot": row.is_bot,
                    "channel_id": str(row.channel_id),
                    "created_at": row.created_at.isoformat(),
                }
                for row in recent
            ],
        }

    @staticmethod
    async def list_messages(
        db: AsyncSession,
        tenant_id: str,
        *,
        search: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        query = select(Message).where(Message.tenant_id == tenant_id)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.where(or_(Message.content.ilike(pattern), Message.author_name.ilike(pattern)))
        return list((await db.scalars(query.order_by(desc(Message.created_at)).limit(max(1, min(limit, 250))))).all())

    @staticmethod
    async def list_tasks(db: AsyncSession, tenant_id: str) -> list[Task]:
        return list(
            (
                await db.scalars(
                    select(Task).where(Task.tenant_id == tenant_id).order_by(desc(Task.created_at))
                )
            ).all()
        )

    @staticmethod
    async def create_task(db: AsyncSession, tenant_id: str, *, title: str, due_at=None) -> Task:
        title = title.strip()
        if not title:
            raise ValueError("Task title is required")
        row = Task(tenant_id=tenant_id, title=title[:500], due_at=due_at, status="open")
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def complete_task(db: AsyncSession, tenant_id: str, task_id: str) -> Task:
        row = await db.scalar(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id))
        if row is None:
            raise LookupError("Task not found")
        row.status = "completed"
        return row

    @staticmethod
    async def complete_task_prefix(db: AsyncSession, tenant_id: str, task_id: str) -> Task:
        rows = (
            await db.scalars(
                select(Task).where(Task.tenant_id == tenant_id, Task.id.like(f"{task_id}%"))
            )
        ).all()
        if len(rows) != 1:
            raise LookupError("Provide one unambiguous task ID or ID prefix")
        rows[0].status = "completed"
        return rows[0]

    @staticmethod
    async def list_memories(db: AsyncSession, tenant_id: str) -> list[Memory]:
        return list(
            (
                await db.scalars(
                    select(Memory).where(Memory.tenant_id == tenant_id).order_by(desc(Memory.created_at))
                )
            ).all()
        )

    @staticmethod
    async def create_memory(db: AsyncSession, tenant_id: str, *, kind: str = "fact", content: str) -> Memory:
        content = content.strip()
        if not content:
            raise ValueError("Memory content is required")
        row = Memory(
            tenant_id=tenant_id,
            kind=(kind.strip() or "fact")[:80],
            content=content[:10000],
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def search_memory(db: AsyncSession, tenant_id: str, query: str, *, limit: int = 20) -> list[Memory]:
        query = query.strip()
        if not query:
            raise ValueError("Memory query is required")
        return list(
            (
                await db.scalars(
                    select(Memory)
                    .where(Memory.tenant_id == tenant_id, Memory.content.ilike(f"%{query}%"))
                    .order_by(desc(Memory.created_at))
                    .limit(max(1, min(limit, 20)))
                )
            ).all()
        )
