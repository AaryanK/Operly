from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from packages.database.models import DiscordGuild, Integration

router = APIRouter(prefix="/api", tags=["integrations"])


@router.get("/integrations")
async def integrations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    guilds = (
        await db.scalars(
            select(DiscordGuild).where(
                DiscordGuild.tenant_id == auth.tenant.id,
                DiscordGuild.enabled.is_(True),
            )
        )
    ).all()
    connected = (
        await db.scalars(
            select(Integration).where(Integration.tenant_id == auth.tenant.id)
        )
    ).all()
    status_map = {row.provider: row.status for row in connected}

    return [
        {
            "provider": "discord",
            "label": "Discord",
            "status": "connected" if guilds else status_map.get("discord", "disconnected"),
            "detail": guilds[0].guild_name if guilds else None,
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
            "label": "WhatsApp",
            "status": status_map.get("whatsapp", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "reminders", "workflow_triggers", "controlled_solution_updates"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "instagram",
            "label": "Instagram",
            "status": status_map.get("instagram", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "facebook",
            "label": "Facebook",
            "status": status_map.get("facebook", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "x",
            "label": "X",
            "status": status_map.get("x", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
    ]
