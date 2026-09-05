from __future__ import annotations

import json
import os
import re

import discord
from sqlalchemy import select

from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute, OpenAICompatibleAgentModel
from packages.agent_runtime.interactive import Runtime1Agent
from packages.agent_runtime.runtime import AgentRuntimeDisabled, AgentRuntimeSettings
from packages.agent_runtime.telemetry import fingerprint, runtime_trace
from packages.database.channel_models import ChannelInstallation, ExternalIdentity
from packages.database.db import session_scope
from packages.database.models import AuthIdentity, Tenant, TenantMember
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContextError,
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.permissions import resolve_workspace_permissions
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.integrations.discord.client import bot
from packages.workspace_modules.tools.runtime import build_workspace_runtime

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def _runtime_agent() -> Runtime1Agent:
    return Runtime1Agent(model=OpenAICompatibleAgentModel())


def _runtime_status() -> tuple[bool, str]:
    if not AgentRuntimeSettings.from_environment().enabled:
        return False, "Agent Runtime 1.0 is disabled by deployment policy."
    try:
        route = InferenceRoute.from_environment()
    except AgentInferenceError as error:
        return False, str(error)
    return True, f"Agent Runtime 1.0 is ready with {route.provider}/{route.model_id}."


async def _linked_operly_user_id(discord_user_id: int) -> str | None:
    subject = str(discord_user_id)
    async with session_scope() as db:
        external = await db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "discord",
                ExternalIdentity.provider_subject == subject,
            )
        )
        if external:
            return external.user_id
        auth = await db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.provider == "discord",
                AuthIdentity.provider_subject == subject,
            )
        )
        return auth.user_id if auth else None


async def _installation(message: discord.Message) -> ChannelInstallation | None:
    if message.guild is None:
        return None
    async with session_scope() as db:
        return await db.scalar(
            select(ChannelInstallation).where(
                ChannelInstallation.provider == "discord",
                ChannelInstallation.external_space_id == str(message.guild.id),
                ChannelInstallation.status == "connected",
            )
        )


