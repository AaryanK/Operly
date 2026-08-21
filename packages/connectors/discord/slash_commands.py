from urllib.parse import quote

import discord
from discord import app_commands

from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.connectors.discord import bot_shared as legacy
from packages.database.channel_models import ChannelInstallation
from packages.database.db import session_scope
from packages.database.models import DiscordGuild
from packages.security.permissions import resolve_workspace_permissions


def build_tree(client: discord.Client) -> app_commands.CommandTree:
    tree = app_commands.CommandTree(client)

    @tree.command(name="link", description="Securely link this Discord identity to your Operly account")
    async def link(interaction: discord.Interaction):
        async with session_scope() as db:
            existing = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if existing:
                await interaction.response.send_message(
                    "This Discord identity is already linked to Operly.",
                    ephemeral=True,
                )
                return
            challenge = await IdentityLinkService.create_from_channel(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
                display_name=interaction.user.display_name,
            )
            await db.commit()
        url = f"{legacy.PUBLIC_BASE_URL}/settings?identity_link={quote(challenge.token or '', safe='')}"
        view = discord.ui.View(timeout=600)
        view.add_item(discord.ui.Button(label="Authenticate with Operly", url=url))
        await interaction.response.send_message(
            "Authenticate with Operly to claim this Discord identity. The link expires in 10 minutes.",
            view=view,
            ephemeral=True,
        )

    @tree.command(name="account", description="Show the Operly identity linked to this Discord account")
    async def account(interaction: discord.Interaction):
        async with session_scope() as db:
            identity = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if not identity:
                await interaction.response.send_message(
                    "This Discord identity is currently a guest. Use `/link` to authenticate with Operly.",
                    ephemeral=True,
                )
                return
            memberships = await IdentityService.memberships(db, user_id=identity.user_id)
        names = ", ".join(tenant.name for _, tenant in memberships) or "no workspaces"
        await interaction.response.send_message(
            f"Linked to an Operly user. Available workspaces: {names}.",
            ephemeral=True,
        )

    @tree.command(name="workspaces", description="List Operly workspaces available to your linked identity")
    async def workspaces(interaction: discord.Interaction):
        async with session_scope() as db:
            identity = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if not identity:
                await interaction.response.send_message("Use `/link` first.", ephemeral=True)
                return
            memberships = await IdentityService.memberships(db, user_id=identity.user_id)
        text = "\n".join(f"• {tenant.name} — {membership.role}" for membership, tenant in memberships) or "No Operly workspaces yet."
        await interaction.response.send_message(text, ephemeral=True)

    @tree.command(name="bind", description="Bind this Discord server to an Operly workspace")
    @app_commands.describe(workspace="Exact Operly workspace name or slug")
    async def bind(interaction: discord.Interaction, workspace: str):
        if interaction.guild is None:
            await interaction.response.send_message("Run `/bind` inside a Discord server.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or not member.guild_permissions.manage_guild:
            await interaction.response.send_message("Discord Manage Server permission is required.", ephemeral=True)
            return

        reference = " ".join(workspace.split()).strip().casefold()
        async with session_scope() as db:
            identity = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if not identity:
                await interaction.response.send_message("Use `/link` first.", ephemeral=True)
                return
            memberships = await IdentityService.memberships(db, user_id=identity.user_id)
            matches = [
                (membership, tenant)
                for membership, tenant in memberships
                if tenant.name.casefold() == reference
                or bool(tenant.slug and tenant.slug.casefold() == reference)
            ]
            if len(matches) != 1:
                names = ", ".join(tenant.name for _, tenant in memberships) or "none"
                await interaction.response.send_message(
                    f"Could not resolve one workspace. Your workspaces: {names}.",
                    ephemeral=True,
                )
                return
            membership, tenant = matches[0]
            permissions = await resolve_workspace_permissions(db, tenant_id=tenant.id, role=membership.role)
            if membership.role != "owner" and "workspace:channels:manage" not in permissions:
                await interaction.response.send_message("Your Operly role cannot bind channels to that workspace.", ephemeral=True)
                return
            installation = await IdentityService.installation(
                db,
                provider="discord",
                external_space_id=str(interaction.guild.id),
            )
            if installation and installation.tenant_id != tenant.id and not installation.provisional:
                await interaction.response.send_message(
                    "This server is already bound to another Operly workspace. Unbind it first.",
                    ephemeral=True,
                )
                return
            if installation is None:
                installation = ChannelInstallation(
                    tenant_id=tenant.id,
                    provider="discord",
                    external_space_id=str(interaction.guild.id),
                    display_name=interaction.guild.name[:200],
                    provisional=False,
                    status="connected",
                    metadata_json="{}",
                )
                db.add(installation)
            else:
                installation.tenant_id = tenant.id
                installation.display_name = interaction.guild.name[:200]
                installation.provisional = False
                installation.status = "connected"
            legacy_guild = await db.get(DiscordGuild, interaction.guild.id)
            if legacy_guild is None:
                db.add(DiscordGuild(guild_id=interaction.guild.id, tenant_id=tenant.id, guild_name=interaction.guild.name[:200], enabled=True))
            else:
                legacy_guild.tenant_id = tenant.id
                legacy_guild.guild_name = interaction.guild.name[:200]
                legacy_guild.enabled = True
            await db.commit()
        await interaction.response.send_message(
            f"Bound this Discord server to `{tenant.name}`.",
            ephemeral=True,
        )

    return tree
