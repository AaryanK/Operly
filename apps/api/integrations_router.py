from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.channel_models import ChannelInstallation
from packages.database.models import DiscordGuild, Integration

router = APIRouter(prefix="/api", tags=["integrations"])


@router.get("/integrations")
async def integrations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    bindings = (
        await db.scalars(
            select(ChannelInstallation)
            .where(
                ChannelInstallation.tenant_id == auth.tenant.id,
                ChannelInstallation.status == "connected",
                ChannelInstallation.provisional.is_(False),
            )
            .order_by(ChannelInstallation.provider, ChannelInstallation.display_name)
        )
    ).all()
    by_provider: dict[str, list[ChannelInstallation]] = {}
    for binding in bindings:
        by_provider.setdefault(binding.provider, []).append(binding)

    # Compatibility fallback for deployments that still have the pre-generic
    # DiscordGuild projection but no ChannelInstallation row yet.
    guilds = (
        await db.scalars(
            select(DiscordGuild).where(
                DiscordGuild.tenant_id == auth.tenant.id,
                DiscordGuild.enabled.is_(True),
            )
        )
    ).all()
    if guilds and not by_provider.get("discord"):
        by_provider["discord"] = []

    connected = (
        await db.scalars(
            select(Integration).where(Integration.tenant_id == auth.tenant.id)
        )
    ).all()
    status_map = {row.provider: row.status for row in connected}

    def spaces(provider: str) -> list[dict]:
        rows = by_provider.get(provider, [])
        if provider == "discord" and not rows and guilds:
            return [
                {
                    "id": str(guild.guild_id),
                    "external_space_id": str(guild.guild_id),
                    "name": guild.guild_name,
                    "provider": "discord",
                    "status": "connected",
                    "legacy": True,
                }
                for guild in guilds
            ]
        return [
            {
                "id": row.id,
                "external_space_id": row.external_space_id,
                "name": row.display_name,
                "provider": row.provider,
                "status": row.status,
                "legacy": False,
            }
            for row in rows
        ]

    discord_spaces = spaces("discord")
    return [
        {
            "provider": "discord",
            "label": "Discord servers",
            "status": "connected" if discord_spaces else status_map.get("discord", "disconnected"),
            "detail": discord_spaces[0]["name"] if discord_spaces else None,
            "spaces": discord_spaces,
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": [
                "messages",
                "reminders",
                "workflow_triggers",
                "approvals",
                "controlled_solution_updates",
            ],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "whatsapp",
            "label": "WhatsApp groups",
            "status": status_map.get("whatsapp", "coming_soon"),
            "detail": None,
            "spaces": spaces("whatsapp"),
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": ["messages", "reminders", "workflow_triggers", "controlled_solution_updates"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "slack",
            "label": "Slack workspaces",
            "status": status_map.get("slack", "coming_soon"),
            "detail": None,
            "spaces": spaces("slack"),
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": ["messages", "reminders", "workflow_triggers", "approvals"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "instagram",
            "label": "Instagram",
            "status": status_map.get("instagram", "coming_soon"),
            "detail": None,
            "spaces": spaces("instagram"),
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "facebook",
            "label": "Facebook",
            "status": status_map.get("facebook", "coming_soon"),
            "detail": None,
            "spaces": spaces("facebook"),
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "x",
            "label": "X",
            "status": status_map.get("x", "coming_soon"),
            "detail": None,
            "spaces": spaces("x"),
            "scope": "workspace",
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
    ]
