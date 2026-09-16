from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime.spend import (
    AgentSpendMeter,
    ModelPrice,
    PriceSnapshot,
    SpendBudgetExceeded,
    SpendCallBudgetExceeded,
    SpendLimits,
    SpendPriceUnknown,
    SpendScope,
)
from packages.database.agent_spend_models import AgentSpendReservationRecord
from packages.database.db import Base
from packages.database.schema import import_all_models


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def snapshot(*, input_rate: int = 0, output_rate: int = 1_000_000) -> PriceSnapshot:
    return PriceSnapshot.from_mapping(
        {
            "version": "fixture-2026-09-16",
            "models": {
                "fixture:model": {
                    "input_micros_per_million_tokens": input_rate,
                    "output_micros_per_million_tokens": output_rate,
                }
            },
        }
    )


def scope(*, task_id: str = "task-1", approved: bool = False) -> SpendScope:
    return SpendScope(
        scope_kind="personal",
        scope_id="user-alpha",
        task_id=task_id,
        project_id="project-alpha",
        approved_composite=approved,
    )


class AgentSpendBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.tempdir = tempfile.TemporaryDirectory()
        database = Path(self.tempdir.name) / "spend.db"
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{database}",
            connect_args={"timeout": 10},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tempdir.cleanup()

    def meter(
        self,
        *,
        spend_scope: SpendScope | None = None,
        limits: SpendLimits | None = None,
        prices: PriceSnapshot | None = None,
    ) -> AgentSpendMeter:
        return AgentSpendMeter(
            scope=spend_scope or scope(),
            session_factory=self.sessions,
            prices=prices or snapshot(),
            limits=limits
            or SpendLimits(
                small_task_micros=100,
                approved_composite_task_micros=500,
                scope_month_micros=10_000,
                project_month_micros=5_000,
                max_model_calls=12,
            ),
            now=NOW,
        )

    async def test_concurrent_reservations_cannot_over_reserve_task_budget(self):
        meter_a = self.meter()
        meter_b = self.meter()

        async def reserve(meter: AgentSpendMeter):
            return await meter.reserve_model_call(
                run_id="run-concurrent",
                phase="reasoning",
                provider="fixture",
                model_id="model",
                input_token_cap=0,
                output_token_cap=60,
            )

        results = await asyncio.gather(reserve(meter_a), reserve(meter_b), return_exceptions=True)
        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, Exception)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 1, results)
        self.assertIsInstance(failures[0], SpendBudgetExceeded)
        task = next(row for row in await meter_a.budget_state() if row["kind"] == "task")
        self.assertEqual(task["reserved_micros"], 60)
        self.assertEqual(task["calls_used"], 1)

    async def test_reservations_and_settlement_survive_meter_restart(self):
        first = self.meter()
        reservation = await first.reserve_model_call(
            run_id="run-restart",
            phase="interpretation",
            provider="fixture",
            model_id="model",
            input_token_cap=0,
            output_token_cap=60,
        )
        restarted = self.meter()
        with self.assertRaises(SpendBudgetExceeded):
            await restarted.reserve_model_call(
                run_id="run-restart",
                phase="reasoning",
                provider="fixture",
                model_id="model",
                input_token_cap=0,
                output_token_cap=50,
            )

        actual = await restarted.settle_usage(
            reservation.reservation_id,
            prompt_tokens=0,
            completion_tokens=30,
        )
        self.assertEqual(actual, 30)
        second = await restarted.reserve_model_call(
            run_id="run-restart",
            phase="reasoning",
            provider="fixture",
            model_id="model",
            input_token_cap=0,
            output_token_cap=70,
        )
        self.assertEqual(second.reserved_micros, 70)
        task = next(row for row in await restarted.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 30)
        self.assertEqual(task["reserved_micros"], 70)

    async def test_missing_usage_keeps_conservative_reservation(self):
        meter = self.meter()
        reservation = await meter.reserve_model_call(
            run_id="run-uncertain",
            phase="response",
            provider="fixture",
            model_id="model",
            input_token_cap=0,
            output_token_cap=40,
        )
        settled = await meter.settle_from_provider_usage(reservation.reservation_id, None)
        self.assertIsNone(settled)
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 0)
        self.assertEqual(task["reserved_micros"], 40)
        async with self.sessions() as db:
            record = await db.get(AgentSpendReservationRecord, reservation.reservation_id)
            self.assertEqual(record.status, "uncertain")
            self.assertEqual(record.uncertainty_reason, "provider_usage_missing")

    async def test_provider_errors_and_retries_consume_model_call_budget(self):
        meter = self.meter(
            prices=snapshot(input_rate=0, output_rate=0),
            limits=SpendLimits(
                small_task_micros=100,
                approved_composite_task_micros=500,
                scope_month_micros=10_000,
                project_month_micros=5_000,
                max_model_calls=2,
            ),
        )
        for attempt in range(2):
            reservation = await meter.reserve_model_call(
                run_id="run-calls",
                phase="reasoning",
                provider="fixture",
                model_id="model",
                input_token_cap=100,
                output_token_cap=100,
            )
            await meter.mark_uncertain(
                reservation.reservation_id,
                reason=f"provider_retry_{attempt + 1}",
            )
        with self.assertRaises(SpendCallBudgetExceeded):
            await meter.reserve_model_call(
                run_id="run-calls",
                phase="reasoning",
                provider="fixture",
                model_id="model",
                input_token_cap=100,
                output_token_cap=100,
            )
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["calls_used"], 2)

    async def test_all_runtime_phases_share_one_persistent_task_budget(self):
        meter = self.meter(prices=snapshot(input_rate=0, output_rate=0))
        phases = ("interpretation", "planning", "reasoning", "response", "verification")
        for phase in phases:
            await meter.reserve_model_call(
                run_id="run-phases",
                phase=phase,
                provider="fixture",
                model_id="model",
                input_token_cap=10,
                output_token_cap=10,
            )
        async with self.sessions() as db:
            rows = (
                await db.scalars(
                    select(AgentSpendReservationRecord)
                    .where(AgentSpendReservationRecord.run_id == "run-phases")
                    .order_by(AgentSpendReservationRecord.created_at)
                )
            ).all()
        self.assertEqual({row.phase for row in rows}, set(phases))
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["calls_used"], len(phases))

    async def test_unknown_price_fails_closed_before_creating_budget_rows(self):
        meter = self.meter(prices=PriceSnapshot(version="empty", prices={}))
        with self.assertRaises(SpendPriceUnknown):
            await meter.reserve_model_call(
                run_id="run-price",
                phase="interpretation",
                provider="fixture",
                model_id="unknown",
                input_token_cap=100,
                output_token_cap=100,
            )
        self.assertEqual(await meter.budget_state(), ())

    def test_invalid_price_snapshots_are_rejected(self):
        with self.assertRaises(ValueError):
            PriceSnapshot.from_mapping(
                {
                    "version": "bad",
                    "models": {
                        "fixture:model": {
                            "input_micros_per_million_tokens": -1,
                            "output_micros_per_million_tokens": 100,
                        }
                    },
                }
            )
        with self.assertRaises(ValueError):
            ModelPrice(1.5, 100)  # type: ignore[arg-type]

    async def test_existing_task_limit_cannot_be_raised_by_later_profile(self):
        small = self.meter()
        await small.reserve_model_call(
            run_id="run-profile",
            phase="interpretation",
            provider="fixture",
            model_id="model",
            input_token_cap=0,
            output_token_cap=10,
        )
        approved = self.meter(spend_scope=scope(approved=True))
        await approved.reserve_model_call(
            run_id="run-profile",
            phase="reasoning",
            provider="fixture",
            model_id="model",
            input_token_cap=0,
            output_token_cap=10,
        )
        task = next(row for row in await approved.budget_state() if row["kind"] == "task")
        self.assertEqual(task["limit_micros"], 100)


if __name__ == "__main__":
    unittest.main()
