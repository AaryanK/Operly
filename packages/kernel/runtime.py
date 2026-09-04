from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Tenant
from packages.kernel.approvals import (
    ApprovalError,
    consume_approval,
    create_pending_approval,
    validate_approved_invocation,
)
from packages.kernel.audit import RuntimeAuditBuffer, persist_audit
from packages.kernel.contracts import (
    AuthorizationDecision,
    CapabilityRisk,
    CapabilitySpec,
    RuntimePlan,
    RuntimeRequest,
    RuntimeResponse,
    RuntimeStage,
)
from packages.kernel.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    complete_request,
    find_completed_request,
    reserve_request,
)
from packages.kernel.policy import CapabilityPolicyEngine
from packages.kernel.providers import ProviderRegistry
from packages.kernel.registry import CapabilityRegistry, CapabilityRegistryError
from packages.kernel.schema_validation import SchemaValidationError, validate_schema
from packages.security.execution_context import ExecutionContext


class RuntimeExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        code: str,
        status_code: int = 400,
        approval_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.code = code
        self.status_code = status_code
        self.approval_id = approval_id


@dataclass(frozen=True, slots=True)
class MinimumContextLoader:
    async def load(self, db: AsyncSession, context: ExecutionContext) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope_kind": context.scope_kind.value,
            "role": context.role,
            "channel": context.channel,
            "surface": context.surface.value,
            "workspace_mode": context.workspace_mode,
        }
        if context.workspace_id:
            workspace = await db.get(Tenant, context.workspace_id)
            if workspace is None:
                raise LookupError("Workspace is unavailable")
            payload["workspace"] = {
                "id": workspace.id,
                "name": workspace.name,
                "timezone": workspace.timezone,
            }
        elif context.user_id:
            payload["personal"] = {"user_id": context.user_id}
        return payload


class DeterministicPlanner:
    """Temporary non-model planner used while AI runtime remains offline."""

    def resolve_capability(
        self,
        request: RuntimeRequest,
        *,
        registry: CapabilityRegistry,
        context: ExecutionContext,
    ) -> CapabilitySpec:
        if request.capability_id:
            return registry.get(request.capability_id)
        matches = registry.search(request.goal, context=context, effective_only=False, limit=2)
        if not matches:
            raise CapabilityRegistryError("No capability matches this goal")
        if len(matches) > 1:
            raise CapabilityRegistryError("Goal is ambiguous; provide capability_id explicitly")
        return matches[0]

    def plan(self, request: RuntimeRequest, spec: CapabilitySpec) -> RuntimePlan:
        return RuntimePlan(
            capability_id=spec.id,
            arguments=dict(request.arguments),
            reason="deterministic capability plan",
        )


