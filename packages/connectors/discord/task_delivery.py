from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import discord
from sqlalchemy import select

from packages.artifacts.delivery import resolve_delivery_artifacts
from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.database.channel_models import ChannelInstallation, ExternalIdentity
from packages.database.db import session_scope
from packages.tasks.delivery import TaskDeliveryError


DEFAULT_UPLOAD_LIMIT = 8 * 1024 * 1024
MAX_NATIVE_ARTIFACTS = 10


def _chunks(text: str, limit: int = 1900) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    output: list[str] = []
    while value:
        if len(value) <= limit:
            output.append(value)
            break
        split_at = value.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = value.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        output.append(value[:split_at].strip())
        value = value[split_at:].strip()
    return [item for item in output if item]


async def _current_discord_user_id(target: dict) -> int:
    """Re-resolve the linked Discord identity at delivery time."""
    operly_user_id = str(target.get("user_id") or "").strip()
    if operly_user_id:
        async with session_scope() as db:
            identity = await db.scalar(
                select(ExternalIdentity).where(
                    ExternalIdentity.user_id == operly_user_id,
                    ExternalIdentity.provider == "discord",
                )
            )
        if identity is None:
            raise TaskDeliveryError("discord_delivery_identity_unlinked")
        return int(identity.provider_subject)

    raw_user_id = target.get("external_user_id")
    if raw_user_id is None:
        raise TaskDeliveryError("discord_delivery_user_missing")
    return int(raw_user_id)


async def _validate_workspace_installation(target: dict) -> None:
    tenant_id = str(target.get("tenant_id") or "").strip()
    external_space_id = str(target.get("external_space_id") or "").strip()
    if not tenant_id or not external_space_id:
        raise TaskDeliveryError("discord_delivery_workspace_binding_missing")
    async with session_scope() as db:
        installation = await db.scalar(
            select(ChannelInstallation).where(
                ChannelInstallation.tenant_id == tenant_id,
                ChannelInstallation.provider == "discord",
                ChannelInstallation.external_space_id == external_space_id,
                ChannelInstallation.status == "connected",
                ChannelInstallation.provisional.is_(False),
            )
        )
    if installation is None:
        raise TaskDeliveryError("discord_delivery_workspace_binding_revoked")


def _artifact_scope(target: dict) -> ArtifactScope:
    scope = str(target.get("scope") or "workspace").strip().lower()
    if scope == "personal":
        user_id = str(target.get("user_id") or "").strip()
        if not user_id:
            raise TaskDeliveryError("discord_artifact_personal_user_missing")
        return ArtifactScope("personal", f"personal:{user_id}", owner_user_id=user_id)
    tenant_id = str(target.get("tenant_id") or "").strip()
    if not tenant_id:
        raise TaskDeliveryError("discord_artifact_workspace_missing")
    return ArtifactScope("workspace", tenant_id, tenant_id=tenant_id)


def _upload_limit(destination) -> int:
    guild = getattr(destination, "guild", None)
    if guild is None:
        return DEFAULT_UPLOAD_LIMIT
    return max(1, int(getattr(guild, "filesize_limit", DEFAULT_UPLOAD_LIMIT) or DEFAULT_UPLOAD_LIMIT))


async def _send_artifacts(destination, target: dict, message_ids: list[str]) -> list[str]:
    ids = [str(item) for item in (target.get("artifact_ids") or []) if str(item).strip()][:20]
    if not ids:
        return []
    scope = _artifact_scope(target)
    fallback: list[str] = []
    upload_limit = _upload_limit(destination)
    async with session_scope() as db:
        resolved = await resolve_delivery_artifacts(db, scope, ids, limit=20)
        service = ArtifactService(db)
        native_count = 0
        for item in resolved:
            artifact_id = str(item.get("artifact_id") or "")
            filename = str(item.get("filename") or "generated-file")
            size_bytes = int(item.get("size_bytes") or 0)
            url = str(item.get("download_url") or "")
            if native_count >= MAX_NATIVE_ARTIFACTS or size_bytes > upload_limit:
                if url:
                    fallback.append(f"• {filename}: {url}")
                continue
            try:
                raw = await service.read_bytes(scope, artifact_id)
            except (LookupError, ValueError):
                if url:
                    fallback.append(f"• {filename}: {url}")
                continue
            kwargs = {"file": discord.File(BytesIO(raw), filename=filename)}
            if not isinstance(destination, discord.User):
                kwargs["allowed_mentions"] = discord.AllowedMentions.none()
            sent = await destination.send(**kwargs)
            message_ids.append(str(sent.id))
            native_count += 1
    if fallback:
        text = "Files available from Operly:\n" + "\n".join(fallback)
        for chunk in _chunks(text):
            kwargs = {}
            if not isinstance(destination, discord.User):
                kwargs["allowed_mentions"] = discord.AllowedMentions.none()
            sent = await destination.send(chunk, **kwargs)
            message_ids.append(str(sent.id))
    return ids


@dataclass(slots=True)
class DiscordTaskDeliveryAdapter:
    providers: tuple[str, ...] = ("discord",)

    async def deliver(self, target: dict, message: str) -> dict:
        from packages.connectors.discord import secure_runtime

        bot = secure_runtime.bot
        if bot.is_closed():
            raise TaskDeliveryError("discord_delivery_client_closed")
        kind = str(target.get("kind") or "channel")
        chunks = _chunks(message)
        message_ids: list[str] = []

        if kind == "dm":
            user_id = await _current_discord_user_id(target)
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            for chunk in chunks:
                sent = await user.send(chunk)
                message_ids.append(str(sent.id))
            delivered_artifacts = await _send_artifacts(user, target, message_ids)
            return {
                "status": "VERIFIED",
                "provider": "discord",
                "kind": "dm",
                "message_ids": message_ids,
                "artifact_ids": delivered_artifacts,
                "external_user_id": str(user_id),
                "authority": {
                    "owner_type": str(target.get("scope") or "personal"),
                    "owner_id": target.get("tenant_id") or target.get("user_id"),
                },
            }

        await _validate_workspace_installation(target)
        raw_channel_id = target.get("external_conversation_id")
        if raw_channel_id is None:
            raise TaskDeliveryError("discord_delivery_channel_missing")
        channel_id = int(raw_channel_id)
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        for chunk in chunks:
            sent = await channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            message_ids.append(str(sent.id))
        delivered_artifacts = await _send_artifacts(channel, target, message_ids)
        return {
            "status": "VERIFIED",
            "provider": "discord",
            "kind": "channel",
            "message_ids": message_ids,
            "artifact_ids": delivered_artifacts,
            "external_conversation_id": str(channel_id),
            "external_space_id": str(target.get("external_space_id") or ""),
            "authority": {
                "owner_type": "workspace",
                "owner_id": target.get("tenant_id"),
            },
        }