async def _bind_workspace(message: discord.Message, reference: str) -> None:
    if message.guild is None:
        await message.reply("Run `!operly bind WORKSPACE` inside the Discord server.")
        return
    if not bool(message.author.guild_permissions.manage_guild):
        await message.reply("Discord Manage Server permission is required to bind this server.")
        return
    user_id = await _linked_operly_user_id(message.author.id)
    if not user_id:
        await message.reply(
            "Your Discord identity is not linked to an Operly account yet. "
            f"Open {PUBLIC_BASE_URL}/login and connect/sign in with Discord first."
        )
        return
    normalized = " ".join(str(reference or "").split()).strip().casefold()
    if not normalized:
        await message.reply("Use `!operly bind WORKSPACE`, for example `!operly bind My Business`.")
        return
    async with session_scope() as db:
        memberships = (
            await db.execute(
                select(TenantMember, Tenant)
                .join(Tenant, Tenant.id == TenantMember.tenant_id)
                .where(TenantMember.user_id == user_id)
            )
        ).all()
        matches = [
            (member, tenant)
            for member, tenant in memberships
            if tenant.name.casefold() == normalized
            or bool(tenant.slug and tenant.slug.casefold() == normalized)
        ]
        if len(matches) != 1:
            names = ", ".join(tenant.name for _, tenant in memberships) or "none"
            await message.reply(
                f"Could not resolve exactly one workspace. Your Operly workspaces: {names}."
            )
            return
        member, tenant = matches[0]
        permissions = await resolve_workspace_permissions(
            db, tenant_id=tenant.id, role=member.role
        )
        if "workspace:channels:manage" not in permissions and member.role != "owner":
            await message.reply(
                "Your Operly role does not have permission to bind external channels."
            )
            return
        row = await db.scalar(
            select(ChannelInstallation).where(
                ChannelInstallation.provider == "discord",
                ChannelInstallation.external_space_id == str(message.guild.id),
            )
        )
        metadata = json.dumps(
            {
                "bound_by_user_id": user_id,
                "discord_guild_id": str(message.guild.id),
                "source": "agent_runtime_1_discord",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        if row is None:
            row = ChannelInstallation(
                tenant_id=tenant.id,
                provider="discord",
                external_space_id=str(message.guild.id),
                display_name=message.guild.name,
                provisional=False,
                status="connected",
                metadata_json=metadata,
            )
            db.add(row)
        else:
            row.tenant_id = tenant.id
            row.display_name = message.guild.name
            row.provisional = False
            row.status = "connected"
            row.metadata_json = metadata
        await db.commit()
    runtime_trace(
        "discord.workspace_bound",
        workspace_id=tenant.id,
        guild_id=str(message.guild.id),
        user_id=user_id,
    )
    await message.reply(
        f"This Discord server is now bound to `{tenant.name}`. "
        "Operly Runtime 1.0 can discover this workspace's authorized built-in and installed plugin capabilities."
    )


async def _handle_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    if not raw.lower().startswith("!operly"):
        return False
    parts = raw.split()
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "help":
        await message.reply(
            "Operly commands: `!operly status`, `!operly link`, `!operly bind WORKSPACE`, `!operly help`. "
            "Mention Operly in a bound server, or DM it, to use Agent Runtime 1.0."
        )
        return True
    if command == "status":
        row = await _installation(message)
        ready, detail = _runtime_status()
        binding = (
            f"Bound workspace: `{row.tenant_id}`."
            if row
            else "This server is not bound to a workspace."
        )
        await message.reply(f"{binding} {detail} Ready={str(ready).lower()}.")
        return True
    if command == "link":
        linked = await _linked_operly_user_id(message.author.id)
        if linked:
            await message.reply("This Discord identity is already linked to an Operly account.")
        else:
            await message.reply(
                f"Open {PUBLIC_BASE_URL}/login and connect/sign in with Discord, then return here."
            )
        return True
    if command == "bind":
        reference = raw.split(None, 2)[2] if len(parts) >= 3 else ""
        await _bind_workspace(message, reference)
        return True
    await message.reply("Unknown command. Use `!operly help`.")
    return True


def _addressed(message: discord.Message) -> bool:
    if message.guild is None:
        return True
    return bool(bot.user and bot.user in message.mentions)


def _prompt(message: discord.Message) -> str:
    text = str(message.content or "")
    if bot.user:
        text = text.replace(f"<@{bot.user.id}>", " ").replace(f"<@!{bot.user.id}>", " ")
    clean = " ".join(text.split()).strip()
    if not clean and message.attachments:
        return "Work with the attached file(s)."
    return clean


def _table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _discordify(text: str) -> str:
    """Convert portable model Markdown into Discord-friendly Markdown.

    Discord does not render GitHub-style tables. Preserve ordinary headings, bullets,
    code fences and emphasis while rewriting table blocks into compact labeled bullets.
    """

    source = str(text or "Done.").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    output: list[str] = []
    index = 0
    converted_tables = 0
    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index] and _is_table_separator(lines[index + 1]):
            headers = _table_cells(lines[index])
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                row = _table_cells(lines[index])
                if len(row) >= 2:
                    rows.append(row)
                    index += 1
                    continue
                break
            if rows:
                converted_tables += 1
                for row in rows:
                    first = row[0] if row else "Item"
                    details = []
                    for cell_index, cell in enumerate(row[1:], 1):
                        if not cell:
                            continue
                        label = headers[cell_index] if cell_index < len(headers) and headers[cell_index] else f"Field {cell_index + 1}"
                        details.append(f"**{label}:** {cell}")
                    output.append(f"- **{first}**" + (f" — {' · '.join(details)}" if details else ""))
                continue
        output.append(lines[index])
        index += 1

    rendered = "\n".join(output).strip() or "Done."
    if converted_tables:
        runtime_trace(
            "discord.output_formatted",
            table_blocks=converted_tables,
            original_chars=len(source),
            rendered_chars=len(rendered),
        )
    return rendered


async def _reply_chunks(message: discord.Message, text: str) -> None:
    remaining = _discordify(text)
    first = True
    while remaining:
        if len(remaining) <= 1900:
            chunk, remaining = remaining, ""
        else:
            split = remaining.rfind("\n", 0, 1900)
            if split < 800:
                split = remaining.rfind(" ", 0, 1900)
            if split < 800:
                split = 1900
            chunk, remaining = remaining[:split], remaining[split:].lstrip()
        if first:
            await message.reply(
                chunk,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            first = False
        else:
            await message.channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )


@bot.event
async def on_ready() -> None:
    ready, detail = _runtime_status()
    runtime_trace(
        "discord.connected",
        bot_user=str(bot.user),
        runtime_ready=ready,
        runtime_detail=detail,
    )
    print(f"OPERLY Discord bot connected as {bot.user}", flush=True)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if await _handle_command(message):
        return
    if not _addressed(message):
        return

    prompt = _prompt(message)
    if not prompt:
        await message.reply("What would you like Operly to do?", mention_author=False)
        return
    if message.attachments:
        await message.reply(
            "Text chat is enabled through Runtime 1.0. Discord attachment ingestion is not enabled in this test slice yet.",
            mention_author=False,
        )
        return

    user_id = await _linked_operly_user_id(message.author.id)
    run_id = f"discord-{message.id}"
    runtime_trace(
        "discord.ingress",
        run_id=run_id,
        guild_id=str(message.guild.id) if message.guild else None,
        channel_id=str(message.channel.id),
        discord_user_id=str(message.author.id),
        linked_user=bool(user_id),
        message_chars=len(prompt),
        message_sha256_16=fingerprint(prompt),
    )

    try:
        async with message.channel.typing():
            async with session_scope() as db:
                if message.guild is None:
                    if not user_id:
                        await _reply_chunks(
                            message,
                            "Link your Discord identity to Operly first using `!operly link`.",
                        )
                        return
                    context = await resolve_personal_execution_context(
                        db,
                        user_id=user_id,
                        channel="discord",
                        surface=SurfaceKind.DISCORD_DM,
                        conversation_id=f"discord-dm:{message.author.id}",
                        metadata={"is_direct": True},
                    )
                    kernel = build_personal_runtime()
                else:
                    installation = await db.scalar(
                        select(ChannelInstallation).where(
                            ChannelInstallation.provider == "discord",
                            ChannelInstallation.external_space_id == str(message.guild.id),
                            ChannelInstallation.status == "connected",
                        )
                    )
                    if installation is None:
                        await _reply_chunks(
                            message,
                            "This server is not bound to an Operly workspace. "
                            "A server manager can use `!operly bind WORKSPACE`.",
                        )
                        return
                    metadata = {
                        "external_space_id": str(message.guild.id),
                        "discord_guild_id": str(message.guild.id),
                        "_guest_principal_id": f"discord:{message.author.id}",
                        "is_direct": False,
                    }
                    context = await resolve_execution_context(
                        db,
                        workspace_id=installation.tenant_id,
                        user_id=user_id,
                        channel="discord",
                        surface=SurfaceKind.DISCORD_GUILD,
                        conversation_id=f"discord:{message.guild.id}:{message.channel.id}",
                        metadata=metadata,
                        require_membership=False,
                    )
                    facade = build_workspace_runtime()
                    kernel = await facade.request_runtime(db, context=context)

                result = await _runtime_agent().run(
                    db,
                    context=context,
                    message=prompt,
                    kernel=kernel,
                    context_items=(),
                    run_id=run_id,
                )
                await db.commit()
        await _reply_chunks(message, result.message)
    except AgentRuntimeDisabled:
        runtime_trace("discord.request_failed", run_id=run_id, error_code="agent_runtime_disabled")
        await _reply_chunks(
            message,
            "Operly Agent Runtime 1.0 is disabled for this deployment.",
        )
    except (AgentInferenceError, ExecutionContextError) as error:
        runtime_trace(
            "discord.request_failed",
            run_id=run_id,
            error_code=getattr(error, "code", type(error).__name__),
            error_type=type(error).__name__,
        )
        await _reply_chunks(
            message,
            "Operly could not safely complete that request. The failure is recorded in the Railway runtime trace.",
        )
    except Exception as error:
        runtime_trace(
            "discord.request_failed",
            run_id=run_id,
            error_code="unexpected",
            error_type=type(error).__name__,
        )
        await _reply_chunks(
            message,
            "Operly hit an unexpected runtime failure. The failure type is recorded in the Railway runtime trace.",
        )