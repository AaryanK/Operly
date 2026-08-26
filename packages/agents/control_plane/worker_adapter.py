"""Adapter that demotes the existing AgentRuntime to one disposable factory worker.

The adapter deliberately starts a fresh model transcript for every stage. Persistent
factory state flows through ContextCapsule/artifact/evidence references, never by
replaying a previous worker's conversation or chain of thought.
"""
from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any, Awaitable

from packages.agents.runtime import AgentRuntime
from packages.model_runtime.registry import model_for_role

from .contracts import ContextCapsule, Defect, StageSpec, StageWorkerResult


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
                }:
                    compact[lowered] = value
    return artifacts, evidence_refs, compact


class AgentRuntimeWorker:
    """Run one focused stage with the existing bounded AgentRuntime micro-loop."""

    def __init__(
        self,
        *,
        schemas: SchemaLoader,
        invoke: CapabilityInvoker,
        model_resolver: Callable[[str], Any] | None = None,
        max_steps: int = 8,
        inference_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.schemas = schemas
        self.invoke = invoke
        self.model_resolver = model_resolver or model_for_role
        self.max_steps = max(1, min(int(max_steps), 24))
        # Factory/run identity is application-controlled correlation, never model
        # authority. Every disposable stage shares the root runtime_run_id while its
        # own stage/attempt fields remain distinct in the trace.
        self.inference_metadata = dict(inference_metadata or {})

    async def _stage_schemas(self, capsule: ContextCapsule) -> list[dict[str, Any]]:
        available = list(await _resolve(self.schemas()) or [])
        allowed = set(capsule.capability_ids) | set(_KERNEL_CAPABILITIES)
        # If the injector could not resolve a stage intent ahead of time, keep only the
        # discovery kernel rather than dumping the whole authorized registry. The
        # worker can discover/describe an additional operation just in time.
        return [schema for schema in available if _schema_id(schema) in allowed]

    @staticmethod
    def _messages(stage: StageSpec, capsule: ContextCapsule, defect: Defect | None) -> list[dict[str, Any]]:
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
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, default=str)},
        ]

    async def __call__(
        self,
        stage: StageSpec,
        capsule: ContextCapsule,
        attempt: int,
        defect: Defect | None,
    ) -> StageWorkerResult:
        model = self.model_resolver(stage.assigned_role)

        async def schemas():
            return await self._stage_schemas(capsule)

        result = await AgentRuntime(max_steps=self.max_steps).run(
            model=model,
            messages=self._messages(stage, capsule, defect),
            schemas=schemas,
            invoke=self.invoke,
            inference_metadata={
                **self.inference_metadata,
                "runtime_component": "factory_worker",
                "factory_stage_id": stage.id,
                "factory_attempt": attempt,
                "worker_role": stage.assigned_role,
            },
        )
        trace = list(result.get("trace") or [])
        artifacts, evidence_refs, compact_evidence = _extract_handles(trace)
        truth = result.get("execution_truth") if isinstance(result.get("execution_truth"), dict) else {}
        if truth:
            compact_evidence["execution_truth"] = dict(truth)
        capability_sequence = [
            str(getattr(entry, "capability_id", "") or "")
            for entry in trace
            if str(getattr(entry, "capability_id", "") or "")
        ]
        strategy = " -> ".join(capability_sequence[-8:]) or "reasoning_only"
        status = str((truth or {}).get("status") or result.get("stop_reason") or "completed").lower()
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
            token_usage=int((result.get("budget") or {}).get("approxTokensUsed") or 0),
            cost_usd=0.0,
        )
