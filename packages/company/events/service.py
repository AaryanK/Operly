import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.company_models import BusinessEventRecord


@dataclass(frozen=True, slots=True)
class BusinessEvent:
    # Keep the legacy workspace-event positional shape stable. Before 0040 every
    # BusinessEvent was workspace-owned, so defaulting legacy callers to workspace
    # is backwards-compatible while Personal events must opt into their owner scope.
    id: str
    tenant_id: str | None
    event_type: str
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    source: str
    payload: dict[str, Any]
    correlation_id: str | None
    causation_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    scope_kind: str = field(default="workspace", kw_only=True)
    owner_user_id: str | None = field(default=None, kw_only=True)


def _event(row: BusinessEventRecord) -> BusinessEvent:
    return BusinessEvent(
        id=row.id,
        tenant_id=row.tenant_id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        source=row.source,
        payload=json.loads(row.payload_json),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        metadata=json.loads(row.metadata_json),
        scope_kind=row.scope_kind,
        owner_user_id=row.owner_user_id,
    )


def _scope(*, tenant_id: str | None, owner_user_id: str | None) -> str:
    if bool(tenant_id) == bool(owner_user_id):
        raise ValueError("Event must belong to exactly one Personal or Workspace scope")
    return "workspace" if tenant_id else "personal"


async def append_event(
    db: AsyncSession,
    *,
    tenant_id: str | None,
    event_type: str,
    owner_user_id: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    source: str = "operly",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> BusinessEvent:
    payload_json = json.dumps(payload or {}, sort_keys=True)
    metadata_json = json.dumps(metadata or {}, sort_keys=True)
    scope_kind = _scope(tenant_id=tenant_id, owner_user_id=owner_user_id)
    row = BusinessEventRecord(
        scope_kind=scope_kind,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        source=source,
        payload_json=payload_json,
        correlation_id=correlation_id,
        causation_id=causation_id,
        metadata_json=metadata_json,
    )
    db.add(row)
    await db.flush()
    event = _event(row)

    # Personal events never inherit a workspace merely because the owner belongs to
    # one. Only explicitly workspace-owned events participate in workspace wakeups.
    if event.scope_kind == "workspace":
        from packages.tasks.events import wake_workspace_tasks

        await wake_workspace_tasks(db, event)
    return event


async def query_events(
    db: AsyncSession,
    tenant_id: str,
    *,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    correlation_id: str | None = None,
    limit: int = 100,
) -> list[BusinessEvent]:
    query = select(BusinessEventRecord).where(
        BusinessEventRecord.scope_kind == "workspace",
        BusinessEventRecord.tenant_id == tenant_id,
    )
    if event_type:
        query = query.where(BusinessEventRecord.event_type == event_type)
    if since:
        query = query.where(BusinessEventRecord.occurred_at >= since)
    if until:
        query = query.where(BusinessEventRecord.occurred_at <= until)
    if correlation_id:
        query = query.where(BusinessEventRecord.correlation_id == correlation_id)
    rows = (
        await db.scalars(query.order_by(BusinessEventRecord.occurred_at.desc()).limit(limit))
    ).all()
    return [_event(row) for row in rows]


async def query_personal_events(
    db: AsyncSession,
    owner_user_id: str,
    *,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    correlation_id: str | None = None,
    limit: int = 100,
) -> list[BusinessEvent]:
    query = select(BusinessEventRecord).where(
        BusinessEventRecord.scope_kind == "personal",
        BusinessEventRecord.owner_user_id == owner_user_id,
    )
    if event_type:
        query = query.where(BusinessEventRecord.event_type == event_type)
    if since:
        query = query.where(BusinessEventRecord.occurred_at >= since)
    if until:
        query = query.where(BusinessEventRecord.occurred_at <= until)
    if correlation_id:
        query = query.where(BusinessEventRecord.correlation_id == correlation_id)
    rows = (
        await db.scalars(query.order_by(BusinessEventRecord.occurred_at.desc()).limit(limit))
    ).all()
    return [_event(row) for row in rows]
