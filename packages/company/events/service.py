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
    # ``actor_*`` remains the legacy effective executor. New workflows should use
    # initiator/executor explicitly whenever attribution matters.
    initiator_type: str = field(default="system", kw_only=True)
    initiator_id: str | None = field(default=None, kw_only=True)
    executor_type: str = field(default="system", kw_only=True)
    executor_id: str | None = field(default=None, kw_only=True)
    delegation_chain: tuple[dict[str, Any], ...] = field(default=(), kw_only=True)


def _loads(value: str | None) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _actor_chain(
    metadata: dict[str, Any],
    *,
    actor_type: str,
    actor_id: str | None,
) -> tuple[str, str | None, str, str | None, tuple[dict[str, Any], ...]]:
    raw = metadata.get("actor_chain")
    raw = raw if isinstance(raw, dict) else {}
    initiator = raw.get("initiator")
    initiator = initiator if isinstance(initiator, dict) else {}
    executor = raw.get("executor")
    executor = executor if isinstance(executor, dict) else {}
    chain = raw.get("delegation")
    chain = chain if isinstance(chain, list) else []
    delegation = tuple(item for item in chain if isinstance(item, dict))
    return (
        str(initiator.get("type") or actor_type or "system"),
        str(initiator.get("id") or "").strip() or actor_id,
        str(executor.get("type") or actor_type or "system"),
        str(executor.get("id") or "").strip() or actor_id,
        delegation,
    )


def _event(row: BusinessEventRecord) -> BusinessEvent:
    metadata = _loads(row.metadata_json)
    initiator_type, initiator_id, executor_type, executor_id, delegation = _actor_chain(
        metadata,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
    )
    return BusinessEvent(
        id=row.id,
        tenant_id=row.tenant_id,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        source=row.source,
        payload=_loads(row.payload_json),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        metadata=metadata,
        scope_kind=row.scope_kind,
        owner_user_id=row.owner_user_id,
        initiator_type=initiator_type,
        initiator_id=initiator_id,
        executor_type=executor_type,
        executor_id=executor_id,
        delegation_chain=delegation,
    )


def _scope(*, tenant_id: str | None, owner_user_id: str | None) -> str:
    if bool(tenant_id) == bool(owner_user_id):
        raise ValueError("Event must belong to exactly one Personal or Workspace scope")
    return "workspace" if tenant_id else "personal"


def _normalize_actor_chain(
    *,
    actor_type: str,
    actor_id: str | None,
    initiator_type: str | None,
    initiator_id: str | None,
    executor_type: str | None,
    executor_id: str | None,
    delegation_chain: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> tuple[str, str | None, str, str | None, dict[str, Any]]:
    effective_executor_type = str(executor_type or actor_type or "system")
    effective_executor_id = str(executor_id or actor_id or "").strip() or None
    effective_initiator_type = str(initiator_type or actor_type or effective_executor_type)
    effective_initiator_id = (
        str(initiator_id or actor_id or effective_executor_id or "").strip() or None
    )
    delegation = [
        dict(item)
        for item in (delegation_chain or ())
        if isinstance(item, dict)
    ]
    chain = {
        "initiator": {
            "type": effective_initiator_type,
            "id": effective_initiator_id,
        },
        "executor": {
            "type": effective_executor_type,
            "id": effective_executor_id,
        },
        "delegation": delegation,
    }
    return (
        effective_initiator_type,
        effective_initiator_id,
        effective_executor_type,
        effective_executor_id,
        chain,
    )


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
    initiator_type: str | None = None,
    initiator_id: str | None = None,
    executor_type: str | None = None,
    executor_id: str | None = None,
    delegation_chain: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> BusinessEvent:
    scope_kind = _scope(tenant_id=tenant_id, owner_user_id=owner_user_id)
    (
        _initiator_type,
        _initiator_id,
        effective_executor_type,
        effective_executor_id,
        actor_chain,
    ) = _normalize_actor_chain(
        actor_type=actor_type,
        actor_id=actor_id,
        initiator_type=initiator_type,
        initiator_id=initiator_id,
        executor_type=executor_type,
        executor_id=executor_id,
        delegation_chain=delegation_chain,
    )
    event_metadata = dict(metadata or {})
    # Canonical provenance is application state. Callers may add unrelated metadata,
    # but cannot replace the actor chain assembled from explicit trusted arguments.
    event_metadata["actor_chain"] = actor_chain
    row = BusinessEventRecord(
        scope_kind=scope_kind,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        actor_type=effective_executor_type,
        actor_id=effective_executor_id,
        source=source,
        payload_json=json.dumps(payload or {}, sort_keys=True),
        correlation_id=correlation_id,
        causation_id=causation_id,
        metadata_json=json.dumps(event_metadata, sort_keys=True),
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
