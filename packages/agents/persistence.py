from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from packages.database.artifact_models import AgentRunEventRecord, AgentRunRecord
from packages.database.db import session_scope


def _scope(metadata: dict[str, Any]) -> tuple[str, str, str | None, str | None]:
    tenant_id = str(metadata.get("tenant_id") or "").strip() or None
    user_id = str(metadata.get("user_id") or metadata.get("owner_user_id") or "").strip() or None
    if tenant_id:
        return "workspace", tenant_id, tenant_id, None
    if not user_id:
        raise ValueError("Durable agent run requires tenant_id or user_id")
    return "personal", f"personal:{user_id}", None, user_id


def _conversation_id(metadata: dict[str, Any]) -> str | None:
    return str(metadata.get("_conversation_id") or metadata.get("conversation_id") or "").strip()[:255] or None


def _json(value: Any, limit: int = 250_000) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:limit]


def _loaded(row: AgentRunRecord) -> dict[str, Any]:
    try:
        checkpoint = json.loads(row.checkpoint_json or "{}")
    except json.JSONDecodeError:
        checkpoint = {}
    return {
        "run_id": row.id,
        "state": row.state,
        "objective": row.objective,
        "checkpoint": checkpoint,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


async def checkpoint_agent_run(
    *,
    runtime_run_id: str,
    objective: str,
    metadata: dict[str, Any],
    state: dict[str, Any] | None,
    event_type: str,
    lifecycle_state: str = "running",
    payload: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Persist one resumable checkpoint without changing the agent's authority."""

    try:
        kind, scope_id, tenant_id, owner_user_id = _scope(metadata)
    except ValueError:
        return
    run_id = str(runtime_run_id)[:120]
    now = datetime.utcnow()
    async with session_scope() as db:
        row = await db.get(AgentRunRecord, run_id)
        if row is None:
            row = AgentRunRecord(
                id=run_id,
                scope_kind=kind,
                scope_id=scope_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                actor_id=(str(metadata.get("user_id") or "")[:120] or None),
                surface=str(metadata.get("surface") or "unknown")[:60],
                channel=str(metadata.get("channel") or "operly")[:60],
                conversation_id=_conversation_id(metadata),
                workflow_job_id=(str(metadata.get("workflow_job_id") or metadata.get("task_execution_id") or "")[:120] or None),
                objective=str(objective or "")[:50_000],
                state=lifecycle_state,
                started_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.flush()
        elif row.scope_kind != kind or row.scope_id != scope_id:
            raise PermissionError("Agent run scope mismatch")

        compact = dict(state or {})
        row.objective = str(objective or row.objective or "")[:50_000]
        row.state = str(lifecycle_state or "running")[:32]
        row.plan_json = _json(compact.get("plan") or {})
        row.checkpoint_json = _json(compact)
        row.artifact_refs_json = _json(compact.get("artifact_refs") or [], 50_000)
        row.pending_approval_ids_json = _json(compact.get("pending_approval_ids") or [], 50_000)
        row.last_error = (str(error)[:20_000] if error else None)
        row.updated_at = now
        if lifecycle_state in {"completed", "failed", "cancelled"}:
            row.completed_at = now
        else:
            row.completed_at = None

        sequence = int(
            await db.scalar(
                select(func.coalesce(func.max(AgentRunEventRecord.sequence), 0)).where(
                    AgentRunEventRecord.run_id == run_id
                )
            )
            or 0
        ) + 1
        db.add(
            AgentRunEventRecord(
                run_id=run_id,
                sequence=sequence,
                event_type=str(event_type or "checkpoint")[:80],
                payload_json=_json(payload or compact),
            )
        )


async def load_agent_run(
    runtime_run_id: str,
    *,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Load a checkpoint only when the caller still owns the exact same scope."""

    try:
        kind, scope_id, _, _ = _scope(metadata)
    except ValueError:
        return None
    async with session_scope() as db:
        row = await db.get(AgentRunRecord, str(runtime_run_id)[:120])
        if row is None:
            return None
        if row.scope_kind != kind or row.scope_id != scope_id:
            raise PermissionError("Agent run is outside the current execution scope")
        return _loaded(row)


async def find_resumable_agent_run(
    *,
    objective: str,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    """Find only an unfinished run for the exact same scoped conversation/objective.

    This supports worker-crash recovery without conflating a new user message or a
    later recurrence with the old run. Explicit runtime_run_id callers may still
    resume a known failed run via ``load_agent_run``.
    """

    conversation_id = _conversation_id(metadata)
    if not conversation_id:
        return None
    try:
        kind, scope_id, _, _ = _scope(metadata)
    except ValueError:
        return None
    normalized_objective = str(objective or "")[:50_000]
    if not normalized_objective:
        return None
    async with session_scope() as db:
        row = await db.scalar(
            select(AgentRunRecord)
            .where(
                AgentRunRecord.scope_kind == kind,
                AgentRunRecord.scope_id == scope_id,
                AgentRunRecord.conversation_id == conversation_id,
                AgentRunRecord.objective == normalized_objective,
                AgentRunRecord.state.in_(["running", "waiting_approval"]),
                AgentRunRecord.completed_at.is_(None),
            )
            .order_by(AgentRunRecord.updated_at.desc())
            .limit(1)
        )
        return _loaded(row) if row is not None else None
