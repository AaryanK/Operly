"""Application-controlled runtime facts and trace lifecycle for the strict Operly Factory."""
from __future__ import annotations

from typing import Any, Iterable
from uuid import uuid4

from packages.database.db import session_scope
from packages.database.runtime_trace_events import emit_runtime_trace_event
from packages.model_runtime.trace_events import RuntimeTraceEvent
from packages.security.temporal_context import resolve_temporal_context

from .contracts import StageWorkerResult
from .safe_factory import SafeAgentFactoryControlPlane


class RuntimeAwareAgentFactoryControlPlane(SafeAgentFactoryControlPlane):
    """Inject canonical temporal facts and make every Factory run observable.

    Relative time is operational state, not ambient history. Workers receive the
    actor/workspace clocks as application-authored facts so intents such as "tomorrow"
    never need semantic retrieval over old workspace messages.

    Factory tracing starts before compilation/worker inference. This is important for
    fail-closed stages: a missing required capability may correctly stop the run before
    any model call, but that zero-token execution must still exist in AI Debug.
    """

    @staticmethod
    async def _with_runtime_facts(
        metadata: dict[str, Any],
        facts: dict[str, Any] | None,
    ) -> dict[str, Any]:
        output = dict(facts or {})
        if isinstance(output.get("temporal_context"), dict):
            return output
        tenant_id = str(metadata.get("tenant_id") or "").strip() or None
        user_id = str(metadata.get("user_id") or "").strip() or None
        if not tenant_id and not user_id:
            return output
        async with session_scope() as db:
            temporal = await resolve_temporal_context(
                db,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        output["temporal_context"] = temporal.as_dict()
        return output

    @staticmethod
    def _trace_metadata(metadata: dict[str, Any], runtime_run_id: str) -> dict[str, Any]:
        output = {
            **dict(metadata),
            "runtime_run_id": runtime_run_id,
            "runtime_controller": "factory",
        }
        # Runtime trace persistence requires a canonical conversation_id. Factory
        # callers historically pass the same durable ID as _conversation_id.
        if not str(output.get("conversation_id") or "").strip():
            output["conversation_id"] = output.get("_conversation_id")
        return output

    @staticmethod
    def _capability_block_details(response) -> dict[str, Any] | None:
        if not response.execution.blocked:
            return None
        for attempt in reversed(response.execution.attempts):
            evidence = attempt.result.evidence if isinstance(attempt.result.evidence, dict) else {}
            if evidence.get("failure_class") != "capability_missing":
                continue
            missing = evidence.get("missing_capability_intents")
            return {
                "stage_id": attempt.stage_id,
                "failure_class": "capability_missing",
                "missing_capability_intents": list(missing) if isinstance(missing, list) else [],
                "stop_reason": response.execution.stop_reason,
                "token_usage": response.execution.token_usage,
                "external_actions": response.execution.external_actions,
            }
        return None

    async def run(
        self,
        *,
        objective: str,
        metadata: dict[str, Any],
        ingress_metadata: dict[str, Any] | None = None,
        initial_context_refs: set[str] | None = None,
        initial_artifact_refs: set[str] | None = None,
        stage_input_artifact_refs: dict[str, Iterable[str]] | None = None,
        facts: dict[str, Any] | None = None,
    ):
        runtime_run_id = str(metadata.get("runtime_run_id") or uuid4())
        run_metadata = self._trace_metadata(metadata, runtime_run_id)

        await emit_runtime_trace_event(
            RuntimeTraceEvent.ROUTE_SELECTED,
            {
                "controller": "factory",
                "state": "started",
                "objective_chars": len(str(objective or "")),
                "zero_model_trace_safe": True,
            },
            metadata=run_metadata,
            component="factory",
            resource_id="factory:control-plane",
        )

        response = await super().run(
            objective=objective,
            metadata=run_metadata,
            ingress_metadata=ingress_metadata,
            initial_context_refs=initial_context_refs,
            initial_artifact_refs=initial_artifact_refs,
            stage_input_artifact_refs=stage_input_artifact_refs,
            facts=await self._with_runtime_facts(run_metadata, facts),
        )

        capability_block = self._capability_block_details(response)
        if capability_block is not None:
            await emit_runtime_trace_event(
                RuntimeTraceEvent.CAPABILITY_REJECTED,
                {
                    "controller": "factory",
                    "state": "blocked",
                    **capability_block,
                },
                metadata=run_metadata,
                component="factory",
                resource_id="factory:capability-preflight",
                classification="capability_missing",
                retryable=False,
            )
        elif response.execution.completed:
            await emit_runtime_trace_event(
                RuntimeTraceEvent.WORKFLOW_COMPLETED,
                {
                    "controller": "factory",
                    "state": "completed",
                    "token_usage": response.execution.token_usage,
                    "external_actions": response.execution.external_actions,
                },
                metadata=run_metadata,
                component="factory",
                resource_id="factory:control-plane",
            )
        return response

    async def resume(
        self,
        *,
        runtime_run_id: str,
        metadata: dict[str, Any],
        stage_result: StageWorkerResult,
        stage_id: str | None = None,
        facts: dict[str, Any] | None = None,
    ):
        run_metadata = self._trace_metadata(metadata, runtime_run_id)
        return await super().resume(
            runtime_run_id=runtime_run_id,
            metadata=run_metadata,
            stage_result=stage_result,
            stage_id=stage_id,
            facts=await self._with_runtime_facts(run_metadata, facts),
        )
