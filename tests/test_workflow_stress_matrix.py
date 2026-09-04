import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import select

from packages.database.db import Base, SessionFactory, engine
from packages.database.kernel_models import KernelEventRecord
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.workflow import personal_workflow_capabilities, workflow_capabilities
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowRun,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.spec import (
    evaluate_condition,
    next_schedule_time,
    render_value,
    validate_schedule,
    validate_workflow_spec,
)
from packages.workflow.triggers import event_matches_pattern, workflow_event_dispatcher


CAPABILITIES = (
    "workspace.summary.read",
    "workspace.record.list",
    "google.gmail.search_messages",
    "google.gmail.send_email",
    "google.calendar.list_events",
    "google.calendar.create_event",
    "discord.message.send",
    "studio.solution.deploy",
    "computer.python.exec",
    "artifact.list",
)


def _linear_case(index: int) -> dict:
    size = 2 + (index % 5)
    steps = []
    for pos in range(size):
        step_id = f"s{pos}"
        step = {
            "id": step_id,
            "capability_id": CAPABILITIES[(index + pos) % len(CAPABILITIES)],
            "arguments": {
                "case": index,
                "position": pos,
                "seed": "{{trigger.seed}}",
            },
        }
        if pos:
            step["depends_on"] = [f"s{pos - 1}"]
            step["arguments"]["previous"] = f"{{{{steps.s{pos - 1}.result.value}}}}"
        steps.append(step)
    return {"steps": steps}


def _branching_case(index: int) -> dict:
    return {
        "steps": [
            {
                "id": "source",
                "capability_id": CAPABILITIES[index % len(CAPABILITIES)],
                "arguments": {"query": f"case-{index}"},
            },
            {
                "id": "left",
                "capability_id": CAPABILITIES[(index + 1) % len(CAPABILITIES)],
                "depends_on": ["source"],
                "arguments": {"input": "{{steps.source.result}}"},
            },
            {
                "id": "right",
                "capability_id": CAPABILITIES[(index + 2) % len(CAPABILITIES)],
                "depends_on": ["source"],
                "arguments": {"input": "{{steps.source.result}}"},
            },
            {
                "id": "join",
                "capability_id": CAPABILITIES[(index + 3) % len(CAPABILITIES)],
                "depends_on": ["left", "right"],
                "arguments": {
                    "left": "{{steps.left.result}}",
                    "right": "{{steps.right.result}}",
                },
            },
        ]
    }


def _wait_condition_case(index: int) -> dict:
    seconds = 1 + (index % 300)
    op = ("eq", "ne", "gt", "gte", "lt")[index % 5]
    expected = index if op in {"gt", "gte", "lt"} else f"v{index}"
    actual = index + 1 if op in {"gt", "gte", "lt"} else f"v{index}"
    return {
        "steps": [
            {
                "id": "read",
                "capability_id": CAPABILITIES[index % len(CAPABILITIES)],
                "arguments": {"value": actual},
            },
            {"id": "pause", "kind": "wait", "seconds": seconds, "depends_on": ["read"]},
            {
                "id": "conditional",
                "capability_id": CAPABILITIES[(index + 4) % len(CAPABILITIES)],
                "depends_on": ["pause"],
                "when": {"ref": "trigger.guard", "op": op, "value": expected},
                "arguments": {"from": "{{steps.read.result}}", "case": index},
            },
        ]
    }


