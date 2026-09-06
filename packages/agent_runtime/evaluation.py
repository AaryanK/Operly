from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.objective import ObjectiveInterpretationError, ObjectiveInterpreter
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.agent_runtime.telemetry import runtime_trace
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


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
    scope: str = "personal"


def _case(
    case_id: str,
    prompt: str,
    *,
    kind: str,
    external: bool,
    mutation: bool = False,
    wait: bool = False,
    dispatch: str,
    ops: tuple[str, ...] = (),
    resources: tuple[str, ...] = (),
    capability: str | None = None,
    complexity: str | None = None,
    context: tuple[str, ...] = (),
    scope: str = "personal",
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
        scope=scope,
    )


# The first block is intentionally the fast operator slice. It mixes no-tool questions
# with clear Personal and Workspace capability boundaries, including SMB language that
# is easy to over-route merely because business nouns appear in the prompt.
NO_TOOL_AND_SCOPE_CASES: tuple[ObjectiveEvalCase, ...] = (
    # Personal: ordinary reasoning / writing must remain model-only.
    _case("personal.no_tool.invoice_vs_receipt", "whats the difference between an invoice and a receipt", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.study_plan", "give me a simple 3 day study plan for a physics quiz", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.brainstorm", "give me 5 names for a personal ai memory layer", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.rewrite", "make this sound nicer: hey professor i got back yesterday and can meet tomorrow", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.math", "whats 17*23", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.email_concept", "what even is an email header and why does it matter", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.calendar_concept", "why do calendars have leap years", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.context_summary", "sum that up in one line", kind="respond", external=False, dispatch="respond", context=("assistant: Retrieval should stay separate from authorization and execution.",)),
    _case("personal.no_tool.budget_advice", "how should i split a monthly budget if im trying to save more", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.email_template", "write me a generic email template asking for a deadline extension", kind="respond", external=False, dispatch="respond"),
    _case("personal.no_tool.workout", "whats a reasonable 4 day workout split", kind="respond", external=False, dispatch="respond"),

    # Workspace: SMB advice/writing should not touch business state merely because the
    # request arrived inside a Workspace.
    _case("workspace.no_tool.cashflow", "explain cash flow vs profit like im a first time business owner", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.gross_margin", "how do i calculate gross margin and whats a healthy number", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.pricing", "give me a basic pricing strategy for a new neighborhood coffee shop", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.no_shows", "brainstorm 5 ways a small salon can reduce appointment no shows", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.customer_apology", "write a short polite apology to an angry customer whose order was late", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.saas_metrics", "what metrics should a 5 person saas company look at every week", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.first_hire", "make me a checklist for hiring a small business's first employee", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.inventory_concept", "explain inventory turnover in plain english", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.refund_policy", "what should a reasonable refund policy cover for a small online store", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.product_copy", "rewrite this product description to sound clearer: durable bottle for everyday use", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.reorder_point", "how should a small bakery think about reorder points for ingredients", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.invoice_template", "draft a generic invoice reminder message i can reuse with customers", kind="respond", external=False, dispatch="respond", scope="workspace"),
    _case("workspace.no_tool.ar_aging", "explain accounts receivable aging to a new shop owner", kind="respond", external=False, dispatch="respond", scope="workspace"),

    # Personal tool boundary: external account state really is needed.
    _case("gmail.search.dad", "search my emails for dad's emails", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("personal.boundary.calendar", "whats on my calendar tomorrow", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("personal.boundary.send", "email dad that i got home safe", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.send_email"),
    _case("personal.boundary.freebusy", "check if im free around 3ish friday", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "availability", "schedule"), capability="google.calendar.freebusy"),
    _case("personal.boundary.wait", "keep an eye on my inbox and ping me if professor replies", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait",), resources=("email", "mail", "inbox", "message")),
    _case("personal.boundary.draft", "draft an email to dad saying ill call tonight but dont send it", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "draft", "message"), capability="google.gmail.create_draft"),
    _case("personal.boundary.read_message", "read gmail message 18d3abc for me", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "message"), capability="google.gmail.read_message"),
    _case("personal.boundary.create_event", "put dentist on my calendar friday 2 to 3", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event"), capability="google.calendar.create_event"),
    _case("personal.boundary.create_task", "add a task to submit my report tomorrow", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("task", "todo"), capability="tasks.create"),

    # Workspace tool boundary: current business state should trigger Workspace capabilities.
    _case("workspace.boundary.search_customer", "find acme in this workspace", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("customer", "workspace", "contact"), capability="workspace.search", scope="workspace"),
    _case("workspace.boundary.attention", "what needs my attention in the business today", kind="retrieve", external=True, dispatch="agent_loop", ops=("retrieve",), resources=("business", "attention", "workspace"), capability="workspace.attention.list", scope="workspace"),
    _case("workspace.boundary.invoice", "create a $500 invoice for design work due in 14 days", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("invoice", "finance"), capability="workspace.finance.invoice.create_simple", scope="workspace"),
    _case("workspace.boundary.customer_snapshot", "show me the full snapshot for that customer", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("customer", "contact", "crm"), capability="workspace.customer.snapshot", context=("assistant: The selected customer has contact_id contact-123.",), scope="workspace"),
    _case("workspace.boundary.search_invoice", "search the workspace for invoice INV-1042", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("invoice", "workspace", "file"), capability="workspace.search", scope="workspace"),
    _case("workspace.boundary.search_project", "find the launch project in this workspace", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("project", "workspace"), capability="workspace.search", scope="workspace"),
    _case("workspace.boundary.search_supplier", "search this workspace for supplier northstar", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("supplier", "workspace"), capability="workspace.search", scope="workspace"),
    _case("workspace.boundary.record_payment", "record a $500 payment against invoice INV-1042", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("payment", "invoice", "finance"), capability="workspace.finance.payment.record", scope="workspace"),
)


# Broader natural-language coverage retained after the fast scope slice.
BROAD_CASES: tuple[ObjectiveEvalCase, ...] = (
    _case("respond.recursion", "explain recursion like im 12", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("respond.rewrite", "make this sound less awkward: hey prof i got back yesterday", kind="respond", external=False, dispatch="respond", ops=("transform",)),
    _case("respond.code", "write me a python function that reverses a list", kind="respond", external=False, dispatch="respond", ops=("respond",)),
    _case("gmail.search.casual", "yo what did dad email me about the flight", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.typo", "find dads emial abt my ticket", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.inbox", "look thru my inbox for the tuition receipt", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "inbox", "message"), capability="google.gmail.search"),
    _case("gmail.search.fragment", "dad emails", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.prof", "did professor muether send anything about the workshop", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.search.latest", "what was the last email i got from jeffrey", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search"),
    _case("gmail.read.id", "read gmail message 18d3abc for me", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.read_message"),
    _case("gmail.search.then_read", "find my most recent visa email and tell me exactly what it says", kind="retrieve", external=True, dispatch="agent_loop", ops=("retrieve",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search", complexity="compound"),
    _case("gmail.send.typo", "send dad an emial saying im back in wichita", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "message"), capability="google.gmail.send_email"),
    _case("gmail.draft", "draft an email to dad saying ill call tonight dont send it", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "gmail", "draft", "message"), capability="google.gmail.create_draft"),
    _case("gmail.reply.latest", "reply yes sounds good to dads latest email", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "gmail", "message"), capability="google.gmail.search", complexity="compound"),
    _case("calendar.typo", "wht meetings do i have tmrw", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.fragment", "tomorrow calendar", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.week", "show me everything im booked for this week", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events"),
    _case("calendar.create", "put dentist on my calendar friday 2 to 3", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event", "meeting"), capability="google.calendar.create_event"),
    _case("calendar.create.casual", "book me a study block tmrw 6-8pm", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.create_event"),
    _case("calendar.move.semantic", "move whatever meeting i have at 3 tomorrow to friday", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events", complexity="compound"),
    _case("calendar.cancel.semantic", "cancel my meeting with jeffrey tomorrow", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "event", "meeting", "schedule"), capability="google.calendar.list_events", complexity="compound"),
    _case("tasks.list", "what tasks do i still have open", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("task", "tasks", "todo"), capability="tasks.list"),
    _case("tasks.fragment", "my todos", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("task", "tasks", "todo"), capability="tasks.list"),
    _case("tasks.create", "add a task to submit my report tomorrow", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("task", "tasks", "todo"), capability="tasks.create"),
    _case("workflow.list", "show my workflows", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("workflow",), capability="workflow.list"),
    _case("workflow.runs", "what workflow runs happened recently", kind="retrieve", external=True, dispatch="direct_capability", ops=("retrieve",), resources=("workflow", "run"), capability="workflow.run.list"),
    _case("workflow.create", "make a workflow called morning brief", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("workflow",), capability="workflow.create"),
    _case("workflow.run", "run the morning brief workflow now", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("workflow", "run"), capability="workflow.run.start"),
    _case("compound.calendar_email", "check when im free friday and email dad the open times", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("calendar", "email", "mail", "schedule"), complexity="compound"),
    _case("compound.email_task", "find the deadline in professors last email and make me a task for it", kind="composite", external=True, mutation=True, dispatch="agent_loop", ops=("retrieve", "act"), resources=("email", "mail", "task"), complexity="compound"),
    _case("wait.email", "tell me when dad replies", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait",), resources=("email", "mail", "message")),
    _case("wait.workflow", "tell me when that workflow finishes", kind="wait", external=True, wait=True, dispatch="wait", ops=("wait",), resources=("workflow", "run")),
    _case("context.email.reply", "reply saying yes that works", kind="act", external=True, mutation=True, dispatch="direct_capability", ops=("act",), resources=("email", "mail", "message"), capability="google.gmail.send_email", context=("assistant: Dad's selected email is from dad@example.test and asks whether 7 PM works.",)),
)


OBJECTIVE_EVAL_CASES: tuple[ObjectiveEvalCase, ...] = NO_TOOL_AND_SCOPE_CASES + BROAD_CASES


def _execution_context(scope: str) -> ExecutionContext:
    if scope == "workspace":
        return ExecutionContext(
            workspace_id="objective-eval-workspace",
            user_id="objective-eval-user",
            membership_id="objective-eval-membership",
            role="owner",
            permissions=frozenset(),
            channel="operator_eval",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id="objective-eval-workspace-conversation",
            scope_kind=ScopeKind.WORKSPACE,
            focus_workspace_id="objective-eval-workspace",
            principal_id="user:objective-eval-user",
            workspace_mode="full",
        )
    return ExecutionContext(
        workspace_id=None,
        user_id="objective-eval-user",
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="operator_eval",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="objective-eval-personal-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:objective-eval-user",
        workspace_mode="personal",
    )


def _registry(scope: str):
    if scope == "workspace":
        return build_workspace_runtime().registry
    return build_personal_runtime().registry


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
        joined = f"{objective.objective} {' '.join(objective.resource_hints)}".lower()
        if not any(resource in joined for resource in case.resource_any):
            mismatches.append(f"resource:{list(objective.resource_hints)}")
    if case.expected_capability and case.expected_capability not in tool_ids:
        mismatches.append(f"tool_missing:{case.expected_capability}")
    return not mismatches, mismatches


async def _interpret_with_backoff(
    interpreter: ObjectiveInterpreter,
    *,
    case: ObjectiveEvalCase,
    context: ExecutionContext,
    max_attempts: int,
    retry_delay_seconds: float,
):
    for attempt in range(1, max_attempts + 1):
        try:
            return await interpreter.interpret(
                message=case.prompt,
                context=context,
                context_items=_context_items(case),
            )
        except ObjectiveInterpretationError as error:
            if error.code != "objective_model_failed" or attempt >= max_attempts:
                raise
            delay = retry_delay_seconds * attempt
            runtime_trace("objective_eval.model_retry", case_id=case.case_id, attempt=attempt, delay_seconds=delay)
            await asyncio.sleep(delay)
    raise RuntimeError("objective evaluation retry loop exhausted")


async def run_live_objective_eval(
    *,
    limit: int | None = None,
    interval_seconds: float = 9.0,
    max_model_attempts: int = 3,
    retry_delay_seconds: float = 15.0,
) -> dict[str, Any]:
    """Measure semantic routing and tool-boundary behavior on synthetic prompts only.

    The evaluator uses the exact configured production model and the real Personal or
    Workspace capability registry, but never reads provider/user data and never executes
    a capability. A correctly classified no-tool case therefore performs zero capability
    search; an external-state case is ranked against the appropriate scoped registry.
    """

    interpreter = ObjectiveInterpreter(model=OpenAICompatibleAgentModel(), settings=AgentRuntimeSettings(enabled=True))
    cases = OBJECTIVE_EVAL_CASES[: limit or len(OBJECTIVE_EVAL_CASES)]
    passed = scored_total = classifier_passed = retrieval_passed = retrieval_total = model_errors = 0
    no_tool_total = no_tool_passed = 0
    failures: list[dict[str, Any]] = []
    scope_stats: dict[str, dict[str, int]] = {
        "personal": {"scored": 0, "passed": 0, "no_tool_total": 0, "no_tool_passed": 0},
        "workspace": {"scored": 0, "passed": 0, "no_tool_total": 0, "no_tool_passed": 0},
    }

    interval_seconds = max(0.0, min(float(interval_seconds), 60.0))
    max_model_attempts = max(1, min(int(max_model_attempts), 5))
    retry_delay_seconds = max(1.0, min(float(retry_delay_seconds), 60.0))
    runtime_trace("objective_eval.started", total=len(cases), interval_seconds=interval_seconds, max_model_attempts=max_model_attempts)

    for index, case in enumerate(cases):
        context = _execution_context(case.scope)
        try:
            objective = await _interpret_with_backoff(
                interpreter,
                case=case,
                context=context,
                max_attempts=max_model_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            scored_total += 1
            scope_stats[case.scope]["scored"] += 1
            tool_ids: list[str] = []
            if objective.requires_external_state:
                tool_ids = [
                    spec.id
                    for spec in _registry(case.scope).search(
                        objective.capability_query(),
                        context=context,
                        effective_only=True,
                        limit=12,
                    )
                ]

            ok, mismatches = _evaluate_case(case, objective, tool_ids)
            classifier_mismatches = [item for item in mismatches if not item.startswith("tool_missing:")]
            retrieval_mismatches = [item for item in mismatches if item.startswith("tool_missing:")]
            classifier_ok = not classifier_mismatches
            retrieval_ok = not retrieval_mismatches
            if classifier_ok:
                classifier_passed += 1
            if case.expected_capability:
                retrieval_total += 1
                if retrieval_ok:
                    retrieval_passed += 1
            if not case.external_state:
                no_tool_total += 1
                scope_stats[case.scope]["no_tool_total"] += 1
                if not objective.requires_external_state:
                    no_tool_passed += 1
                    scope_stats[case.scope]["no_tool_passed"] += 1
            if ok:
                passed += 1
                scope_stats[case.scope]["passed"] += 1
            else:
                failure = {
                    "case_id": case.case_id,
                    "scope": case.scope,
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
            runtime_trace(
                "objective_eval.case_scored",
                case_id=case.case_id,
                scope=case.scope,
                ordinal=index + 1,
                total=len(cases),
                classifier_ok=classifier_ok,
                no_tool_ok=(not objective.requires_external_state) if not case.external_state else None,
                tool_search_performed=objective.requires_external_state,
                retrieval_ok=retrieval_ok if case.expected_capability else None,
            )
        except ObjectiveInterpretationError as error:
            is_model_error = error.code == "objective_model_failed"
            if is_model_error:
                model_errors += 1
            failure = {"case_id": case.case_id, "scope": case.scope, "prompt": case.prompt, "mismatches": [f"exception:{error.code}"]}
            failures.append(failure)
            runtime_trace("objective_eval.case_unscored" if is_model_error else "objective_eval.case_failed", case_id=case.case_id, scope=case.scope, prompt=case.prompt, error_code=error.code, error=str(error)[:500])
        except Exception as error:
            model_errors += 1
            failure = {"case_id": case.case_id, "scope": case.scope, "prompt": case.prompt, "mismatches": [f"exception:{type(error).__name__}"]}
            failures.append(failure)
            runtime_trace("objective_eval.case_unscored", case_id=case.case_id, scope=case.scope, prompt=case.prompt, error_type=type(error).__name__, error=str(error)[:500])

        if index + 1 < len(cases) and interval_seconds:
            await asyncio.sleep(interval_seconds)

    semantic_failures = max(0, scored_total - passed)
    summary = {
        "total": len(cases),
        "scored_total": scored_total,
        "passed": passed,
        "semantic_failures": semantic_failures,
        "model_errors": model_errors,
        "classifier_passed": classifier_passed,
        "classifier_accuracy": round(classifier_passed / scored_total, 4) if scored_total else None,
        "retrieval_total": retrieval_total,
        "retrieval_passed": retrieval_passed,
        "retrieval_hit_rate": round(retrieval_passed / retrieval_total, 4) if retrieval_total else None,
        "no_tool_total": no_tool_total,
        "no_tool_passed": no_tool_passed,
        "no_tool_accuracy": round(no_tool_passed / no_tool_total, 4) if no_tool_total else None,
        "scope_stats": scope_stats,
        "failure_case_ids": [failure["case_id"] for failure in failures],
    }
    runtime_trace("objective_eval.completed", **summary)
    return {**summary, "failures": failures}


async def run_startup_objective_eval_if_enabled() -> dict[str, Any] | None:
    if os.getenv("OPERLY_AGENT_OBJECTIVE_EVAL_ON_START", "0").strip() != "1":
        return None

    def _int_env(name: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(int(os.getenv(name, str(default)) or str(default)), high))
        except ValueError:
            return default

    def _float_env(name: str, default: float, low: float, high: float) -> float:
        try:
            return max(low, min(float(os.getenv(name, str(default)) or str(default)), high))
        except ValueError:
            return default

    limit = _int_env("OPERLY_AGENT_OBJECTIVE_EVAL_LIMIT", len(OBJECTIVE_EVAL_CASES), 1, len(OBJECTIVE_EVAL_CASES))
    interval = _float_env("OPERLY_AGENT_OBJECTIVE_EVAL_INTERVAL_SECONDS", 9.0, 0.0, 60.0)
    attempts = _int_env("OPERLY_AGENT_OBJECTIVE_EVAL_MAX_ATTEMPTS", 3, 1, 5)
    retry_delay = _float_env("OPERLY_AGENT_OBJECTIVE_EVAL_RETRY_DELAY_SECONDS", 15.0, 1.0, 60.0)
    try:
        return await run_live_objective_eval(limit=limit, interval_seconds=interval, max_model_attempts=attempts, retry_delay_seconds=retry_delay)
    except Exception as error:
        runtime_trace("objective_eval.failed", error_type=type(error).__name__, error=str(error)[:500])
        return None


__all__ = [
    "BROAD_CASES",
    "NO_TOOL_AND_SCOPE_CASES",
    "OBJECTIVE_EVAL_CASES",
    "ObjectiveEvalCase",
    "run_live_objective_eval",
    "run_startup_objective_eval_if_enabled",
]
