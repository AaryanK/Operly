from __future__ import annotations

import json
import re

import discord
from sqlalchemy import select

from packages.agent_runtime.telemetry import runtime_trace
from packages.database.channel_models import ChannelInstallation, ExternalIdentity
from packages.database.db import session_scope
from packages.database.models import AuthIdentity, Tenant, TenantMember
from packages.security.permissions import resolve_workspace_permissions
from packages.workspace_modules.integrations.discord.client import bot
from packages.workspace_modules.integrations.discord.runtime_status import discord_ai_runtime_status

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


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
        "Workspace permissions remain enforced by Operly's business Kernel and MCP gateway."
    )


async def _runtime_test(message: discord.Message, prompt: str) -> None:
    # This was previously an independent LLM-backed semantic scorer.
    # Do not invoke a local model or issue an unscoped Discord impersonation.
    del prompt
    await message.reply(
        "Local Operly agent diagnostics are retired. DragonZpyder's "
        "signed Discord-channel delegation is not yet available.",
        mention_author=False,
    )


async def _handle_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    if not raw.lower().startswith("!operly"):
        return False
    parts = raw.split()
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "help":
        await message.reply(
            "Operly commands: `!operly status`, `!operly test REQUEST`, `!operly link`, "
            "`!operly bind WORKSPACE`, `!operly help`. Local AI and dry-run "
            "execution have been retired. Discord agent chat requires a signed "
            "DragonZpyder channel bridge; account linking and workspace binding remain available."
        )
        return True
    if command == "status":
        row = await _installation(message)
        ready, detail = discord_ai_runtime_status()
        binding = (
            f"Bound workspace: `{row.tenant_id}`."
            if row
            else "This server is not bound to a workspace."
        )
        await message.reply(f"{binding} {detail} Ready={str(ready).lower()}.")
        return True
    if command == "test":
        prompt = raw.split(None, 2)[2] if len(parts) >= 3 else ""
        await _runtime_test(message, prompt)
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
    ready, detail = discord_ai_runtime_status()
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
            "Discord AI attachment handling requires the signed DragonZpyder channel bridge.",
            mention_author=False,
        )
        return

    # Discord sender/guild authority cannot be substituted for a first-party
    # user session. Preserve !operly link and !operly bind, but do not execute
    # independent intelligence or fabricate a DragonZpyder MCP grant.
    await _reply_chunks(
        message,
        "Operly AI is powered by DragonZpyder. Discord AI requires an "
        "authenticated channel bridge that is not enabled yet. "
        "Use your signed-in Operly workspace for agent requests.",
    )
    return
