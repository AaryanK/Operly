from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.discord.client import bot, bot_status
from packages.workspace_modules.integrations.discord.permissions import authorized_channel, workspace_discord_installations

PROVIDER_ID = "operly.discord"


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": additional}


def _array(item: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item}


def _capability(capability_id: str, name: str, description: str, *, permission: str, input_schema: dict[str, Any] | None = None, output_schema: dict[str, Any] | None = None, risk: CapabilityRisk = CapabilityRisk.READ_ONLY, approval: bool = False, reversible: bool = False, emits: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> CapabilitySpec:
    return CapabilitySpec(id=capability_id, version="1.0.0", display_name=name, description=description, provider_id=PROVIDER_ID, scopes=frozenset({"workspace"}), input_schema=input_schema or _object({}), output_schema=output_schema or _object({}, additional=True), permissions=(permission,), risk=risk, approval_required=approval, reversible=reversible, emits=emits, tags=frozenset(("discord", "connector", "external", *tags)), resource_scope="workspace")


def workspace_discord_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        _capability("discord.bot.status", "Read Discord bot status", "Inspect whether the deterministic Operly Discord bot is configured and connected. AI chat is not part of this bot runtime.", permission="integrations:read", output_schema=_object({}, additional=True), tags=("bot", "status", "read")),
        _capability("discord.installations.list", "List Discord installations", "List Discord servers deterministically bound to this Operly workspace.", permission="discord:read", output_schema=_object({"installations": _array(_object({}, additional=True))}, required=["installations"]), tags=("installation", "read")),
        _capability("discord.channels.list", "List Discord channels", "List channels the Operly bot can view inside Discord servers bound to this workspace.", permission="discord:read", output_schema=_object({"channels": _array(_object({}, additional=True))}, required=["channels"]), tags=("channel", "read")),
        _capability("discord.messages.list", "Read Discord messages", "Read a bounded set of recent messages from an installed Discord channel after checking live bot permissions.", permission="discord:read", input_schema=_object({"channel_id": {"type": "string", "minLength": 1, "maxLength": 30}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, required=["channel_id"]), output_schema=_object({"messages": _array(_object({}, additional=True))}, required=["messages"]), tags=("message", "history", "read")),
        _capability("discord.message.send", "Send Discord message", "Send a message to an installed workspace Discord channel after exact-invocation approval and live Discord permission checks.", permission="discord:write", input_schema=_object({"channel_id": {"type": "string", "minLength": 1, "maxLength": 30}, "content": {"type": "string", "minLength": 1, "maxLength": 1900}}, required=["channel_id", "content"]), output_schema=_object({}, additional=True), risk=CapabilityRisk.HIGH, approval=True, reversible=False, emits=("discord.message.sent",), tags=("message", "send", "write")),
        _capability("discord.reaction.add", "Add Discord reaction", "Add a reaction to a message in an installed workspace Discord channel after approval.", permission="discord:write", input_schema=_object({"channel_id": {"type": "string", "minLength": 1, "maxLength": 30}, "message_id": {"type": "string", "minLength": 1, "maxLength": 30}, "emoji": {"type": "string", "minLength": 1, "maxLength": 100}}, required=["channel_id", "message_id", "emoji"]), output_schema=_object({}, additional=True), risk=CapabilityRisk.MEDIUM, approval=True, reversible=True, emits=("discord.reaction.added",), tags=("reaction", "write")),
        _capability("discord.thread.create", "Create Discord thread", "Create a public thread from a message in an installed workspace Discord channel after approval.", permission="discord:write", input_schema=_object({"channel_id": {"type": "string", "minLength": 1, "maxLength": 30}, "message_id": {"type": "string", "minLength": 1, "maxLength": 30}, "name": {"type": "string", "minLength": 1, "maxLength": 100}}, required=["channel_id", "message_id", "name"]), output_schema=_object({}, additional=True), risk=CapabilityRisk.MEDIUM, approval=True, reversible=False, emits=("discord.thread.created",), tags=("thread", "write")),
    )


def _invite_url() -> str | None:
    application_id = os.getenv("DISCORD_BOT_APPLICATION_ID", "").strip() or os.getenv("DISCORD_AUTH_CLIENT_ID", "").strip()
    if not application_id:
        return None
    return "https://discord.com/oauth2/authorize?" + urlencode({"client_id": application_id, "scope": "bot applications.commands", "permissions": "274877991936"})


class WorkspaceDiscordProvider:
    async def execute(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec, arguments: dict[str, Any], minimum_context: dict[str, Any]) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Discord workspace capability requires workspace authority")
        workspace_id, capability_id = context.workspace_id, capability.id
        if capability_id == "discord.bot.status":
            status = bot_status()
            status.update({"configured": bool(os.getenv("DISCORD_BOT_TOKEN", "").strip()), "enabled": os.getenv("OPERLY_DISCORD_BOT_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}, "invite_url": _invite_url(), "ai_enabled": False})
            return CapabilityExecutionResult(value=status, resource_type="discord_bot")
        installations = await workspace_discord_installations(db, workspace_id)
        if capability_id == "discord.installations.list":
            return CapabilityExecutionResult(value={"installations": [{"id": row.id, "guild_id": row.external_space_id, "display_name": row.display_name, "provisional": bool(row.provisional), "status": row.status} for row in installations]}, resource_type="discord_installation")
        if not bot.is_ready():
            raise RuntimeError("Discord bot is not connected")
        if capability_id == "discord.channels.list":
            channels: list[dict[str, Any]] = []
            for installation in installations:
                guild = bot.get_guild(int(installation.external_space_id))
                if guild is None:
                    continue
                member = guild.me
                if member is None:
                    continue
                for channel in guild.channels:
                    try:
                        permissions = channel.permissions_for(member)
                    except Exception:
                        continue
                    if not bool(getattr(permissions, "view_channel", False)):
                        continue
                    channels.append({"guild_id": str(guild.id), "guild_name": guild.name, "channel_id": str(channel.id), "name": getattr(channel, "name", str(channel.id)), "type": str(getattr(channel, "type", "unknown")), "can_read_history": bool(getattr(permissions, "read_message_history", False)), "can_send": bool(getattr(permissions, "send_messages", False))})
                    if len(channels) >= 500:
                        break
            return CapabilityExecutionResult(value={"channels": channels})
        channel_id = str(arguments.get("channel_id") or "").strip()
        if capability_id == "discord.messages.list":
            channel, _, _ = await authorized_channel(db, workspace_id, channel_id, require="read")
            limit = max(1, min(int(arguments.get("limit") or 20), 50))
            messages: list[dict[str, Any]] = []
            async for message in channel.history(limit=limit):
                messages.append({"message_id": str(message.id), "author_id": str(message.author.id), "author": message.author.display_name, "content": (message.content or "")[:2000], "created_at": message.created_at.isoformat(), "is_bot": bool(message.author.bot)})
            messages.reverse()
            return CapabilityExecutionResult(value={"messages": messages}, resource_type="discord_channel", resource_id=str(channel.id))
        if capability_id == "discord.message.send":
            channel, _, _ = await authorized_channel(db, workspace_id, channel_id, require="send")
            content = str(arguments.get("content") or "").strip()
            if not content:
                raise ValueError("content is required")
            sent = await channel.send(content[:1900], allowed_mentions=discord.AllowedMentions.none())
            return CapabilityExecutionResult(value={"message_id": str(sent.id), "channel_id": str(channel.id), "sent": True}, resource_type="discord_message", resource_id=str(sent.id), event_payload={"channel_id": str(channel.id), "message_id": str(sent.id)})
        message_id = str(arguments.get("message_id") or "").strip()
        if not message_id:
            raise ValueError("message_id is required")
        if capability_id == "discord.reaction.add":
            channel, _, _ = await authorized_channel(db, workspace_id, channel_id, require="reaction")
            message = await channel.fetch_message(int(message_id))
            emoji = str(arguments.get("emoji") or "").strip()
            if not emoji:
                raise ValueError("emoji is required")
            await message.add_reaction(emoji[:100])
            return CapabilityExecutionResult(value={"message_id": str(message.id), "channel_id": str(channel.id), "emoji": emoji[:100], "added": True}, resource_type="discord_message", resource_id=str(message.id), event_payload={"message_id": str(message.id), "emoji": emoji[:100]})
        if capability_id == "discord.thread.create":
            channel, _, _ = await authorized_channel(db, workspace_id, channel_id, require="thread")
            message = await channel.fetch_message(int(message_id))
            name = str(arguments.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            thread = await message.create_thread(name=name[:100])
            return CapabilityExecutionResult(value={"thread_id": str(thread.id), "channel_id": str(channel.id), "name": thread.name}, resource_type="discord_thread", resource_id=str(thread.id), event_payload={"thread_id": str(thread.id), "channel_id": str(channel.id)})
        raise LookupError(f"Discord capability is not implemented: {capability_id}")
