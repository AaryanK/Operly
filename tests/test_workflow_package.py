import unittest
from datetime import datetime
from pathlib import Path

from packages.kernel.contracts import CapabilityRisk
from packages.security.permissions import DEFAULT_ROLE_AUTHORITY, KNOWN_PERMISSIONS
from packages.workflow import workflow_capabilities
from packages.workflow.models import WorkflowStepAttempt, WorkflowTraceEvent, WorkflowVersion
from packages.workflow.spec import (
    WorkflowSpecError,
    evaluate_condition,
    next_schedule_time,
    validate_schedule,
    validate_workflow_spec,
)


class WorkflowPackageTests(unittest.TestCase):
    def test_workflow_capabilities_are_workspace_scoped_and_governed(self):
        specs = {spec.id: spec for spec in workflow_capabilities()}
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
            "workflow.run.start",
            "workflow.run.list",
            "workflow.run.get",
            "workflow.run.cancel",
            "workflow.run.retry",
            "workflow.trace",
            "workflow.schedule.preview",
            "workflow.runtime.status",
        }
        self.assertEqual(set(specs), expected)
        for spec in specs.values():
            self.assertEqual(spec.scopes, frozenset({"workspace"}))
            self.assertEqual(spec.resource_scope, "workspace")
            self.assertIn("workflow", spec.tags)
            self.assertIn("operations", spec.tags)
        for capability_id in (
            "workflow.create",
            "workflow.update",
            "workflow.enable",
            "workflow.archive",
        ):
            self.assertEqual(specs[capability_id].risk, CapabilityRisk.HIGH)
            self.assertTrue(specs[capability_id].approval_required)
        self.assertEqual(
            specs["workflow.run.start"].permissions, ("workflows:run",)
        )
        self.assertEqual(
            specs["workflow.runtime.status"].risk, CapabilityRisk.READ_ONLY
        )

    def test_builtin_workflow_authority_is_owner_only(self):
        workflow_permissions = {"workflows:read", "workflows:write", "workflows:run"}
        self.assertTrue(workflow_permissions <= KNOWN_PERMISSIONS)
        self.assertTrue(workflow_permissions <= DEFAULT_ROLE_AUTHORITY["owner"])
        for role in ("manager", "agent", "employee"):
            self.assertFalse(workflow_permissions & DEFAULT_ROLE_AUTHORITY[role], role)

    def test_steps_can_target_any_non_workflow_workspace_capability(self):
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
            [
                "google.gmail.send_email",
                "computer.python.exec",
                "studio.solution.deploy",
            ],
        )
        with self.assertRaises(WorkflowSpecError):
            validate_workflow_spec(
                {
                    "steps": [
                        {
                            "id": "loop",
                            "capability_id": "workflow.run.start",
                            "arguments": {},
                        }
                    ]
                }
            )

    def test_dependencies_must_be_strictly_backward_and_waits_are_bounded(self):
        for invalid in (
            {
                "steps": [
                    {
                        "id": "a",
                        "capability_id": "workspace.summary.read",
                        "depends_on": ["b"],
                    },
                    {"id": "b", "capability_id": "workspace.summary.read"},
                ]
            },
            {
                "steps": [
                    {
                        "id": "a",
                        "capability_id": "workspace.summary.read",
                        "depends_on": ["a"],
                    },
                ]
            },
            {
                "steps": [
                    {"id": "a", "capability_id": "workspace.summary.read"},
                    {
                        "id": "b",
                        "capability_id": "workspace.summary.read",
                        "depends_on": ["a", "a"],
                    },
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
                        "when": {
                            "ref": "steps.a.status",
                            "op": "eq",
                            "value": "completed",
                        },
                    },
                ]
            }
        )
        self.assertEqual(validated["steps"][0]["kind"], "wait")
        with self.assertRaises(WorkflowSpecError):
            validate_workflow_spec(
                {
                    "steps": [
                        {
                            "id": "too-long",
                            "kind": "wait",
                            "seconds": 31 * 24 * 60 * 60 + 1,
                        }
                    ]
                }
            )

    def test_condition_type_errors_fail_deterministically(self):
        with self.assertRaises(WorkflowSpecError):
            evaluate_condition(
                {"ref": "trigger.value", "op": "gt", "value": 3},
                {"trigger": {"value": "not-a-number"}},
            )

    def test_schedules_support_once_interval_daily_weekly_and_cron(self):
        after = datetime(2026, 8, 31, 5, 0, 0)
        interval = validate_schedule(
            {"type": "interval", "every_seconds": 300, "timezone": "UTC"}
        )
        self.assertEqual(
            next_schedule_time(interval, after=after),
            datetime(2026, 8, 31, 5, 5, 0),
        )
        cron = validate_schedule(
            {"type": "cron", "expression": "*/15 * * * *", "timezone": "UTC"}
        )
        self.assertEqual(
            next_schedule_time(cron, after=after),
            datetime(2026, 8, 31, 5, 15, 0),
        )
        weekly = validate_schedule(
            {
                "type": "weekly",
                "days": [0, 2],
                "time": "09:30",
                "timezone": "UTC",
            }
        )
        self.assertIsNotNone(next_schedule_time(weekly, after=after))
        self.assertIsNotNone(
            validate_schedule({"type": "once", "at": "2026-09-01T00:00:00Z"})
        )
        self.assertIsNotNone(
            validate_schedule({"type": "daily", "time": "08:00", "timezone": "UTC"})
        )
        with self.assertRaises(WorkflowSpecError):
            validate_schedule({"type": "cron", "expression": "* *"})
        with self.assertRaises(WorkflowSpecError):
            validate_schedule({"type": "cron", "expression": "x * * * *"})

    def test_database_models_preserve_version_and_attempt_lineage(self):
        self.assertIn("snapshot_json", WorkflowVersion.__table__.columns)
        self.assertIn("authority_user_id", WorkflowStepAttempt.__table__.metadata.tables["workflow_runs"].columns)
        self.assertIn("kernel_run_id", WorkflowStepAttempt.__table__.columns)
        self.assertIn("approval_id", WorkflowStepAttempt.__table__.columns)
        self.assertIn("arguments_json", WorkflowStepAttempt.__table__.columns)
        self.assertIn("result_json", WorkflowStepAttempt.__table__.columns)
        self.assertIn("step_attempt_id", WorkflowTraceEvent.__table__.columns)

    def test_execution_delegates_to_workspace_runtime_and_preserves_correlation(self):
        root = Path(__file__).resolve().parents[1]
        engine = (root / "packages" / "workflow" / "engine.py").read_text(
            encoding="utf-8"
        )
        scheduler = (root / "packages" / "workflow" / "scheduler.py").read_text(
            encoding="utf-8"
        )
        tracing = (root / "packages" / "workflow" / "tracing.py").read_text(
            encoding="utf-8"
        )
        access = (root / "packages" / "workflow" / "access.py").read_text(
            encoding="utf-8"
        )
        provider = (root / "packages" / "workflow" / "provider.py").read_text(
            encoding="utf-8"
        )
        models = (root / "packages" / "workflow" / "models.py").read_text(
            encoding="utf-8"
        )
        migration = (
            root / "alembic" / "versions" / "0051_workflow_engine.py"
        ).read_text(encoding="utf-8")
        package = (root / "packages" / "workflow" / "__init__.py").read_text(
            encoding="utf-8"
        )
        composition = (
            root / "packages" / "workspace_modules" / "tools" / "__init__.py"
        ).read_text(encoding="utf-8")
        schema = (root / "packages" / "database" / "schema.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("build_workspace_runtime", engine)
        self.assertIn("resolve_execution_context", engine)
        self.assertIn("RuntimeRequest", engine)
        self.assertIn("WorkflowStepAttempt", engine)
        self.assertIn("workflow:{run.id}:{row.step_key}:attempt:{row.attempt}", engine)
        self.assertIn("workflow.step.waiting_approval", engine)
        self.assertIn("workflow.step.approval_resumed", engine)
        self.assertIn("workflow.run.resumed", engine)
        self.assertIn("authority_user_id", engine)
        self.assertIn("KernelApproval", scheduler)
        self.assertIn("with_for_update(skip_locked=True)", scheduler)
        self.assertIn("workflow_engine.execute_run", scheduler)
        self.assertIn("_lease_heartbeat", scheduler)
        self.assertIn("OPERLY_WORKFLOW_MAX_WORKERS", scheduler)
        self.assertIn('run.status = "orphaned"', scheduler)
        self.assertIn("execution_outcome_uncertain", scheduler)
        self.assertIn("KernelEventRecord", tracing)
        self.assertIn("WorkflowTraceEvent", tracing)
        self.assertIn("workflow_step_attempt_id", tracing)
        self.assertIn("WorkflowStepAttempt", models)
        self.assertIn("snapshot_json", models)
        self.assertIn("workflow_step_attempts", migration)
        self.assertIn("snapshot_json", migration)
        self.assertIn("workflow.version.get", provider)
        self.assertIn("workflow.runtime.status", provider)
        self.assertIn("workflow_capabilities", composition)
        self.assertIn("WorkflowProvider", composition)
        self.assertIn("A non-owner may only access their own workflows", access)
        self.assertIn("WorkflowRun.authority_user_id == context.user_id", access)
        self.assertIn("from packages.workflow.access import WorkflowProvider", package)
        self.assertNotIn("class WorkflowProvider", package)
        self.assertIn('ALEMBIC_HEAD = "0055_platform_job_idempotency_scope"', schema)

    def test_workflow_does_not_import_external_provider_executors(self):
        root = Path(__file__).resolve().parents[1]
        engine = (root / "packages" / "workflow" / "engine.py").read_text(
            encoding="utf-8"
        )
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
