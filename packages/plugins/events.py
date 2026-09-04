from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.kernel_models import KernelEventRecord
from packages.database.plugin_platform_models import DigitalEventOutboxRecord


class DigitalEventService:
    """Durable DigitalEvent outbox plus canonical Kernel semantic-event bridge.

    Delivery workers lease outbox rows; callers do not execute webhook/plugin side
    effects in the API transaction that produced the event. Every emitted Workspace
    DigitalEvent is also represented by a minimized Kernel event in the *same database
    transaction*, so Workflow can consume one semantic event plane without copying
    arbitrary external payloads into Kernel audit.
    """

    async def emit(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        event_type: str,
        source_kind: str,
        source_id: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        trigger_payload: Mapping[str, Any] | None = None,
        available_at: datetime | None = None,
        bridge_to_kernel: bool = True,
    ) -> DigitalEventOutboxRecord:
        event_type = str(event_type or "").strip().lower()
        if not event_type or len(event_type) > 160:
            raise ValueError("event_type is invalid; canonical semantic events are at most 160 characters")
        row = DigitalEventOutboxRecord(
            tenant_id=tenant_id,
            event_type=event_type,
            source_kind=source_kind,
            source_id=source_id,
            subject_type=subject_type,
            subject_id=subject_id,
            payload_json=json.dumps(dict(payload or {}), separators=(",", ":"), sort_keys=True),
            available_at=available_at or datetime.utcnow(),
        )
        db.add(row)
        await db.flush()

        if bridge_to_kernel:
            # Only trusted, explicitly selected trigger metadata crosses into Kernel
            # audit. The full DigitalEvent/webhook payload remains in the outbox or
            # artifact store and is never mirrored implicitly.
            safe_trigger = dict(trigger_payload or {})
            encoded_trigger = json.dumps(safe_trigger, separators=(",", ":"), sort_keys=True)
            if len(encoded_trigger.encode("utf-8")) > 8192:
                raise ValueError("trigger_payload exceeds the 8 KiB semantic-event projection limit")
            envelope = {
                "digital_event_id": row.id,
                "digital_source_kind": str(source_kind or "")[:80],
                "digital_source_id": str(source_id)[:160] if source_id else None,
                "digital_subject_type": str(subject_type)[:100] if subject_type else None,
                "digital_subject_id": str(subject_id)[:160] if subject_id else None,
                "trigger": safe_trigger,
            }
            external = str(source_kind or "").lower() in {"webhook", "plugin", "plugin_runtime", "external"}
            db.add(
                KernelEventRecord(
                    event_type=event_type,
                    scope_kind="workspace",
                    workspace_id=tenant_id,
                    owner_user_id=None,
                    principal_id=f"digital:{str(source_kind or 'system')[:120]}",
                    actor_type="external" if external else "system",
                    actor_id=str(source_id)[:160] if source_id else None,
                    initiator_principal_id=None,
                    executor_principal_id="operly:digital-event-bridge",
                    capability_id=None,
                    resource_type="digital_event",
                    resource_id=row.id,
                    payload_json=json.dumps(envelope, separators=(",", ":"), sort_keys=True),
                )
            )
            await db.flush()
        return row

    async def lease_batch(
        self,
        db: AsyncSession,
        *,
        worker_id: str,
        limit: int = 50,
        lease_seconds: int = 60,
    ) -> list[DigitalEventOutboxRecord]:
        now = datetime.utcnow()
        rows = (
            await db.scalars(
                select(DigitalEventOutboxRecord)
                .where(
                    DigitalEventOutboxRecord.available_at <= now,
                    or_(
                        DigitalEventOutboxRecord.status.in_(["pending", "retry"]),
                        and_(
                            DigitalEventOutboxRecord.status == "leased",
                            DigitalEventOutboxRecord.lease_expires_at.is_not(None),
                            DigitalEventOutboxRecord.lease_expires_at < now,
                        ),
                    ),
                )
                .order_by(DigitalEventOutboxRecord.available_at, DigitalEventOutboxRecord.created_at)
                .limit(max(1, min(int(limit), 200)))
                .with_for_update(skip_locked=True)
            )
        ).all()
        lease_until = now + timedelta(seconds=max(15, min(int(lease_seconds), 600)))
        for row in rows:
            row.status = "leased"
            row.locked_by = worker_id
            row.lease_expires_at = lease_until
            row.attempts += 1
        await db.flush()
        return list(rows)

    async def complete(self, db: AsyncSession, *, event_id: str, worker_id: str) -> None:
        row = await db.get(DigitalEventOutboxRecord, event_id)
        if row is None:
            raise LookupError("Digital event not found")
        if row.locked_by != worker_id or row.status != "leased":
            raise PermissionError("Digital event lease is not owned by this worker")
        row.status = "delivered"
        row.delivered_at = datetime.utcnow()
        row.locked_by = None
        row.lease_expires_at = None
        row.last_error = None
        await db.flush()

    async def fail(
        self,
        db: AsyncSession,
        *,
        event_id: str,
        worker_id: str,
        error: str,
        retry_after_seconds: int = 60,
        max_attempts: int = 10,
    ) -> None:
        row = await db.get(DigitalEventOutboxRecord, event_id)
        if row is None:
            raise LookupError("Digital event not found")
        if row.locked_by != worker_id or row.status != "leased":
            raise PermissionError("Digital event lease is not owned by this worker")
        row.last_error = str(error or "")[:4000]
        row.locked_by = None
        row.lease_expires_at = None
        if row.attempts >= max(1, int(max_attempts)):
            row.status = "dead_letter"
        else:
            row.status = "retry"
            row.available_at = datetime.utcnow() + timedelta(seconds=max(1, min(int(retry_after_seconds), 86400)))
        await db.flush()


digital_events = DigitalEventService()
