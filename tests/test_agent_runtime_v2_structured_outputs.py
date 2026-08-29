import json
from types import SimpleNamespace

import pytest

import packages.agent_runtime_v2.engine as engine_module
import packages.agent_runtime_v2.planner as planner_module
from packages.agent_runtime_v2 import Plan, RuntimeV2Engine, RuntimeV2Planner, Step


class SequenceModel:
    def __init__(self, messages):
        self.messages = list(messages)
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        message = self.messages.pop(0)
        return SimpleNamespace(
            message=message,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def _tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "additionalProperties": True},
        },
    }


def _call(name, arguments, call_id):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _terminal(summary, findings=None, *, complete=None, reason=""):
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "summary": summary,
                "findings": list(findings or []),
                "refs": [],
                "coverage": {"complete": complete, "reason": reason},
            }
        ),
    }


@pytest.mark.asyncio
async def test_planner_enforces_conditional_guards_completeness_and_duplicate_check(monkeypatch):
    model = SequenceModel(
        [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "goal": "Review obligations",
                        "constraints": ["Do not create duplicate tasks"],
                        "blocked": [],
                        "steps": [
                            {
                                "id": "email_scan",
                                "objective": "Review recent email obligations",
                                "capabilities": ["gmail.search", "gmail.read_thread"],
                                "depends_on": [],
                                "mutating": False,
                                "run_if": None,
                                "requires_complete_coverage": False,
                            },
                            {
                                "id": "calendar_check",
                                "objective": "Check identified obligations against upcoming calendar events",
                                "capabilities": ["calendar.list_events"],
                                "depends_on": ["email_scan"],
                                "mutating": False,
                                "run_if": None,
                                "requires_complete_coverage": False,
                            },
                            {
                                "id": "tasks",
                                "objective": "Create justified tasks only",
                                "capabilities": ["task.create"],
                                "depends_on": ["calendar_check"],
                                "mutating": True,
                                "run_if": None,
                                "requires_complete_coverage": False,
                            },
                            {
                                "id": "summary",
                                "objective": "Summarize",
                                "capabilities": [],
                                "depends_on": ["email_scan", "calendar_check", "tasks"],
                                "mutating": False,
                                "run_if": None,
                                "requires_complete_coverage": False,
                            },
                        ],
                        "final_step_id": "summary",
                    }
                ),
            }
        ]
    )
    monkeypatch.setattr(planner_module, "model_for_role", lambda _role: model)
    catalog = [
        {"id": "gmail.search", "available": True},
        {"id": "gmail.read_thread", "available": True},
        {"id": "calendar.list_events", "available": True},
        {"id": "task.list", "available": True},
        {"id": "task.create", "available": True},
    ]
    objective = (
        "Review my emails from the last 7 days and identify obligations. "
        "For each real obligation you find, check my calendar. "
        "Create a task only when there is a clear action. Do not create duplicate tasks."
    )

    planned = await RuntimeV2Planner().plan(
        objective=objective,
        capability_catalog=catalog,
        runtime_context={"now": "2026-08-29T16:00:00Z", "timezone": "UTC"},
    )

    by_id = {step.id: step for step in planned.plan.steps}
    assert planned.plan.blocked == ()
    assert by_id["email_scan"].requires_complete_coverage is True
    assert by_id["calendar_check"].run_if_step_id == "email_scan"
    assert by_id["calendar_check"].run_if_field == "has_findings"
    assert by_id["tasks"].run_if_step_id == "email_scan"
    assert by_id["tasks"].run_if_field == "has_findings"
    assert by_id["tasks"].capabilities == ("task.list", "task.create")


