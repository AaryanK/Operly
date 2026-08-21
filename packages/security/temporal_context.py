from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.channel_models import ContextRecord
from packages.database.models import Tenant


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """Application-resolved time context shared by every Operly surface.

    Actor time is the default for phrases such as "today" and "tomorrow".
    Workspace time is also supplied so the model/tools can honor explicit business-
    timezone requests without asking each connector to implement its own clock logic.
    """

    now_utc: datetime
    actor_timezone: str
    workspace_timezone: str
    actor_now: datetime
    workspace_now: datetime

    def as_dict(self) -> dict[str, str]:
        return {
            "now_utc": self.now_utc.isoformat().replace("+00:00", "Z"),
            "actor_timezone": self.actor_timezone,
            "workspace_timezone": self.workspace_timezone,
            "actor_now": self.actor_now.isoformat(),
            "workspace_now": self.workspace_now.isoformat(),
            "relative_time_default": "actor",
        }

    def as_prompt(self) -> str:
        return "\n".join(
            [
                "CURRENT TIME CONTEXT (application-controlled):",
                f"Current instant UTC: {self.now_utc.isoformat().replace('+00:00', 'Z')}",
                f"Actor timezone: {self.actor_timezone}",
                f"Actor local date/time: {self.actor_now.isoformat()}",
                f"Workspace timezone: {self.workspace_timezone}",
                f"Workspace local date/time: {self.workspace_now.isoformat()}",
                "Interpret unqualified relative phrases such as today, tonight, tomorrow, and next Monday in the actor timezone.",
                "Use workspace time only when the user explicitly refers to business/workspace/local-office time or a capability contract requires it.",
            ]
        )


def validate_timezone(value: str | None, *, fallback: str = "UTC") -> str:
    candidate = str(value or "").strip() or fallback
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        candidate = fallback
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            candidate = "UTC"
    return candidate


async def user_timezone(db: AsyncSession, user_id: str | None) -> str | None:
    if not user_id:
        return None
    row = await db.scalar(
        select(ContextRecord)
        .where(
            ContextRecord.scope_type == "human",
            ContextRecord.visibility == "private",
            ContextRecord.owner_user_id == user_id,
            ContextRecord.tenant_id.is_(None),
            ContextRecord.kind == "timezone",
        )
        .order_by(ContextRecord.updated_at.desc())
        .limit(1)
    )
    return validate_timezone(row.content) if row else None


async def set_user_timezone(db: AsyncSession, *, user_id: str, timezone_name: str) -> str:
    value = validate_timezone(timezone_name, fallback="")
    if not value:
        raise ValueError("Invalid IANA timezone")
    row = await db.scalar(
        select(ContextRecord).where(
            ContextRecord.scope_type == "human",
            ContextRecord.visibility == "private",
            ContextRecord.owner_user_id == user_id,
            ContextRecord.tenant_id.is_(None),
            ContextRecord.kind == "timezone",
        )
    )
    if row is None:
        row = ContextRecord(
            scope_type="human",
            visibility="private",
            owner_user_id=user_id,
            tenant_id=None,
            kind="timezone",
            content=value,
            metadata_json='{"source":"personal_preference"}',
        )
        db.add(row)
    else:
        row.content = value
    await db.flush()
    return value


async def resolve_temporal_context(
    db: AsyncSession,
    *,
    user_id: str | None,
    tenant_id: str,
) -> TemporalContext:
    tenant = await db.get(Tenant, tenant_id)
    workspace_tz = validate_timezone(tenant.timezone if tenant else "UTC")
    actor_tz = await user_timezone(db, user_id) or workspace_tz
    now = datetime.now(timezone.utc)
    return TemporalContext(
        now_utc=now,
        actor_timezone=actor_tz,
        workspace_timezone=workspace_tz,
        actor_now=now.astimezone(ZoneInfo(actor_tz)),
        workspace_now=now.astimezone(ZoneInfo(workspace_tz)),
    )
