from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityRisk, RuntimeRequest, RuntimeResponse
from packages.kernel.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    find_completed_request,
)
from packages.kernel.registry import CapabilityRegistryError
from packages.kernel.runtime import OperlyKernelRuntime, RuntimeExecutionError
from packages.security.execution_context import ExecutionContext

from .contracts import (
    AgentPlan,
    AgentPlanStep,
    AgentRunResult,
    AgentRunStatus,
    AgentStepResult,
    AgentStepStatus,
    stable_step_request_id,
)


class AgentRuntimeDisabled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AgentRuntimeSettings:
    """Global kill switch for the new runtime.

    Only the new explicit variable can enable this runtime. Legacy agent flags are
    intentionally ignored so stale deployment configuration cannot revive it.
    """

    enabled: bool = False

    @classmethod
    def from_environment(cls) -> "AgentRuntimeSettings":
        return cls(enabled=os.getenv("OPERLY_AGENT_RUNTIME_ENABLED", "0").strip() == "1")


@dataclass(slots=True)
class AgentCancellation:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class GovernedAgentRuntime:
    """Execute agent steps only through the canonical Operly Kernel."""

    def __init__(
        self,
        *,
        kernel: OperlyKernelRuntime,
        settings: AgentRuntimeSettings | None = None,
    ) -> None:
        self.kernel = kernel
        self.settings = settings or AgentRuntimeSettings.from_environment()

    def require_enabled(self) -> None:
        if not self.settings.enabled:
            raise AgentRuntimeDisabled("Agent runtime is disabled")

    def preflight_plan(self, plan: AgentPlan) -> str | None:
        if len(plan.steps) > plan.budget.max_steps:
            return (
                f"Plan contains {len(plan.steps)} steps but the budget allows "
                f"{plan.budget.max_steps}"
            )

        mutations = 0
        try:
            for step in plan.steps:
                spec = self.kernel.registry.get(step.capability_id)
                if spec.risk is not CapabilityRisk.READ_ONLY:
                    mutations += 1
        except CapabilityRegistryError as error:
            return f"Plan references an unknown capability: {error}"

        if mutations > plan.budget.max_mutations:
            return (
                f"Plan contains {mutations} mutating steps but the budget allows "
                f"{plan.budget.max_mutations}"
            )
        return None

    async def _mutation_recovery_state(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        request: RuntimeRequest,
        step: AgentPlanStep,
        kernel_error: RuntimeExecutionError,
    ) -> tuple[RuntimeResponse | None, bool]:
        """Inspect Kernel's durable mutation claim after a possibly post-reservation error.

        A completed claim means the response became durable despite the observed error
        and can be replayed safely. A running claim means the provider boundary may have
        been crossed, so the outcome is fail-closed/uncertain until reconciliation.
        This helper must never be called for errors known to happen before reservation,
        such as approval_required, invalid_request, or forbidden.
        """

        try:
            spec = self.kernel.registry.get(step.capability_id)
        except CapabilityRegistryError:
            return None, kernel_error.code == "request_in_progress"
        if spec.risk is CapabilityRisk.READ_ONLY:
            return None, False

        uncertain = kernel_error.code == "request_in_progress"
        try:
            replay = await find_completed_request(
                db,
                context=context,
                request=request,
            )
        except IdempotencyInProgress:
            return None, True
        except IdempotencyConflict:
            return None, uncertain
        return replay, uncertain

    async def execute_step(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        run_id: str,
        goal: str,
        step: AgentPlanStep,
    ) -> AgentStepResult:
        """Execute exactly one logical step through Kernel.

        The deterministic request ID is reused for approval resume and crash recovery.
        Kernel remains responsible for canonical capability resolution, authorization,
        approval validation, durable mutation reservation, provider execution and audit.
        """

        self.require_enabled()
        request_id = stable_step_request_id(run_id, step.step_id)
        request = RuntimeRequest(
            goal=goal,
            capability_id=step.capability_id,
            arguments=dict(step.arguments),
            conversation_id=context.conversation_id,
            request_id=request_id,
            approval_id=step.approval_id,
        )
        try:
            response = await self.kernel.execute(
                db,
                context=context,
                request=request,
            )
        except RuntimeExecutionError as error:
            # These outcomes are produced before Kernel's durable mutation reservation
            # or are already classified as an exact-request conflict. Do not probe the
            # idempotency table: approval_required in particular must remain a clean
            # WAITING_APPROVAL transition and must work with lightweight/fake DBs.
            pre_reservation_codes = {
                "approval_required",
                "approval_invalid",
                "idempotency_conflict",
                "invalid_request",
                "forbidden",
            }
            if error.code in pre_reservation_codes:
                status = (
                    AgentStepStatus.WAITING_APPROVAL
                    if error.code == "approval_required"
                    else AgentStepStatus.FAILED
                )
                return AgentStepResult(
                    step_id=step.step_id,
                    capability_id=step.capability_id,
                    request_id=request_id,
                    status=status,
                    kernel_run_id=error.run_id,
                    approval_id=error.approval_id,
                    error_code=error.code,
                    error=str(error),
                )

            replay, uncertain = await self._mutation_recovery_state(
                db,
                context=context,
                request=request,
                step=step,
                kernel_error=error,
            )
            if replay is not None:
                return AgentStepResult(
                    step_id=step.step_id,
                    capability_id=step.capability_id,
                    request_id=request_id,
                    status=AgentStepStatus.COMPLETED,
                    kernel_run_id=replay.run_id,
                    result=dict(replay.result or {}),
                )
            if uncertain:
                return AgentStepResult(
                    step_id=step.step_id,
                    capability_id=step.capability_id,
                    request_id=request_id,
                    status=AgentStepStatus.EXECUTION_UNCERTAIN,
                    kernel_run_id=error.run_id,
                    approval_id=error.approval_id,
                    error_code="execution_outcome_uncertain",
                    error=(
                        "Mutating capability outcome is uncertain and must be reconciled "
                        f"before any fresh execution: {error.code}: {error}"
                    ),
                )
            return AgentStepResult(
                step_id=step.step_id,
                capability_id=step.capability_id,
                request_id=request_id,
                status=AgentStepStatus.FAILED,
                kernel_run_id=error.run_id,
                approval_id=error.approval_id,
                error_code=error.code,
                error=str(error),
            )

        return AgentStepResult(
            step_id=step.step_id,
            capability_id=step.capability_id,
            request_id=request_id,
            status=AgentStepStatus.COMPLETED,
            kernel_run_id=response.run_id,
            result=dict(response.result or {}),
        )

    async def execute_plan(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        plan: AgentPlan,
        cancellation: AgentCancellation | None = None,
    ) -> AgentRunResult:
        self.require_enabled()

        budget_error = self.preflight_plan(plan)
        if budget_error:
            return AgentRunResult(
                run_id=plan.run_id,
                status=AgentRunStatus.BUDGET_EXHAUSTED,
                steps=(),
                error_code="budget_exhausted",
                error=budget_error,
            )

        records: list[AgentStepResult] = []
        token = cancellation or AgentCancellation()

        for step in plan.steps:
            request_id = stable_step_request_id(plan.run_id, step.step_id)
            if token.cancelled:
                records.append(
                    AgentStepResult(
                        step_id=step.step_id,
                        capability_id=step.capability_id,
                        request_id=request_id,
                        status=AgentStepStatus.CANCELLED,
                        error_code="cancelled",
                        error="Agent run was cancelled before capability execution",
                    )
                )
                return AgentRunResult(
                    run_id=plan.run_id,
                    status=AgentRunStatus.CANCELLED,
                    steps=tuple(records),
                    next_step_id=step.step_id,
                    error_code="cancelled",
                    error="Agent run was cancelled",
                )

            step_result = await self.execute_step(
                db,
                context=context,
                run_id=plan.run_id,
                goal=plan.goal,
                step=step,
            )
            records.append(step_result)
            if step_result.status is AgentStepStatus.EXECUTION_UNCERTAIN:
                return AgentRunResult(
                    run_id=plan.run_id,
                    status=AgentRunStatus.EXECUTION_UNCERTAIN,
                    steps=tuple(records),
                    next_step_id=step.step_id,
                    approval_id=step_result.approval_id,
                    error_code=step_result.error_code,
                    error=step_result.error,
                )
            if step_result.status is AgentStepStatus.WAITING_APPROVAL:
                return AgentRunResult(
                    run_id=plan.run_id,
                    status=AgentRunStatus.WAITING_APPROVAL,
                    steps=tuple(records),
                    next_step_id=step.step_id,
                    approval_id=step_result.approval_id,
                    error_code=step_result.error_code,
                    error=step_result.error,
                )
            if step_result.status is AgentStepStatus.FAILED:
                return AgentRunResult(
                    run_id=plan.run_id,
                    status=AgentRunStatus.FAILED,
                    steps=tuple(records),
                    next_step_id=step.step_id,
                    approval_id=step_result.approval_id,
                    error_code=step_result.error_code,
                    error=step_result.error,
                )

        return AgentRunResult(
            run_id=plan.run_id,
            status=AgentRunStatus.COMPLETED,
            steps=tuple(records),
        )