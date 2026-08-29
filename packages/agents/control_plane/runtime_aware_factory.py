"""Application-controlled runtime facts and trace lifecycle for the strict Operly Factory."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable
from uuid import uuid4

from packages.database.db import session_scope
from packages.database.runtime_trace_events import emit_runtime_trace_event
from packages.model_runtime.trace_context import runtime_trace_scope
from packages.model_runtime.trace_events import RuntimeTraceEvent
from packages.security.temporal_context import resolve_temporal_context

from .contracts import StageGraph, StageWorkerResult
from .safe_factory import SafeAgentFactoryControlPlane


_FALLBACK_CAPABILITY_FAMILIES: tuple[tuple[str, frozenset[str], str], ...] = (
    ("calendar", frozenset({"calendar", "calendars", "meeting", "meetings", "appointment", "appointments"}), "calendar events"),
    ("email", frozenset({"gmail", "email", "emails", "mail", "inbox"}), "Gmail messages"),
    ("task", frozenset({"task", "tasks", "todo", "todos", "reminder", "reminders"}), "tasks"),
    ("crm", frozenset({"crm", "contact", "contacts", "customer", "customers", "lead", "leads"}), "CRM records"),
    ("discord", frozenset({"discord"}), "Discord"),
    ("canva", frozenset({"canva"}), "Canva designs"),
    ("file", frozenset({"file", "files", "document", "documents", "spreadsheet", "spreadsheets", "pdf", "pdfs", "artifact", "artifacts"}), "files"),
)


def _fallback_words(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").lower())
        if token
    }


def _fallback_operation(family: str, words: set[str]) -> str:
    """Choose one conservative canonical operation for a fallback intent."""

    if words & {"delete", "remove"}:
        return "Delete"
    if words & {"send"}:
        return "Send"
    if words & {"draft"}:
        return "Draft"
    if words & {"create", "add", "make"}:
        return "Create"
    if words & {"update", "edit", "modify", "change"}:
        return "Update"
    if words & {"complete", "finish", "close"}:
        return "Complete"
    if words & {"search", "find", "lookup", "query", "look", "review", "check", "scan"}:
        return "Search" if family in {"email", "crm"} else "List"
    if words & {"list", "show"}:
        return "List"
    if words & {"read", "get", "retrieve", "fetch", "view", "inspect"}:
        return "Search" if family in {"email", "crm"} else "List"
    if family == "calendar":
        return "List"
    if family == "email":
        return "Search"
    if family == "task":
        return "List"
    if family == "crm":
        return "Search"
    return "Use"


def _fallback_capability_intents(objective: str) -> tuple[str, ...]:
    """Split a failed blueprint's literal request into independent capability needs.

    The old fallback supplied the first 500 characters of the entire request as one
    capability intent. A multi-domain request containing Calendar + Gmail + Tasks then
    acquired contradictory domain/operation constraints and correctly failed closed.
    This fallback stays conservative but keeps each named capability family separate.
    """

    clean = " ".join(str(objective or "").split()).strip()
    if not clean:
        return ()

    segments = [
        " ".join(item.split()).strip()
        for item in re.split(r"[.!?;,:]+|\b(?:and then|then|and)\b", clean, flags=re.IGNORECASE)
        if " ".join(item.split()).strip()
    ]
    intents: list[str] = []
    seen: set[str] = set()
    for segment in segments[:24]:
        words = _fallback_words(segment)
        for family, hints, label in _FALLBACK_CAPABILITY_FAMILIES:
            if not (words & hints):
                continue
            operation = _fallback_operation(family, words)
            intent = f"{operation} {label}"
            if intent in seen:
                continue
            seen.add(intent)
            intents.append(intent)
            if len(intents) >= 8:
                return tuple(intents)

    # Preserve the historical generic semantic fallback for unknown capability
    # families, but never collapse a known multi-domain request back into one string.
    return tuple(intents) if intents else (clean[:500],)


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

    async def _compile(
        self,
        objective: str,
        *,
        ingress_metadata: dict[str, Any],
        root_inference_budget,
    ):
        blueprint = await super()._compile(
            objective,
            ingress_metadata=ingress_metadata,
            root_inference_budget=root_inference_budget,
        )
        clean = " ".join(str(objective or "").split()).strip()
        stages = tuple(blueprint.graph.stages)
        if (
            len(stages) == 1
            and stages[0].objective == clean
            and stages[0].capability_intents == (clean[:500],)
        ):
            repaired = replace(
                stages[0],
                capability_intents=_fallback_capability_intents(clean),
            )
            return replace(blueprint, graph=StageGraph((repaired,)))
        return blueprint

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

        # Bind the Factory run once so compiler, validators, repair models, workers,
        # provider wire telemetry, and orchestration events all share one AI Debug run.
        with runtime_trace_scope(run_metadata):
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
        with runtime_trace_scope(run_metadata):
            return await super().resume(
                runtime_run_id=runtime_run_id,
                metadata=run_metadata,
                stage_result=stage_result,
                stage_id=stage_id,
                facts=await self._with_runtime_facts(run_metadata, facts),
            )
