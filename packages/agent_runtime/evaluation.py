from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.objective import ObjectiveInterpreter, RuntimeDispatchPath
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.agent_runtime.telemetry import runtime_trace
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind


@dataclass(frozen=True, slots=True)
class ObjectiveEvalCase:
    case_id: str
    prompt: str
    kind: str
    external_state: bool
    mutation: bool
    future_wait: bool
    dispatch: str
    required_operations: tuple[str, ...]
    resource_any: tuple[str, ...] = ()
    expected_capability: str | None = None
    complexity: str | None = None
    context: tuple[str, ...] = ()


def _case(
    case_id: str,
    prompt: str,
    *,
    kind: str,
    external: bool,
    mutation: bool = False,
    wait: bool = False,
    dispatch: str,
    ops: tuple[str, ...],
    resources: tuple[str, ...] = (),
    capability: str | None = None,
    complexity: str | None = None,
    context: tuple[str, ...] = (),
) -> ObjectiveEvalCase:
    return ObjectiveEvalCase(
        case_id=case_id,
        prompt=prompt,
        kind=kind,
        external_state=external,
        mutation=mutation,
        future_wait=wait,
        dispatch=dispatch,
        required_operations=ops,
        resource_any=resources,
        expected_capability=capability,
        complexity=complexity,
        context=context,
    )


