import asyncio
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from sqlalchemy import select

from packages.business_brain import AgentInput, get_agent_service
from packages.business_brain.attachments import AttachmentBundle, AttachmentInput, MultimodalProcessor
from packages.business_brain.attachments.formatter import processing_manifest, requested_format, split_discord_text
from packages.business_brain.attachments.multimodal_processor import attachment_hashes
from packages.business_brain.attachments.privacy import redacted_name
from packages.database.agent_models import AttachmentAudit
from packages.database.db import init_db, session_scope
from packages.database.models import (
    DiscordGuild,
    Message,
    ScheduledJob,
    Tenant,
)

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
ALWAYS_LISTEN = os.getenv("OPERLY_DISCORD_ALWAYS_LISTEN", "false").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()
agent = None
attachment_processor = MultimodalProcessor()


def shared_agent():
    global agent
    if agent is None:
        agent = get_agent_service()
    return agent


async def ensure_tenant(guild, user) -> str:
    key = guild.id if guild else -int(user.id)

    async with session_scope() as db:
        row = await db.get(DiscordGuild, key)
        if row:
            return row.tenant_id

        name = guild.name if guild else f"DM:{user.id}"
        tenant = Tenant(name=name)
        db.add(tenant)
        await db.flush()

        db.add(
            DiscordGuild(
                guild_id=key,
                tenant_id=tenant.id,
                guild_name=name,
            )
        )
        return tenant.id


async def store_message(
    message: discord.Message,
    tenant_id: str,
    content: str,
    *,
    is_bot: bool,
) -> None:
    async with session_scope() as db:
        existing = await db.scalar(
            select(Message).where(Message.message_id == message.id)
        )
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


async def attachment_already_processed(message_id: int) -> bool:
    async with session_scope() as db:
        return bool(await db.scalar(select(AttachmentAudit.id).where(AttachmentAudit.message_id == str(message_id))))


async def audit_attachments(bundle, result=None, error_category=None) -> None:
    categories=[x.detected_content_type or "rejected" for x in bundle.attachments]
    async with session_scope() as db:
        db.add(AttachmentAudit(
            tenant_id=bundle.tenant_id,actor_id=bundle.actor_id,
            guild_id=str(bundle.guild_id) if bundle.guild_id is not None else None,
            channel_id=str(bundle.channel_id),message_id=str(bundle.message_id),
            attachment_count=len(bundle.attachments),
            filenames_json=json.dumps([redacted_name(x.filename) for x in bundle.attachments]),
            hashes_json=json.dumps(attachment_hashes(bundle)),categories_json=json.dumps(categories),
            operation=result.operation_summary if result else "failed",success=result is not None,
            generated_output_count=len(result.files) if result else 0,
            error_category=(error_category or "")[:100] or None,
        ))


