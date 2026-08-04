import base64
import os
from datetime import datetime

import aiohttp
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from sqlalchemy import desc, select

from packages.database.db import init_db, session_scope
from packages.database.models import DiscordGuild, Message, ScheduledJob, Tenant
from packages.harness.agent import AgentHarness
from packages.harness.context import ToolContext
from packages.harness.tools.register import build_registry

load_dotenv()

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OLLAMA_API_KEY = os.environ["OLLAMA_API_KEY"]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:cloud")
OLLAMA_URL = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat")

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()

registry = build_registry()
harness = AgentHarness(
    api_url=OLLAMA_URL,
    api_key=OLLAMA_API_KEY,
    model=OLLAMA_MODEL,
    registry=registry,
    max_steps=5,
)


async def ensure_tenant(guild, user=None) -> str:
    key = guild.id if guild else -int(user.id)

    async with session_scope() as db:
        row = await db.get(DiscordGuild, key)
        if row:
            return row.tenant_id

        name = guild.name if guild else f"DM:{user.id}"
        tenant = Tenant(name=name)
        db.add(tenant)
        await db.flush()
        db.add(DiscordGuild(guild_id=key, tenant_id=tenant.id, guild_name=name))
        return tenant.id


async def save_message(message, tenant_id: str, content: str, is_bot=False):
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


async def recent_context(tenant_id: str, channel_id: int, limit=30) -> str:
    async with session_scope() as db:
        rows = (
            await db.scalars(
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.channel_id == channel_id,
                )
                .order_by(desc(Message.created_at))
                .limit(limit)
            )
        ).all()

    rows.reverse()
    return "\n".join(f"{row.author_name}: {row.content}" for row in rows)


async def simple_chat(messages):
    headers = {
        "Authorization": f"Bearer {OLLAMA_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OLLAMA_URL, headers=headers, json=body) as response:
            data = await response.json()
            if response.status != 200:
                raise RuntimeError(data)
            return data["message"]["content"].strip()


async def intended_for_bot(message, tenant_id: str) -> bool:
    if message.guild is None or bot.user in message.mentions:
        return True

    if message.reference and isinstance(message.reference.resolved, discord.Message):
        if message.reference.resolved.author.id == bot.user.id:
            return True

    context = await recent_context(tenant_id, message.channel.id, 12)
    verdict = await simple_chat(
        [
            {
                "role": "system",
                "content": (
                    "Return only YES or NO. Decide whether the latest Discord message "
                    "is intended for OPERLY. Be conservative."
                ),
            },
            {"role": "user", "content": context},
        ]
    )
    return verdict.upper().startswith("YES")


async def run_scheduled_job(job_id: str):
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
            await channel.send(f"<@{user_id}> ⏰ {content}")

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


async def restore_pending_jobs():
    now = datetime.utcnow()

    async with session_scope() as db:
        jobs = (
            await db.scalars(
                select(ScheduledJob).where(ScheduledJob.status == "pending")
            )
        ).all()

    for job in jobs:
        run_at = max(job.run_at, now)
        scheduler.add_job(
            run_scheduled_job,
            "date",
            run_date=run_at,
            args=[job.id],
            id=job.id,
            replace_existing=True,
        )


async def send_chunks(message, text):
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or ["Done."]
    sent = await message.reply(chunks[0])
    for chunk in chunks[1:]:
        await message.channel.send(chunk)
    return sent


@bot.event
async def on_ready():
    await init_db()

    if not scheduler.running:
        scheduler.start()

    bot.operly_scheduler = scheduler
    bot.operly_run_scheduled_job = run_scheduled_job

    await restore_pending_jobs()
    print(f"OPERLY harness connected as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    tenant_id = await ensure_tenant(message.guild, message.author)

    prompt = message.content
    for mention in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
        prompt = prompt.replace(mention, "")
    prompt = prompt.strip()

    attachment_names = [a.filename for a in message.attachments]
    stored_content = message.content or "[attachment]"
    if attachment_names:
        stored_content += " [attachments: " + ", ".join(attachment_names) + "]"

    await save_message(message, tenant_id, stored_content)

    if not await intended_for_bot(message, tenant_id):
        return

    images = []
    for attachment in message.attachments:
        if (attachment.content_type or "").startswith("image/"):
            images.append(base64.b64encode(await attachment.read()).decode())

    context_text = await recent_context(tenant_id, message.channel.id, 30)

    user_content = (
        f"Tenant-scoped Discord context:\n{context_text}\n\n"
        f"Latest user request: {prompt or 'Analyze the supplied attachment.'}"
    )

    if images:
        image_results = []
        for index, image in enumerate(images, start=1):
            result = await simple_chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Analyze exactly one image. Follow the user request. "
                            "Do not guess unreadable information."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"{user_content}\nAttachment {index} of {len(images)}.\n"
                            f"Image base64 is supplied in the images field."
                        ),
                        "images": [image],
                    },
                ]
            )
            image_results.append(f"Attachment {index}:\n{result}")

        user_content += "\n\nImage analyses:\n" + "\n\n".join(image_results)

    tool_context = ToolContext(
        tenant_id=tenant_id,
        message=message,
        bot=bot,
    )

    system_prompt = (
        "You are OPERLY, a tenant-isolated business agent inside Discord. "
        "Use tools for actions. Never claim an action succeeded until a tool result "
        "confirms success. Never invent another tenant's information. "
        "Use create_reminder whenever asked to schedule a reminder. "
        "Use send_dm only when explicitly requested. "
        "Keep responses concise."
    )

    try:
        async with message.channel.typing():
            answer = await harness.run(
                context=tool_context,
                system_prompt=system_prompt,
                user_content=user_content,
            )

        sent = await send_chunks(message, answer)
        await save_message(sent, tenant_id, answer, is_bot=True)

    except Exception as error:
        print(f"Harness error: {error}")
        await message.reply("The requested action failed.")


bot.run(TOKEN)