# Synthetic, non-sensitive prompts deliberately include fragments, typos, slang,
# indirect phrasing, contrastive no-tool cases, and compound objectives. They are
# an operator scorecard for the exact model-backed ObjectiveInterpreter used by Operly.
OBJECTIVE_EVAL_CASES: tuple[ObjectiveEvalCase, ...] = (
    # No-tool / response negatives: words like email/calendar must not force tools.
    _case("respond.recursion", "explain recursion like im 12", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.rewrite", "make this sound less awkward: hey prof i got back yesterday", kind="respond", external=False, dispatch="respond", ops=("transform",)),
    _case("respond.email_concept", "what even is an email header?", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.calendar_concept", "why do calendars have leap years", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.code", "write me a python function that reverses a list", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.summarize_context", "sum that up in one line", kind="respond", external=False, dispatch="respond", ops=("transform",), context=("assistant: The runtime separates semantic intent from Kernel authorization and execution.",)),
    _case("respond.math", "whats 17*23", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.brainstorm", "give me 5 names for a personal ai memory layer", kind="respond", external=False, dispatch="respond", ops=("respond",)),

    # Gmail retrieval: natural, fragmentary, typo-heavy and indirect wording.
    _case("gmail.search.dad", "search my emails for dad's emails", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.casual", "yo what did dad email me about the flight", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.typo", "find dads emial abt my ticket", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.inbox", "look thru my inbox for the tuition receipt", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "inbox", "message"), capability="google.gmail.search"),
    _case("gmail.search.fragment", "dad emails", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.prof", "did professor muether send anything about the workshop", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.latest", "what was the last email i got from jeffrey", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.receipt", "can u see if the university ever sent me a payment receipt", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.read.id", "read gmail message 18d3abc for me", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.read_message"),
    _case("gmail.search.then_read", "find my most recent visa email and tell me exactly what it says", kind="retrieve", external=True, dispatch="agent_loop", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search", complexity="compound"),

    # Gmail mutations and retrieve+act distinction.
    _case("gmail.send.simple", "email dad that i got home safe", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.send_email"),
    _case("gmail.send.typo", "send dad an emial saying im back in wichita", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.send_email"),
    _case("gmail.draft", "draft an email to dad saying ill call tonight dont send it", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "draft", "message"), capability="google.gmail.create_draft"),
    _case("gmail.reply.latest", "reply yes sounds good to dads latest email", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search", complexity="compound"),
    _case("gmail.forwardish", "find the workshop email then email dad the date and time from it", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search", complexity="compound"),

    # Calendar reads with colloquial and abbreviated language.
    _case("calendar.tomorrow", "whats on my calendar tomorrow", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.typo", "wht meetings do i have tmrw", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.fragment", "tomorrow calendar", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.week", "show me everything im booked for this week", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.free", "am i free friday afternoon", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "availability", "schedule", "meeting"), capability="google.calendar.freebusy"),
    _case("calendar.free.casual", "check if im booked around 3ish friday", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "availability", "schedule", "meeting"), capability="google.calendar.freebusy"),
    _case("calendar.list", "what calendars do i have connected", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar",), capability="google.calendar.list_calendars"),

    # Calendar mutations. If the target must first be resolved, classify composite.
    _case("calendar.create", "put dentist on my calendar friday 2 to 3", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event", "meeting"), capability="google.calendar.create_event"),
    _case("calendar.create.casual", "book me a study block tmrw 6-8pm", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.create_event"),
    _case("calendar.move.semantic", "move whatever meeting i have at 3 tomorrow to friday", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events", complexity="compound"),
    _case("calendar.cancel.semantic", "cancel my meeting with jeffrey tomorrow", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events", complexity="compound"),
    _case("calendar.update.context", "move that to 4pm", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event", "meeting"), capability="google.calendar.update_event", context=("assistant: The selected calendar event is event_id evt-123, currently Friday at 3 PM.",)),

    # Personal tasks.
    _case("tasks.list", "what tasks do i still have open", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("task", "tasks", "todo"), capability="tasks.list"),
    _case("tasks.fragment", "my todos", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("task", "tasks", "todo"), capability="tasks.list"),
    _case("tasks.create", "add a task to submit my report tomorrow", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("task", "tasks", "todo"), capability="tasks.create"),
    _case("tasks.done", "mark the report task done", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("task", "tasks", "todo"), capability="tasks.list", complexity="compound"),

    # Workflows and runtime state.
    _case("workflow.list", "show my workflows", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("workflow",), capability="workflow.list"),
    _case("workflow.runs", "what workflow runs happened recently", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("workflow", "run"), capability="workflow.run.list"),
    _case("workflow.trace", "why did that workflow fail show me the trace", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("workflow", "trace", "run"), capability="workflow.trace"),
    _case("workflow.create", "make a workflow called morning brief", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("workflow",), capability="workflow.create"),
    _case("workflow.run", "run the morning brief workflow now", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("workflow", "run"), capability="workflow.run.start"),
    _case("workflow.retry", "retry the failed workflow run", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("workflow", "run"), capability="workflow.run.retry"),
    _case("runtime.status", "is operly runtime healthy rn", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("runtime", "system", "health"), capability="system.runtime.status"),

    # Compound cross-resource objectives should enter the agent loop.
    _case("compound.calendar_email", "check when im free friday and email dad the open times", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "email", "mail", "schedule"), complexity="compound"),
    _case("compound.email_task", "find the deadline in professors last email and make me a task for it", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "task"), complexity="compound"),
    _case("compound.calendar_create", "see if im free friday at 3 and if i am put gym on my calendar", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "availability", "event"), complexity="compound"),
    _case("compound.email_calendar", "find the meeting time from jeffreys email and add it to my calendar", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "calendar", "event"), complexity="compound"),

    # Future/event-driven objectives.
    _case("wait.email", "tell me when dad replies", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait", "retrieve"), resources=("email", "mail", "message")),
    _case("wait.email.casual", "keep an eye on my inbox and ping me if professor replies", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait", "retrieve"), resources=("email", "mail", "inbox", "message")),
    _case("wait.calendar", "let me know if anything gets added to my calendar tomorrow", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait", "retrieve"), resources=("calendar", "event")),
    _case("wait.workflow", "tell me when that workflow finishes", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait", "retrieve"), resources=("workflow", "run")),

    # Relevant-context pronoun / elliptical cases.
    _case("context.email.reply", "reply saying yes that works", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "message"), capability="google.gmail.send_email", context=("assistant: Dad's selected email is from dad@example.test and asks whether 7 PM works.",)),
    _case("context.calendar.read", "what time is that again", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting"), capability="google.calendar.list_events", context=("user: I was asking about my meeting with Jeffrey tomorrow.",)),
)


def _personal_context() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=None,
        user_id="objective-eval-user",
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="operator_eval",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="objective-eval-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:objective-eval-user",
        workspace_mode="personal",
    )


def _context_items(case: ObjectiveEvalCase) -> tuple[ContextItem, ...]:
    return tuple(
        ContextItem(
            key=f"eval:{case.case_id}:{index}",
            kind=ContextKind.CONVERSATION,
            text=text,
            relevance=1.0,
            priority=50,
        )
        for index, text in enumerate(case.context, 1)
    )


def _evaluate_case(case: ObjectiveEvalCase, objective, tool_ids: list[str]) -> tuple[bool, list[str]]:
    mismatches: list[str] = []
    if objective.kind.value != case.kind:
        mismatches.append(f"kind:{objective.kind.value}!={case.kind}")
    if objective.requires_external_state != case.external_state:
        mismatches.append(f"external:{objective.requires_external_state}!={case.external_state}")
    if objective.requires_mutation != case.mutation:
        mismatches.append(f"mutation:{objective.requires_mutation}!={case.mutation}")
    if objective.requires_future_wait != case.future_wait:
        mismatches.append(f"wait:{objective.requires_future_wait}!={case.future_wait}")
    if objective.dispatch_path().value != case.dispatch:
        mismatches.append(f"dispatch:{objective.dispatch_path().value}!={case.dispatch}")
    actual_ops = {operation.value for operation in objective.operations}
    for required in case.required_operations:
        if required not in actual_ops:
            mismatches.append(f"missing_operation:{required}")
    if case.complexity and objective.complexity.value != case.complexity:
        mismatches.append(f"complexity:{objective.complexity.value}!={case.complexity}")
    if case.resource_any:
        joined = " ".join(objective.resource_hints).lower()
        if not any(resource in joined for resource in case.resource_any):
            mismatches.append(f"resource:{list(objective.resource_hints)}")
    if case.expected_capability and case.expected_capability not in tool_ids:
        mismatches.append(f"tool_missing:{case.expected_capability}")
    return not mismatches, mismatches


async def run_live_objective_eval(*, limit: int | None = None) -> dict[str, Any]:
    """Run synthetic raw prompts through Operly's real production objective model.

    No user records, connector data, capabilities, or mutations are executed. The only
    external calls are inference requests used by ObjectiveInterpreter. Capability
    retrieval is then evaluated locally against the effective Personal registry.
    """

    context = _personal_context()
    model = OpenAICompatibleAgentModel()
    interpreter = ObjectiveInterpreter(
        model=model,
        settings=AgentRuntimeSettings(enabled=True),
    )
    registry = build_personal_runtime().registry
    cases = OBJECTIVE_EVAL_CASES[: limit or len(OBJECTIVE_EVAL_CASES)]
    passed = 0
    classifier_passed = 0
    retrieval_passed = 0
    retrieval_total = 0
    failures: list[dict[str, Any]] = []

    runtime_trace("objective_eval.started", total=len(cases))
    for case in cases:
        try:
            objective = await interpreter.interpret(
                message=case.prompt,
                context=context,
                context_items=_context_items(case),
            )
            tool_ids: list[str] = []
            if objective.requires_external_state:
                tool_ids = [
                    spec.id
                    for spec in registry.search(
                        objective.capability_query(),
                        context=context,
                        effective_only=True,
                        limit=12,
                    )
                ]
            ok, mismatches = _evaluate_case(case, objective, tool_ids)
            classifier_mismatches = [item for item in mismatches if not item.startswith("tool_missing:")]
            retrieval_mismatches = [item for item in mismatches if item.startswith("tool_missing:")]
            if not classifier_mismatches:
                classifier_passed += 1
            if case.expected_capability:
                retrieval_total += 1
                if not retrieval_mismatches:
                    retrieval_passed += 1
            if ok:
                passed += 1
            else:
                failure = {
                    "case_id": case.case_id,
                    "prompt": case.prompt,
                    "mismatches": mismatches,
                    "actual_kind": objective.kind.value,
                    "actual_operations": [operation.value for operation in objective.operations],
                    "actual_resources": list(objective.resource_hints),
                    "actual_external": objective.requires_external_state,
                    "actual_mutation": objective.requires_mutation,
                    "actual_wait": objective.requires_future_wait,
                    "actual_complexity": objective.complexity.value,
                    "actual_dispatch": objective.dispatch_path().value,
                    "tool_ids": tool_ids[:12],
                }
                failures.append(failure)
                runtime_trace("objective_eval.case_failed", **failure)
        except Exception as error:  # operator-only scorecard must continue across cases
            failure = {
                "case_id": case.case_id,
                "prompt": case.prompt,
                "mismatches": [f"exception:{type(error).__name__}"],
            }
            failures.append(failure)
            runtime_trace(
                "objective_eval.case_failed",
                case_id=case.case_id,
                prompt=case.prompt,
                mismatches=failure["mismatches"],
                error_type=type(error).__name__,
                error=str(error)[:500],
            )

    summary = {
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "classifier_passed": classifier_passed,
        "classifier_accuracy": round(classifier_passed / len(cases), 4) if cases else 0.0,
        "retrieval_total": retrieval_total,
        "retrieval_passed": retrieval_passed,
        "retrieval_hit_rate": round(retrieval_passed / retrieval_total, 4) if retrieval_total else 1.0,
        "failure_case_ids": [failure["case_id"] for failure in failures],
    }
    runtime_trace("objective_eval.completed", **summary)
    return {**summary, "failures": failures}


async def run_startup_objective_eval_if_enabled() -> dict[str, Any] | None:
    if os.getenv("OPERLY_AGENT_OBJECTIVE_EVAL_ON_START", "0").strip() != "1":
        return None
    raw_limit = os.getenv("OPERLY_AGENT_OBJECTIVE_EVAL_LIMIT", "").strip()
    limit = None
    if raw_limit:
        try:
            limit = max(1, min(int(raw_limit), len(OBJECTIVE_EVAL_CASES)))
        except ValueError:
            limit = None
    try:
        return await run_live_objective_eval(limit=limit)
    except Exception as error:
        runtime_trace(
            "objective_eval.failed",
            error_type=type(error).__name__,
            error=str(error)[:500],
        )
        return None


__all__ = [
    "OBJECTIVE_EVAL_CASES",
    "ObjectiveEvalCase",
    "run_live_objective_eval",
    "run_startup_objective_eval_if_enabled",
]
