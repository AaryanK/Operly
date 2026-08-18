import asyncio
import base64
import json
import os
import re
from datetime import datetime, timedelta

import aiohttp
import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from discord import app_commands
from dotenv import load_dotenv
from sqlalchemy import desc, select

from packages.database.db import init_db, session_scope
from packages.database.models import Approval, DiscordGuild, Memory, Message, Task, Tenant
from packages.database.connector_models import TenantConnector
from packages.company.events import append_event

load_dotenv()

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
OLLAMA_API_KEY = os.environ["OLLAMA_API_KEY"]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:cloud")
OLLAMA_URL = os.getenv("OLLAMA_URL", "https://ollama.com/api/chat")
OWNER_IDS = {int(x) for x in os.getenv("BOT_OWNER_IDS", "").split(",") if x.strip()}

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


scheduler = AsyncIOScheduler()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

HEADERS = {
    "Authorization": f"Bearer {OLLAMA_API_KEY}",
    "Content-Type": "application/json",
}


async def ollama(messages, images=None):
    body = {"model": OLLAMA_MODEL, "messages": messages, "stream": False}
    if images:
        body["messages"][-1]["images"] = images
    async with aiohttp.ClientSession() as session:
        async with session.post(OLLAMA_URL, headers=HEADERS, json=body) as r:
            data = await r.json()
            if r.status != 200:
                raise RuntimeError(data)
            return data["message"]["content"].strip()


async def ensure_tenant(
    guild: discord.Guild | None,
    user: discord.abc.User | None = None,
) -> str:
    # One isolated tenant per Discord server.
    if guild is not None:
        async with session_scope() as db:
            row = await db.get(DiscordGuild, guild.id)
            if row:
                return row.tenant_id

            tenant = Tenant(name=guild.name)
            db.add(tenant)
            await db.flush()
            db.add(
                DiscordGuild(
                    guild_id=guild.id,
                    tenant_id=tenant.id,
                    guild_name=guild.name,
                )
            )
            db.add(TenantConnector(tenant_id=tenant.id,connector_type="messaging",provider="discord",display_name=guild.name,status="connected",enabled=True,provider_account_id=str(guild.id),granted_scopes_json='["messages.read","messages.send"]',health_status="healthy"))
            return tenant.id

    # DMs must not share a single "dm" tenant.
    if user is None:
        raise RuntimeError("A user is required for DM tenant resolution")

    dm_guild_id = -int(user.id)

    async with session_scope() as db:
        row = await db.get(DiscordGuild, dm_guild_id)
        if row:
            return row.tenant_id

        tenant = Tenant(name=f"DM:{user.id}")
        db.add(tenant)
        await db.flush()
        db.add(
            DiscordGuild(
                guild_id=dm_guild_id,
                tenant_id=tenant.id,
                guild_name=f"DM:{user.id}",
            )
        )
        return tenant.id


async def save_message(message: discord.Message, tenant_id: str, content: str, is_bot=False):
    async with session_scope() as db:
        exists = await db.scalar(select(Message).where(Message.message_id == message.id))
        if exists:
            return
        db.add(Message(
            tenant_id=tenant_id,
            guild_id=message.guild.id if message.guild else None,
            channel_id=message.channel.id,
            message_id=message.id,
            author_id=message.author.id,
            author_name=message.author.display_name,
            content=content,
            is_bot=is_bot,
        ))
        await append_event(db,tenant_id=tenant_id,event_type="customer.message.sent" if is_bot else "customer.message.received",payload={"provider":"discord","message_id":str(message.id),"channel_id":str(message.channel.id)},source="discord")


async def recent_context(tenant_id: str, channel_id: int, limit: int = 30) -> str:
    async with session_scope() as db:
        rows = (await db.scalars(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.channel_id == channel_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )).all()
    rows.reverse()
    return "\n".join(f"{r.author_name}: {r.content}" for r in rows)


async def intended_for_bot(message: discord.Message, tenant_id: str) -> bool:
    if message.guild is None:
        return True
    if bot.user in message.mentions:
        return True
    if message.reference and isinstance(message.reference.resolved, discord.Message):
        if message.reference.resolved.author.id == bot.user.id:
            return True

    context = await recent_context(tenant_id, message.channel.id, 12)
    verdict = await ollama([
        {"role": "system", "content":
         "Return only YES or NO. Decide whether the latest Discord message is directed to OPERLY. "
         "Be conservative. Ordinary human conversation is NO."},
        {"role": "user", "content": context},
    ])
    return verdict.upper().startswith("YES")


async def send_chunks(message: discord.Message, text: str):
    chunks = [text[i:i+1900] for i in range(0, len(text), 1900)] or ["No response."]
    sent = await message.reply(chunks[0])
    for chunk in chunks[1:]:
        await message.channel.send(chunk)
    return sent


