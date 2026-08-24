import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.company_models import BusinessEventRecord


@dataclass(frozen=True, slots=True)
class BusinessEvent:
    id: str
    tenant_id: str
    event_type: str
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    source: str
    payload: dict[str, Any]
    correlation_id: str | None
    causation_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _event(row: BusinessEventRecord) -> BusinessEvent:
    return BusinessEvent(row.id, row.tenant_id, row.event_type, row.occurred_at, row.actor_type,
                         row.actor_id, row.source, json.loads(row.payload_json), row.correlation_id,
                         row.causation_id, json.loads(row.metadata_json))


async def append_event(db: AsyncSession, *, tenant_id: str, event_type: str,
                       payload: dict[str, Any] | None = None, actor_type: str = "system",
                       actor_id: str | None = None, source: str = "operly",
                       correlation_id: str | None = None, causation_id: str | None = None,
                       metadata: dict[str, Any] | None = None) -> BusinessEvent:
    # Serialization here rejects opaque objects before durable state is mutated.
    payload_json = json.dumps(payload or {}, sort_keys=True)
    metadata_json = json.dumps(metadata or {}, sort_keys=True)
    row = BusinessEventRecord(tenant_id=tenant_id, event_type=event_type, actor_type=actor_type,
                              actor_id=actor_id, source=source, payload_json=payload_json,
                              correlation_id=correlation_id, causation_id=causation_id,
                              metadata_json=metadata_json)
    db.add(row)
    await db.flush()
    event = _event(row)

    # Plugin/business events do not execute models inside the producer transaction.
    # They only mark matching durable Tasks pending; the existing scheduled-task
    # worker later re-enters the normal harness/firewall boundary.
    from packages.tasks.events import wake_workspace_tasks

    await wake_workspace_tasks(db, event)
    return event


async def query_events(db: AsyncSession, tenant_id: str, *, event_type: str | None = None,
                       since: datetime | None = None, until: datetime | None = None,
                       correlation_id: str | None = None, limit: int = 100) -> list[BusinessEvent]:
    query = select(BusinessEventRecord).where(BusinessEventRecord.tenant_id == tenant_id)
    if event_type: query = query.where(BusinessEventRecord.event_type == event_type)
    if since: query = query.where(BusinessEventRecord.occurred_at >= since)
    if until: query = query.where(BusinessEventRecord.occurred_at <= until)
    if correlation_id: query = query.where(BusinessEventRecord.correlation_id == correlation_id)
    rows = (await db.scalars(query.order_by(BusinessEventRecord.occurred_at.desc()).limit(limit))).all()
    return [_event(row) for row in rows]
