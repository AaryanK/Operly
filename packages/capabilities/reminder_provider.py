from datetime import datetime, timedelta, timezone

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
            "Schedule a reminder back to the current Discord conversation. For phrases like tomorrow at 9, call runtime.context first and pass run_at as an ISO-8601 timestamp with the actor's timezone offset. For pure durations such as in 20 minutes, use value/unit.",
            {
                "type": "object",
                "properties": {
                    "value": {"type": "integer", "minimum": 1},
                    "unit": {
                        "type": "string",
                        "enum": ["seconds", "minutes", "hours", "days"],
                    },
                    "run_at": {
                        "type": "string",
                        "description": "Absolute ISO-8601 timestamp including UTC offset, normally resolved from runtime.context actor time.",
                    },
                    "content": {"type": "string"},
                    "delivery": {
                        "type": "string",
                        "enum": ["channel", "dm"],
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
                "anyOf": [
                    {"required": ["value", "unit"]},
                    {"required": ["run_at"]},
                ],
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

        now = datetime.now(timezone.utc)
        if arguments.get("run_at"):
            try:
                parsed = datetime.fromisoformat(str(arguments["run_at"]).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return CapabilityResult(False, False, {"reason": "run_at_requires_timezone_offset"})
                run_at_aware = parsed.astimezone(timezone.utc)
            except ValueError:
                return CapabilityResult(False, False, {"reason": "invalid_run_at"})
        else:
            if arguments.get("value") is None or arguments.get("unit") is None:
                return CapabilityResult(False, False, {"reason": "value/unit or run_at is required"})
            value = int(arguments["value"])
            unit = str(arguments["unit"]).lower()
            multipliers = {
                "seconds": 1,
                "minutes": 60,
                "hours": 3600,
                "days": 86400,
            }
            seconds = value * multipliers[unit]
            if seconds <= 0 or seconds > 365 * 86400:
                return CapabilityResult(False, False, {"reason": "Reminder duration must be within one year"})
            run_at_aware = now + timedelta(seconds=seconds)

        if run_at_aware <= now or run_at_aware > now + timedelta(days=365):
            return CapabilityResult(False, False, {"reason": "Reminder time must be in the next year"})

        content = str(arguments["content"]).strip()[:1000]
        if not content:
            return CapabilityResult(False, False, {"reason": "Reminder content is required"})

        # Existing persistence uses naive UTC. The offset-aware timestamp is normalized
        # at this single boundary so all adapters and models share the same semantics.
        run_at_utc = run_at_aware.replace(tzinfo=None)
        row = ScheduledJob(
            tenant_id=context.tenant_id,
            guild_id=int(guild_id) if guild_id is not None else None,
            channel_id=int(channel_id),
            user_id=int(user_id),
            job_type="reminder",
            content=content,
            delivery=str(arguments.get("delivery") or "channel"),
            run_at=run_at_utc,
            status="pending",
        )
        context.db.add(row)
        await context.db.flush()
        temporal = invocation.get("temporal_context") or metadata.get("temporal_context") or {}
        return CapabilityResult(
            True,
            True,
            {
                "job_id": row.id,
                "run_at_utc": row.run_at.isoformat() + "Z",
                "actor_timezone": temporal.get("actor_timezone"),
                "workspace_timezone": temporal.get("workspace_timezone"),
            },
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
