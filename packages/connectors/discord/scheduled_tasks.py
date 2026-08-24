from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

import discord

from packages.capabilities.task_provider import (
    TASK_NO_CHANGE,
    load_task_payload,
    next_task_run,
    scheduled_task_prompt,
)
from packages.channels.envelope import ChannelEnvelope
from packages.channels.service import ChannelService
from packages.database.db import session_scope
from packages.database.models import ScheduledJob, Task


Runner = Callable[[str], Awaitable[None]]


def _chunks(text: str, limit: int = 1900) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    chunks: list[str] = []
    while value:
        if len(value) <= limit:
            chunks.append(value)
            break
        split_at = value.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = value.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(value[:split_at].strip())
        value = value[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


async def _deliver(bot: discord.Client, job: ScheduledJob, message: str) -> None:
    chunks = _chunks(message)
    if not chunks:
        return
    if job.delivery == "dm":
        user = bot.get_user(job.user_id) or await bot.fetch_user(job.user_id)
        for chunk in chunks:
            await user.send(chunk)
        return
    channel = bot.get_channel(job.channel_id) or await bot.fetch_channel(job.channel_id)
    for chunk in chunks:
        await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())


async def run_harness_task_job(
    *,
    bot: discord.Client,
    scheduler,
    job_id: str,
    runner: Runner,
) -> None:
    """Wake an existing Task by re-entering the normal Discord ChannelService path."""
    async with session_scope() as db:
        job = await db.get(ScheduledJob, job_id)
        if job is None or job.status != "pending" or not job.task_id:
            return
        task = await db.get(Task, job.task_id)
        if task is None:
            job.status = "cancelled"
            return
        if task.status != "open":
            job.status = "paused" if task.status == "paused" else "cancelled"
            return
        payload = load_task_payload(job.content)
        origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
        if origin.get("provider") != "discord":
            job.status = "failed"
            return
        job.status = "running"
        current_run = job.run_at
        execution_prompt = (
            scheduled_task_prompt(task, payload)
            + "\n\nDELIVERY CONTRACT:\n"
            + "Return the task's final user-facing result as your assistant response. The Task delivery layer will send that response to the configured Discord destination exactly once. "
            + "Do not call Discord send-message/send-DM capabilities merely to deliver this final result. Only use a messaging capability if the task objective explicitly requires an additional side effect distinct from its configured delivery."
        )
        # Each durable task gets its own logical ChannelService conversation. The real
        # Discord destination stays in metadata/job delivery fields, so scheduled
        # system prompts and task-run history never pollute the human channel thread.
        execution_conversation_id = f"task:{task.id}"
        envelope = ChannelEnvelope(
            provider="discord",
            external_user_id=str(origin.get("external_user_id") or job.user_id),
            external_space_id=(
                str(origin.get("external_space_id"))
                if origin.get("external_space_id") is not None
                else None
            ),
            external_conversation_id=execution_conversation_id,
            actor_name=str(origin.get("actor_name") or "Operly user")[:200],
            text=execution_prompt,
            space_name=None,
            is_direct=bool(origin.get("is_direct")),
            metadata={
                "discord_guild_id": task.guild_id,
                "discord_channel_id": job.channel_id,
                "discord_user_id": job.user_id,
                "task_origin_conversation_id": str(origin.get("external_conversation_id") or job.channel_id),
                "scheduled_task_id": task.id,
                "scheduled_job_id": job.id,
                "scheduled_run": True,
            },
        )

    try:
        response = await ChannelService.handle(envelope)
        output = str(response.message or "").strip()
        if output and output != TASK_NO_CHANGE:
            async with session_scope() as db:
                delivery_job = await db.get(ScheduledJob, job_id)
                if delivery_job is not None:
                    await _deliver(bot, delivery_job, output)

        next_run = next_task_run(payload, current_run)
        async with session_scope() as db:
            job = await db.get(ScheduledJob, job_id)
            if job is None:
                return
            task = await db.get(Task, job.task_id) if job.task_id else None
            if task is None:
                job.status = "cancelled"
                return
            if task.status != "open":
                job.status = "paused" if task.status == "paused" else "cancelled"
                return
            if next_run is None:
                job.status = "completed"
                task.status = "completed"
                task.due_at = None
                return
            job.run_at = next_run
            job.status = "pending"
            task.due_at = next_run

        scheduler.add_job(
            runner,
            "date",
            run_date=max(next_run, datetime.utcnow()),
            args=[job_id],
            id=job_id,
            replace_existing=True,
        )
    except Exception:
        async with session_scope() as db:
            job = await db.get(ScheduledJob, job_id)
            if job is not None:
                job.status = "failed"
        raise
