from __future__ import annotations

from typing import Any

from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.channel_models import ExternalIdentity


class DiscordProvider(BaseProvider):
    """High-level governed Discord capabilities backed by discord.py.

    The model never receives a raw Discord REST client or bot token. Each operation
    is a bounded semantic capability. Additional Discord API surfaces can be added
    here without changing the agent or channel adapters.
    """

    name = "discord_connector"
    capabilities = (
        CapabilityDefinition(
            "discord.context",
            "discord_context",
            "Inspect the current Discord execution context (DM/server/channel/message IDs and whether this is a DM). Does not reveal credentials.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            permissions=("discord:read",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
        ),
        CapabilityDefinition(
            "discord.read_recent_messages",
            "discord_read_recent_messages",
            "Read a bounded set of recent messages from the current Discord conversation when Discord permissions allow it.",
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="read_only",
            permissions=("discord:read",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
        ),
        CapabilityDefinition(
            "discord.send_dm",
            "discord_send_dm",
            "Send a Discord DM to the current authenticated Operly human's linked Discord identity. Use when the user asks things like 'DM me the summary'.",
            {
                "type": "object",
                "properties": {"content": {"type": "string", "minLength": 1, "maxLength": 1900}},
                "required": ["content"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("discord:write",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
        ),
        CapabilityDefinition(
            "discord.send_message",
            "discord_send_message",
            "Send a message to the current Discord conversation. Only available when the current origin is Discord and the channel belongs to this execution context.",
            {
                "type": "object",
                "properties": {"content": {"type": "string", "minLength": 1, "maxLength": 1900}},
                "required": ["content"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("discord:write",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
        ),
        CapabilityDefinition(
            "discord.add_reaction",
            "discord_add_reaction",
            "Add a reaction to the Discord message that triggered the current Operly request.",
            {
                "type": "object",
                "properties": {"emoji": {"type": "string", "minLength": 1, "maxLength": 100}},
                "required": ["emoji"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("discord:write",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
            reversible=True,
        ),
        CapabilityDefinition(
            "discord.create_thread",
            "discord_create_thread",
            "Create a thread from the Discord message that triggered the current request. Only valid in a Discord server/channel where Discord permits thread creation.",
            {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 100}},
                "required": ["name"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("discord:write",),
            approval_policy=ApprovalPolicy.AUTO,
            provider="discord",
            integration_provider="discord",
            category="messaging",
        ),
    )

    @staticmethod
    def _runtime(context) -> tuple[dict[str, Any], dict[str, Any]]:
        invocation = context.invocation or {}
        metadata = invocation.get("metadata") if isinstance(invocation.get("metadata"), dict) else {}
        return invocation, metadata

    @staticmethod
    async def _bot():
        from packages.connectors.discord.bot_shared import bot
        return bot

    @staticmethod
    async def _linked_discord_subject(context) -> str | None:
        if not context.actor_id:
            return None
        identity = await context.db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.user_id == context.actor_id,
                ExternalIdentity.provider == "discord",
            )
        )
        return str(identity.provider_subject) if identity else None

    async def execute(self, context, capability_name, arguments):
        invocation, metadata = self._runtime(context)
        bot = await self._bot()
        current_origin = invocation.get("channel") == "discord"
        channel_id = metadata.get("discord_channel_id")
        message_id = metadata.get("external_message_id")

        if capability_name == "discord.context":
            return CapabilityResult(
                True,
                False,
                {
                    "origin_is_discord": current_origin,
                    "is_direct": bool(metadata.get("is_direct")),
                    "guild_id": metadata.get("discord_guild_id"),
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "linked_identity": bool(await self._linked_discord_subject(context)),
                },
            )

        if capability_name == "discord.send_dm":
            subject = await self._linked_discord_subject(context)
            if not subject:
                return CapabilityResult(False, False, {"reason": "discord_identity_not_linked"})
            try:
                user = bot.get_user(int(subject)) or await bot.fetch_user(int(subject))
                sent = await user.send(str(arguments["content"])[:1900])
            except Exception as error:
                return CapabilityResult(False, False, {"reason": "discord_dm_failed", "type": type(error).__name__})
            return CapabilityResult(True, True, {"message_id": str(sent.id), "delivery": "dm"}, str(sent.id))

        if not current_origin or channel_id is None:
            return CapabilityResult(False, False, {"reason": "current_discord_conversation_required"})
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        except Exception as error:
            return CapabilityResult(False, False, {"reason": "discord_channel_unavailable", "type": type(error).__name__})

        if capability_name == "discord.send_message":
            try:
                sent = await channel.send(str(arguments["content"])[:1900])
            except Exception as error:
                return CapabilityResult(False, False, {"reason": "discord_send_failed", "type": type(error).__name__})
            return CapabilityResult(True, True, {"message_id": str(sent.id), "delivery": "channel"}, str(sent.id))

        if capability_name == "discord.read_recent_messages":
            try:
                limit = max(1, min(int(arguments.get("limit", 20)), 50))
                rows = []
                async for item in channel.history(limit=limit):
                    rows.append(
                        {
                            "message_id": str(item.id),
                            "author": item.author.display_name,
                            "content": (item.content or "")[:2000],
                            "created_at": item.created_at.isoformat(),
                            "is_bot": bool(item.author.bot),
                        }
                    )
                rows.reverse()
            except Exception as error:
                return CapabilityResult(False, False, {"reason": "discord_history_failed", "type": type(error).__name__})
            return CapabilityResult(True, False, {"messages": rows, "count": len(rows)})

        if message_id is None:
            return CapabilityResult(False, False, {"reason": "trigger_message_required"})
        try:
            message = await channel.fetch_message(int(message_id))
        except Exception as error:
            return CapabilityResult(False, False, {"reason": "discord_message_unavailable", "type": type(error).__name__})

        if capability_name == "discord.add_reaction":
            try:
                emoji = str(arguments["emoji"])[:100]
                await message.add_reaction(emoji)
            except Exception as error:
                return CapabilityResult(False, False, {"reason": "discord_reaction_failed", "type": type(error).__name__})
            return CapabilityResult(True, True, {"message_id": str(message.id), "emoji": emoji}, str(message.id))

        if capability_name == "discord.create_thread":
            if metadata.get("is_direct"):
                return CapabilityResult(False, False, {"reason": "threads_require_server_channel"})
            try:
                thread = await message.create_thread(name=str(arguments["name"])[:100])
            except Exception as error:
                return CapabilityResult(False, False, {"reason": "discord_thread_failed", "type": type(error).__name__})
            return CapabilityResult(True, True, {"thread_id": str(thread.id), "name": thread.name}, str(thread.id))

        return CapabilityResult(False, False, {"reason": "unsupported_discord_capability"})

    async def verify(self, context, capability_name, arguments, result):
        # Discord API success responses are external verification for these bounded
        # operations. Read-only observations never claim mutation.
        return CapabilityResult(result.success, result.changed, result.evidence, result.external_reference)
