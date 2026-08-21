from urllib.parse import quote

import discord
from discord import app_commands

from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.channels.space_bindings import ExternalSpaceBindingService, SpaceBindingError
from packages.connectors.discord import bot_shared as legacy
from packages.database.db import session_scope
from packages.database.models import DiscordGuild


def _workspace_match(memberships, reference: str):
    key = " ".join(str(reference or "").split()).strip().casefold()
    return [
        (membership, tenant)
        for membership, tenant in memberships
        if tenant.name.casefold() == key
        or bool(tenant.slug and tenant.slug.casefold() == key)
    ]


def build_tree(client: discord.Client) -> app_commands.CommandTree:
    tree = app_commands.CommandTree(client)

    @tree.command(name="link", description="Securely link this Discord identity to Operly")
    @app_commands.describe(code="Optional one-time code created from Operly Connections")
    async def link(interaction: discord.Interaction, code: str | None = None):
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

            if code:
                try:
                    await IdentityLinkService.claim_from_channel(
                        db,
                        provider="discord",
                        external_user_id=str(interaction.user.id),
                        code=code,
                        display_name=interaction.user.display_name,
                    )
                except ValueError as error:
                    await interaction.response.send_message(str(error), ephemeral=True)
                    return
                await db.commit()
                await interaction.response.send_message(
                    "Discord is now linked to your Operly identity. Existing guest DM history has been retained.",
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

    @tree.command(name="account", description="Show this Discord identity's Operly status")
    async def account(interaction: discord.Interaction):
        async with session_scope() as db:
            identity = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if not identity:
                await interaction.response.send_message(
                    "You are currently using Operly as a guest. Use `/link` to authenticate; your guest DM history will be retained.",
                    ephemeral=True,
                )
                return
            memberships = await IdentityService.memberships(db, user_id=identity.user_id)
        names = ", ".join(tenant.name for _, tenant in memberships) or "no workspaces"
        await interaction.response.send_message(
            f"This Discord identity is linked to Operly. Available workspaces: {names}.",
            ephemeral=True,
        )

    @tree.command(name="workspaces", description="List Operly workspaces available to this identity")
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
        text = "\n".join(
            f"• {tenant.name} — {membership.role}"
            for membership, tenant in memberships
        ) or "No Operly workspaces yet."
        await interaction.response.send_message(text, ephemeral=True)

    @tree.command(name="workspace", description="Select the active Operly workspace for this Discord DM")
    @app_commands.describe(workspace="Exact Operly workspace name or slug")
    async def workspace(interaction: discord.Interaction, workspace: str):
        if interaction.guild is not None:
            await interaction.response.send_message(
                "Inside a server, the server binding determines the workspace. Use `/bind` instead.",
                ephemeral=True,
            )
            return
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
            matches = _workspace_match(memberships, workspace)
            if len(matches) != 1:
                names = ", ".join(tenant.name for _, tenant in memberships) or "none"
                await interaction.response.send_message(
                    f"Could not resolve exactly one workspace. Available: {names}.",
                    ephemeral=True,
                )
                return
            membership, tenant = matches[0]
            await IdentityService.upsert_conversation_state(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
                external_conversation_id=str(interaction.channel_id),
                user_id=identity.user_id,
                active_tenant_id=tenant.id,
                metadata={"direct": True, "selected_via": "slash_command"},
            )
            await db.commit()
        await interaction.response.send_message(
            f"Active Operly workspace for this DM: `{tenant.name}` ({membership.role}).",
            ephemeral=True,
        )

    @tree.command(name="bind", description="Bind this Discord server to an Operly workspace")
    @app_commands.describe(workspace="Exact Operly workspace name or slug")
    async def bind(interaction: discord.Interaction, workspace: str):
        if interaction.guild is None:
            await interaction.response.send_message("Run `/bind` inside a Discord server.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        external_admin = bool(member and member.guild_permissions.manage_guild)
        if not external_admin:
            await interaction.response.send_message("Discord Manage Server permission is required.", ephemeral=True)
            return

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
            matches = _workspace_match(memberships, workspace)
            if len(matches) != 1:
                names = ", ".join(tenant.name for _, tenant in memberships) or "none"
                await interaction.response.send_message(
                    f"Could not resolve one workspace. Your workspaces: {names}.",
                    ephemeral=True,
                )
                return
            _, tenant = matches[0]
            try:
                await ExternalSpaceBindingService.bind(
                    db,
                    provider="discord",
                    external_space_id=str(interaction.guild.id),
                    display_name=interaction.guild.name,
                    user_id=identity.user_id,
                    tenant_id=tenant.id,
                    external_authority_verified=external_admin,
                )
            except SpaceBindingError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return

            # Temporary compatibility projection for older Discord reporting code.
            legacy_guild = await db.get(DiscordGuild, interaction.guild.id)
            if legacy_guild is None:
                db.add(
                    DiscordGuild(
                        guild_id=interaction.guild.id,
                        tenant_id=tenant.id,
                        guild_name=interaction.guild.name[:200],
                        enabled=True,
                    )
                )
            else:
                legacy_guild.tenant_id = tenant.id
                legacy_guild.guild_name = interaction.guild.name[:200]
                legacy_guild.enabled = True
            await db.commit()
        await interaction.response.send_message(
            f"Bound this Discord server to `{tenant.name}`.",
            ephemeral=True,
        )

    @tree.command(name="unbind", description="Disconnect this Discord server from its Operly workspace")
    async def unbind(interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("Run `/unbind` inside a Discord server.", ephemeral=True)
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        external_admin = bool(member and member.guild_permissions.manage_guild)
        if not external_admin:
            await interaction.response.send_message("Discord Manage Server permission is required.", ephemeral=True)
            return
        async with session_scope() as db:
            identity = await IdentityService.resolve_external_identity(
                db,
                provider="discord",
                external_user_id=str(interaction.user.id),
            )
            if not identity:
                await interaction.response.send_message("Use `/link` first.", ephemeral=True)
                return
            try:
                await ExternalSpaceBindingService.unbind(
                    db,
                    provider="discord",
                    external_space_id=str(interaction.guild.id),
                    user_id=identity.user_id,
                    external_authority_verified=external_admin,
                )
            except SpaceBindingError as error:
                await interaction.response.send_message(str(error), ephemeral=True)
                return
            legacy_guild = await db.get(DiscordGuild, interaction.guild.id)
            if legacy_guild:
                legacy_guild.enabled = False
            await db.commit()
        await interaction.response.send_message("Discord server disconnected from Operly.", ephemeral=True)

    return tree