async def process_discord_attachments(message,tenant_id,prompt):
    if await attachment_already_processed(message.id):
        return
    progress=await message.reply(
        f"Processing {len(message.attachments)} attachment(s)…",
        mention_author=False,allowed_mentions=discord.AllowedMentions.none(),
    )
    inputs=[]
    try:
        limits=attachment_processor.limits
        declared_total=sum(max(0,int(getattr(x,"size",0) or 0)) for x in message.attachments)
        if len(message.attachments)>limits.max_attachments:
            raise ValueError(f"maximum {limits.max_attachments} attachments")
        if declared_total>limits.max_total_bytes:
            raise ValueError("total attachment size limit exceeded")
        for index,attachment in enumerate(message.attachments,1):
            declared_size=max(0,int(getattr(attachment,"size",0) or 0))
            if declared_size>limits.max_attachment_bytes:
                inputs.append(AttachmentInput(index=index,filename=attachment.filename,
                    declared_content_type=attachment.content_type,size_bytes=declared_size,
                    content_bytes=b"",rejection_reason="file too large"))
                continue
            raw=await attachment.read()
            inputs.append(AttachmentInput(index=index,filename=attachment.filename,
                declared_content_type=attachment.content_type,size_bytes=len(raw),content_bytes=raw))
        bundle=AttachmentBundle(user_request=prompt or "Summarize each supplied attachment.",attachments=inputs,
            requested_output_format=requested_format(prompt),tenant_id=tenant_id,
            actor_id=str(message.author.id),guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,message_id=message.id)
        with tempfile.TemporaryDirectory(prefix="operly-discord-") as temp_dir:
            result=await attachment_processor.process(bundle,temp_dir)
            manifest=processing_manifest(result.accepted,result.skipped)
            chunks=split_discord_text(manifest+"\n\n"+result.message)
            await progress.edit(content=chunks[0],allowed_mentions=discord.AllowedMentions.none())
            for chunk in chunks[1:]:
                await message.channel.send(chunk,allowed_mentions=discord.AllowedMentions.none())
            if result.warnings:
                warning_text="Warnings:\n"+"\n".join(f"• {x}" for x in result.warnings)
                for chunk in split_discord_text(warning_text):await message.channel.send(chunk,allowed_mentions=discord.AllowedMentions.none())
            upload_limit=getattr(message.guild,"filesize_limit",8*1024*1024) if message.guild else 8*1024*1024
            for output in result.files:
                if output.size_bytes>upload_limit:
                    await message.channel.send(f"Generated `{output.filename}` but it exceeds this Discord channel's upload limit.",allowed_mentions=discord.AllowedMentions.none())
                    continue
                await message.channel.send(file=discord.File(str(output.path),filename=output.filename),allowed_mentions=discord.AllowedMentions.none())
            await audit_attachments(bundle,result=result)
        # Sensitive extracted contents are intentionally not stored in Message/AgentMessage.
        await store_message(progress,tenant_id,"[attachment processing completed]",is_bot=True)
    except Exception as error:
        if inputs:
            bundle=AttachmentBundle(user_request=prompt,attachments=inputs,tenant_id=tenant_id,actor_id=str(message.author.id),guild_id=message.guild.id if message.guild else None,channel_id=message.channel.id,message_id=message.id)
            try:await audit_attachments(bundle,error_category=type(error).__name__)
            except Exception:pass
        await progress.edit(content="Attachment processing failed safely. No files were saved. Check the server logs.",allowed_mentions=discord.AllowedMentions.none())
        print(f"OPERLY attachment gateway error category: {type(error).__name__}")


async def run_scheduled_job(job_id: str) -> None:
    async with session_scope() as db:
        job = await db.get(ScheduledJob, job_id)
        if job is None or job.status != "pending":
            return

        job.status = "running"
        delivery = job.delivery
        content = job.content
        channel_id = job.channel_id
        user_id = job.user_id

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
                select(ScheduledJob).where(
                    ScheduledJob.status == "pending"
                )
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
                select(ScheduledJob).where(
                    ScheduledJob.status == "pending"
                )
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
    print(f"OPERLY shared agent connected as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    tenant_id = await ensure_tenant(message.guild, message.author)

    stored_content = message.content or "[attachment]"
    if message.attachments:
        stored_content += " [attachments: " + ", ".join(
            attachment.filename for attachment in message.attachments
        ) + "]"

    await store_message(
        message,
        tenant_id,
        stored_content,
        is_bot=False,
    )

    if not addressed_to_operly(message):
        return

    prompt = message.content
    if bot.user:
        prompt = prompt.replace(f"<@{bot.user.id}>", "")
        prompt = prompt.replace(f"<@!{bot.user.id}>", "")
    prompt = prompt.strip()

    if message.attachments:
        async with message.channel.typing():
            await process_discord_attachments(message,tenant_id,prompt)
        return

    try:
        async with message.channel.typing():
            result = await shared_agent().run(
                AgentInput(
                    tenant_id=tenant_id,
                    principal_id=f"discord-channel:{message.channel.id}",
                    actor_name=message.author.display_name,
                    channel="discord",
                    conversation_id=f"discord:{message.channel.id}",
                    text=prompt or "Analyze the supplied attachment.",
                    images=[],
                    metadata={
                        "discord_guild_id": (
                            message.guild.id if message.guild else None
                        ),
                        "discord_channel_id": message.channel.id,
                        "discord_user_id": message.author.id,
                    },
                )
            )

        sent = await send_chunks(message, result["message"])
        await store_message(
            sent,
            tenant_id,
            result["message"],
            is_bot=True,
        )
        await schedule_new_pending_jobs()

    except Exception as error:
        print(f"OPERLY shared-agent error category: {type(error).__name__}")
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
