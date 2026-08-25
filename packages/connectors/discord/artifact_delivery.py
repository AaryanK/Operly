from __future__ import annotations

from io import BytesIO
from typing import Any

import discord

from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.business_brain.attachments.formatter import split_discord_text
from packages.channels.envelope import ChannelResponse
from packages.database.db import session_scope

MAX_NATIVE_ARTIFACTS = 10
DEFAULT_DM_UPLOAD_LIMIT = 8 * 1024 * 1024


def response_artifact_scope(response: ChannelResponse) -> ArtifactScope | None:
    if response.tenant_id:
        return ArtifactScope("workspace", response.tenant_id, tenant_id=response.tenant_id)
    if response.user_id:
        return ArtifactScope(
            "personal",
            f"personal:{response.user_id}",
            owner_user_id=response.user_id,
        )
    return None


def discord_upload_limit(message: discord.Message) -> int:
    if message.guild is None:
        return DEFAULT_DM_UPLOAD_LIMIT
    return max(
        1,
        int(getattr(message.guild, "filesize_limit", DEFAULT_DM_UPLOAD_LIMIT) or DEFAULT_DM_UPLOAD_LIMIT),
    )


async def _send_text(message: discord.Message, text: str) -> discord.Message:
    chunks = split_discord_text(text or "Done.")
    sent = await message.reply(
        chunks[0],
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    for chunk in chunks[1:]:
        await message.channel.send(
            chunk,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    return sent


async def send_discord_response(
    message: discord.Message,
    response: ChannelResponse,
) -> discord.Message:
    """Render a channel response with native scoped artifact attachments.

    The Artifact Store remains authoritative. Discord receives bytes only after a
    fresh scope-checked read. Files that exceed the channel's upload limit (or files
    beyond the bounded native-upload count) fall back to the canonical Operly
    download URL instead of disappearing.
    """
    sent = await _send_text(message, response.base_message or response.message)
    if not response.artifacts:
        return sent

    scope = response_artifact_scope(response)
    if scope is None:
        return sent

    upload_limit = discord_upload_limit(message)
    fallback: list[str] = []
    native_count = 0

    async with session_scope() as db:
        service = ArtifactService(db)
        for artifact in response.artifacts:
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            filename = str(artifact.get("filename") or "generated-file").strip() or "generated-file"
            size_bytes = max(0, int(artifact.get("size_bytes") or 0))
            url = str(artifact.get("download_url") or "").strip()

            if not artifact_id:
                continue
            if native_count >= MAX_NATIVE_ARTIFACTS or (size_bytes and size_bytes > upload_limit):
                if url:
                    fallback.append(f"• {filename}: {url}")
                else:
                    fallback.append(f"• {filename}: available in Operly's artifact library")
                continue

            try:
                row = await service.get(scope, artifact_id)
                if row.size_bytes > upload_limit:
                    if url:
                        fallback.append(f"• {row.filename}: {url}")
                    else:
                        fallback.append(f"• {row.filename}: available in Operly's artifact library")
                    continue
                raw = await service.read_bytes(scope, artifact_id)
            except (LookupError, ValueError):
                if url:
                    fallback.append(f"• {filename}: {url}")
                continue

            await message.channel.send(
                file=discord.File(BytesIO(raw), filename=row.filename),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            native_count += 1

    if fallback:
        fallback_text = "Files too large or unavailable for native Discord upload:\n" + "\n".join(fallback)
        for chunk in split_discord_text(fallback_text):
            await message.channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    return sent
