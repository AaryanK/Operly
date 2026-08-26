import json
import os
import tempfile
from datetime import datetime
from urllib.parse import quote

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from sqlalchemy import func, select

from packages.business_brain.attachments import (
    AttachmentBundle,
    AttachmentIngestionPlugin,
    AttachmentInput,
    MultimodalProcessor,
)
from packages.business_brain.attachments.formatter import processing_manifest, requested_format, split_discord_text
from packages.business_brain.attachments.multimodal_processor import attachment_hashes
from packages.business_brain.attachments.privacy import redacted_name
from packages.channels.envelope import ChannelEnvelope
from packages.channels.identity import IdentityService
from packages.channels.linking import IdentityLinkService
from packages.channels.service import ChannelService
from packages.connectors.discord.scheduled_tasks import run_harness_task_job
from packages.connectors.discord.transport import collect_discord_attachments, send_discord_response
from packages.database.agent_models import AttachmentAudit
from packages.database.channel_models import ChannelInstallation
from packages.database.db import init_db, session_scope
from packages.database.models import Message, ScheduledJob, TenantMember
from packages.harness.plugins import RuntimePluginContext, RuntimePluginRegistry
from packages.model_runtime import ModelInferenceError

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
ALWAYS_LISTEN = os.getenv("OPERLY_DISCORD_ALWAYS_LISTEN", "false").lower() == "true"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()
# Kept as an injectable compatibility seam for tests/operators. Each ingestion
# invocation wraps the current processor in the runtime plugin registry.
attachment_processor = MultimodalProcessor()


async def server_tenant(message: discord.Message) -> str | None:
    if message.guild is None:
        return None
    async with session_scope() as db:
        installation = await IdentityService.ensure_installation(
            db,
            provider="discord",
            external_space_id=str(message.guild.id),
            display_name=message.guild.name,
        )
        return installation.tenant_id


async def store_message(
    message: discord.Message,
    tenant_id: str,
    content: str,
    *,
    is_bot: bool,
) -> None:
    async with session_scope() as db:
        existing = await db.scalar(select(Message).where(Message.message_id == message.id))
        if existing:
            return
        db.add(
            Message(
                tenant_id=tenant_id,
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
                message_id=message.id,
                author_id=message.author.id,
                author_name=message.author.display_name,
                content=content,
                is_bot=is_bot,
            )
        )


def addressed_to_operly(message: discord.Message) -> bool:
    if message.guild is None:
        return True
    if ALWAYS_LISTEN:
        return True
    if bot.user and bot.user in message.mentions:
        return True
    if (
        message.reference
        and isinstance(message.reference.resolved, discord.Message)
        and bot.user
        and message.reference.resolved.author.id == bot.user.id
    ):
        return True
    return False


def clean_prompt(message: discord.Message) -> str:
    prompt = message.content or ""
    if bot.user:
        prompt = prompt.replace(f"<@{bot.user.id}>", "")
        prompt = prompt.replace(f"<@!{bot.user.id}>", "")
    return prompt.strip()


def envelope_for(message: discord.Message, prompt: str) -> ChannelEnvelope:
    return ChannelEnvelope(
        provider="discord",
        external_user_id=str(message.author.id),
        external_space_id=str(message.guild.id) if message.guild else None,
        external_conversation_id=str(message.channel.id),
        actor_name=message.author.display_name,
        text=prompt or "Analyze the supplied attachment.",
        space_name=message.guild.name if message.guild else None,
        is_direct=message.guild is None,
        metadata={
            "discord_guild_id": message.guild.id if message.guild else None,
            "discord_channel_id": message.channel.id,
            "discord_user_id": message.author.id,
            "external_message_id": str(message.id),
            "has_attachments": bool(message.attachments),
            "attachment_count": len(message.attachments),
        },
    )


async def send_chunks(message: discord.Message, text: str) -> discord.Message:
    chunks = split_discord_text(text)
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


