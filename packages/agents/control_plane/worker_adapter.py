"""Adapter that demotes the existing AgentRuntime to one disposable factory worker.

The adapter deliberately starts a fresh model transcript for every stage. Persistent
factory state flows through ContextCapsule/artifact/evidence references, never by
replaying a previous worker's conversation or chain of thought.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any, Awaitable

from packages.agents.runtime import AgentExecutionBudget, AgentRuntime
from packages.model_runtime.registry import model_for_role

from .contracts import ContextCapsule, Defect, StageSpec, StageWorkerResult
from .inference_budget import (
    FactoryInferenceBudget,
    FactoryInferenceBudgetExceeded,
    budgeted_model,
)


SchemaLoader = Callable[[], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


_KERNEL_CAPABILITIES = frozenset(
    {
        "capability.search",
        "capability.describe",
        "context.search",
        "context.get",
        "model.invoke",
        "model.deep_reason",
        "runtime.context",
    }
)
_FACTORY_CAUSATION_PREFIX = "factory"
_TERMINAL_CAPABILITY_STATUSES = frozenset(
    {
        "rejected",
        "denied",
        "cancelled",
        "expired",
        "failed",
        "verification_failed",
        "unverified",
    }
)


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _schema_id(schema: dict[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema.get("function"), dict) else {}
    return str(function.get("name") or "").strip()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _short_hash(value: str, *, size: int = 12) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[: max(6, min(int(size), 24))]


def factory_action_call_id(
    runtime_run_id: str,
    stage_id: str,
    attempt: int,
    call_id: str | None,
) -> str:
    """Create a bounded action causation ID that points back to the root Factory run.

    The model-facing tool-call ID is left untouched. Only the application-side
    capability invocation receives this correlation ID, so provider/action records can
    durably locate the paused Factory run without leaking stage state into the model
    protocol. The format remains below BusinessActionRecord.TRACE_ID_LENGTH.
    """

    run_id = str(runtime_run_id or "").strip()
    if not run_id:
        return str(call_id or "").strip()
    return (
        f"{_FACTORY_CAUSATION_PREFIX}:{run_id}:"
        f"{_short_hash(stage_id)}:{max(1, int(attempt))}:"
        f"{_short_hash(str(call_id or 'generated'))}"
    )[:160]


def factory_run_id_from_causation(causation_id: str | None) -> str | None:
    """Extract a root Factory run ID from an application-generated causation ID."""

    value = str(causation_id or "").strip()
    if not value.startswith(f"{_FACTORY_CAUSATION_PREFIX}:"):
        return None
    parts = value.split(":", 5)
    if len(parts) < 3:
        return None
    run_id = parts[1].strip()
    return run_id or None


def _extract_handles(trace: list[Any]) -> tuple[set[str], set[str], dict[str, Any]]:
    artifacts: set[str] = set()
    evidence_refs: set[str] = set()
    compact: dict[str, Any] = {}
    for entry in trace[-40:]:
        observation = getattr(entry, "observation", {})
        if not isinstance(observation, dict):
            continue
        payloads = [observation]
        nested = observation.get("observation")
        if isinstance(nested, dict):
            payloads.append(nested)
        for payload in payloads:
            for key, value in payload.items():
                lowered = str(key).lower()
                if lowered in {"artifact_id", "artifact_ref"}:
                    artifacts.update(_strings(value))
                elif lowered in {"artifact_ids", "artifact_refs"}:
                    artifacts.update(_strings(value))
                elif lowered in {"evidence_ref", "evidence_refs"}:
                    evidence_refs.update(_strings(value))
                elif lowered in {
                    "status",
                    "verified",
                    "success",
                    "count",
                    "row_count",
                    "processed_count",
                    "page_count",
                    "delivery_status",
                    "external_reference",
                    "action_id",
                    "approval_id",
                    "deferred",
                    "continuation_kind",
                    "job_id",
                    "project_id",
                    "solution_id",
                    "build_state",
                    "lifecycle_status",
                }:
                    compact[lowered] = value
    return artifacts, evidence_refs, compact


class AgentRuntimeWorker:
    """Run one focused stage with a short, metered AgentRuntime micro-loop."""

    def __init__(
        self,
        *,
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        model_resolver: Callable[[str], Any] | None = None,
        max_steps: int = 8,
        inference_metadata: dict[str, Any] | None = None,
        root_inference_budget: FactoryInferenceBudget | None = None,
        max_output_tokens: int = 2_000,
    ) -> None:
        self.schemas = schemas
        self.invoke = invoke
        self.model_resolver = model_resolver or model_for_role
        self.max_steps = max(1, min(int(max_steps), 12))
        self.inference_metadata = dict(inference_metadata or {})
        # One AgentRuntimeWorker instance is shared by the deterministic stage runner,
        # so this default ledger is root-scoped across all stages/parallel attempts.
        self.root_inference_budget = root_inference_budget or FactoryInferenceBudget()
        self.max_output_tokens = max(256, min(int(max_output_tokens), 8_000))

    async def _stage_schemas(self, capsule: ContextCapsule) -> list[dict[str, Any]]:
        available = list(await _resolve(self.schemas()) or [])
        allowed = set(capsule.capability_ids) | set(_KERNEL_CAPABILITIES)
        return [schema for schema in available if _schema_id(schema) in allowed]

    @staticmethod
    def _messages(
        stage: StageSpec,
        capsule: ContextCapsule,
        defect: Defect | None,
    ) -> list[dict[str, Any]]:
        system = (
            "You are one disposable OPERLY factory worker. Complete only this bounded stage. "
            "The Factory owns the root objective, authorization, retries and completion truth. "
            "Use only supplied tools/context. Do not claim the whole user request is complete. "
            "Return concise stage output; durable results must be represented by tool evidence or artifact references."
        )
        user_payload: dict[str, Any] = {
            "stage": stage.as_dict(),
            "context_capsule": capsule.as_dict(),
            "worker_contract": {
                "do_only_stage": True,
                "do_not_replay_prior_worker_history": True,
                "return_artifact_or_evidence_handles": True,
            },
        }
        if defect is not None:
            user_payload["repair_defect"] = defect.as_dict()
            user_payload["repair_instruction"] = (
                "Use the defect evidence to choose a materially different repair when the previous strategy failed."
            )
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, default=str),
            },
        ]

    async def __call__(
        self,
        stage: StageSpec,
        capsule: ContextCapsule,
        attempt: int,
        defect: Defect | None,
    ) -> StageWorkerResult:
        raw_model = self.model_resolver(stage.assigned_role)
        model = budgeted_model(
            raw_model,
            root_budget=self.root_inference_budget,
            max_output_tokens=self.max_output_tokens,
        )
        runtime_run_id = str(self.inference_metadata.get("runtime_run_id") or "").strip()

        async def schemas():
            return await self._stage_schemas(capsule)

        async def invoke(name: str, arguments: dict[str, Any], call_id: str | None):
            correlated_call_id = factory_action_call_id(
                runtime_run_id,
                stage.id,
                attempt,
                call_id,
            )
            return await _resolve(self.invoke(name, arguments, correlated_call_id or call_id))

        # Long-horizon work belongs to the Factory DAG. A worker gets only a short
        # reason-act-observe loop; progress may extend it by at most two calls rather
        # than silently turning an 8-step worker into the old 24-call loop.
        execution_budget = AgentExecutionBudget(
            base_steps=self.max_steps,
            max_steps=min(12, self.max_steps + 2),
            extension_steps=2,
            max_tool_calls=24,
        )
        try:
            result = await AgentRuntime(
                max_steps=self.max_steps,
                execution_budget=execution_budget,
            ).run(
                model=model,
                messages=self._messages(stage, capsule, defect),
                schemas=schemas,
                invoke=invoke,
                inference_metadata={
                    **self.inference_metadata,
                    "runtime_component": "factory_worker",
                    "factory_stage_id": stage.id,
                    "factory_attempt": attempt,
                    "worker_role": stage.assigned_role,
                },
            )
        except FactoryInferenceBudgetExceeded as error:
            usage = dict(getattr(model, "usage", {}) or {})
            return StageWorkerResult(
                status="failed",
                strategy="root_inference_budget",
                summary=(
                    "The Factory stopped this stage because the root inference budget "
                    "was exhausted before another model call could run."
                ),
                evidence={
                    "terminal": True,
                    "failure_class": "root_inference_budget_exhausted",
                    "budget_reason": error.reason,
                    "budget": error.snapshot,
                    "runtime_usage": usage,
                },
                external_actions=0,
                token_usage=max(0, int(usage.get("total_tokens") or 0)),
                cost_usd=0.0,
            )

        trace = list(result.get("trace") or [])
        artifacts, evidence_refs, compact_evidence = _extract_handles(trace)
        usage = dict(getattr(model, "usage", {}) or {})
        compact_evidence["runtime_usage"] = usage
        truth = (
            result.get("execution_truth")
            if isinstance(result.get("execution_truth"), dict)
            else {}
        )
        if truth:
            compact_evidence["execution_truth"] = dict(truth)
        capability_sequence = [
            str(getattr(entry, "capability_id", "") or "")
            for entry in trace
            if str(getattr(entry, "capability_id", "") or "")
        ]
        strategy = " -> ".join(capability_sequence[-8:]) or "reasoning_only"
        status = str(
            (truth or {}).get("status") or result.get("stop_reason") or "completed"
        ).lower()
        observed_capability_status = str(
            compact_evidence.get("status")
            or compact_evidence.get("lifecycle_status")
            or ""
        ).strip().lower()
        # AgentRuntime intentionally has a small lifecycle vocabulary. Preserve terminal
        # action truth found in capability observations so the Factory cannot mistake a
        # rejected/denied/failed durable action for an ordinary reasoning completion.
        if observed_capability_status in _TERMINAL_CAPABILITY_STATUSES:
            status = observed_capability_status
            compact_evidence["terminal"] = True
        # A capability may truthfully accept durable work while terminal evidence is
        # still pending. That is neither success nor a defect and must pause the
        # Factory rather than triggering validation/repair.
        elif bool(compact_evidence.get("deferred")):
            status = "waiting_external"
        elif status in {"pending_evidence", "waiting_external_completion"}:
            status = "waiting_external"
        if bool(result.get("stopped")) and status == "completed":
            status = "failed"
        return StageWorkerResult(
            status=status,
            strategy=strategy[:1000],
            summary=str(result.get("message") or "")[: stage.max_output_chars],
            artifacts=tuple(sorted(artifacts)),
            evidence=compact_evidence,
            evidence_refs=tuple(sorted(evidence_refs)),
            external_actions=sum(
                1
                for entry in trace
                if str(getattr(entry, "capability_id", "") or "")
                and not str(getattr(entry, "capability_id", "") or "").startswith(
                    ("context.", "capability.", "model.", "runtime.")
                )
            ),
            token_usage=max(0, int(usage.get("total_tokens") or 0)),
            cost_usd=0.0,
        )