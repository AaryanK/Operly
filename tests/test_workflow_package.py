import unittest
from datetime import datetime
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.security.execution_context import PERSONAL_EXECUTION_PERMISSIONS
from packages.security.permissions import DEFAULT_ROLE_AUTHORITY, KNOWN_PERMISSIONS
from packages.workflow import personal_workflow_capabilities, workflow_capabilities
from packages.workflow.models import (
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowStepAttempt,
    WorkflowTraceEvent,
    WorkflowVersion,
)
from packages.workflow.spec import (
    WorkflowSpecError,
    evaluate_condition,
    next_schedule_time,
    validate_schedule,
    validate_workflow_spec,
)
from packages.workflow.triggers import event_matches_pattern, normalize_event_pattern


class WorkflowPackageTests(unittest.TestCase):
    def test_workflow_capabilities_are_scope_native_and_governed(self):
        workspace = {spec.id: spec for spec in workflow_capabilities()}
        personal = {spec.id: spec for spec in personal_workflow_capabilities()}
        expected = {
            "workflow.list",
            "workflow.get",
            "workflow.version.list",
            "workflow.version.get",
            "workflow.create",
            "workflow.update",
            "workflow.enable",
            "workflow.disable",
            "workflow.archive",
            "workflow.trigger.list",
            "workflow.trigger.create",
            "workflow.trigger.delete",
            "workflow.run.start",
            "workflow.run.list",
            "workflow.run.get",
            "workflow.run.cancel",
            "workflow.run.retry",
            "workflow.trace",
            "workflow.schedule.preview",
            "workflow.runtime.status",
        }
        self.assertEqual(set(workspace), expected)
        self.assertEqual(set(personal), expected)
        for spec in workspace.values():
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
            self.assertIn("workflow", spec.tags)
            self.assertIn("operations", spec.tags)
        for spec in personal.values():
            self.assertEqual(spec.scopes, frozenset({"personal"}))
            self.assertEqual(spec.resource_scope, "personal")
            self.assertIn("workflow", spec.tags)
            self.assertIn("personal", spec.tags)
        for capability_id in (
            "workflow.create",
            "workflow.update",
            "workflow.enable",
            "workflow.archive",
            "workflow.trigger.create",
        ):
            self.assertEqual(workspace[capability_id].risk, CapabilityRisk.HIGH)
            self.assertTrue(workspace[capability_id].approval_required)
            self.assertTrue(personal[capability_id].approval_required)
        self.assertEqual(workspace["workflow.run.start"].permissions, ("workflows:run",))
        self.assertEqual(workspace["workflow.runtime.status"].risk, CapabilityRisk.READ_ONLY)

    def test_workflow_authority_is_explicit_in_both_scopes(self):
        workflow_permissions = {"workflows:read", "workflows:write", "workflows:run"}
        self.assertTrue(workflow_permissions <= KNOWN_PERMISSIONS)
        self.assertTrue(workflow_permissions <= DEFAULT_ROLE_AUTHORITY["owner"])
        self.assertTrue(workflow_permissions <= PERSONAL_EXECUTION_PERMISSIONS)
        for role in ("manager", "agent", "employee"):
            self.assertFalse(workflow_permissions & DEFAULT_ROLE_AUTHORITY[role], role)

    def test_steps_can_target_any_non_workflow_capability(self):
        spec = validate_workflow_spec(
            {
                "steps": [
                    {
                        "id": "mail",
                        "capability_id": "google.gmail.send_email",
                        "arguments": {"to": "a@example.com"},
                    },
                    {
                        "id": "build",
                        "capability_id": "computer.python.exec",
                        "arguments": {
                            "computer_session_id": "{{trigger.computer_session_id}}",
                            "code": "print(1)",
                        },
                        "depends_on": ["mail"],
                    },
                    {
                        "id": "deploy",
                        "capability_id": "studio.solution.deploy",
                        "arguments": {"project_id": "{{trigger.project_id}}"},
                        "depends_on": ["build"],
                    },
                ]
            }
        )
        self.assertEqual(
            [step["capability_id"] for step in spec["steps"]],
            ["google.gmail.send_email", "computer.python.exec", "studio.solution.deploy"],
        )
        with self.assertRaises(WorkflowSpecError):
            validate_workflow_spec(
                {"steps": [{"id": "loop", "capability_id": "workflow.run.start", "arguments": {}}]}
            )

    def test_dependencies_waits_conditions_and_schedules_stay_deterministic(self):
        for invalid in (
            {
                "steps": [
                    {"id": "a", "capability_id": "workspace.summary.read", "depends_on": ["b"]},
                    {"id": "b", "capability_id": "workspace.summary.read"},
                ]
            },
            {"steps": [{"id": "a", "capability_id": "workspace.summary.read", "depends_on": ["a"]}]},
            {
                "steps": [
                    {"id": "a", "capability_id": "workspace.summary.read"},
                    {"id": "b", "capability_id": "workspace.summary.read", "depends_on": ["a", "a"]},
                ]
            },
        ):
            with self.assertRaises(WorkflowSpecError):
                validate_workflow_spec(invalid)

        validated = validate_workflow_spec(
            {
                "steps": [
                    {"id": "a", "kind": "wait", "seconds": 60},
                    {
                        "id": "b",
                        "capability_id": "workspace.summary.read",
                        "depends_on": ["a"],
                        "when": {"ref": "steps.a.status", "op": "eq", "value": "completed"},
                    },
                ]
            }
        )
        self.assertEqual(validated["steps"][0]["kind"], "wait")
        with self.assertRaises(WorkflowSpecError):
            validate_workflow_spec(
                {"steps": [{"id": "too-long", "kind": "wait", "seconds": 31 * 24 * 60 * 60 + 1}]}
            )
        with self.assertRaises(WorkflowSpecError):
            evaluate_condition(
                {"ref": "trigger.value", "op": "gt", "value": 3},
                {"trigger": {"value": "not-a-number"}},
            )

        after = datetime(2026, 8, 31, 5, 0, 0)
        interval = validate_schedule({"type": "interval", "every_seconds": 300, "timezone": "UTC"})
        self.assertEqual(next_schedule_time(interval, after=after), datetime(2026, 8, 31, 5, 5, 0))
        cron = validate_schedule({"type": "cron", "expression": "*/15 * * * *", "timezone": "UTC"})
        self.assertEqual(next_schedule_time(cron, after=after), datetime(2026, 8, 31, 5, 15, 0))
        self.assertIsNotNone(validate_schedule({"type": "once", "at": "2026-09-01T00:00:00Z"}))
        self.assertIsNotNone(validate_schedule({"type": "daily", "time": "08:00", "timezone": "UTC"}))

    def test_semantic_event_patterns_exclude_workflow_recursion(self):
        self.assertEqual(normalize_event_pattern(" CUSTOMER.CREATED "), "customer.created")
        self.assertTrue(event_matches_pattern("customer.created", "customer.created"))
        self.assertTrue(event_matches_pattern("gmail.message.received", "gmail.*"))
        self.assertTrue(event_matches_pattern("calendar.event.created", "*"))
        self.assertFalse(event_matches_pattern("workflow.run.completed", "*"))
        for invalid in ("workflow.*", "workflow.run.completed", "customer.*.created", "foo..bar"):
            with self.assertRaises(ValueError):
                normalize_event_pattern(invalid)

    def test_database_models_preserve_scope_trigger_version_and_attempt_lineage(self):
        self.assertIn("snapshot_json", WorkflowVersion.__table__.columns)
        run_table = WorkflowStepAttempt.__table__.metadata.tables["workflow_runs"]
        definition_table = WorkflowStepAttempt.__table__.metadata.tables["workflow_definitions"]
        self.assertIn("scope_kind", run_table.columns)
        self.assertIn("scope_kind", definition_table.columns)
        self.assertTrue(run_table.columns["workspace_id"].nullable)
        self.assertIn("kernel_run_id", WorkflowStepAttempt.__table__.columns)
        self.assertIn("approval_id", WorkflowStepAttempt.__table__.columns)
        self.assertIn("step_attempt_id", WorkflowTraceEvent.__table__.columns)
        self.assertIn("scope_kind", WorkflowTraceEvent.__table__.columns)
        self.assertIn("owner_user_id", WorkflowTraceEvent.__table__.columns)
        self.assertEqual(WorkflowEventTrigger.__tablename__, "workflow_event_triggers")
        self.assertEqual(WorkflowEventCursor.__tablename__, "workflow_event_cursors")

    def test_execution_is_scope_native_ha_and_correlated(self):
        root = Path(__file__).resolve().parents[1]
        engine = (root / "packages" / "workflow" / "engine.py").read_text(encoding="utf-8")
        scheduler = (root / "packages" / "workflow" / "scheduler.py").read_text(encoding="utf-8")
        trigger_dispatcher = (root / "packages" / "workflow" / "triggers.py").read_text(encoding="utf-8")
        tracing = (root / "packages" / "workflow" / "tracing.py").read_text(encoding="utf-8")
        audit = (root / "packages" / "kernel" / "audit.py").read_text(encoding="utf-8")
        access = (root / "packages" / "workflow" / "access.py").read_text(encoding="utf-8")
        provider = (root / "packages" / "workflow" / "provider.py").read_text(encoding="utf-8")
        schema = (root / "packages" / "database" / "schema.py").read_text(encoding="utf-8")

        self.assertIn("build_workspace_runtime", engine)
        self.assertIn("build_personal_runtime", engine)
        self.assertIn("resolve_execution_context", engine)
        self.assertIn("resolve_personal_execution_context", engine)
        self.assertIn("workflow.step.waiting_approval", engine)
        self.assertIn("workflow.step.approval_resumed", engine)
        self.assertIn("KernelApproval", scheduler)
        self.assertIn("with_for_update(skip_locked=True)", scheduler)
        self.assertIn("_lease_heartbeat", scheduler)
        self.assertIn('run.status = "orphaned"', scheduler)
        self.assertIn("execution_outcome_uncertain", scheduler)
        self.assertIn("WorkflowEventCursor", trigger_dispatcher)
        self.assertIn("with_for_update(skip_locked=True)", trigger_dispatcher)
        self.assertIn('f"event:{trigger.id}:{event.id}"', trigger_dispatcher)
        self.assertIn("WorkflowRun.dedupe_key == dedupe_key", trigger_dispatcher)
        self.assertIn("KernelEventRecord", tracing)
        self.assertIn("workflow_correlation_id", audit)
        self.assertIn("workflow_causation_id", audit)
        self.assertIn("workflow_depth", audit)
        self.assertIn("workflow.trigger.create", provider)
        self.assertIn("A non-owner may only access their own workflows", access)
        self.assertIn('ALEMBIC_HEAD = "0056_universal_workflow_scope_events"', schema)

    def test_workflow_engine_does_not_import_external_provider_executors(self):
        root = Path(__file__).resolve().parents[1]
        engine = (root / "packages" / "workflow" / "engine.py").read_text(encoding="utf-8")
        for forbidden in (
            "GoogleWorkspaceProvider",
            "WorkspaceCanvaProvider",
            "WorkspaceDiscordProvider",
            "WorkspaceStudioProvider",
            "AgentComputerProvider",
        ):
            self.assertNotIn(forbidden, engine)


if __name__ == "__main__":
    unittest.main()