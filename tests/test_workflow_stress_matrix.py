import unittest
from datetime import datetime

from packages.workflow import personal_workflow_capabilities, workflow_capabilities
from packages.workflow.spec import (
    evaluate_condition,
    next_schedule_time,
    render_value,
    validate_schedule,
    validate_workflow_spec,
)
from packages.workflow.triggers import event_matches_pattern


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
    return {
        "steps": [
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
            {
                "id": "finish",
                "capability_id": CAPABILITIES[(index + 6) % len(CAPABILITIES)],
                "depends_on": ["inspect"],
                "arguments": {"case": index, "status": "{{steps.inspect.status}}"},
            },
        ]
    }


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


if __name__ == "__main__":
    unittest.main()