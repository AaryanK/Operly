from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.digital_usage_models import (
    DigitalUsageBucketRecord,
    DigitalUsageLedgerRecord,
)
from packages.database.plugin_platform_models import DigitalResourceBudgetRecord


class ResourceBudgetExceeded(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class BudgetConsumption:
    metric: str
    quantity: int
    used: int
    hard_limit: int | None
    soft_limit: int | None
    window_start: datetime
    window_seconds: int
    soft_limit_exceeded: bool


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    start = epoch - (epoch % window_seconds)
    return datetime.utcfromtimestamp(start)


class ResourceBudgetService:
    """Race-safe quota seam for plugin/runtime/workflow digital resources.

    A configured hard limit denies the increment before execution. Every successful
    increment is also appended to a ledger so later accounting does not depend on the
    mutable aggregate bucket alone.
    """

    async def configure(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        subject_kind: str,
        subject_id: str,
        metric: str,
        window_seconds: int,
        hard_limit: int,
        soft_limit: int | None = None,
    ) -> DigitalResourceBudgetRecord:
        window_seconds = max(60, min(int(window_seconds), 31 * 24 * 60 * 60))
        if hard_limit < 0:
            raise ValueError("hard_limit cannot be negative")
        if soft_limit is not None and (soft_limit < 0 or soft_limit > hard_limit):
            raise ValueError("soft_limit must be between zero and hard_limit")
        row = await db.scalar(
            select(DigitalResourceBudgetRecord).where(
                DigitalResourceBudgetRecord.tenant_id == tenant_id,
                DigitalResourceBudgetRecord.subject_kind == subject_kind,
                DigitalResourceBudgetRecord.subject_id == subject_id,
                DigitalResourceBudgetRecord.metric == metric,
                DigitalResourceBudgetRecord.window_seconds == window_seconds,
            )
        )
        if row is None:
            row = DigitalResourceBudgetRecord(
                tenant_id=tenant_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                metric=metric,
                window_seconds=window_seconds,
                hard_limit=int(hard_limit),
                soft_limit=int(soft_limit) if soft_limit is not None else None,
                enabled=True,
            )
            db.add(row)
        else:
            row.hard_limit = int(hard_limit)
            row.soft_limit = int(soft_limit) if soft_limit is not None else None
            row.enabled = True
        await db.flush()
        return row

    async def consume(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        subject_kind: str,
        subject_id: str,
        metric: str,
        quantity: int = 1,
        reference_kind: str | None = None,
        reference_id: str | None = None,
        now: datetime | None = None,
    ) -> BudgetConsumption:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("usage quantity must be positive")
        now = now or datetime.utcnow()
        budget = await db.scalar(
            select(DigitalResourceBudgetRecord)
            .where(
                DigitalResourceBudgetRecord.tenant_id == tenant_id,
                DigitalResourceBudgetRecord.subject_kind == subject_kind,
                DigitalResourceBudgetRecord.subject_id == subject_id,
                DigitalResourceBudgetRecord.metric == metric,
                DigitalResourceBudgetRecord.enabled.is_(True),
            )
            .order_by(DigitalResourceBudgetRecord.window_seconds.asc())
            .limit(1)
            .with_for_update()
        )
        window_seconds = budget.window_seconds if budget else 3600
        window_start = _window_start(now, window_seconds)

        bucket = await db.scalar(
            select(DigitalUsageBucketRecord)
            .where(
                DigitalUsageBucketRecord.tenant_id == tenant_id,
                DigitalUsageBucketRecord.subject_kind == subject_kind,
                DigitalUsageBucketRecord.subject_id == subject_id,
                DigitalUsageBucketRecord.metric == metric,
                DigitalUsageBucketRecord.window_start == window_start,
                DigitalUsageBucketRecord.window_seconds == window_seconds,
            )
            .with_for_update()
        )
        if bucket is None:
            bucket = DigitalUsageBucketRecord(
                tenant_id=tenant_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                metric=metric,
                window_start=window_start,
                window_seconds=window_seconds,
                quantity=0,
            )
            db.add(bucket)
            try:
                await db.flush()
            except IntegrityError:
                # Concurrent first-use of the same window. Roll back the failed nested
                # insert only and then lock the winner's bucket.
                await db.rollback()
                bucket = await db.scalar(
                    select(DigitalUsageBucketRecord)
                    .where(
                        DigitalUsageBucketRecord.tenant_id == tenant_id,
                        DigitalUsageBucketRecord.subject_kind == subject_kind,
                        DigitalUsageBucketRecord.subject_id == subject_id,
                        DigitalUsageBucketRecord.metric == metric,
                        DigitalUsageBucketRecord.window_start == window_start,
                        DigitalUsageBucketRecord.window_seconds == window_seconds,
                    )
                    .with_for_update()
                )
                if bucket is None:
                    raise RuntimeError("Could not establish a resource usage bucket")

        projected = int(bucket.quantity) + quantity
        if budget and projected > int(budget.hard_limit):
            raise ResourceBudgetExceeded(
                f"{metric} budget exceeded for {subject_kind}:{subject_id}; "
                f"{projected}>{budget.hard_limit} in {window_seconds}s"
            )
        bucket.quantity = projected
        db.add(
            DigitalUsageLedgerRecord(
                tenant_id=tenant_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                metric=metric,
                quantity=quantity,
                reference_kind=reference_kind,
                reference_id=reference_id,
                recorded_at=now,
            )
        )
        await db.flush()
        return BudgetConsumption(
            metric=metric,
            quantity=quantity,
            used=projected,
            hard_limit=int(budget.hard_limit) if budget else None,
            soft_limit=int(budget.soft_limit) if budget and budget.soft_limit is not None else None,
            window_start=window_start,
            window_seconds=window_seconds,
            soft_limit_exceeded=bool(
                budget
                and budget.soft_limit is not None
                and projected > int(budget.soft_limit)
            ),
        )


resource_budgets = ResourceBudgetService()
