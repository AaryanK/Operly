from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import CapabilityRisk, RuntimeRequest
from packages.kernel.registry import CapabilityRegistryError
from packages.kernel.runtime import OperlyKernelRuntime, RuntimeExecutionError
from packages.security.execution_context import ExecutionContext

from .contracts import (
    AgentPlan,
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
    """Execute a pre-built plan only through the canonical Operly Kernel.

    This class is deliberately not a model planner. Plan contents are untrusted input:
    Kernel re-resolves the canonical capability, validates arguments, reloads current
    authority, applies approval policy, reserves mutation idempotency and records the
    audit trail for every step.
    """

    def __init__(
        self,
        *,
        kernel: OperlyKernelRuntime,
        settings: AgentRuntimeSettings | None = None,
    ) -> None:
        self.kernel = kernel
        self.settings = settings or AgentRuntimeSettings.from_environment()

    def _preflight_budget(self, plan: AgentPlan) -> str | None:
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

    async def execute_plan(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        plan: AgentPlan,
        cancellation: AgentCancellation | None = None,
    ) -> AgentRunResult:
        if not self.settings.enabled:
            raise AgentRuntimeDisabled("Agent runtime is disabled")

        budget_error = self._preflight_budget(plan)
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

            request = RuntimeRequest(
                goal=plan.goal,
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
                if error.code == "approval_required":
                    records.append(
                        AgentStepResult(
                            step_id=step.step_id,
                            capability_id=step.capability_id,
                            request_id=request_id,
                            status=AgentStepStatus.WAITING_APPROVAL,
                            kernel_run_id=error.run_id,
                            approval_id=error.approval_id,
                            error_code=error.code,
                            error=str(error),
                        )
                    )
                    return AgentRunResult(
                        run_id=plan.run_id,
                        status=AgentRunStatus.WAITING_APPROVAL,
                        steps=tuple(records),
                        next_step_id=step.step_id,
                        approval_id=error.approval_id,
                        error_code=error.code,
                        error=str(error),
                    )

                records.append(
                    AgentStepResult(
                        step_id=step.step_id,
                        capability_id=step.capability_id,
                        request_id=request_id,
                        status=AgentStepStatus.FAILED,
                        kernel_run_id=error.run_id,
                        approval_id=error.approval_id,
                        error_code=error.code,
                        error=str(error),
                    )
                )
                return AgentRunResult(
                    run_id=plan.run_id,
                    status=AgentRunStatus.FAILED,
                    steps=tuple(records),
                    next_step_id=step.step_id,
                    approval_id=error.approval_id,
                    error_code=error.code,
                    error=str(error),
                )

            records.append(
                AgentStepResult(
                    step_id=step.step_id,
                    capability_id=step.capability_id,
                    request_id=request_id,
                    status=AgentStepStatus.COMPLETED,
                    kernel_run_id=response.run_id,
                    result=dict(response.result or {}),
                )
            )

        return AgentRunResult(
            run_id=plan.run_id,
            status=AgentRunStatus.COMPLETED,
            steps=tuple(records),
        )