def _resilience_case(index: int) -> dict:
    steps = [
        {
            "id": "attempt",
            "capability_id": CAPABILITIES[index % len(CAPABILITIES)],
            "on_error": "continue",
            "arguments": {"request": "{{trigger.request}}", "case": index},
        },
        {
            "id": "inspect",
            "capability_id": CAPABILITIES[(index + 5) % len(CAPABILITIES)],
            "depends_on": ["attempt"],
            "when": {
                "any": [
                    {"ref": "steps.attempt.status", "op": "eq", "value": "completed"},
                    {"ref": "steps.attempt.status", "op": "eq", "value": "failed"},
                ]
            },
            "arguments": {
                "status": "{{steps.attempt.status}}",
                "result": "{{steps.attempt.result}}",
            },
        },
    ]
    finish_dependency = "inspect"
    if index % 2:
        steps.append(
            {
                "id": "cooldown",
                "kind": "wait",
                "seconds": 1 + index,
                "depends_on": ["inspect"],
            }
        )
        finish_dependency = "cooldown"
    steps.append(
        {
            "id": "finish",
            "capability_id": CAPABILITIES[(index + 6) % len(CAPABILITIES)],
            "depends_on": [finish_dependency],
            "arguments": {"case": index, "status": "{{steps.inspect.status}}"},
        }
    )
    return {"steps": steps}


def build_workflow_matrix() -> list[dict]:
    cases = []
    for index in range(20):
        cases.append(_linear_case(index))
    for index in range(20):
        cases.append(_branching_case(index))
    for index in range(20):
        cases.append(_wait_condition_case(index))
    for index in range(20):
        cases.append(_resilience_case(index))
    return cases


EVENT_CASES = (
    ("customer.created", "customer.created", True),
    ("customer.updated", "customer.*", True),
    ("customer.deleted", "customer.created", False),
    ("gmail.message.received", "gmail.*", True),
    ("gmail.draft.created", "gmail.*", True),
    ("calendar.event.created", "calendar.*", True),
    ("calendar.event.updated", "calendar.event.updated", True),
    ("calendar.event.deleted", "calendar.event.created", False),
    ("artifact.created", "artifact.*", True),
    ("approval.approved", "approval.*", True),
    ("approval.rejected", "approval.approved", False),
    ("plugin.installed", "plugin.*", True),
    ("plugin.disabled", "*", True),
    ("workspace.member.added", "workspace.member.*", True),
    ("workflow.run.completed", "*", False),
    ("workflow.step.completed", "*", False),
)


class WorkflowStressMatrixTests(unittest.TestCase):
    def test_80_diverse_workflow_specs_validate(self):
        matrix = build_workflow_matrix()
        self.assertEqual(len(matrix), 80)
        shapes = set()
        for index, raw in enumerate(matrix):
            with self.subTest(workflow=index):
                spec = validate_workflow_spec(raw)
                self.assertGreaterEqual(len(spec["steps"]), 2)
                self.assertLessEqual(len(spec["steps"]), 100)
                ids = [step["id"] for step in spec["steps"]]
                self.assertEqual(len(ids), len(set(ids)))
                shapes.add(
                    (
                        len(spec["steps"]),
                        sum(step["kind"] == "wait" for step in spec["steps"]),
                        sum(bool(step.get("depends_on")) for step in spec["steps"]),
                        sum("when" in step for step in spec["steps"]),
                        sum(step.get("on_error") == "continue" for step in spec["steps"]),
                    )
                )
        self.assertGreaterEqual(len(shapes), 8)

    def test_16_semantic_trigger_scenarios(self):
        self.assertEqual(len(EVENT_CASES), 16)
        for event_type, pattern, expected in EVENT_CASES:
            with self.subTest(event_type=event_type, pattern=pattern):
                self.assertEqual(event_matches_pattern(event_type, pattern), expected)

    def test_matrix_totals_96_scenarios(self):
        self.assertEqual(len(build_workflow_matrix()) + len(EVENT_CASES), 96)

    def test_templates_conditions_and_schedule_families_cover_cross_step_data(self):
        context = {
            "trigger": {"seed": "alpha", "guard": 9},
            "steps": {
                "source": {"status": "completed", "result": {"value": "x"}},
                "left": {"status": "completed", "result": {"value": 2}},
            },
        }
        rendered = render_value(
            {
                "seed": "{{trigger.seed}}",
                "whole": "{{steps.source.result}}",
                "mixed": "value={{steps.source.result.value}}",
            },
            context,
        )
        self.assertEqual(rendered["seed"], "alpha")
        self.assertEqual(rendered["whole"], {"value": "x"})
        self.assertEqual(rendered["mixed"], "value=x")
        self.assertTrue(
            evaluate_condition(
                {
                    "all": [
                        {"ref": "steps.source.status", "op": "eq", "value": "completed"},
                        {"ref": "trigger.guard", "op": "gte", "value": 9},
                    ]
                },
                context,
            )
        )

        after = datetime(2026, 9, 3, 12, 0, 0)
        schedules = (
            {"type": "once", "at": "2026-09-04T12:00:00Z"},
            {"type": "interval", "every_seconds": 300, "start_at": "2026-09-03T12:00:00Z"},
            {"type": "daily", "time": "08:30", "timezone": "America/Chicago"},
            {"type": "weekly", "days": [0, 2, 4], "time": "09:00", "timezone": "UTC"},
            {"type": "cron", "expression": "*/10 * * * *", "timezone": "UTC"},
        )
        for raw in schedules:
            validated = validate_schedule(raw)
            self.assertIsNotNone(validated)
            self.assertIsNotNone(next_schedule_time(validated, after=after))

    def test_personal_and_workspace_catalogs_have_identical_workflow_surface(self):
        workspace = {spec.id: spec for spec in workflow_capabilities()}
        personal = {spec.id: spec for spec in personal_workflow_capabilities()}
        self.assertEqual(set(workspace), set(personal))
        for capability_id in workspace:
            self.assertEqual(workspace[capability_id].input_schema, personal[capability_id].input_schema)
            self.assertEqual(workspace[capability_id].permissions, personal[capability_id].permissions)
            self.assertEqual(workspace[capability_id].risk, personal[capability_id].risk)
            self.assertEqual(workspace[capability_id].approval_required, personal[capability_id].approval_required)
            self.assertEqual(workspace[capability_id].scopes, frozenset({"workspace"}))
            self.assertEqual(personal[capability_id].scopes, frozenset({"personal"}))


class WorkflowDispatcherIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def test_60_real_event_triggered_runs_are_scope_isolated_and_deduplicated(self):
        baseline = datetime.utcnow() - timedelta(seconds=2)
        async with SessionFactory() as db:
            personal_user = AppUser(email="personal-workflow-test@example.com", display_name="Personal")
            workspace_user = AppUser(email="workspace-workflow-test@example.com", display_name="Workspace")
            workspace = Tenant(name="Workflow Stress Workspace", slug="workflow-stress")
            db.add_all([personal_user, workspace_user, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=workspace_user.id, role="owner"))
            db.add(WorkflowEventCursor(id="kernel", last_created_at=baseline, last_event_id=""))

            personal_spec = validate_workflow_spec(
                {
                    "steps": [
                        {
                            "id": "read",
                            "capability_id": "google.gmail.search_messages",
                            "arguments": {"query": "{{trigger.event.payload.query}}"},
                        }
                    ]
                }
            )
            workspace_spec = validate_workflow_spec(
                {
                    "steps": [
                        {
                            "id": "read",
                            "capability_id": "workspace.summary.read",
                            "arguments": {"source": "{{trigger.event.id}}"},
                        }
                    ]
                }
            )

            for index in range(30):
                workflow = WorkflowDefinition(
                    scope_kind="personal",
                    workspace_id=None,
                    owner_user_id=personal_user.id,
                    name=f"Personal event workflow {index}",
                    description="stress",
                    status="enabled",
                    current_version=1,
                )
                db.add(workflow)
                await db.flush()
                db.add(
                    WorkflowVersion(
                        workflow_id=workflow.id,
                        version=1,
                        spec_json=json.dumps(personal_spec),
                        snapshot_json="{}",
                        created_by_user_id=personal_user.id,
                    )
                )
                db.add(
                    WorkflowEventTrigger(
                        workflow_id=workflow.id,
                        event_pattern="customer.created",
                        condition_json="{}",
                        enabled=True,
                        created_by_user_id=personal_user.id,
                    )
                )

            for index in range(30):
                workflow = WorkflowDefinition(
                    scope_kind="workspace",
                    workspace_id=workspace.id,
                    owner_user_id=workspace_user.id,
                    name=f"Workspace event workflow {index}",
                    description="stress",
                    status="enabled",
                    current_version=1,
                )
                db.add(workflow)
                await db.flush()
                db.add(
                    WorkflowVersion(
                        workflow_id=workflow.id,
                        version=1,
                        spec_json=json.dumps(workspace_spec),
                        snapshot_json="{}",
                        created_by_user_id=workspace_user.id,
                    )
                )
                db.add(
                    WorkflowEventTrigger(
                        workflow_id=workflow.id,
                        event_pattern="customer.created",
                        condition_json="{}",
                        enabled=True,
                        created_by_user_id=workspace_user.id,
                    )
                )

            personal_event = KernelEventRecord(
                event_type="customer.created",
                scope_kind="personal",
                workspace_id=None,
                owner_user_id=personal_user.id,
                principal_id=f"user:{personal_user.id}",
                actor_type="human",
                actor_id=personal_user.id,
                initiator_principal_id=f"user:{personal_user.id}",
                executor_principal_id="test",
                capability_id="customer.create",
                resource_type="customer",
                resource_id="personal-customer",
                payload_json=json.dumps({"query": "invoice", "case": "personal"}),
            )
            workspace_event = KernelEventRecord(
                event_type="customer.created",
                scope_kind="workspace",
                workspace_id=workspace.id,
                owner_user_id=None,
                principal_id=f"user:{workspace_user.id}",
                actor_type="human",
                actor_id=workspace_user.id,
                initiator_principal_id=f"user:{workspace_user.id}",
                executor_principal_id="test",
                capability_id="customer.create",
                resource_type="customer",
                resource_id="workspace-customer",
                payload_json=json.dumps({"query": "customer", "case": "workspace"}),
            )
            db.add_all([personal_event, workspace_event])
            await db.commit()
            personal_event_id = personal_event.id
            workspace_event_id = workspace_event.id

        queued = await workflow_event_dispatcher.tick()
        self.assertEqual(queued, 60)

        async with SessionFactory() as db:
            runs = (
                await db.scalars(
                    select(WorkflowRun)
                    .where(WorkflowRun.trigger_type == "event")
                    .order_by(WorkflowRun.created_at)
                )
            ).all()
            self.assertEqual(len(runs), 60)
            personal_runs = [run for run in runs if run.scope_kind == "personal"]
            workspace_runs = [run for run in runs if run.scope_kind == "workspace"]
            self.assertEqual(len(personal_runs), 30)
            self.assertEqual(len(workspace_runs), 30)
            self.assertTrue(all(run.workspace_id is None for run in personal_runs))
            self.assertTrue(all(run.authority_user_id == personal_user.id for run in personal_runs))
            self.assertTrue(all(run.workspace_id == workspace.id for run in workspace_runs))
            self.assertTrue(all(run.authority_user_id == workspace_user.id for run in workspace_runs))
            self.assertEqual(len({run.dedupe_key for run in runs}), 60)

            personal_sources = {
                json.loads(run.trigger_payload_json)["event"]["id"] for run in personal_runs
            }
            workspace_sources = {
                json.loads(run.trigger_payload_json)["event"]["id"] for run in workspace_runs
            }
            self.assertEqual(personal_sources, {personal_event_id})
            self.assertEqual(workspace_sources, {workspace_event_id})

            trace_rows = (await db.scalars(select(WorkflowTraceEvent))).all()
            queued_traces = [row for row in trace_rows if row.event_type == "workflow.run.queued"]
            self.assertEqual(len(queued_traces), 60)

        # The next pass consumes the workflow lifecycle trace events but never turns
        # them into more workflows. No source event is dispatched twice.
        queued_again = await workflow_event_dispatcher.tick()
        self.assertEqual(queued_again, 0)
        async with SessionFactory() as db:
            total_runs = len((await db.scalars(select(WorkflowRun))).all())
            self.assertEqual(total_runs, 60)


if __name__ == "__main__":
    unittest.main()