"""Fail-closed routing and stage handoff for the Operly Factory control plane.

This module layers deterministic safety contracts over the existing Factory without
changing its durable checkpoint format.  The goal is to keep discovery in the control
plane, give disposable workers only executable stage tools, and carry validated
upstream results directly to dependent stages instead of asking workers to rediscover
those results through ambient history search.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import replace
from typing import Any, Iterable, MutableMapping

from .bindings import FactoryCapabilityIntentResolver as _BaseCapabilityIntentResolver
from .context_injector import StageContextInjector as _BaseStageContextInjector
from .contracts import (
    AcceptanceContract,
    ContextCapsule,
    Defect,
    StageSpec,
    StageWorkerResult,
)
from .factory import AgentFactoryControlPlane as _BaseAgentFactoryControlPlane
from .stage_runner import FactoryStageRunner as _BaseFactoryStageRunner
from .validation import ControlPlaneValidator
from .worker_adapter import AgentRuntimeWorker as _BaseAgentRuntimeWorker


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if token
    }


_DISCOVERY_CAPABILITIES = frozenset(
    {
        "context.search",
        "capability.search",
        "capability.describe",
    }
)
_READ_INTENT_TOKENS = frozenset(
    {
        "read",
        "search",
        "find",
        "list",
        "fetch",
        "retrieve",
        "get",
        "check",
        "inspect",
        "view",
        "lookup",
        "query",
        "analyze",
        "analyse",
    }
)
_MUTATING_CAPABILITY_TOKENS = frozenset(
    {
        "create",
        "send",
        "delete",
        "update",
        "modify",
        "write",
        "draft",
        "publish",
        "complete",
        "close",
        "archive",
        "move",
        "rename",
        "adjust",
        "set",
        "add",
        "remove",
        "invite",
    }
)


def _intent_domain(intent: str) -> set[str] | None:
    """Return a strong capability-family constraint when the intent names one.

    Domain filtering is intentionally conservative.  Generic intents still rely on the
    registry's semantic ranking, but explicit domains such as Gmail/Calendar/CRM must
    never resolve to a semantically adjacent capability from another family.
    """

    words = _tokens(intent)
    if "gmail" in words:
        return {"gmail"}
    if "calendar" in words:
        return {"calendar"}
    if "crm" in words:
        return {"crm"}
    if "discord" in words:
        return {"discord"}
    if "canva" in words:
        return {"canva"}
    if "task" in words or "tasks" in words:
        return {"task", "tasks"}
    if words & {"file", "files", "artifact", "artifacts", "pdf", "spreadsheet", "document"}:
        return {"file", "files", "artifact", "artifacts"}
    if "website" in words:
        return {"website"}
    if "software" in words:
        return {"software"}
    if words & {"email", "mail"}:
        return {"gmail", "email", "mail", "messaging"}
    return None


def _capability_matches_intent(capability_id: str, intent: str) -> bool:
    clean_id = str(capability_id or "").strip()
    if not clean_id or clean_id in _DISCOVERY_CAPABILITIES:
        return False

    capability_tokens = _tokens(clean_id)
    intent_tokens = _tokens(intent)
    domain = _intent_domain(intent)
    if domain is not None and not (capability_tokens & domain):
        return False

    # A read/search requirement must never be satisfied by an available write merely
    # because both capabilities live in the same provider family (for example,
    # gmail.create_draft for "Search Gmail").
    if intent_tokens & _READ_INTENT_TOKENS:
        if capability_tokens & _MUTATING_CAPABILITY_TOKENS:
            return False
    return True


class SafeFactoryCapabilityIntentResolver(_BaseCapabilityIntentResolver):
    """Resolve stage intents inside the correct authorized capability family."""

    async def __call__(self, intents: Iterable[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for intent in list(intents)[:8]:
            clean = " ".join(str(intent or "").split()).strip()
            if not clean:
                continue
            rows = self.registry.search(
                self.scope_id,
                clean,
                authority=self.authority,
                limit=max(self.max_per_intent * 4, 8),
            )
            added = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                capability_id = str(row.get("id") or "").strip()
                if (
                    not capability_id
                    or capability_id in seen
                    or not _capability_matches_intent(capability_id, clean)
                    or not self._allowed(capability_id)
                ):
                    continue
                seen.add(capability_id)
                selected.append(capability_id)
                added += 1
                if added >= self.max_per_intent or len(selected) >= self.max_total:
                    break
            if len(selected) >= self.max_total:
                break
        if selected and self.session_view is not None:
            self.session_view.expose(selected)
        return selected


_RUNTIME_CONTEXT_PHRASES = (
    "today",
    "tomorrow",
    "current date",
    "current time",
    "local date",
    "local time",
    "time zone",
    "timezone",
    "time window",
)
_HISTORY_CONTEXT_MARKERS = (
    "history",
    "historical",
    "previous",
    "prior",
    "earlier",
    "conversation",
    "memory",
    "remember",
    "workspace message",
    "channel message",
    "attachment",
    "attached",
    "uploaded",
    "artifact",
    "document",
    "file",
    "record",
    "policy",
    "preference",
    "approved",
)


def _history_intents(stage: StageSpec) -> tuple[str, ...]:
    """Keep ambient-history retrieval separate from runtime/dependency inputs.

    Existing StageSpec uses ``context_intents`` for compatibility.  At execution time
    we interpret those intents as ambient-history requests only.  Runtime time/date
    facts are never RAG queries, and a dependent stage consumes its validated upstream
    result unless it explicitly asks for historical context as well.
    """

    output: list[str] = []
    for raw in stage.context_intents:
        clean = " ".join(str(raw or "").split()).strip()
        lowered = clean.lower()
        if not clean:
            continue
        if any(marker in lowered for marker in _RUNTIME_CONTEXT_PHRASES):
            continue
        if stage.dependencies and not any(
            marker in lowered for marker in _HISTORY_CONTEXT_MARKERS
        ):
            continue
        output.append(clean)
    return tuple(output)


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple, set)):
        return [_bounded_value(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value[:2_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2_000]


def _dependency_result_payload(result: StageWorkerResult) -> dict[str, Any]:
    return {
        "status": str(result.status)[:120],
        "strategy": str(result.strategy)[:1_000],
        "summary": str(result.summary)[:6_000],
        "artifacts": list(result.artifacts)[:32],
        "evidence": _bounded_value(result.evidence),
        "evidence_refs": list(result.evidence_refs)[:32],
    }


class SafeStageContextInjector(_BaseStageContextInjector):
    """Build capsules from explicit history plus validated dependency results."""

    def __init__(
        self,
        *,
        validated_results: MutableMapping[str, StageWorkerResult] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.validated_results = validated_results if validated_results is not None else {}

    async def build(
        self,
        stage: StageSpec,
        *,
        inherited_context_refs: Iterable[str] = (),
        artifact_refs: Iterable[str] = (),
        facts: dict[str, Any] | None = None,
    ) -> ContextCapsule:
        resolved_ids: list[str] = []
        seen: set[str] = set()
        missing_intents: list[str] = []
        if stage.capability_intents:
            if self.resolve_capabilities is None:
                missing_intents.extend(stage.capability_intents)
            else:
                for intent in stage.capability_intents:
                    rows = list(
                        await _resolve(self.resolve_capabilities((intent,))) or []
                    )
                    clean_rows = [str(item).strip() for item in rows if str(item).strip()]
                    if not clean_rows:
                        missing_intents.append(str(intent))
                        continue
                    for capability_id in clean_rows:
                        if capability_id not in seen:
                            seen.add(capability_id)
                            resolved_ids.append(capability_id)

        augmented_facts = dict(facts or {})
        dependency_payload: dict[str, Any] = {}
        dependency_ids = list(
            dict.fromkeys([*stage.dependencies, *stage.input_refs])
        )[:8]
        for dependency_id in dependency_ids:
            result = self.validated_results.get(str(dependency_id))
            if result is not None:
                dependency_payload[str(dependency_id)] = _dependency_result_payload(result)
        if dependency_payload:
            augmented_facts["dependency_results"] = dependency_payload
        if missing_intents:
            augmented_facts["missing_capability_intents"] = missing_intents
        if resolved_ids:
            augmented_facts["resolved_capability_ids"] = resolved_ids[:24]

        # Capability resolution is performed one requirement at a time above so a
        # partially resolved multi-capability stage can be failed closed.  The base
        # injector remains responsible for authorized ref search/materialization.
        scoped_stage = replace(
            stage,
            context_intents=_history_intents(stage),
            capability_intents=(),
        )
        capsule = await super().build(
            scoped_stage,
            inherited_context_refs=inherited_context_refs,
            artifact_refs=artifact_refs,
            facts=augmented_facts,
        )
        return replace(
            capsule,
            stage_id=stage.id,
            objective=stage.objective,
            capability_ids=tuple(resolved_ids[:24]),
        )


class SafeAgentRuntimeWorker(_BaseAgentRuntimeWorker):
    """Fail closed before inference and never expose discovery tools to workers."""

    @staticmethod
    def _worker_capability_allowed(capability_id: str) -> bool:
        clean = str(capability_id or "").strip()
        if clean in _DISCOVERY_CAPABILITIES:
            return False
        if clean.startswith("capability."):
            return False
        return True

    async def _stage_schemas(
        self,
        stage: StageSpec,
        capsule: ContextCapsule,
    ) -> list[dict[str, Any]]:
        tools = await super()._stage_schemas(stage, capsule)
        output = []
        for schema in tools:
            function = schema.get("function") if isinstance(schema, dict) else None
            capability_id = (
                str(function.get("name") or "").strip()
                if isinstance(function, dict)
                else ""
            )
            if capability_id and self._worker_capability_allowed(capability_id):
                output.append(schema)
        return output

    async def __call__(
        self,
        stage: StageSpec,
        capsule: ContextCapsule,
        attempt: int,
        defect: Defect | None,
    ) -> StageWorkerResult:
        facts = {key: value for key, value in capsule.facts}
        missing = facts.get("missing_capability_intents")
        missing_intents = (
            [str(item) for item in missing if str(item).strip()]
            if isinstance(missing, (list, tuple, set))
            else []
        )
        executable_ids = [
            capability_id
            for capability_id in capsule.capability_ids
            if self._worker_capability_allowed(capability_id)
        ]
        if stage.capability_intents and not executable_ids and not missing_intents:
            missing_intents = [str(item) for item in stage.capability_intents]

        if missing_intents:
            return StageWorkerResult(
                status="denied",
                strategy="capability_preflight",
                summary=(
                    "The Factory blocked this stage before model execution because one "
                    "or more required capabilities could not be resolved to an "
                    "authorized executable tool."
                ),
                evidence={
                    "terminal": True,
                    "failure_class": "capability_missing",
                    "missing_capability_intents": missing_intents,
                    "resolved_capability_ids": executable_ids,
                },
                external_actions=0,
                token_usage=0,
                cost_usd=0.0,
            )
        return await super().__call__(stage, capsule, attempt, defect)


class SafeFactoryStageRunner(_BaseFactoryStageRunner):
    """Promote validated StageWorkerResult values for direct dependency handoff."""

    def __init__(
        self,
        *,
        validated_results: MutableMapping[str, StageWorkerResult] | None = None,
        **kwargs,
    ) -> None:
        self.validated_results = validated_results if validated_results is not None else {}
        super().__init__(**kwargs)

    async def _validate(
        self,
        *,
        stage: StageSpec,
        result: StageWorkerResult,
        contract: AcceptanceContract,
        repair_depth: int,
    ) -> tuple[list[Defect], set[str]]:
        defects, evidence = await super()._validate(
            stage=stage,
            result=result,
            contract=contract,
            repair_depth=repair_depth,
        )
        terminal_statuses = {
            "failed",
            "blocked",
            "denied",
            "rejected",
            "cancelled",
            "expired",
            "unverified",
            "verification_failed",
        }
        if not defects and str(result.status or "").lower() not in terminal_statuses:
            self.validated_results[stage.id] = result
        return defects, evidence


class SafeAgentFactoryControlPlane(_BaseAgentFactoryControlPlane):
    """Factory composition root with strict routing and direct stage handoff."""

    def _runner(
        self,
        *,
        run_metadata: dict[str, Any],
        ledger,
        root_inference_budget,
    ) -> SafeFactoryStageRunner:
        validated_results: dict[str, StageWorkerResult] = {}
        injector = SafeStageContextInjector(
            search=self.context_search,
            materialize=self.context_materialize,
            resolve_capabilities=self.capability_resolver,
            validated_results=validated_results,
        )
        worker = SafeAgentRuntimeWorker(
            schemas=self.schemas,
            invoke=self.invoke,
            model_resolver=self.model_resolver,
            max_steps=self.max_worker_steps,
            inference_metadata=run_metadata,
            root_inference_budget=root_inference_budget,
        )
        semantic_validator = self._bind_budget(
            self.semantic_validator,
            root_inference_budget,
        )
        repair_planner = self._bind_budget(
            self.repair_planner,
            root_inference_budget,
        )
        validator = ControlPlaneValidator(
            python_test=self.python_validator,
            semantic=semantic_validator,
        )
        return SafeFactoryStageRunner(
            context_injector=injector,
            worker=worker,
            validator=validator,
            repair=repair_planner,
            event_sink=ledger.append,
            repair_budget=self.repair_budget,
            max_parallelism=self.max_parallelism,
            validated_results=validated_results,
        )
