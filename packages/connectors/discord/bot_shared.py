import base64
import os
from datetime import datetime

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from sqlalchemy import select

from packages.business_brain import AgentInput, get_agent_service
from packages.database.db import init_db, session_scope
from packages.database.models import (
    DiscordGuild,
    Message,
    ScheduledJob,
    Tenant,
)

load_dotenv()

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ALWAYS_LISTEN = os.getenv("OPERLY_DISCORD_ALWAYS_LISTEN", "false").lower() == "true"

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()
agent = get_agent_service()


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
    chunks = [text[index:index + 1900] for index in range(0, len(text), 1900)]
    chunks = chunks or ["Done."]

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

    images: list[str] = []
    for attachment in message.attachments[:4]:
        if (attachment.content_type or "").startswith("image/"):
            raw = await attachment.read()
            if len(raw) <= 8 * 1024 * 1024:
                images.append(base64.b64encode(raw).decode())

    try:
        async with message.channel.typing():
            result = await agent.run(
                AgentInput(
                    tenant_id=tenant_id,
                    principal_id=f"discord-channel:{message.channel.id}",
                    actor_name=message.author.display_name,
                    channel="discord",
                    conversation_id=f"discord:{message.channel.id}",
                    text=prompt or "Analyze the supplied attachment.",
                    images=images,
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
        print(f"OPERLY shared-agent error: {error}")
        await message.reply(
            "The AI request failed safely. Check the server logs.",
            mention_author=False,
        )


bot.run(TOKEN)
