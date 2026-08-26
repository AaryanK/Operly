"""Discord last-mile adapter for canonical Operly envelopes/responses.

The core agent does not need to know Discord message limits or how Discord attaches a
file.  This adapter translates authenticated Discord inputs into bounded
``ChannelAttachment`` bytes and translates a canonical ``ChannelResponse`` back into
Discord-native Markdown + file uploads.
"""
from __future__ import annotations

import io

import discord

from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.business_brain.attachments import MultimodalProcessor
from packages.business_brain.attachments.formatter import split_discord_text
from packages.channels.envelope import ChannelAttachment, ChannelResponse
from packages.channels.presentation import format_for_channel
from packages.database.db import session_scope


async def collect_discord_attachments(message: discord.Message) -> list[ChannelAttachment]:
    """Download Discord-owned attachment URLs through discord.py into bounded bytes."""
    limits = MultimodalProcessor().limits
    if len(message.attachments) > limits.max_attachments:
        raise ValueError(f"maximum {limits.max_attachments} attachments")

    declared_total = sum(max(0, int(getattr(item, "size", 0) or 0)) for item in message.attachments)
    if declared_total > limits.max_total_bytes:
        raise ValueError("total attachment size limit exceeded")

    output: list[ChannelAttachment] = []
    total = 0
    for item in message.attachments:
        declared_size = max(0, int(getattr(item, "size", 0) or 0))
        if declared_size > limits.max_attachment_bytes:
            raise ValueError(f"{item.filename} exceeds the per-attachment size limit")
        raw = await item.read()
        total += len(raw)
        if total > limits.max_total_bytes:
            raise ValueError("total attachment size limit exceeded")
        output.append(
            ChannelAttachment(
                filename=item.filename,
                content_type=item.content_type,
                size_bytes=len(raw),
                url=None,
                content_bytes=raw,
            )
        )
    return output


def _response_scope(response: ChannelResponse) -> ArtifactScope | None:
    if response.tenant_id:
        return ArtifactScope("workspace", response.tenant_id, tenant_id=response.tenant_id)
    if response.user_id:
        return ArtifactScope(
            "personal",
            f"personal:{response.user_id}",
            owner_user_id=response.user_id,
        )
    return None


async def _send_text(message: discord.Message, text: str) -> discord.Message:
    rendered = format_for_channel(text, "discord") or "Done."
    chunks = split_discord_text(rendered)
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


async def send_discord_response(message: discord.Message, response: ChannelResponse) -> discord.Message:
    """Render canonical response text and artifacts using native Discord semantics."""
    # Rich transports use base_message to avoid printing browser links for artifacts
    # that will be uploaded natively below. Text-only adapters may still use
    # ChannelResponse.message and its authenticated-link fallback.
    sent = await _send_text(message, response.base_message or response.message)
    scope = _response_scope(response)
    if not scope or not response.artifacts:
        return sent

    upload_limit = (
        int(getattr(message.guild, "filesize_limit", 8 * 1024 * 1024))
        if message.guild is not None
        else 8 * 1024 * 1024
    )
    async with session_scope() as db:
        service = ArtifactService(db)
        for artifact in response.artifacts[:25]:
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            filename = str(artifact.get("filename") or "operly-output.bin")[:255]
            size = int(artifact.get("size_bytes") or 0)
            fallback = str(artifact.get("download_url") or "").strip()
            if not artifact_id:
                continue
            if size > upload_limit:
                note = f"`{filename}` exceeds this Discord upload limit."
                if fallback:
                    note += f" {fallback}"
                await message.channel.send(note, allowed_mentions=discord.AllowedMentions.none())
                continue
            try:
                raw = await service.read_bytes(scope, artifact_id)
            except (LookupError, RuntimeError):
                # Never fetch from an unverified URL to compensate for scope failure.
                if fallback:
                    await message.channel.send(
                        f"`{filename}`: {fallback}",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                continue
            await message.channel.send(
                file=discord.File(io.BytesIO(raw), filename=filename),
                allowed_mentions=discord.AllowedMentions.none(),
            )
    return sent