def parse_duration(text: str):
    m = re.fullmatch(r"(\d+)(m|h|d)", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


@bot.event
async def on_ready():
    await init_db()
    if not scheduler.running:
        scheduler.start()
    await tree.sync()
    print(f"OPERLY connected as {bot.user}")


@bot.event
async def on_guild_join(guild):
    await ensure_tenant(guild)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    tenant_id = await ensure_tenant(message.guild, message.author)

    prompt = message.content
    for mention in (
        f"<@{bot.user.id}>",
        f"<@!{bot.user.id}>",
    ):
        prompt = prompt.replace(mention, "")
    prompt = prompt.strip()

    attachment_note = ""
    if message.attachments:
        attachment_note = " [attachments: " + ", ".join(
            attachment.filename for attachment in message.attachments
        ) + "]"

    await save_message(
        message,
        tenant_id,
        (message.content or "[attachment]") + attachment_note,
    )

    if not await intended_for_bot(message, tenant_id):
        return

    images = []
    image_names = []

    for attachment in message.attachments:
        content_type = attachment.content_type or ""
        if content_type.startswith("image/"):
            images.append(
                base64.b64encode(await attachment.read()).decode("utf-8")
            )
            image_names.append(attachment.filename)

    context = await recent_context(tenant_id, message.channel.id, 30)

    try:
        async with message.channel.typing():
            if images:
                # Analyze each image independently so none are ignored or blended.
                per_image_results = []

                for index, image in enumerate(images, start=1):
                    filename = image_names[index - 1]

                    result = await ollama(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "You are OPERLY, a tenant-isolated Discord business agent. "
                                    "Analyze exactly one supplied image carefully. "
                                    "Follow the user's request without assuming a specific document type. "
                                    "Do not invent unreadable details. "
                                    "Use only the supplied tenant-scoped context."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Tenant-scoped channel context:{context}"
                                    f"User request: {prompt or 'Analyze the attachment.'}"
                                    f"Current attachment: {index} of {len(images)} "
                                    f"({filename})."
                                ),
                            },
                        ],
                        images=[image],
                    )

                    per_image_results.append(
                        f"**Attachment {index}: {filename}**{result}"
                    )

                # Combine results without re-sending images.
                answer = await ollama(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Combine the independent attachment analyses into one clear response. "
                                "Preserve which result belongs to which attachment. "
                                "Do not add unsupported facts. Be concise."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Original request: {prompt or 'Analyze the attachments.'}"+ "".join(per_image_results)
                            ),
                        },
                    ]
                )
            else:
                answer = await ollama(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are OPERLY, a tenant-isolated Discord business agent. "
                                "Use only the supplied server context. "
                                "Never infer or expose information from another business. "
                                "Be concise. Do not perform sensitive actions without approval."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Tenant-scoped channel context:{context}"
                                f"User request: {prompt}"
                                "Answer the latest message."
                            ),
                        },
                    ]
                )

        sent = await send_chunks(message, answer)
        await save_message(sent, tenant_id, answer, is_bot=True)

    except Exception as error:
        print(f"Message processing failed: {error}")
        await message.reply("Request failed.")


@tree.command(name="remember", description="Store an important server fact")
async def remember(interaction: discord.Interaction, fact: str):
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    async with session_scope() as db:
        db.add(Memory(
            tenant_id=tenant_id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            kind="fact",
            content=fact,
        ))
    await interaction.response.send_message("Remembered.", ephemeral=True)


@tree.command(name="search", description="Search this business server's stored messages")
async def search_messages(interaction: discord.Interaction, query: str):
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    pattern = f"%{query}%"
    async with session_scope() as db:
        rows = (await db.scalars(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.content.ilike(pattern))
            .order_by(desc(Message.created_at))
            .limit(10)
        )).all()
    text = "\n".join(f"• {r.author_name}: {r.content[:250]}" for r in rows) or "No matches."
    await interaction.response.send_message(text[:1900], ephemeral=True)


@tree.command(name="task", description="Create a tenant-scoped task")
async def create_task(interaction: discord.Interaction, title: str, due: str | None = None):
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    due_at = None
    if due:
        delta = parse_duration(due)
        if not delta:
            await interaction.response.send_message("Use 10m, 2h, or 3d.", ephemeral=True)
            return
        due_at = datetime.utcnow() + delta

    async with session_scope() as db:
        task = Task(
            tenant_id=tenant_id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            creator_id=interaction.user.id,
            title=title,
            due_at=due_at,
        )
        db.add(task)
        await db.flush()
        task_id = task.id

    if due_at:
        async def reminder():
            channel = bot.get_channel(interaction.channel_id)
            if channel:
                await channel.send(f"<@{interaction.user.id}> ⏰ Task due: {title}")
        scheduler.add_job(reminder, "date", run_date=due_at, id=task_id)

    await interaction.response.send_message(f"Task created: **{title}**")


@tree.command(name="tasks", description="List open tasks for this business")
async def list_tasks(interaction: discord.Interaction):
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    async with session_scope() as db:
        rows = (await db.scalars(
            select(Task)
            .where(Task.tenant_id == tenant_id, Task.status == "open")
            .order_by(Task.created_at)
            .limit(20)
        )).all()
    text = "\n".join(f"• `{r.id[:8]}` {r.title}" for r in rows) or "No open tasks."
    await interaction.response.send_message(text)


@tree.command(name="summary", description="Summarize recent tenant-scoped channel activity")
async def summary(interaction: discord.Interaction):
    await interaction.response.defer()
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    context = await recent_context(tenant_id, interaction.channel_id, 100)
    result = await ollama([
        {"role": "system", "content":
         "Summarize decisions, action items, blockers, unanswered questions, and important facts. "
         "Use only supplied context."},
        {"role": "user", "content": context or "No messages."},
    ])
    await interaction.followup.send(result[:1900])


@tree.command(name="request_action", description="Request approval for a sensitive action")
async def request_action(interaction: discord.Interaction, action: str, details: str):
    tenant_id = await ensure_tenant(interaction.guild, interaction.user)
    async with session_scope() as db:
        row = Approval(
            tenant_id=tenant_id,
            guild_id=interaction.guild_id,
            requester_id=interaction.user.id,
            action=action,
            payload_json=json.dumps({"details": details}),
        )
        db.add(row)
        await db.flush()
        approval_id = row.id
    await interaction.response.send_message(
        f"Approval requested: `{approval_id[:8]}` — **{action}**"
    )


bot.run(TOKEN)
