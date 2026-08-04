from typing import Any

import discord

from packages.harness.context import ToolContext
from packages.harness.permissions import can_create_threads
from packages.harness.registry import ToolRegistry


async def send_dm(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    content = str(args.get("content", "")).strip()
    if not content:
        return {"ok": False, "error": "content is required"}

    try:
        await context.message.author.send(content[:1900])
        return {"ok": True, "delivered_to_user_id": context.user_id}
    except discord.Forbidden:
        return {"ok": False, "error": "User DMs are disabled"}
    except discord.HTTPException as error:
        return {"ok": False, "error": str(error)}


async def create_thread(
    context: ToolContext,
    args: dict[str, Any],
) -> dict[str, Any]:
    if not can_create_threads(context):
        return {"ok": False, "error": "User lacks permission to create threads"}

    name = str(args.get("name", "")).strip()[:100]
    if not name:
        return {"ok": False, "error": "name is required"}

    try:
        thread = await context.message.create_thread(name=name)
        return {"ok": True, "thread_id": thread.id, "thread_name": thread.name}
    except discord.HTTPException as error:
        return {"ok": False, "error": str(error)}


def register_discord_tools(registry: ToolRegistry) -> None:
    registry.register(
        {
            "type": "function",
            "function": {
                "name": "send_dm",
                "description": (
                    "Send a direct message to the user who made the current request. "
                    "Use only when they explicitly ask for a DM."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["content"],
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Exact DM content to send.",
                        }
                    },
                },
            },
        },
        send_dm,
    )

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "create_thread",
                "description": (
                    "Create a Discord thread from the current message when the user "
                    "explicitly requests a thread."
                ),
                "parameters": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Thread name.",
                        }
                    },
                },
            },
        },
        create_thread,
    )
