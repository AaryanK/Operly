from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.digital_job_models import DigitalPlatformJobRecord


class DigitalPlatformJobService:
    """Durable lease-based queue intended for the one Operly infrastructure Worker."""

    async def enqueue(
        self,
        db: AsyncSession,
        *,
        job_type: str,
        subject_kind: str,
        subject_id: str,
        idempotency_key: str,
        tenant_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        priority: int = 100,
        max_attempts: int = 5,
        available_at: datetime | None = None,
        created_by: str | None = None,
    ) -> DigitalPlatformJobRecord:
        clean_key = str(idempotency_key or "").strip()
        if not clean_key:
            raise ValueError("Platform job idempotency_key is required")
        existing = await db.scalar(
            select(DigitalPlatformJobRecord).where(
                DigitalPlatformJobRecord.tenant_id == tenant_id,
                DigitalPlatformJobRecord.idempotency_key == clean_key,
            )
        )
        if existing is not None:
            return existing
        row = DigitalPlatformJobRecord(
            tenant_id=tenant_id,
            job_type=str(job_type or "").strip()[:100],
            subject_kind=str(subject_kind or "").strip()[:60],
            subject_id=str(subject_id or "").strip()[:160],
            state="queued",
            priority=max(0, min(int(priority), 1000)),
            payload_json=json.dumps(dict(payload or {}), separators=(",", ":"), sort_keys=True),
            result_json="{}",
            attempt=0,
            max_attempts=max(1, min(int(max_attempts), 20)),
            idempotency_key=clean_key[:200],
            available_at=available_at or datetime.utcnow(),
            created_by=created_by,
        )
        if not row.job_type or not row.subject_kind or not row.subject_id:
            raise ValueError("Platform job type and subject are required")
        db.add(row)
        await db.flush()
        return row

    async def lease_batch(
        self,
        db: AsyncSession,
        *,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 120,
    ) -> list[DigitalPlatformJobRecord]:
        now = datetime.utcnow()
        rows = (
            await db.scalars(
                select(DigitalPlatformJobRecord)
                .where(
                    DigitalPlatformJobRecord.state.in_(["queued", "retry"]),
                    DigitalPlatformJobRecord.available_at <= now,
                    or_(
                        DigitalPlatformJobRecord.lease_expires_at.is_(None),
                        DigitalPlatformJobRecord.lease_expires_at < now,
                    ),
                )
                .order_by(
                    DigitalPlatformJobRecord.priority.asc(),
                    DigitalPlatformJobRecord.available_at.asc(),
                    DigitalPlatformJobRecord.created_at.asc(),
                )
                .limit(max(1, min(int(limit), 100)))
                .with_for_update(skip_locked=True)
            )
        ).all()
        lease_until = now + timedelta(seconds=max(30, min(int(lease_seconds), 900)))
        for row in rows:
            row.state = "running"
            row.locked_by = worker_id
            row.lease_expires_at = lease_until
            row.heartbeat_at = now
            row.started_at = row.started_at or now
            row.attempt += 1
        await db.flush()
        return list(rows)

    async def heartbeat(
        self,
        db: AsyncSession,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> None:
        row = await db.get(DigitalPlatformJobRecord, job_id)
        if row is None or row.state != "running" or row.locked_by != worker_id:
            raise PermissionError("Platform job lease is not owned by this worker")
        now = datetime.utcnow()
        row.heartbeat_at = now
        row.lease_expires_at = now + timedelta(seconds=max(30, min(int(lease_seconds), 900)))
        await db.flush()

    async def complete(
        self,
        db: AsyncSession,
        *,
        job_id: str,
        worker_id: str,
        result: Mapping[str, Any] | None = None,
    ) -> None:
        row = await db.get(DigitalPlatformJobRecord, job_id)
        if row is None or row.state != "running" or row.locked_by != worker_id:
            raise PermissionError("Platform job lease is not owned by this worker")
        row.state = "completed"
        row.result_json = json.dumps(dict(result or {}), separators=(",", ":"), sort_keys=True)
        row.completed_at = datetime.utcnow()
        row.locked_by = None
        row.lease_expires_at = None
        row.last_error = None
        await db.flush()

    async def fail(
        self,
        db: AsyncSession,
        *,
        job_id: str,
        worker_id: str,
        error: str,
        retry_after_seconds: int = 60,
    ) -> None:
        row = await db.get(DigitalPlatformJobRecord, job_id)
        if row is None or row.state != "running" or row.locked_by != worker_id:
            raise PermissionError("Platform job lease is not owned by this worker")
        row.last_error = str(error or "")[:8000]
        row.locked_by = None
        row.lease_expires_at = None
        if row.attempt >= row.max_attempts:
            row.state = "failed"
            row.completed_at = datetime.utcnow()
        else:
            row.state = "retry"
            row.available_at = datetime.utcnow() + timedelta(
                seconds=max(1, min(int(retry_after_seconds), 86400))
            )
        await db.flush()


digital_platform_jobs = DigitalPlatformJobService()
