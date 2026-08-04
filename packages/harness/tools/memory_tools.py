from typing import Any

from sqlalchemy import desc, select

from packages.database.db import session_scope
from packages.database.models import Memory, Message
from packages.harness.context import ToolContext
from packages.harness.registry import ToolRegistry


async def remember_fact(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    fact = str(args.get("fact", "")).strip()
    if not fact:
        return {"ok": False, "error": "fact is required"}

    async with session_scope() as db:
        row = Memory(
            tenant_id=context.tenant_id,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            kind="fact",
            content=fact,
        )
        db.add(row)
        await db.flush()
        memory_id = row.id

    return {"ok": True, "memory_id": memory_id}


async def search_messages(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    limit = min(max(int(args.get("limit", 8)), 1), 20)

    if not query:
        return {"ok": False, "error": "query is required"}

    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == context.tenant_id,
                    Message.content.ilike(f"%{query}%"),
                )
                .order_by(desc(Message.created_at))
                .limit(limit)
            )
        ).all()

    return {
        "ok": True,
        "matches": [
            {
                "author": row.author_name,
                "content": row.content[:500],
                "channel_id": row.channel_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


def register_memory_tools(registry: ToolRegistry) -> None:
    registry.register(
        {
            "type": "function",
            "function": {
                "name": "remember_fact",
                "description": (
                    "Persist an important business fact when the user explicitly asks "
                    "OPERLY to remember it."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["fact"],
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "The fact to store.",
                        }
                    },
                },
            },
        },
        remember_fact,
    )

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "search_messages",
                "description": (
                    "Search messages belonging only to the current business tenant."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                        },
                    },
                },
            },
        },
        search_messages,
    )
