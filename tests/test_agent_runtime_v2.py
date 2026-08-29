import json
from types import SimpleNamespace

import pytest

import packages.agent_runtime_v2.engine as engine_module
import packages.agent_runtime_v2.planner as planner_module
import packages.business_brain.runtime_v2 as runtime_v2_module
from packages.agent_runtime_v2 import Plan, RuntimeV2Engine, RuntimeV2Planner, Step
from packages.agent_runtime_v2.contracts import Observation, StepState
from packages.agent_runtime_v2.state_projection import (
    RuntimeV2ProjectedEngineMixin,
    current_observations,
    project_result,
)


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


def test_runtime_v2_projection_preserves_context_refs_after_large_search():
    refs = [
        {
            "id": f"gmail:message:{index}",
            "source": "gmail",
            "title": f"Subject {index}",
            "snippet": "x" * 2_000,
            "estimated_tokens": 300,
        }
        for index in range(20)
    ]
    projected = project_result(
        "context.search",
        {
            "ok": True,
            "status": "VERIFIED",
            "plugin": "context.search",
            "action_id": "action-not-a-context-ref",
            "verification": {"success": True},
            "observation": {
                "refs": refs,
                "ranked_refs": [row["id"] for row in refs],
                "count": 20,
                "sources": ["gmail"],
                "estimated_tokens_if_all_materialized": 6_000,
            },
        },
    )

    assert projected["usable_refs"] == [row["id"] for row in refs]
    assert projected["observation"]["refs"][0]["id"] == "gmail:message:0"
    assert projected["observation"]["refs"][-1]["id"] == "gmail:message:19"
    assert projected["action_id"] == "action-not-a-context-ref"
    assert len(projected["observation"]["refs"][0]["snippet"]) <= 700


def test_runtime_v2_current_state_drops_corrected_argument_error():
    step_state = StepState(id="scan_emails")
    step_state.observations.extend(
        [
            Observation(
                capability_id="context.search",
                arguments={"query": "after:2026-08-22", "limit": 50},
                result={
                    "ok": False,
                    "status": "INVALID_ARGUMENTS",
                    "error": "limit exceeds maximum",
                },
                signature="bad-limit",
            ),
            Observation(
                capability_id="context.search",
                arguments={"query": "after:2026-08-22", "limit": 20},
                result={
                    "ok": True,
                    "status": "VERIFIED",
                    "verification": {"success": True},
                    "observation": {
                        "ranked_refs": ["gmail:message:1", "gmail:message:2"],
                        "count": 2,
                    },
                },
                signature="good-limit",
            ),
        ]
    )

    current = current_observations(step_state.observations)
    assert [item.signature for item in current] == ["good-limit"]

    payload = RuntimeV2ProjectedEngineMixin._working_payload(step_state)
    assert len(payload) == 1
    assert payload[0]["arguments"]["limit"] == 20
    assert payload[0]["result"]["usable_refs"] == [
        "gmail:message:1",
        "gmail:message:2",
    ]


@pytest.mark.asyncio
async def test_runtime_v2_catalog_keeps_exact_gmail_calendar_and_task_operations():
    rows = {
        capability_id: {"id": capability_id}
        for capability_id in (
            "gmail.search",
            "gmail.read_message",
            "gmail.read_thread",
            "calendar.list_events",
            "task.list",
            "task.create",
            "files.create_document",
            "crm.search_contacts",
        )
    }

    class Definition:
        description = "test"
        risk_level = "read_only"
        input_schema = {"type": "object", "required": []}

    class Registry:
        def search(self, _tenant_id, query, *, authority, limit):
            del authority, limit
            if query in rows:
                return [rows[query]]
            if query.startswith("gmail"):
                return [
                    rows["crm.search_contacts"],
                    rows["gmail.read_thread"],
                    rows["gmail.search"],
                    rows["gmail.read_message"],
                    rows["files.create_document"],
                ]
            if query.startswith("calendar"):
                return [rows["crm.search_contacts"], rows["calendar.list_events"]]
            if query.startswith("task"):
                return [
                    rows["files.create_document"],
                    rows["task.create"],
                    rows["task.list"],
                ]
            return []

        def definition(self, _capability_id):
            return Definition()

        def availability(self, _tenant_id, _capability_id, *, authority):
            del authority
            return SimpleNamespace(available=True, reason=None, next_action=None)

    class Harness:
        def capability_authorized(self, _capability_id, _authority, _context):
            return True

    catalog = await runtime_v2_module._compact_catalog(
        objective=(
            "Review my emails, check my calendar, and create tasks without duplicate "
            "tasks. I may owe someone a document."
        ),
        tenant_id="tenant",
        authority={"all"},
        registry=Registry(),
        plugin_harness=Harness(),
        plugin_context=SimpleNamespace(),
    )
    capability_ids = [row["id"] for row in catalog]

    assert "gmail.search" in capability_ids
    assert "gmail.read_message" in capability_ids
    assert "gmail.read_thread" in capability_ids
    assert "calendar.list_events" in capability_ids
    assert "task.list" in capability_ids
    assert "task.create" in capability_ids
    assert "crm.search_contacts" not in capability_ids
    assert "files.create_document" not in capability_ids