class OperlyKernelRuntime:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        providers: ProviderRegistry,
        policy: CapabilityPolicyEngine | None = None,
        context_loader: MinimumContextLoader | None = None,
        planner: DeterministicPlanner | None = None,
    ) -> None:
        self.registry = registry
        self.providers = providers
        self.policy = policy or CapabilityPolicyEngine()
        self.context_loader = context_loader or MinimumContextLoader()
        self.planner = planner or DeterministicPlanner()

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        request: RuntimeRequest,
    ) -> RuntimeResponse:
        audit = RuntimeAuditBuffer()
        capability: CapabilitySpec | None = None
        decision = AuthorizationDecision.DENY
        execution_result = None
        idempotency_claim = None
        planned_arguments: dict[str, Any] | None = None
        execution_request: RuntimeRequest | None = None
        used_approval = None
        try:
            goal = str(request.goal or "").strip()
            if not goal and not request.capability_id:
                raise ValueError("A goal or capability_id is required")
            audit.step(1, RuntimeStage.UNDERSTAND.value, "ok", {"has_goal": bool(goal)})

            capability = self.planner.resolve_capability(
                request, registry=self.registry, context=context
            )
            audit.step(
                2,
                RuntimeStage.CLASSIFY.value,
                "ok",
                {"capability_id": capability.id, "risk": capability.risk.value},
            )

            if context.scope_kind.value not in capability.scopes:
                raise PermissionError("Capability does not support this scope")
            audit.step(
                3,
                RuntimeStage.RESOLVE_SCOPE.value,
                "ok",
                {"scope_kind": context.scope_kind.value, "scope_id": context.scope_id},
            )

            effective = {spec.id for spec in self.registry.effective(context)}
            audit.step(
                4,
                RuntimeStage.RESOLVE_CAPABILITIES.value,
                "ok",
                {
                    "requested": capability.id,
                    "effective": capability.id in effective,
                    "effective_count": len(effective),
                },
            )

            minimum_context = await self.context_loader.load(db, context)
            audit.step(
                5,
                RuntimeStage.LOAD_CONTEXT.value,
                "ok",
                {"context_keys": sorted(minimum_context)},
            )

            exposed = sorted(effective)
            audit.step(
                6,
                RuntimeStage.EXPOSE_TOOLS.value,
                "ok",
                {"exposed_count": len(exposed), "requested_exposed": capability.id in effective},
            )

            plan = self.planner.plan(request, capability)
            planned_arguments = dict(plan.arguments)
            validate_schema(planned_arguments, capability.input_schema)
            # Idempotency and approval claims must describe the exact operation the
            # provider will execute, not merely the ingress payload. Today the planner
            # is pass-through; a future model planner may normalize arguments. Resolve
            # the canonical capability ID and exact planned arguments once, then use
            # this execution request for replay/reservation and provider authorization.
            execution_request = RuntimeRequest(
                goal=request.goal,
                capability_id=capability.id,
                arguments=planned_arguments,
                conversation_id=request.conversation_id,
                request_id=request.request_id,
                approval_id=request.approval_id,
            )
            audit.step(
                7,
                RuntimeStage.REASON_PLAN.value,
                "ok",
                {"capability_id": plan.capability_id, "planner": "deterministic"},
            )

            # A mutation must have one stable transport/request identity before it can
            # reach authorization or generate an approval. Otherwise an approval could
            # be created that can never be safely resumed, and a client retry could be
            # indistinguishable from a second write.
            if (
                capability.risk is not CapabilityRisk.READ_ONLY
                and not str(execution_request.request_id or "").strip()
            ):
                raise ValueError("Mutating capability execution requires a stable request_id")

            # Policy is always evaluated from the freshly resolved ExecutionContext
            # before an idempotent response can be replayed. A cached result therefore
            # cannot outlive revoked workspace membership, role permissions, or surface
            # visibility. Approval is not required merely to replay an action that has
            # already committed because replay performs no new side effect.
            authorization = self.policy.evaluate(context, capability)
            decision = authorization.decision
            authorization_reason = authorization.reason
            if decision is AuthorizationDecision.DENY:
                audit.step(
                    8,
                    RuntimeStage.AUTHORIZE.value,
                    decision.value,
                    {"reason": authorization_reason},
                )
                raise PermissionError(authorization_reason)

            if capability.risk is not CapabilityRisk.READ_ONLY:
                replay = await find_completed_request(
                    db,
                    context=context,
                    request=execution_request,
                )
                if replay is not None:
                    return replay

            if decision is AuthorizationDecision.ASK and execution_request.approval_id:
                used_approval = await validate_approved_invocation(
                    db,
                    context=context,
                    approval_id=execution_request.approval_id,
                    capability_id=capability.id,
                    arguments=planned_arguments,
                )
                decision = AuthorizationDecision.ALLOW
                authorization_reason = "approved exact invocation"
            audit.step(
                8,
                RuntimeStage.AUTHORIZE.value,
                decision.value,
                {
                    "reason": authorization_reason,
                    "approval_id": execution_request.approval_id if used_approval is not None else None,
                },
            )
            if decision is AuthorizationDecision.ASK:
                raise RuntimeExecutionError(
                    "Approval is required before this capability can run",
                    run_id=audit.run_id,
                    code="approval_required",
                    status_code=409,
                )
            if decision is not AuthorizationDecision.ALLOW:
                raise PermissionError(authorization_reason)

            # Claim only an already-authorized mutating request, immediately before
            # provider execution. The idempotency layer durably reserves the exact
            # planned operation and, for approval-gated calls, claims the exact approval
            # before any side effect so a crash or response loss cannot resurrect it.
            if capability.risk is not CapabilityRisk.READ_ONLY:
                reservation = await reserve_request(
                    db,
                    context=context,
                    request=execution_request,
                    run_id=audit.run_id,
                )
                if reservation.replay is not None:
                    return reservation.replay
                idempotency_claim = reservation.claim

            provider = self.providers.get(capability.provider_id)
            execution_result = await provider.execute(
                db,
                context=context,
                capability=capability,
                arguments=planned_arguments,
                minimum_context=minimum_context,
            )
            audit.step(
                9,
                RuntimeStage.EXECUTE.value,
                "ok",
                {"provider_id": capability.provider_id},
            )

            validate_schema(dict(execution_result.value), capability.output_schema)
            audit.step(10, RuntimeStage.VALIDATE.value, "ok", {"schema": "valid"})

            audit.step(11, RuntimeStage.RECORD_TRACE.value, "ok", {"run_id": audit.run_id})
            event_types = capability.emits or ("capability.executed",)
            for event_type in event_types:
                audit.event(
                    event_type,
                    {
                        "run_id": audit.run_id,
                        "capability_id": capability.id,
                        **dict(execution_result.event_payload),
                    },
                )
            audit.step(
                12,
                RuntimeStage.EMIT_EVENTS.value,
                "ok",
                {"events": list(event_types)},
            )
            audit.step(13, RuntimeStage.RESPOND.value, "done", {"done": True})

            response = RuntimeResponse(
                run_id=audit.run_id,
                status="completed",
                capability_id=capability.id,
                decision=decision,
                result=dict(execution_result.value),
                done=True,
                trace=audit.public_trace(),
            )
            await persist_audit(
                db,
                buffer=audit,
                context=context,
                goal=goal,
                capability_id=capability.id,
                status="completed",
                result=dict(execution_result.value),
                resource_type=execution_result.resource_type,
                resource_id=execution_result.resource_id,
            )
            await consume_approval(db, approval=used_approval, run_id=audit.run_id)
            await complete_request(db, claim=idempotency_claim, response=response)
            await db.commit()
            return response
        except RuntimeExecutionError as error:
            await db.rollback()
            if (
                error.code == "approval_required"
                and capability is not None
                and planned_arguments is not None
            ):
                approval = await create_pending_approval(
                    db,
                    context=context,
                    capability_id=capability.id,
                    arguments=planned_arguments,
                    request_id=(execution_request.request_id if execution_request else request.request_id),
                    conversation_id=request.conversation_id,
                    source_run_id=audit.run_id,
                )
                error.approval_id = approval.id
            if not audit.steps or audit.steps[-1]["name"] != RuntimeStage.RESPOND.value:
                audit.step(
                    13,
                    RuntimeStage.RESPOND.value,
                    "blocked",
                    {"code": error.code, "approval_id": error.approval_id},
                )
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code=error.code,
                error=str(error),
            )
            raise
        except ApprovalError as error:
            await db.rollback()
            audit.step(13, RuntimeStage.RESPOND.value, "blocked", {"code": "approval_invalid"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="approval_invalid",
                error=str(error),
            )
            raise RuntimeExecutionError(
                str(error), run_id=audit.run_id, code="approval_invalid", status_code=409
            ) from error
        except IdempotencyConflict as error:
            await db.rollback()
            audit.step(13, RuntimeStage.RESPOND.value, "blocked", {"code": "idempotency_conflict"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="idempotency_conflict",
                error=str(error),
            )
            raise RuntimeExecutionError(
                str(error), run_id=audit.run_id, code="idempotency_conflict", status_code=409
            ) from error
        except IdempotencyInProgress as error:
            await db.rollback()
            audit.step(13, RuntimeStage.RESPOND.value, "blocked", {"code": "request_in_progress"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="request_in_progress",
                error=str(error),
            )
            raise RuntimeExecutionError(
                str(error), run_id=audit.run_id, code="request_in_progress", status_code=409
            ) from error
        except (CapabilityRegistryError, SchemaValidationError, ValueError) as error:
            await db.rollback()
            audit.step(13, RuntimeStage.RESPOND.value, "failed", {"code": "invalid_request"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="invalid_request",
                error=str(error),
            )
            raise RuntimeExecutionError(
                str(error), run_id=audit.run_id, code="invalid_request", status_code=422
            ) from error
        except PermissionError as error:
            await db.rollback()
            if not audit.steps or audit.steps[-1]["name"] != RuntimeStage.RESPOND.value:
                audit.step(13, RuntimeStage.RESPOND.value, "denied", {"code": "forbidden"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="forbidden",
                error=str(error),
            )
            raise RuntimeExecutionError(
                str(error), run_id=audit.run_id, code="forbidden", status_code=403
            ) from error
        except (LookupError, RuntimeError) as error:
            await db.rollback()
            audit.step(13, RuntimeStage.RESPOND.value, "failed", {"code": "runtime_unavailable"})
            await self._persist_failure(
                db,
                audit=audit,
                context=context,
                request=request,
                capability=capability,
                code="runtime_unavailable",
                error=str(error),
            )
            raise RuntimeExecutionError(
                "Runtime dependency is unavailable",
                run_id=audit.run_id,
                code="runtime_unavailable",
                status_code=503,
            ) from error

    async def _persist_failure(
        self,
        db: AsyncSession,
        *,
        audit: RuntimeAuditBuffer,
        context: ExecutionContext,
        request: RuntimeRequest,
        capability: CapabilitySpec | None,
        code: str,
        error: str,
    ) -> None:
        audit.event(
            "runtime.failed",
            {"run_id": audit.run_id, "code": code, "capability_id": capability.id if capability else None},
        )
        await persist_audit(
            db,
            buffer=audit,
            context=context,
            goal=str(request.goal or ""),
            capability_id=capability.id if capability else request.capability_id,
            status="failed",
            result=None,
            error=error[:2000],
        )
        await db.commit()
