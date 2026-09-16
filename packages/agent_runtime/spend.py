from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.database.agent_spend_models import (
    AgentSpendBudgetRecord,
    AgentSpendReservationRecord,
)
from packages.database.db import SessionFactory
from packages.security.execution_context import ExecutionContext


_MICROS_PER_MILLION = 1_000_000
_MAX_PRICE_MICROS_PER_MILLION = 1_000_000_000


def _ceil_div(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        raise ValueError("ceil division requires a nonnegative numerator and positive denominator")
    if numerator == 0:
        return 0
    return (numerator + denominator - 1) // denominator


class SpendControlError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SpendBudgetExceeded(SpendControlError):
    def __init__(self, message: str = "Agent monetary budget is exhausted") -> None:
        super().__init__(message, code="inference_spend_budget_exhausted")


class SpendCallBudgetExceeded(SpendControlError):
    def __init__(self, message: str = "Agent model-call budget is exhausted") -> None:
        super().__init__(message, code="inference_model_call_budget_exhausted")


class SpendPriceUnknown(SpendControlError):
    def __init__(self, provider: str, model_id: str) -> None:
        super().__init__(
            f"No trusted price snapshot exists for {provider}/{model_id}",
            code="inference_price_unknown",
        )


class SpendConfigurationError(SpendControlError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="inference_spend_not_configured")


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_micros_per_million_tokens: int
    output_micros_per_million_tokens: int

    def __post_init__(self) -> None:
        for name, value in (
            ("input", self.input_micros_per_million_tokens),
            ("output", self.output_micros_per_million_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} price must be an integer micro-dollar amount")
            if value < 0 or value > _MAX_PRICE_MICROS_PER_MILLION:
                raise ValueError(f"{name} price is outside the accepted range")

    def cost_micros(self, *, prompt_tokens: int, completion_tokens: int) -> int:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token usage cannot be negative")
        return _ceil_div(
            prompt_tokens * self.input_micros_per_million_tokens,
            _MICROS_PER_MILLION,
        ) + _ceil_div(
            completion_tokens * self.output_micros_per_million_tokens,
            _MICROS_PER_MILLION,
        )


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    version: str
    prices: Mapping[str, ModelPrice]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "PriceSnapshot":
        version = str(payload.get("version") or "").strip()
        if not version or len(version) > 80:
            raise ValueError("price snapshot version is required")
        raw_models = payload.get("models")
        if not isinstance(raw_models, Mapping):
            raise ValueError("price snapshot models must be an object")
        prices: dict[str, ModelPrice] = {}
        for raw_key, raw_price in raw_models.items():
            key = str(raw_key or "").strip().lower()
            if not key or ":" not in key or not isinstance(raw_price, Mapping):
                raise ValueError("price snapshot model keys must be provider:model")
            input_rate = raw_price.get("input_micros_per_million_tokens")
            output_rate = raw_price.get("output_micros_per_million_tokens")
            if not isinstance(input_rate, int) or isinstance(input_rate, bool):
                raise ValueError("input price must be an integer")
            if not isinstance(output_rate, int) or isinstance(output_rate, bool):
                raise ValueError("output price must be an integer")
            prices[key] = ModelPrice(input_rate, output_rate)
        return cls(version=version, prices=prices)

    @classmethod
    def from_environment(cls) -> "PriceSnapshot":
        raw = os.getenv("OPERLY_AGENT_PRICE_SNAPSHOT_JSON", "").strip()
        if not raw:
            return cls(version="unconfigured", prices={})
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise SpendConfigurationError("OPERLY_AGENT_PRICE_SNAPSHOT_JSON is invalid JSON") from error
        try:
            return cls.from_mapping(payload)
        except ValueError as error:
            raise SpendConfigurationError(str(error)) from error

    def price_for(self, provider: str, model_id: str) -> ModelPrice:
        provider_key = str(provider or "").strip().lower()
        model_key = str(model_id or "").strip().lower()
        if provider_key == "ollama":
            return ModelPrice(0, 0)
        try:
            return self.prices[f"{provider_key}:{model_key}"]
        except KeyError as error:
            raise SpendPriceUnknown(provider_key, model_key) from error


@dataclass(frozen=True, slots=True)
class SpendLimits:
    small_task_micros: int = 10_000
    approved_composite_task_micros: int = 50_000
    scope_month_micros: int = 1_000_000
    project_month_micros: int = 25_000_000
    max_model_calls: int = 12

    def __post_init__(self) -> None:
        for name, value in (
            ("small_task_micros", self.small_task_micros),
            ("approved_composite_task_micros", self.approved_composite_task_micros),
            ("scope_month_micros", self.scope_month_micros),
            ("project_month_micros", self.project_month_micros),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.approved_composite_task_micros < self.small_task_micros:
            raise ValueError("approved composite budget cannot be below the small-task budget")
        if not isinstance(self.max_model_calls, int) or not 1 <= self.max_model_calls <= 100:
            raise ValueError("max_model_calls must be between 1 and 100")

    @classmethod
    def from_environment(cls) -> "SpendLimits":
        def integer(name: str, default: int) -> int:
            raw = os.getenv(name, "").strip()
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError as error:
                raise SpendConfigurationError(f"{name} must be an integer") from error
            return value

        try:
            return cls(
                small_task_micros=integer("OPERLY_AGENT_SMALL_TASK_BUDGET_MICROS", 10_000),
                approved_composite_task_micros=integer(
                    "OPERLY_AGENT_APPROVED_COMPOSITE_BUDGET_MICROS", 50_000
                ),
                scope_month_micros=integer("OPERLY_AGENT_SCOPE_MONTH_BUDGET_MICROS", 1_000_000),
                project_month_micros=integer("OPERLY_AGENT_PROJECT_MONTH_BUDGET_MICROS", 25_000_000),
                max_model_calls=integer("OPERLY_AGENT_MAX_MODEL_CALLS", 12),
            )
        except ValueError as error:
            raise SpendConfigurationError(str(error)) from error


@dataclass(frozen=True, slots=True)
class SpendScope:
    scope_kind: str
    scope_id: str
    task_id: str
    project_id: str
    approved_composite: bool = False

    @classmethod
    def from_execution_context(cls, context: ExecutionContext, *, run_id: str) -> "SpendScope":
        if context.is_personal:
            scope_id = str(context.user_id or "").strip()
        else:
            scope_id = str(context.workspace_id or "").strip()
        if not scope_id:
            raise SpendConfigurationError("trusted execution scope is unavailable for spend accounting")
        metadata = dict(context.metadata or {})
        task_id = str(metadata.get("agent_task_id") or run_id or "").strip()
        if not task_id:
            raise SpendConfigurationError("agent task identity is required for spend accounting")
        project_id = str(metadata.get("project_id") or context.conversation_id or "default").strip()
        if not project_id:
            project_id = "default"
        approved = str(metadata.get("agent_budget_profile") or "").strip().lower() == "approved_composite"
        return cls(
            scope_kind=context.scope_kind.value,
            scope_id=scope_id,
            task_id=task_id[:160],
            project_id=project_id[:200],
            approved_composite=approved,
        )


@dataclass(frozen=True, slots=True)
class SpendReservation:
    reservation_id: str
    reserved_micros: int
    price_snapshot_version: str


def _month_window(now: datetime) -> tuple[str, datetime, datetime]:
    aware = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    utc = aware.astimezone(timezone.utc)
    start = datetime(utc.year, utc.month, 1)
    if utc.month == 12:
        end = datetime(utc.year + 1, 1, 1)
    else:
        end = datetime(utc.year, utc.month + 1, 1)
    return f"{utc.year:04d}-{utc.month:02d}", start, end


class AgentSpendMeter:
    """Persistent, conservative spend and model-call accounting.

    Every provider dispatch first reserves the worst-case amount against task,
    account/workspace-month and project-month buckets in one database transaction.
    Provider usage later settles the reservation. Missing or ambiguous usage keeps the
    reservation in place, so unknown cost is never silently treated as zero.
    """

    def __init__(
        self,
        *,
        scope: SpendScope,
        run_id: str | None = None,
        session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
        prices: PriceSnapshot | None = None,
        limits: SpendLimits | None = None,
        now: datetime | None = None,
    ) -> None:
        self.scope = scope
        self.run_id = str(run_id or scope.task_id or "").strip()[:120]
        if not self.run_id:
            raise SpendConfigurationError("runtime run identity is required for spend accounting")
        self.session_factory = session_factory
        self.prices = prices or PriceSnapshot.from_environment()
        self.limits = limits or SpendLimits.from_environment()
        self.now = now or datetime.now(timezone.utc)

    @classmethod
    def from_execution_context(
        cls,
        context: ExecutionContext,
        *,
        run_id: str,
        session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    ) -> "AgentSpendMeter":
        return cls(
            scope=SpendScope.from_execution_context(context, run_id=run_id),
            run_id=run_id,
            session_factory=session_factory,
        )

    @property
    def task_limit_micros(self) -> int:
        return (
            self.limits.approved_composite_task_micros
            if self.scope.approved_composite
            else self.limits.small_task_micros
        )

    async def _get_or_create_budget(
        self,
        db: AsyncSession,
        *,
        budget_kind: str,
        budget_key: str,
        limit_micros: int,
        max_calls: int | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> AgentSpendBudgetRecord:
        query = select(AgentSpendBudgetRecord).where(
            AgentSpendBudgetRecord.scope_kind == self.scope.scope_kind,
            AgentSpendBudgetRecord.scope_id == self.scope.scope_id,
            AgentSpendBudgetRecord.budget_kind == budget_kind,
            AgentSpendBudgetRecord.budget_key == budget_key,
        )
        row = await db.scalar(query)
        if row is None:
            candidate = AgentSpendBudgetRecord(
                id=str(uuid4()),
                scope_kind=self.scope.scope_kind,
                scope_id=self.scope.scope_id,
                budget_kind=budget_kind,
                budget_key=budget_key,
                limit_micros=limit_micros,
                max_calls=max_calls,
                window_start=window_start,
                window_end=window_end,
            )
            try:
                async with db.begin_nested():
                    db.add(candidate)
                    await db.flush()
                row = candidate
            except IntegrityError:
                row = await db.scalar(query)
                if row is None:
                    raise
        # Policy can tighten an existing budget but never raise it implicitly. A later
        # model or resumed request therefore cannot grant itself more money or calls.
        if row.limit_micros > limit_micros:
            row.limit_micros = limit_micros
        if max_calls is not None and (row.max_calls is None or row.max_calls > max_calls):
            row.max_calls = max_calls
        return row

    async def _budget_rows(self, db: AsyncSession) -> tuple[AgentSpendBudgetRecord, ...]:
        month_key, month_start, month_end = _month_window(self.now)
        task = await self._get_or_create_budget(
            db,
            budget_kind="task",
            budget_key=self.scope.task_id,
            limit_micros=self.task_limit_micros,
            max_calls=self.limits.max_model_calls,
        )
        scope_month = await self._get_or_create_budget(
            db,
            budget_kind="scope_month",
            budget_key=month_key,
            limit_micros=self.limits.scope_month_micros,
            window_start=month_start,
            window_end=month_end,
        )
        project_month = await self._get_or_create_budget(
            db,
            budget_kind="project_month",
            budget_key=f"{self.scope.project_id}:{month_key}",
            limit_micros=self.limits.project_month_micros,
            window_start=month_start,
            window_end=month_end,
        )
        await db.flush()
        return task, scope_month, project_month

    async def reserve_model_call(
        self,
        *,
        run_id: str,
        phase: str,
        provider: str,
        model_id: str,
        input_token_cap: int,
        output_token_cap: int,
    ) -> SpendReservation:
        phase_key = str(phase or "").strip().lower()
        if not phase_key or len(phase_key) > 40:
            raise SpendConfigurationError("inference phase is invalid")
        if input_token_cap < 0 or output_token_cap < 0:
            raise SpendConfigurationError("inference token caps cannot be negative")
        price = self.prices.price_for(provider, model_id)
        reserve_micros = price.cost_micros(
            prompt_tokens=int(input_token_cap),
            completion_tokens=int(output_token_cap),
        )
        reservation_id = str(uuid4())
        reservation_key = f"{run_id}:{phase_key}:{reservation_id}"

        async with self.session_factory() as db:
            async with db.begin():
                budgets = await self._budget_rows(db)
                for row in budgets:
                    conditions = [
                        AgentSpendBudgetRecord.id == row.id,
                        AgentSpendBudgetRecord.spent_micros
                        + AgentSpendBudgetRecord.reserved_micros
                        + reserve_micros
                        <= AgentSpendBudgetRecord.limit_micros,
                    ]
                    values: dict[str, Any] = {
                        "reserved_micros": AgentSpendBudgetRecord.reserved_micros + reserve_micros,
                        "updated_at": datetime.utcnow(),
                    }
                    if row.budget_kind == "task":
                        conditions.append(
                            or_(
                                AgentSpendBudgetRecord.max_calls.is_(None),
                                AgentSpendBudgetRecord.calls_used < AgentSpendBudgetRecord.max_calls,
                            )
                        )
                        values["calls_used"] = AgentSpendBudgetRecord.calls_used + 1
                    result = await db.execute(
                        update(AgentSpendBudgetRecord)
                        .where(*conditions)
                        .values(**values)
                    )
                    if result.rowcount != 1:
                        current = await db.get(AgentSpendBudgetRecord, row.id)
                        if (
                            row.budget_kind == "task"
                            and current is not None
                            and current.max_calls is not None
                            and current.calls_used >= current.max_calls
                        ):
                            raise SpendCallBudgetExceeded()
                        raise SpendBudgetExceeded(
                            f"{row.budget_kind} monetary budget cannot reserve this provider call"
                        )

                db.add(
                    AgentSpendReservationRecord(
                        id=reservation_id,
                        reservation_key=reservation_key,
                        run_id=str(run_id)[:120],
                        task_id=self.scope.task_id,
                        scope_kind=self.scope.scope_kind,
                        scope_id=self.scope.scope_id,
                        project_id=self.scope.project_id,
                        phase=phase_key,
                        provider=str(provider)[:80],
                        model_id=str(model_id)[:255],
                        price_snapshot_version=self.prices.version,
                        input_micros_per_million_tokens=price.input_micros_per_million_tokens,
                        output_micros_per_million_tokens=price.output_micros_per_million_tokens,
                        input_token_cap=int(input_token_cap),
                        output_token_cap=int(output_token_cap),
                        reserved_micros=reserve_micros,
                        status="reserved",
                        budget_ids_json=json.dumps([row.id for row in budgets]),
                    )
                )
        return SpendReservation(
            reservation_id=reservation_id,
            reserved_micros=reserve_micros,
            price_snapshot_version=self.prices.version,
        )

    async def settle_usage(
        self,
        reservation_id: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> int:
        if prompt_tokens < 0 or completion_tokens < 0:
            raise SpendConfigurationError("provider token usage cannot be negative")
        async with self.session_factory() as db:
            async with db.begin():
                reservation = await db.get(AgentSpendReservationRecord, reservation_id)
                if reservation is None:
                    raise LookupError("spend reservation not found")
                if reservation.status in {"settled", "settled_overage"}:
                    return int(reservation.actual_micros or 0)
                if reservation.status not in {"reserved", "uncertain"}:
                    raise SpendConfigurationError("spend reservation cannot be settled from its current state")
                price = ModelPrice(
                    reservation.input_micros_per_million_tokens,
                    reservation.output_micros_per_million_tokens,
                )
                actual = price.cost_micros(
                    prompt_tokens=int(prompt_tokens),
                    completion_tokens=int(completion_tokens),
                )
                budget_ids = json.loads(reservation.budget_ids_json or "[]")
                for budget_id in budget_ids:
                    budget = await db.get(AgentSpendBudgetRecord, str(budget_id))
                    if budget is None:
                        raise SpendConfigurationError("spend reservation references a missing budget")
                    if budget.reserved_micros < reservation.reserved_micros:
                        raise SpendConfigurationError("spend reservation exceeds persisted reserved balance")
                    budget.reserved_micros -= reservation.reserved_micros
                    budget.spent_micros += actual
                    budget.updated_at = datetime.utcnow()
                reservation.actual_micros = actual
                reservation.prompt_tokens = int(prompt_tokens)
                reservation.completion_tokens = int(completion_tokens)
                reservation.status = (
                    "settled_overage" if actual > reservation.reserved_micros else "settled"
                )
                reservation.uncertainty_reason = None
                reservation.settled_at = datetime.utcnow()
        return actual

    async def mark_uncertain(self, reservation_id: str, *, reason: str) -> None:
        async with self.session_factory() as db:
            async with db.begin():
                reservation = await db.get(AgentSpendReservationRecord, reservation_id)
                if reservation is None:
                    raise LookupError("spend reservation not found")
                if reservation.status in {"settled", "settled_overage"}:
                    return
                reservation.status = "uncertain"
                reservation.uncertainty_reason = str(reason or "unknown")[:120]

    async def settle_from_provider_usage(
        self,
        reservation_id: str,
        usage: Mapping[str, Any] | None,
    ) -> int | None:
        if not isinstance(usage, Mapping):
            await self.mark_uncertain(reservation_id, reason="provider_usage_missing")
            return None
        prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        if (
            not isinstance(prompt, int)
            or isinstance(prompt, bool)
            or prompt < 0
            or not isinstance(completion, int)
            or isinstance(completion, bool)
            or completion < 0
        ):
            await self.mark_uncertain(reservation_id, reason="provider_usage_invalid")
            return None
        return await self.settle_usage(
            reservation_id,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )

    async def budget_state(self) -> tuple[dict[str, Any], ...]:
        async with self.session_factory() as db:
            rows = (
                await db.scalars(
                    select(AgentSpendBudgetRecord)
                    .where(
                        AgentSpendBudgetRecord.scope_kind == self.scope.scope_kind,
                        AgentSpendBudgetRecord.scope_id == self.scope.scope_id,
                    )
                    .order_by(AgentSpendBudgetRecord.budget_kind, AgentSpendBudgetRecord.budget_key)
                )
            ).all()
        return tuple(
            {
                "kind": row.budget_kind,
                "key": row.budget_key,
                "limit_micros": row.limit_micros,
                "spent_micros": row.spent_micros,
                "reserved_micros": row.reserved_micros,
                "max_calls": row.max_calls,
                "calls_used": row.calls_used,
            }
            for row in rows
        )