async def create_channel_link(message: discord.Message) -> None:
    async with session_scope() as db:
        existing = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
        if existing:
            await message.reply(
                "This Discord account is already linked to an Operly user.",
                mention_author=False,
            )
            return
        challenge = await IdentityLinkService.create_from_channel(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
            display_name=message.author.display_name,
        )
    link = f"{PUBLIC_BASE_URL}/settings?identity_link={quote(challenge.token or '', safe='')}"
    await message.reply(
        "Link this Discord identity to your Operly account here. "
        f"The link expires in 10 minutes:\n{link}",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def consume_operly_link_code(message: discord.Message, code: str) -> None:
    async with session_scope() as db:
        try:
            identity = await IdentityLinkService.claim_from_channel(
                db,
                provider="discord",
                external_user_id=str(message.author.id),
                code=code,
                display_name=message.author.display_name,
            )
        except ValueError as error:
            await message.reply(str(error), mention_author=False)
            return

        auto_claimed = False
        if message.guild is not None and message.author.guild_permissions.manage_guild:
            installation = await IdentityService.ensure_installation(
                db,
                provider="discord",
                external_space_id=str(message.guild.id),
                display_name=message.guild.name,
            )
            member_count = await db.scalar(
                select(func.count(TenantMember.id)).where(
                    TenantMember.tenant_id == installation.tenant_id
                )
            ) or 0
            if installation.provisional and member_count == 0:
                db.add(
                    TenantMember(
                        tenant_id=installation.tenant_id,
                        user_id=identity.user_id,
                        role="owner",
                    )
                )
                installation.provisional = False
                auto_claimed = True

    suffix = " I also claimed this new Discord workspace for you as owner." if auto_claimed else ""
    await message.reply(
        "Discord is now linked to your Operly identity." + suffix,
        mention_author=False,
    )


async def claim_current_discord_workspace(message: discord.Message) -> None:
    if message.guild is None:
        await message.reply("Run this command inside the Discord server you want to claim.", mention_author=False)
        return
    if not message.author.guild_permissions.manage_guild:
        await message.reply("Discord Manage Server permission is required to claim this workspace.", mention_author=False)
        return

    async with session_scope() as db:
        identity = await IdentityService.resolve_external_identity(
            db,
            provider="discord",
            external_user_id=str(message.author.id),
        )
        if not identity:
            await message.reply("Link your Discord identity first with `!operly link`.", mention_author=False)
            return
        installation = await IdentityService.ensure_installation(
            db,
            provider="discord",
            external_space_id=str(message.guild.id),
            display_name=message.guild.name,
        )
        membership = await IdentityService.membership(
            db,
            user_id=identity.user_id,
            tenant_id=installation.tenant_id,
        )
        if membership:
            await message.reply("You are already a member of this Operly workspace.", mention_author=False)
            return
        member_count = await db.scalar(
            select(func.count(TenantMember.id)).where(
                TenantMember.tenant_id == installation.tenant_id
            )
        ) or 0
        if not installation.provisional or member_count:
            await message.reply(
                "This Discord server is already attached to a claimed Operly workspace. Ask an owner to invite your Operly account.",
                mention_author=False,
            )
            return
        db.add(
            TenantMember(
                tenant_id=installation.tenant_id,
                user_id=identity.user_id,
                role="owner",
            )
        )
        installation.provisional = False

    await message.reply("This Discord server is now your Operly workspace.", mention_author=False)


async def handle_operly_command(message: discord.Message) -> bool:
    raw = (message.content or "").strip()
    parts = raw.split()
    if not parts or parts[0].lower() != "!operly":
        return False
    command = parts[1].lower() if len(parts) > 1 else "help"
    if command == "link":
        if len(parts) >= 3:
            await consume_operly_link_code(message, parts[2])
        else:
            await create_channel_link(message)
        return True
    if command == "claim":
        await claim_current_discord_workspace(message)
        return True
    await message.reply(
        "Operly commands: `!operly link`, `!operly link CODE`, `!operly claim`.",
        mention_author=False,
    )
    return True


async def attachment_already_processed(message_id: int) -> bool:
    async with session_scope() as db:
        return bool(
            await db.scalar(
                select(AttachmentAudit.id).where(
                    AttachmentAudit.message_id == str(message_id)
                )
            )
        )


async def audit_attachments(bundle, result=None, error_category=None) -> None:
    categories = [item.detected_content_type or "rejected" for item in bundle.attachments]
    async with session_scope() as db:
        db.add(
            AttachmentAudit(
                tenant_id=bundle.tenant_id,
                actor_id=bundle.actor_id,
                guild_id=str(bundle.guild_id) if bundle.guild_id is not None else None,
                channel_id=str(bundle.channel_id),
                message_id=str(bundle.message_id),
                attachment_count=len(bundle.attachments),
                filenames_json=json.dumps([redacted_name(item.filename) for item in bundle.attachments]),
                hashes_json=json.dumps(attachment_hashes(bundle)),
                categories_json=json.dumps(categories),
                operation=result.operation_summary if result else "failed",
                success=result is not None,
                generated_output_count=len(result.files) if result else 0,
                error_category=(error_category or "")[:100] or None,
            )
        )


async def _run_attachment_plugin(bundle, temp_dir, *, channel: str, is_direct: bool):
    registry = RuntimePluginRegistry()
    registry.register(AttachmentIngestionPlugin(attachment_processor))
    return await registry.invoke(
        "attachment_ingestion",
        {"bundle": bundle, "temp_dir": temp_dir},
        RuntimePluginContext(
            channel=channel,
            surface="private/direct" if is_direct else "shared/workspace",
            metadata={
                "attachment_count": len(bundle.attachments),
                "perception_only": True,
            },
        ),
    )


async def process_discord_attachments(message, tenant_id, prompt, *, shared_message_store: bool):
    """Legacy shared-workspace attachment preprocessor.

    Personal Discord DMs no longer use this path; their files enter Personal artifact
    scope through the transport-neutral ChannelEnvelope and normal ``files.process``
    capability. Shared/full/guest workspace attachment behavior remains here during
    the migration so existing server continuity is preserved.
    """
    if await attachment_already_processed(message.id):
        return True
    progress = await message.reply(
        f"Processing {len(message.attachments)} attachment(s)…",
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    inputs = []
    try:
        limits = attachment_processor.limits
        declared_total = sum(
            max(0, int(getattr(item, "size", 0) or 0))
            for item in message.attachments
        )
        if len(message.attachments) > limits.max_attachments:
            raise ValueError(f"maximum {limits.max_attachments} attachments")
        if declared_total > limits.max_total_bytes:
            raise ValueError("total attachment size limit exceeded")
        for index, attachment in enumerate(message.attachments, 1):
            declared_size = max(0, int(getattr(attachment, "size", 0) or 0))
            if declared_size > limits.max_attachment_bytes:
                inputs.append(
                    AttachmentInput(
                        index=index,
                        filename=attachment.filename,
                        declared_content_type=attachment.content_type,
                        size_bytes=declared_size,
                        content_bytes=b"",
                        rejection_reason="file too large",
                    )
                )
                continue
            raw = await attachment.read()
            inputs.append(
                AttachmentInput(
                    index=index,
                    filename=attachment.filename,
                    declared_content_type=attachment.content_type,
                    size_bytes=len(raw),
                    content_bytes=raw,
                )
            )
        bundle = AttachmentBundle(
            user_request=prompt or "Summarize each supplied attachment.",
            attachments=inputs,
            requested_output_format=requested_format(prompt),
            tenant_id=tenant_id,
            actor_id=str(message.author.id),
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
            message_id=message.id,
        )
        with tempfile.TemporaryDirectory(prefix="operly-discord-") as temp_dir:
            result = await _run_attachment_plugin(
                bundle,
                temp_dir,
                channel="discord",
                is_direct=message.guild is None,
            )
            manifest = processing_manifest(result.accepted, result.skipped)
            await progress.edit(
                content=(
                    manifest
                    + "\n\nAttachment ingestion complete. Passing the derived context into Operly's normal capability harness…"
                )[:1900],
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if result.warnings:
                warning_text = "Warnings:\n" + "\n".join(
                    f"• {item}" for item in result.warnings
                )
                for chunk in split_discord_text(warning_text):
                    await message.channel.send(
                        chunk,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            upload_limit = (
                getattr(message.guild, "filesize_limit", 8 * 1024 * 1024)
                if message.guild
                else 8 * 1024 * 1024
            )
            for output in result.files:
                if output.size_bytes > upload_limit:
                    await message.channel.send(
                        f"Generated `{output.filename}` but it exceeds this Discord channel's upload limit.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    continue
                await message.channel.send(
                    file=discord.File(str(output.path), filename=output.filename),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            await audit_attachments(bundle, result=result)
        if shared_message_store:
            await store_message(
                progress,
                tenant_id,
                "[attachment ingestion completed; continued to agent harness]",
                is_bot=True,
            )
        return True
    except Exception as error:
        if inputs:
            bundle = AttachmentBundle(
                user_request=prompt,
                attachments=inputs,
                tenant_id=tenant_id,
                actor_id=str(message.author.id),
                guild_id=message.guild.id if message.guild else None,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            try:
                await audit_attachments(bundle, error_category=type(error).__name__)
            except Exception:
                pass
        await progress.edit(
            content="Attachment processing failed safely. No files were saved. Check the server logs.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        print(f"OPERLY attachment gateway error category: {type(error).__name__}")
        return False


async def run_scheduled_job(job_id: str) -> None:
    async with session_scope() as db:
        job = await db.get(ScheduledJob, job_id)
        if job is None or job.status != "pending":
            return
        is_task = bool(job.task_id)
        if not is_task:
            job.status = "running"
            delivery = job.delivery
            content = job.content
            channel_id = job.channel_id
            user_id = job.user_id

    if is_task:
        await run_harness_task_job(
            bot=bot,
            scheduler=scheduler,
            job_id=job_id,
            runner=run_scheduled_job,
        )
        return

    try:
        if delivery == "dm":
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await user.send(f"⏰ {content}")
        else:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            await channel.send(
                f"<@{user_id}> ⏰ {content}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        async with session_scope() as db:
            job = await db.get(ScheduledJob, job_id)
            if job:
                job.status = "completed"
    except Exception:
        async with session_scope() as db:
            job = await db.get(ScheduledJob, job_id)
            if job:
                job.status = "failed"
        raise


async def restore_pending_jobs() -> None:
    now = datetime.utcnow()
    async with session_scope() as db:
        jobs = (
            await db.scalars(
                select(ScheduledJob).where(ScheduledJob.status == "pending")
            )
        ).all()
    for job in jobs:
        scheduler.add_job(
            run_scheduled_job,
            "date",
            run_date=max(job.run_at, now),
            args=[job.id],
            id=job.id,
            replace_existing=True,
        )


async def schedule_new_pending_jobs() -> None:
    async with session_scope() as db:
        jobs = (
            await db.scalars(
                select(ScheduledJob).where(ScheduledJob.status == "pending")
            )
        ).all()
    existing = {job.id for job in scheduler.get_jobs()}
    for job in jobs:
        if job.id in existing:
            continue
        scheduler.add_job(
            run_scheduled_job,
            "date",
            run_date=job.run_at,
            args=[job.id],
            id=job.id,
            replace_existing=True,
        )


@bot.event
async def on_ready():
    await init_db()
    if not scheduler.running:
        scheduler.start()
    await restore_pending_jobs()
    print(f"OPERLY channel adapter connected as {bot.user}")


def _log_channel_error(error: Exception) -> None:
    if isinstance(error, ModelInferenceError):
        print(
            "OPERLY channel-agent model error "
            f"provider={error.provider or 'unknown'} "
            f"model={error.model_id or 'unknown'} "
            f"classification={error.classification or 'unknown'} "
            f"retryable={bool(error.retryable)}"
        )
        return
    print(f"OPERLY channel-agent error category: {type(error).__name__}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if await handle_operly_command(message):
        return

    tenant_id = await server_tenant(message)
    stored_content = message.content or "[attachment]"
    if message.attachments:
        stored_content += " [attachments: " + ", ".join(
            attachment.filename for attachment in message.attachments
        ) + "]"

    # Server messages are shared channel records. DMs are intentionally not copied
    # into the tenant-wide Message table; Personal AI owns DM history/artifacts.
    if tenant_id:
        await store_message(message, tenant_id, stored_content, is_bot=False)

    if not addressed_to_operly(message):
        return

    prompt = clean_prompt(message)
    envelope = envelope_for(message, prompt)

    try:
        if message.attachments:
            async with session_scope() as db:
                resolved = await ChannelService.resolve(db, envelope)

            if envelope.is_direct:
                if not resolved.user_id:
                    await send_chunks(
                        message,
                        "Link your Discord identity first with `!operly link` before sending private files.",
                    )
                    return
                # Personal files are now canonical Personal artifacts. The Discord
                # adapter only authenticates/downloads bytes; ChannelService persists
                # them under the resolved user before Personal AI receives handles.
                envelope.attachments = await collect_discord_attachments(message)
            else:
                if not resolved.tenant_id or not resolved.allow_tenant_context:
                    await send_chunks(
                        message,
                        "This Discord workspace does not authorize file processing for your current identity.",
                    )
                    return
                async with message.channel.typing():
                    ingested = await process_discord_attachments(
                        message,
                        resolved.tenant_id,
                        prompt,
                        shared_message_store=True,
                    )
                if not ingested:
                    return

        async with message.channel.typing():
            response = await ChannelService.handle(envelope)

        sent = await send_discord_response(message, response)
        if message.guild is not None and response.tenant_id:
            await store_message(
                sent,
                response.tenant_id,
                response.base_message or response.message,
                is_bot=True,
            )
        if response.status == "ok":
            await schedule_new_pending_jobs()

    except Exception as error:
        _log_channel_error(error)
        await message.reply(
            "The AI request failed safely. Check the server logs.",
            mention_author=False,
        )


def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
