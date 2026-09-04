from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.agent_runtime_models import AgentRuntimeRun
from packages.security.execution_context import (
    ExecutionContext,
    ExecutionContextError,
    ScopeKind,
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind

from .contracts import (
    AgentRunResult,
    AgentRunStatus,
    AgentStepResult,
    AgentStepStatus,
    stable_step_request_id,
)
from .runtime import GovernedAgentRuntime
from .store import (
    AgentRunStateError,
    cancellation_requested,
    claim_run,
    list_steps,
    load_plan,
    record_step_result,
    renew_run_lease,
    transition_run,
)


class AgentLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DurableAgentOrchestrator:
    """Drive one durable run while Kernel remains the only capability executor.

    This is not a background service or HTTP ingress. A future worker loop may call
    ``run_once``. Current authority is reconstructed from trusted application state
    before every pending step so membership/role revocation takes effect mid-run.

    Long capability execution is protected by a lease heartbeat that uses its own DB
    session. Sharing the worker's execution session with the heartbeat would be unsafe
    because SQLAlchemy AsyncSession is not designed for concurrent use.
    """

    runtime: GovernedAgentRuntime
    heartbeat_session_factory: Callable[[], AsyncSession]
    lease_seconds: int = 300
    heartbeat_interval_seconds: float | None = None

    def __post_init__(self) -> None:
        if not 30 <= self.lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 30 and 900")
        if self.heartbeat_session_factory is None:
            raise ValueError("heartbeat_session_factory is required for durable execution")
        interval = self.heartbeat_interval_seconds
        if interval is None:
            interval = max(1.0, min(30.0, self.lease_seconds / 3))
        if not 0.01 <= float(interval) < self.lease_seconds:
            raise ValueError("heartbeat_interval_seconds must be positive and below lease_seconds")
        object.__setattr__(self, "heartbeat_interval_seconds", float(interval))

    async def _lease_heartbeat(
        self,
        *,
        run_id: str,
        lease_token: str,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        """Renew the run lease independently while one capability is executing."""

        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=float(self.heartbeat_interval_seconds or 1.0),
                )
                return
            except TimeoutError:
                pass

            try:
                async with self.heartbeat_session_factory() as heartbeat_db:
                    renewed = await renew_run_lease(
                        heartbeat_db,
                        run_id=run_id,
                        lease_token=lease_token,
                        lease_seconds=self.lease_seconds,
                    )
                    if not renewed:
                        await heartbeat_db.rollback()
                        lost.set()
                        return
                    await heartbeat_db.commit()
            except Exception:
                # A heartbeat infrastructure failure is treated exactly like lease
                # loss. The capability may still finish, but the durable worker must
                # not claim continued ownership and must rely on Kernel idempotency on
                # recovery rather than recording under an unproven lease.
                lost.set()
                return

    async def _resolve_current_context(
        self,
        db: AsyncSession,
        *,
        row: AgentRuntimeRun,
    ) -> ExecutionContext:
        user_id = str(row.authority_user_id or "").strip()
        if not user_id:
            raise ExecutionContextError("Agent authority user is unavailable")
        surface = SurfaceKind.coerce(row.source_surface)

        if row.scope_kind == ScopeKind.PERSONAL.value:
            if surface is not SurfaceKind.PERSONAL_PRIVATE or row.workspace_id is not None:
                raise ExecutionContextError("Stored Personal agent provenance is invalid")
            context = await resolve_personal_execution_context(
                db,
                user_id=user_id,
                channel=row.source_channel,
                surface=surface,
                conversation_id=row.conversation_id,
            )
        elif row.scope_kind == ScopeKind.WORKSPACE.value:
            if not row.workspace_id or surface not in {
                SurfaceKind.WORKSPACE_PRIVATE,
                SurfaceKind.WORKSPACE_SHARED,
            }:
                raise ExecutionContextError("Stored Workspace agent provenance is invalid")
            context = await resolve_execution_context(
                db,
                workspace_id=row.workspace_id,
                user_id=user_id,
                channel=row.source_channel,
                surface=surface,
                conversation_id=row.conversation_id,
                require_membership=True,
            )
        else:
            raise ExecutionContextError("Stored agent scope is invalid")

        if context.is_guest_workspace:
            raise ExecutionContextError("Guest Workspace authority cannot resume this agent run")
        if str(context.principal_id or "") != row.principal_id:
            raise ExecutionContextError("Agent principal changed during authority resolution")
        if context.surface.value != row.source_surface:
            raise ExecutionContextError("Agent surface changed during authority resolution")
        return context

    async def _fail_run(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        code: str,
        message: str,
        current_step_id: str | None = None,
        records: tuple[AgentStepResult, ...] = (),
    ) -> AgentRunResult:
        await transition_run(
            db,
            run_id=run_id,
            to_status="failed",
            current_step_id=current_step_id,
            error_code=code,
            error_message=message,
        )
        await db.commit()
        return AgentRunResult(
            run_id=run_id,
            status=AgentRunStatus.FAILED,
            steps=records,
            next_step_id=current_step_id,
            error_code=code,
            error=message,
        )

    async def _cancel_run(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        message: str,
        current_step_id: str | None = None,
        records: tuple[AgentStepResult, ...] = (),
    ) -> AgentRunResult:
        await transition_run(
            db,
            run_id=run_id,
            to_status="cancelled",
            current_step_id=current_step_id,
            error_code="cancelled",
            error_message=message,
        )
        await db.commit()
        return AgentRunResult(
            run_id=run_id,
            status=AgentRunStatus.CANCELLED,
            steps=records,
            next_step_id=current_step_id,
            error_code="cancelled",
            error="Agent run was cancelled",
        )

    async def _mark_execution_uncertain(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        step_result: AgentStepResult,
        records: tuple[AgentStepResult, ...],
    ) -> AgentRunResult:
        message = step_result.error or "Mutating capability outcome is uncertain"
        await transition_run(
            db,
            run_id=run_id,
            to_status="execution_uncertain",
            current_step_id=step_result.step_id,
            error_code="execution_outcome_uncertain",
            error_message=message,
        )
        await db.commit()
        return AgentRunResult(
            run_id=run_id,
            status=AgentRunStatus.EXECUTION_UNCERTAIN,
            steps=records,
            next_step_id=step_result.step_id,
            approval_id=step_result.approval_id,
            error_code="execution_outcome_uncertain",
            error=message,
        )

    async def run_once(
        self,
        db: AsyncSession,
        *,
        run_id: str,
        lease_token: str,
    ) -> AgentRunResult | None:
        self.runtime.require_enabled()
        claimed = await claim_run(
            db,
            run_id=run_id,
            lease_token=lease_token,
            lease_seconds=self.lease_seconds,
        )
        if claimed is None:
            await db.rollback()
            return None
        await db.commit()

        # A recovery worker may legitimately claim an expired running lease after a
        # cancellation request. Terminalize before reconstructing or executing work.
        if await cancellation_requested(db, run_id=run_id):
            return await self._cancel_run(
                db,
                run_id=run_id,
                message="Agent run was cancelled before capability execution",
            )

        try:
            plan = await load_plan(db, run_id=run_id)
        except AgentRunStateError as error:
            return await self._fail_run(
                db,
                run_id=run_id,
                code="invalid_durable_plan",
                message=str(error),
            )

        budget_error = self.runtime.preflight_plan(plan)
        if budget_error:
            await transition_run(
                db,
                run_id=run_id,
                to_status="budget_exhausted",
                error_code="budget_exhausted",
                error_message=budget_error,
            )
            await db.commit()
            return AgentRunResult(
                run_id=run_id,
                status=AgentRunStatus.BUDGET_EXHAUSTED,
                steps=(),
                error_code="budget_exhausted",
                error=budget_error,
            )

        durable_steps = await list_steps(db, run_id=run_id)
        plan_steps = {step.step_id: step for step in plan.steps}
        records: list[AgentStepResult] = []

        for durable_step in durable_steps:
            step = plan_steps.get(durable_step.step_id)
            if step is None:
                return await self._fail_run(
                    db,
                    run_id=run_id,
                    code="invalid_durable_plan",
                    message="Durable step is missing from the reconstructed plan",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            expected_request_id = stable_step_request_id(run_id, durable_step.step_id)
            if durable_step.request_id != expected_request_id:
                return await self._fail_run(
                    db,
                    run_id=run_id,
                    code="invalid_step_identity",
                    message="Durable agent step request identity is corrupted",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            if durable_step.status == AgentStepStatus.COMPLETED.value:
                continue
            if durable_step.status == AgentStepStatus.WAITING_APPROVAL.value and not durable_step.approval_id:
                await transition_run(
                    db,
                    run_id=run_id,
                    to_status="waiting_approval",
                    current_step_id=durable_step.step_id,
                    error_code="approval_required",
                    error_message="Agent step is waiting for approval",
                )
                await db.commit()
                return AgentRunResult(
                    run_id=run_id,
                    status=AgentRunStatus.WAITING_APPROVAL,
                    steps=tuple(records),
                    next_step_id=durable_step.step_id,
                    error_code="approval_required",
                    error="Agent step is waiting for approval",
                )
            if durable_step.status not in {"pending", AgentStepStatus.WAITING_APPROVAL.value}:
                return await self._fail_run(
                    db,
                    run_id=run_id,
                    code="invalid_step_state",
                    message=f"Cannot execute durable step in state {durable_step.status}",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            if await cancellation_requested(db, run_id=run_id):
                return await self._cancel_run(
                    db,
                    run_id=run_id,
                    message="Agent run was cancelled before the next capability",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            if not await renew_run_lease(
                db,
                run_id=run_id,
                lease_token=lease_token,
                lease_seconds=self.lease_seconds,
            ):
                await db.rollback()
                raise AgentLeaseLost("Agent run lease was lost before capability execution")
            await db.commit()

            # Cancellation and lease ownership are independent state. Renewal must not
            # turn cancellation into a fake lease-loss signal; re-check immediately
            # after the lease transaction and stop before entering Kernel.
            if await cancellation_requested(db, run_id=run_id):
                return await self._cancel_run(
                    db,
                    run_id=run_id,
                    message="Agent run was cancelled before the next capability",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            row = await db.get(AgentRuntimeRun, run_id)
            if row is None:
                raise AgentRunStateError("Agent run disappeared during execution")
            try:
                context = await self._resolve_current_context(db, row=row)
            except ExecutionContextError as error:
                return await self._fail_run(
                    db,
                    run_id=run_id,
                    code="authority_unavailable",
                    message=str(error),
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            heartbeat_stop = asyncio.Event()
            heartbeat_lost = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                self._lease_heartbeat(
                    run_id=run_id,
                    lease_token=lease_token,
                    stop=heartbeat_stop,
                    lost=heartbeat_lost,
                )
            )
            try:
                step_result = await self.runtime.execute_step(
                    db,
                    context=context,
                    run_id=run_id,
                    goal=plan.goal,
                    step=step,
                )
            finally:
                heartbeat_stop.set()
                await heartbeat_task

            if heartbeat_lost.is_set():
                await db.rollback()
                raise AgentLeaseLost("Agent run lease heartbeat was lost during capability execution")

            cancelled_after_step = await cancellation_requested(db, run_id=run_id)
            if not cancelled_after_step:
                if not await renew_run_lease(
                    db,
                    run_id=run_id,
                    lease_token=lease_token,
                    lease_seconds=self.lease_seconds,
                ):
                    await db.rollback()
                    raise AgentLeaseLost("Agent run lease was lost during capability execution")

            await record_step_result(db, run_id=run_id, step_result=step_result)
            records.append(step_result)

            # An uncertain mutation outcome dominates cancellation. Cancellation cannot
            # prove whether an external side effect happened and must never hide the
            # reconciliation requirement.
            if step_result.status is AgentStepStatus.EXECUTION_UNCERTAIN:
                return await self._mark_execution_uncertain(
                    db,
                    run_id=run_id,
                    step_result=step_result,
                    records=tuple(records),
                )

            # Close the small race between the first post-step cancellation check and
            # lease renewal/step recording before deciding whether another step may run.
            if not cancelled_after_step:
                cancelled_after_step = await cancellation_requested(db, run_id=run_id)

            if cancelled_after_step:
                return await self._cancel_run(
                    db,
                    run_id=run_id,
                    message="Agent run was cancelled after the current capability completed",
                    current_step_id=durable_step.step_id,
                    records=tuple(records),
                )

            if step_result.status is AgentStepStatus.WAITING_APPROVAL:
                await transition_run(
                    db,
                    run_id=run_id,
                    to_status="waiting_approval",
                    current_step_id=durable_step.step_id,
                    error_code=step_result.error_code,
                    error_message=step_result.error,
                )
                await db.commit()
                return AgentRunResult(
                    run_id=run_id,
                    status=AgentRunStatus.WAITING_APPROVAL,
                    steps=tuple(records),
                    next_step_id=durable_step.step_id,
                    approval_id=step_result.approval_id,
                    error_code=step_result.error_code,
                    error=step_result.error,
                )
            if step_result.status is AgentStepStatus.FAILED:
                await transition_run(
                    db,
                    run_id=run_id,
                    to_status="failed",
                    current_step_id=durable_step.step_id,
                    error_code=step_result.error_code,
                    error_message=step_result.error,
                )
                await db.commit()
                return AgentRunResult(
                    run_id=run_id,
                    status=AgentRunStatus.FAILED,
                    steps=tuple(records),
                    next_step_id=durable_step.step_id,
                    approval_id=step_result.approval_id,
                    error_code=step_result.error_code,
                    error=step_result.error,
                )
            await db.commit()

        summary = {"completed_steps": len(durable_steps)}
        await transition_run(
            db,
            run_id=run_id,
            to_status="completed",
            result=summary,
        )
        await db.commit()
        return AgentRunResult(
            run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            steps=tuple(records),
        )