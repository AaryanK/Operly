from datetime import datetime, timedelta

from sqlalchemy import select

from packages.capabilities.contracts import ApprovalPolicy, CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.database.models import ScheduledJob


class ReminderProvider(BaseProvider):
    name = "operly_reminders"
    capabilities = (
        CapabilityDefinition(
            "reminders.create",
            "reminders_create",
            "Schedule a reminder back to the current Discord conversation. Only available from Discord.",
            {
                "type": "object",
                "properties": {
                    "value": {"type": "integer", "minimum": 1},
                    "unit": {
                        "type": "string",
                        "enum": ["seconds", "minutes", "hours", "days"],
                    },
                    "content": {"type": "string"},
                    "delivery": {
                        "type": "string",
                        "enum": ["channel", "dm"],
                    },
                },
                "required": ["value", "unit", "content"],
                "additionalProperties": False,
            },
            {"type": "object"},
            risk_level="low",
            permissions=("reminders:write",),
            approval_policy=ApprovalPolicy.AUTO,
            reversible=True,
        ),
    )

    async def execute(self, context, capability_name, arguments):
        invocation = context.invocation or {}
        if invocation.get("channel") != "discord":
            return CapabilityResult(
                False,
                False,
                {"reason": "Direct reminders currently require Discord"},
            )

        metadata = invocation.get("metadata") or {}
        channel_id = metadata.get("discord_channel_id")
        user_id = metadata.get("discord_user_id")
        guild_id = metadata.get("discord_guild_id")
        if channel_id is None or user_id is None:
            return CapabilityResult(
                False,
                False,
                {"reason": "Discord delivery metadata is missing"},
            )

        value = int(arguments["value"])
        unit = str(arguments["unit"]).lower()
        content = str(arguments["content"]).strip()[:1000]
        multipliers = {
            "seconds": 1,
            "minutes": 60,
            "hours": 3600,
            "days": 86400,
        }
        seconds = value * multipliers[unit]
        if seconds <= 0 or seconds > 365 * 86400:
            return CapabilityResult(
                False,
                False,
                {"reason": "Reminder duration must be within one year"},
            )
        if not content:
            return CapabilityResult(False, False, {"reason": "Reminder content is required"})

        row = ScheduledJob(
            tenant_id=context.tenant_id,
            guild_id=int(guild_id) if guild_id is not None else None,
            channel_id=int(channel_id),
            user_id=int(user_id),
            job_type="reminder",
            content=content,
            delivery=str(arguments.get("delivery") or "channel"),
            run_at=datetime.utcnow() + timedelta(seconds=seconds),
            status="pending",
        )
        context.db.add(row)
        await context.db.flush()
        return CapabilityResult(
            True,
            True,
            {"job_id": row.id, "run_at_utc": row.run_at.isoformat() + "Z"},
            row.id,
        )

    async def verify(self, context, capability_name, arguments, result):
        row = None
        if result.external_reference:
            row = await context.db.scalar(
                select(ScheduledJob).where(
                    ScheduledJob.id == result.external_reference,
                    ScheduledJob.tenant_id == context.tenant_id,
                )
            )
        valid = row is not None and row.status == "pending"
        return CapabilityResult(
            valid,
            result.changed,
            {"scheduled": valid, **result.evidence},
            result.external_reference,
        )
