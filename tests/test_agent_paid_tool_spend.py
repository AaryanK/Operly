from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.agent_runtime.contracts import AgentPlanStep, AgentStepStatus
from packages.agent_runtime.runtime import AgentRuntimeSettings, GovernedAgentRuntime
from packages.agent_runtime.spend import AgentSpendMeter, PriceSnapshot, SpendLimits, SpendScope
from packages.agent_runtime.tool_spend import PaidToolPriceSnapshot
from packages.database.agent_spend_models import AgentSpendReservationRecord
from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimeResponse,
)
from packages.kernel.runtime import RuntimeExecutionError
from packages.security.execution_context import ExecutionContext, ScopeKind
from packages.security.surfaces import SurfaceKind


def context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="paid-tool-user",
        membership_id=None,
        role="personal_owner",
        permissions=frozenset({"workspace:read"}),
        channel="web",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="paid-tool-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:paid-tool-user",
        workspace_mode="personal",
    )


def paid_spec() -> CapabilitySpec:
    return CapabilitySpec(
        id="fixture.lookup.paid",
        version="1",
        display_name="Paid lookup",
        description="Fixture paid lookup.",
        provider_id="fixture",
        scopes=frozenset({"personal"}),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object"},
        risk=CapabilityRisk.READ_ONLY,
        tags=frozenset({"fixture", "paid"}),
    )


class FakeRegistry:
    def __init__(self, spec: CapabilitySpec) -> None:
        self.spec = spec

    def get(self, capability_id: str) -> CapabilitySpec:
        if capability_id != self.spec.id:
            raise KeyError(capability_id)
        return self.spec


class FakeKernel:
    def __init__(self, *, error_code: str | None = None) -> None:
        self.spec = paid_spec()
        self.registry = FakeRegistry(self.spec)
        self.error_code = error_code
        self.calls = 0

    async def execute(self, db, *, context, request):
        del db, context
        self.calls += 1
        if self.error_code:
            raise RuntimeExecutionError(
                f"fixture {self.error_code}",
                run_id="kernel-paid-tool",
                code=self.error_code,
                approval_id="approval-1" if self.error_code == "approval_required" else None,
            )
        return RuntimeResponse(
            run_id="kernel-paid-tool",
            status="completed",
            capability_id=request.capability_id,
            decision=AuthorizationDecision.ALLOW,
            result={"value": "paid-result"},
            done=True,
            trace=(),
        )


class PaidToolSpendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.tempdir = tempfile.TemporaryDirectory()
        database = Path(self.tempdir.name) / "paid-tool-spend.db"
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

    def meter(self, *, task_limit: int = 100) -> AgentSpendMeter:
        return AgentSpendMeter(
            scope=SpendScope(
                scope_kind="personal",
                scope_id="paid-tool-user",
                task_id="paid-tool-run",
                project_id="paid-tool-conversation",
            ),
            run_id="paid-tool-run",
            session_factory=self.sessions,
            prices=PriceSnapshot(version="fixture", prices={}),
            limits=SpendLimits(
                small_task_micros=task_limit,
                approved_composite_task_micros=max(task_limit, 500),
                scope_month_micros=10_000,
                project_month_micros=5_000,
                max_model_calls=12,
            ),
        )

    def prices(self, *, cost_micros: int = 25) -> PaidToolPriceSnapshot:
        return PaidToolPriceSnapshot.from_mapping(
            {
                "version": "fixture-2026-09-16",
                "tools": {
                    "fixture.lookup.paid": {"cost_micros": cost_micros},
                },
            }
        )

    async def execute(self, kernel: FakeKernel, *, prices: PaidToolPriceSnapshot, task_limit: int = 100):
        runtime = GovernedAgentRuntime(
            kernel=kernel,
            settings=AgentRuntimeSettings(enabled=True),
            spend_meter=self.meter(task_limit=task_limit),
            paid_tool_prices=prices,
        )
        return await runtime.execute_step(
            None,
            context=context(),
            run_id="paid-tool-run",
            goal="Use the paid lookup",
            step=AgentPlanStep(
                step_id="paid-tool-step",
                capability_id="fixture.lookup.paid",
                arguments={},
            ),
        )

    async def reservations(self):
        async with self.sessions() as db:
            return list(
                (
                    await db.scalars(
                        select(AgentSpendReservationRecord).where(
                            AgentSpendReservationRecord.run_id == "paid-tool-run"
                        )
                    )
                ).all()
            )

    async def test_paid_capability_reserves_and_settles_same_task_budget(self):
        kernel = FakeKernel()
        result = await self.execute(kernel, prices=self.prices(cost_micros=25))
        self.assertEqual(result.status, AgentStepStatus.COMPLETED)
        self.assertEqual(kernel.calls, 1)
        meter = self.meter()
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 25)
        self.assertEqual(task["reserved_micros"], 0)
        rows = await self.reservations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "tool")
        self.assertEqual(rows[0].model_id, "fixture.lookup.paid")
        self.assertEqual(rows[0].status, "settled")
        self.assertEqual(rows[0].price_snapshot_version, "fixture-2026-09-16")

    async def test_unknown_paid_tool_price_fails_closed_before_kernel_dispatch(self):
        kernel = FakeKernel()
        result = await self.execute(
            kernel,
            prices=PaidToolPriceSnapshot(version="fixture-empty", costs_micros={}),
        )
        self.assertEqual(result.status, AgentStepStatus.FAILED)
        self.assertEqual(result.error_code, "paid_tool_price_unknown")
        self.assertEqual(kernel.calls, 0)
        self.assertEqual(await self.reservations(), [])

    async def test_paid_tool_budget_exhaustion_blocks_kernel_dispatch(self):
        kernel = FakeKernel()
        result = await self.execute(
            kernel,
            prices=self.prices(cost_micros=25),
            task_limit=20,
        )
        self.assertEqual(result.status, AgentStepStatus.FAILED)
        self.assertEqual(result.error_code, "inference_spend_budget_exhausted")
        self.assertEqual(kernel.calls, 0)

    async def test_pre_dispatch_approval_stop_releases_paid_tool_reservation(self):
        kernel = FakeKernel(error_code="approval_required")
        result = await self.execute(kernel, prices=self.prices(cost_micros=25))
        self.assertEqual(result.status, AgentStepStatus.WAITING_APPROVAL)
        rows = await self.reservations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "released")
        meter = self.meter()
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 0)
        self.assertEqual(task["reserved_micros"], 0)

    async def test_uncertain_paid_tool_failure_keeps_conservative_reservation(self):
        kernel = FakeKernel(error_code="provider_failed")
        result = await self.execute(kernel, prices=self.prices(cost_micros=25))
        self.assertEqual(result.status, AgentStepStatus.FAILED)
        rows = await self.reservations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "uncertain")
        meter = self.meter()
        task = next(row for row in await meter.budget_state() if row["kind"] == "task")
        self.assertEqual(task["spent_micros"], 0)
        self.assertEqual(task["reserved_micros"], 25)

    def test_paid_tool_snapshot_rejects_negative_and_non_integer_prices(self):
        for value in (-1, float("nan"), "25"):
            with self.assertRaises(ValueError):
                PaidToolPriceSnapshot.from_mapping(
                    {
                        "version": "bad",
                        "tools": {"fixture.lookup.paid": {"cost_micros": value}},
                    }
                )


if __name__ == "__main__":
    unittest.main()
