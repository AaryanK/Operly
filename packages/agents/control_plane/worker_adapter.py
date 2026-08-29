"""Adapter that runs one disposable worker per Factory stage.

Factory workers use a resettable stage runtime rather than a growing conversational
transcript. Persistent state flows through ContextCapsule/artifact/evidence references;
materialized workspace context is single-use and is never replayed after a tool round.
Verified working observations are retained separately from promoted stage evidence so
repair attempts can continue from completed work without weakening completion truth.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
from collections.abc import Callable
from typing import Any, Awaitable

from packages.agents.compaction import compact_tool_content
from packages.agents.runtime import AgentExecutionBudget
from packages.model_runtime import InferenceBudget
from packages.model_runtime.registry import model_for_role

from .contracts import ContextCapsule, Defect, StageSpec, StageWorkerResult
from .inference_budget import (
    FactoryInferenceBudget,
    FactoryInferenceBudgetExceeded,
    budgeted_model,
)
from .stage_prompt_pipeline import FactoryStagePromptPipeline
from .stage_runtime import FactoryStageRuntime as AgentRuntime


SchemaLoader = Callable[[], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
CapabilityInvoker = Callable[
    [str, dict[str, Any], str | None],
    Awaitable[dict[str, Any]] | dict[str, Any],
]


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
_CACHEABLE_READ_OPERATIONS = frozenset(
    {
        "search",
        "list",
        "read",
        "get",
        "fetch",
        "retrieve",
        "check",
        "inspect",
        "view",
        "lookup",
        "query",
    }
)
_CACHEABLE_AI_OPERATIONS = frozenset(
    {
        "extract",
        "classify",
        "analyze",
        "analyse",
        "summarize",
        "assess",
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


def _capability_tokens(capability_id: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(capability_id or "").lower())
        if token
    }


def _cacheable_capability(capability_id: str) -> bool:
    """Return True only for same-run operations safe to replay from observation cache.

    Connector reads are cacheable until a successful mutation advances the run epoch.
    A narrow set of AI analysis/extraction capabilities is also pure for identical
    arguments and can be memoized. Unknown operations fail closed as non-cacheable.
    """

    clean = str(capability_id or "").strip().lower()
    tokens = _capability_tokens(clean)
    if tokens & _CACHEABLE_READ_OPERATIONS:
        return True
    return clean.startswith("ai.") and bool(tokens & _CACHEABLE_AI_OPERATIONS)


def _verified_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is False or result.get("success") is False:
        return False
    status = str(
        result.get("status") or result.get("lifecycle_status") or ""
    ).strip().lower()
    if status in _TERMINAL_CAPABILITY_STATUSES:
        return False
    verification = result.get("verification")
    if isinstance(verification, dict) and verification.get("success") is True:
        return True
    if result.get("verified") is True or result.get("success") is True:
        return True
    if result.get("ok") is True and status in {
        "",
        "verified",
        "completed",
        "success",
        "succeeded",
    }:
        return True
    return status in {"verified", "completed", "success", "succeeded"}


def _canonical_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)


def _bounded_arguments(arguments: dict[str, Any]) -> Any:
    raw = _canonical_arguments(arguments)
    if len(raw) <= 1_200:
        return copy.deepcopy(arguments)
    return {
        "preview": raw[:1_000] + "… [bounded]",
        "digest": hashlib.sha256(raw.encode()).hexdigest()[:16],
    }


def _compact_result(result: dict[str, Any]) -> Any:
    raw = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
    compacted = compact_tool_content(raw, max_chars=1_600)
    try:
        return json.loads(compacted)
    except (TypeError, json.JSONDecodeError):
        return compacted


def _memoized_trace_entry(entry: Any) -> bool:
    observation = getattr(entry, "observation", {})
    if not isinstance(observation, dict):
        return False
    if observation.get("_operly_memoized") is True:
        return True
    nested = observation.get("observation")
    return isinstance(nested, dict) and nested.get("_operly_memoized") is True


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


def _external_action_count(trace: list[Any]) -> int:
    return sum(
        1
        for entry in trace
        if str(getattr(entry, "capability_id", "") or "")
        and not _memoized_trace_entry(entry)
        and not str(getattr(entry, "capability_id", "") or "").startswith(
            ("context.", "capability.", "model.", "runtime.")
        )
    )


class AgentRuntimeWorker:
    """Run one focused stage with a bounded, resettable Factory stage loop."""

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
        # so these ledgers are root-scoped across all stages/repair attempts.
        self.root_inference_budget = root_inference_budget or FactoryInferenceBudget()
        self.max_output_tokens = max(256, min(int(max_output_tokens), 8_000))
        self._read_observation_cache: dict[str, dict[str, Any]] = {}
        self._mutation_epoch = 0
        self._stage_working_state: dict[str, dict[str, dict[str, Any]]] = {}

    def _observation_signature(
        self,
        capability_id: str,
        arguments: dict[str, Any],
    ) -> tuple[str, str]:
        canonical = _canonical_arguments(arguments)
        raw_key = f"{self._mutation_epoch}:{capability_id}:{canonical}"
        return raw_key, hashlib.sha256(raw_key.encode()).hexdigest()[:16]

    def _remember_observation(
        self,
        *,
        stage_id: str,
        capability_id: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        signature: str,
        memoized: bool,
    ) -> None:
        stage_state = self._stage_working_state.setdefault(stage_id, {})
        existing = stage_state.get(signature)
        if existing is not None:
            existing["cache_hits"] = max(0, int(existing.get("cache_hits") or 0)) + (
                1 if memoized else 0
            )
            return
        stage_state[signature] = {
            "signature": signature,
            "capability_id": capability_id,
            "arguments": _bounded_arguments(arguments),
            "status": "verified",
            "cache_epoch": self._mutation_epoch,
            "cache_hits": 1 if memoized else 0,
            "observation": _compact_result(result),
        }

    def _working_state_snapshot(self, stage_id: str) -> list[dict[str, Any]]:
        state = self._stage_working_state.get(stage_id, {})
        return [copy.deepcopy(item) for item in list(state.values())[-16:]]

    async def _stage_schemas(
        self,
        stage: StageSpec,
        capsule: ContextCapsule,
    ) -> list[dict[str, Any]]:
        del stage  # Resolution/search belongs to the control plane, never the worker.
        available = list(await _resolve(self.schemas()) or [])
        allowed = set(capsule.capability_ids)

        # StageContextInjector owns authorized retrieval. Workers may dereference exact
        # refs selected for their capsule but may not widen scope with context.search.
        if capsule.context_refs:
            allowed.add("context.get")

        # capability.search/describe and context.search are intentionally absent. If the
        # control plane failed to resolve a required capability/context ref, validation or
        # repair handles that failure instead of allowing an unbounded discovery loop.
        return [schema for schema in available if _schema_id(schema) in allowed]

    @staticmethod
    def _messages(
        stage: StageSpec,
        capsule: ContextCapsule,
        defect: Defect | None,
    ) -> list[dict[str, Any]]:
        """Compatibility seam for tests/callers; no raw capsule replay occurs here."""

        return FactoryStagePromptPipeline(
            stage=stage,
            capsule=capsule,
            defect=defect,
        ).initial_messages()

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
        stage_state = self._stage_working_state.setdefault(stage.id, {})
        prompt_pipeline = FactoryStagePromptPipeline(
            stage=stage,
            capsule=capsule,
            defect=defect,
            working_state=list(stage_state.values()),
        )
        # Preserve a live reference: new observations are appended to this list as tool
        # calls complete, so continuation turns see them immediately. On a later repair
        # attempt a fresh pipeline receives the same run-scoped ledger snapshot.
        prompt_pipeline.working_state = []

        def sync_prompt_working_state() -> None:
            prompt_pipeline.working_state[:] = list(
                self._stage_working_state.get(stage.id, {}).values()
            )

        sync_prompt_working_state()

        async def schemas():
            return await self._stage_schemas(stage, capsule)

        async def invoke(name: str, arguments: dict[str, Any], call_id: str | None):
            cacheable = _cacheable_capability(name)
            cache_key, signature = self._observation_signature(name, arguments)
            if cacheable and cache_key in self._read_observation_cache:
                cached = copy.deepcopy(self._read_observation_cache[cache_key])
                self._remember_observation(
                    stage_id=stage.id,
                    capability_id=name,
                    arguments=arguments,
                    result=cached,
                    signature=signature,
                    memoized=True,
                )
                sync_prompt_working_state()
                cached["_operly_memoized"] = True
                cached["_operly_cache_signature"] = signature
                return cached

            correlated_call_id = factory_action_call_id(
                runtime_run_id,
                stage.id,
                attempt,
                call_id,
            )
            result = await _resolve(
                self.invoke(name, arguments, correlated_call_id or call_id)
            )
            if not isinstance(result, dict) or not _verified_result(result):
                return result

            self._remember_observation(
                stage_id=stage.id,
                capability_id=name,
                arguments=arguments,
                result=result,
                signature=signature,
                memoized=False,
            )
            if cacheable:
                self._read_observation_cache[cache_key] = copy.deepcopy(result)
            else:
                # A verified mutation may make any prior read stale. Advancing the epoch
                # invalidates memoization without deleting historical working-state facts.
                self._mutation_epoch += 1
                self._read_observation_cache.clear()
            sync_prompt_working_state()
            return result

        # Long-horizon work belongs to the Factory DAG. A worker gets a short bounded
        # loop, and every capability round resets its model-visible working set while the
        # verified observation ledger survives those resets and repair attempts.
        execution_budget = AgentExecutionBudget(
            base_steps=self.max_steps,
            max_steps=min(12, self.max_steps + 2),
            extension_steps=2,
            max_tool_calls=24,
        )
        inference_budget = InferenceBudget(
            timeout_seconds=45.0,
            attempts_per_model=1,
            max_models=2,
            max_output_tokens=self.max_output_tokens,
        )
        try:
            result = await AgentRuntime(
                max_steps=self.max_steps,
                execution_budget=execution_budget,
                inference_budget=inference_budget,
            ).run(
                model=model,
                messages=prompt_pipeline.initial_messages(),
                schemas=schemas,
                invoke=invoke,
                reduce_working_messages=prompt_pipeline.continuation_messages,
                inference_metadata={
                    **self.inference_metadata,
                    "runtime_component": "factory_worker",
                    "factory_stage_id": stage.id,
                    "factory_attempt": attempt,
                    "worker_role": stage.assigned_role,
                    "factory_context_pipeline": "bounded-reset-v3-working-state",
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
                    "working_state": self._working_state_snapshot(stage.id),
                },
                external_actions=0,
                token_usage=max(0, int(usage.get("total_tokens") or 0)),
                cost_usd=0.0,
            )

        trace = list(result.get("trace") or [])
        artifacts, evidence_refs, compact_evidence = _extract_handles(trace)
        usage = dict(getattr(model, "usage", {}) or {})
        compact_evidence["runtime_usage"] = usage
        working_state = self._working_state_snapshot(stage.id)
        if working_state:
            compact_evidence["working_state"] = working_state
        runtime_budget = result.get("budget")
        if isinstance(runtime_budget, dict):
            compact_evidence["factory_stage_runtime"] = dict(runtime_budget)
        budget_error = getattr(model, "budget_exhausted", None)
        if isinstance(budget_error, FactoryInferenceBudgetExceeded):
            compact_evidence.update(
                {
                    "terminal": True,
                    "failure_class": "root_inference_budget_exhausted",
                    "budget_reason": budget_error.reason,
                    "budget": budget_error.snapshot,
                }
            )
            return StageWorkerResult(
                status="failed",
                strategy="root_inference_budget",
                summary=(
                    "The Factory stopped this stage at the root inference budget. "
                    "Earlier capability evidence from this worker was preserved."
                ),
                artifacts=tuple(sorted(artifacts)),
                evidence=compact_evidence,
                evidence_refs=tuple(sorted(evidence_refs)),
                external_actions=_external_action_count(trace),
                token_usage=max(0, int(usage.get("total_tokens") or 0)),
                cost_usd=0.0,
            )

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
        # Preserve terminal action truth found in capability observations so the Factory
        # cannot mistake a rejected/denied/failed durable action for normal completion.
        if observed_capability_status in _TERMINAL_CAPABILITY_STATUSES:
            status = observed_capability_status
            compact_evidence["terminal"] = True
        # A capability may truthfully accept durable work while terminal evidence is
        # still pending. That pauses the Factory instead of triggering validation/repair.
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
            external_actions=_external_action_count(trace),
            token_usage=max(0, int(usage.get("total_tokens") or 0)),
            cost_usd=0.0,
        )
