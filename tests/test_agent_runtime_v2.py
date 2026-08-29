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


@pytest.mark.asyncio
async def test_runtime_v2_planner_uses_exact_catalog_ids_and_preserves_constraints(monkeypatch):
    model = SequenceModel(
        [
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "goal": "Review obligations safely",
                        "constraints": [
                            "Do not send emails",
                            "Do not modify the calendar",
                            "Do not create duplicate or speculative tasks",
                        ],
                        "blocked": [],
                        "steps": [
                            {
                                "id": "emails",
                                "objective": "Read recent email",
                                "capabilities": ["gmail.search", "gmail.read_message"],
                                "depends_on": [],
                                "mutating": False,
                            },
                            {
                                "id": "calendar",
                                "objective": "Read upcoming calendar",
                                "capabilities": ["calendar.list_events"],
                                "depends_on": ["emails"],
                                "mutating": False,
                            },
                            {
                                "id": "tasks",
                                "objective": "Create only justified non-duplicate tasks",
                                "capabilities": ["task.list", "task.create"],
                                "depends_on": ["emails", "calendar"],
                                "mutating": True,
                            },
                            {
                                "id": "summary",
                                "objective": "Summarize obligations and actions",
                                "capabilities": [],
                                "depends_on": ["emails", "calendar", "tasks"],
                                "mutating": False,
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
        {"id": "gmail.read_message", "available": True},
        {"id": "calendar.list_events", "available": True},
        {"id": "task.list", "available": True},
        {"id": "task.create", "available": True},
    ]

    planned = await RuntimeV2Planner().plan(
        objective="Review email and calendar; create tasks only when justified.",
        capability_catalog=catalog,
        runtime_context={"now": "2026-08-29T15:00:00Z", "timezone": "America/Chicago"},
    )

    assert planned.plan.final_step_id == "summary"
    assert planned.plan.blocked == ()
    assert set(planned.plan.constraints) == {
        "Do not send emails",
        "Do not modify the calendar",
        "Do not create duplicate or speculative tasks",
    }
    assert planned.plan.steps[0].capabilities == ("gmail.search", "gmail.read_message")
    assert planned.input_tokens == 100
    assert planned.output_tokens == 20

    planner_payload = json.loads(model.requests[0].messages[1]["content"])
    assert planner_payload["output_shape"] == {
        "goal": "",
        "constraints": [],
        "blocked": [],
        "steps": [],
        "final_step_id": "",
    }


@pytest.mark.asyncio
async def test_runtime_v2_reuses_identical_verified_read_and_projects_working_state(monkeypatch):
    model = SequenceModel(
        [
            _call("gmail.search", {"query": "newer_than:7d"}, "read-1"),
            _call("gmail.search", {"query": "newer_than:7d"}, "read-2"),
            {"role": "assistant", "content": "Email scan complete."},
        ]
    )
    monkeypatch.setattr(engine_module, "model_for_role", lambda _role: model)
    connector_calls = []

    async def schemas():
        return [_tool("gmail.search")]

    async def invoke(name, arguments, _call_id):
        connector_calls.append((name, dict(arguments)))
        return {
            "ok": True,
            "status": "VERIFIED",
            "verification": {"success": True},
            "changed": False,
            "messages": [{"id": "m1", "subject": "Follow up"}],
        }

    plan = Plan(
        goal="Read recent email",
        constraints=(),
        steps=(Step("emails", "Read recent email", ("gmail.search",)),),
        final_step_id="emails",
    )
    state = await RuntimeV2Engine().run(
        objective="Read recent email",
        plan=plan,
        schemas=schemas,
        invoke=invoke,
    )

    assert state.status == "completed"
    assert connector_calls == [("gmail.search", {"query": "newer_than:7d"})]
    observations = state.steps["emails"].observations
    assert len(observations) == 2
    assert observations[1].memoized is True

    second_payload = json.loads(model.requests[1].messages[1]["content"])
    assert second_payload["working_state"][0]["capability_id"] == "gmail.search"
    third_payload = json.loads(model.requests[2].messages[1]["content"])
    assert len(third_payload["working_state"]) == 2


@pytest.mark.asyncio
async def test_runtime_v2_verified_mutation_invalidates_read_cache(monkeypatch):
    model = SequenceModel(
        [
            _call("gmail.search", {"query": "newer_than:7d"}, "read-1"),
            {"role": "assistant", "content": "Read complete."},
            _call("task.create", {"title": "Follow up", "objective": "Reply"}, "write-1"),
            {"role": "assistant", "content": "Task created."},
            _call("gmail.search", {"query": "newer_than:7d"}, "read-2"),
            {"role": "assistant", "content": "Fresh read complete."},
        ]
    )
    monkeypatch.setattr(engine_module, "model_for_role", lambda _role: model)
    connector_calls = []

    async def schemas():
        return [_tool("gmail.search"), _tool("task.create")]

    async def invoke(name, arguments, _call_id):
        connector_calls.append(name)
        return {
            "ok": True,
            "status": "VERIFIED",
            "verification": {"success": True},
            "changed": name == "task.create",
        }

    plan = Plan(
        goal="Read, act, verify",
        constraints=(),
        steps=(
            Step("read-before", "Read Gmail", ("gmail.search",)),
            Step("act", "Create task", ("task.create",), ("read-before",), True),
            Step("read-after", "Read Gmail again", ("gmail.search",), ("act",)),
        ),
        final_step_id="read-after",
    )
    state = await RuntimeV2Engine().run(
        objective="Read, act, verify",
        plan=plan,
        schemas=schemas,
        invoke=invoke,
    )

    assert state.status == "completed"
    assert state.mutation_epoch == 1
    assert connector_calls == ["gmail.search", "task.create", "gmail.search"]


@pytest.mark.asyncio
async def test_runtime_v2_blocks_before_workers_when_planner_reports_requirement(monkeypatch):
    model = SequenceModel([])
    monkeypatch.setattr(engine_module, "model_for_role", lambda _role: model)
    plan = Plan(
        goal="Use calendar",
        constraints=(),
        steps=(Step("summary", "Explain block"),),
        final_step_id="summary",
        blocked=(
            {
                "requirement": "calendar.list_events",
                "reason": "oauth_scope_missing",
            },
        ),
    )

    state = await RuntimeV2Engine().run(
        objective="Use calendar",
        plan=plan,
        schemas=lambda: [],
        invoke=lambda *_args: {},
    )

    assert state.status == "blocked"
    assert state.stop_reason == "planner_blocked_requirement"
    assert model.requests == []