@pytest.mark.asyncio
async def test_empty_findings_skip_conditional_steps_but_final_summary_runs(monkeypatch):
    model = SequenceModel(
        [
            _call("gmail.search", {"query": "after:2026-08-22", "limit": 10}, "search"),
            _terminal(
                "No clear obligations found.",
                [],
                complete=True,
                reason="The bounded search returned fewer rows than its limit.",
            ),
            _terminal("No outstanding obligations; no tasks were created.", []),
        ]
    )
    monkeypatch.setattr(engine_module, "model_for_role", lambda _role: model)
    connector_calls = []

    async def schemas():
        return [
            _tool("gmail.search"),
            _tool("calendar.list_events"),
            _tool("task.list"),
            _tool("task.create"),
        ]

    async def invoke(name, arguments, _call_id):
        connector_calls.append((name, dict(arguments)))
        assert name == "gmail.search"
        return {
            "ok": True,
            "status": "VERIFIED",
            "verification": {"success": True},
            "observation": {
                "query": arguments["query"],
                "messages": [
                    {"id": "m1", "thread_id": "t1", "subject": "Automated notice"},
                    {"id": "m2", "thread_id": "t2", "subject": "Promotion"},
                    {"id": "m3", "thread_id": "t3", "subject": "Receipt"},
                ],
            },
        }

    plan = Plan(
        goal="Review obligations",
        constraints=("Do not create duplicate tasks",),
        steps=(
            Step(
                "email_scan",
                "Review the last seven days",
                ("gmail.search",),
                requires_complete_coverage=True,
            ),
            Step(
                "calendar_check",
                "Check meetings for obligations",
                ("calendar.list_events",),
                ("email_scan",),
                run_if_step_id="email_scan",
                run_if_field="has_findings",
            ),
            Step(
                "tasks",
                "Create justified non-duplicate tasks",
                ("task.list", "task.create"),
                ("email_scan", "calendar_check"),
                True,
                run_if_step_id="email_scan",
                run_if_field="has_findings",
            ),
            Step(
                "summary",
                "Summarize",
                (),
                ("email_scan", "calendar_check", "tasks"),
            ),
        ),
        final_step_id="summary",
    )

    state = await RuntimeV2Engine().run(
        objective="Review obligations",
        plan=plan,
        schemas=schemas,
        invoke=invoke,
    )

    assert state.status == "completed"
    assert state.steps["email_scan"].status == "completed"
    assert state.steps["calendar_check"].status == "skipped"
    assert state.steps["tasks"].status == "skipped"
    assert state.steps["summary"].status == "completed"
    assert connector_calls == [("gmail.search", {"query": "after:2026-08-22", "limit": 10})]

    final_payload = json.loads(model.requests[-1].messages[1]["content"])
    email_dependency = final_payload["dependency_state"]["email_scan"]
    assert "output" in email_dependency
    assert "observations" not in email_dependency
    assert email_dependency["output"]["has_findings"] is False


@pytest.mark.asyncio
async def test_saturated_gmail_search_cannot_publish_negative_completion(monkeypatch):
    model = SequenceModel(
        [
            _call("gmail.search", {"query": "after:2026-08-22", "limit": 10}, "broad"),
            _terminal("Nothing found.", [], complete=True, reason="I reviewed the results."),
            _call("gmail.search", {"query": "after:2026-08-22 before:2026-08-26", "limit": 10}, "left"),
            _call("gmail.search", {"query": "after:2026-08-25 before:2026-08-30", "limit": 10}, "right"),
            _terminal(
                "No obligations found after splitting the saturated window.",
                [],
                complete=True,
                reason="Both narrower searches were unsaturated.",
            ),
        ]
    )
    monkeypatch.setattr(engine_module, "model_for_role", lambda _role: model)
    calls = []

    async def schemas():
        return [_tool("gmail.search")]

    async def invoke(name, arguments, _call_id):
        calls.append(dict(arguments))
        count = 10 if arguments["query"] == "after:2026-08-22" else 3
        return {
            "ok": True,
            "status": "VERIFIED",
            "verification": {"success": True},
            "observation": {
                "query": arguments["query"],
                "messages": [{"id": f"m{i}", "thread_id": f"t{i}"} for i in range(count)],
            },
        }

    plan = Plan(
        goal="Exhaustively review recent Gmail",
        constraints=(),
        steps=(Step(
            "email_scan",
            "Review all recent Gmail",
            ("gmail.search",),
            requires_complete_coverage=True,
        ),),
        final_step_id="email_scan",
    )
    state = await RuntimeV2Engine().run(
        objective="Review all recent Gmail",
        plan=plan,
        schemas=schemas,
        invoke=invoke,
    )

    assert state.status == "completed"
    assert len(calls) == 3
    assert state.steps["email_scan"].model_calls == 5
    third_payload = json.loads(model.requests[2].messages[1]["content"])
    feedback = [
        row for row in third_payload["working_state"]
        if row["capability_id"] == "runtime.completion"
    ]
    assert feedback
    assert "saturated" in feedback[-1]["result"]["reason"].lower()
