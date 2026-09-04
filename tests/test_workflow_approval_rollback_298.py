import json
import unittest

from sqlalchemy import select

from packages.database.db import Base, SessionFactory, engine
from packages.database.kernel_models import KernelApproval, KernelRun
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.kernel.approvals import decide_approval
from packages.kernel.contracts import CapabilityExecutionResult
from packages.kernel.providers import ProviderRegistry
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime import OperlyKernelRuntime
from packages.security.execution_context import resolve_execution_context
from packages.security.surfaces import SurfaceKind
from packages.workflow.engine import WorkflowEngine, queue_workflow_run
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowStepAttempt,
    WorkflowStepRun,
    WorkflowVersion,
)
from packages.workflow.spec import validate_workflow_spec
from packages.workspace_modules.tools.runtime import build_workspace_runtime


def _sample(schema):
    schema = dict(schema or {})
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    expected = schema.get("type")
    if isinstance(expected, list):
        expected = next((item for item in expected if item != "null"), "null")
    if expected is None:
        if isinstance(schema.get("properties"), dict):
            expected = "object"
        elif isinstance(schema.get("items"), dict):
            expected = "array"
    if expected == "object":
        properties = schema.get("properties") or {}
        return {
            key: _sample(properties.get(key) or {})
            for key in schema.get("required") or []
            if key in properties
        }
    if expected == "array":
        count = schema.get("minItems") if isinstance(schema.get("minItems"), int) else 0
        return [_sample(schema.get("items") or {}) for _ in range(count)]
    if expected == "string":
        length = max(1, int(schema.get("minLength") or 1))
        if isinstance(schema.get("maxLength"), int):
            length = min(length, schema["maxLength"])
        return "x" * length
    if expected == "integer":
        return int(schema.get("minimum") or 0)
    if expected == "number":
        return float(schema.get("minimum") or 0)
    if expected == "boolean":
        return True
    if expected == "null":
        return None
    return {}


class SchemaContractProvider:
    async def execute(self, db, *, context, capability, arguments, minimum_context):
        del db, context, arguments, minimum_context
        value = _sample(capability.output_schema)
        if not isinstance(value, dict):
            raise AssertionError(f"Capability output must be an object: {capability.id}")
        return CapabilityExecutionResult(
            value=value,
            resource_type="approval_regression",
            resource_id=capability.id,
            event_payload={"approval_regression": True},
        )


def _runtime_and_capability():
    real = build_workspace_runtime()
    specs = tuple(real.registry.all())
    capability = next(
        spec
        for spec in specs
        if spec.approval_required and not spec.id.startswith("workflow.")
    )
    registry = CapabilityRegistry(specs)
    providers = ProviderRegistry()
    provider = SchemaContractProvider()
    for provider_id in sorted({spec.provider_id for spec in specs}):
        providers.register(provider_id, provider)
    return OperlyKernelRuntime(registry=registry, providers=providers), capability


class WorkflowApprovalRollback298Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def test_approval_required_step_survives_kernel_rollback_and_resumes(self):
        runtime, capability = _runtime_and_capability()
        workflow_engine = WorkflowEngine()
        workflow_engine._workspace_runtime = runtime

        async with SessionFactory() as db:
            user = AppUser(email="approval-298@example.com", display_name="Approval 298")
            workspace = Tenant(name="Approval 298", slug="approval-298")
            db.add_all([user, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=user.id, role="owner"))

            workflow = WorkflowDefinition(
                scope_kind="workspace",
                workspace_id=workspace.id,
                owner_user_id=user.id,
                name="Approval rollback regression",
                description="Issue #298 regression",
                status="enabled",
                current_version=1,
            )
            db.add(workflow)
            await db.flush()
            spec = validate_workflow_spec(
                {
                    "steps": [
                        {
                            "id": "approved_action",
                            "capability_id": capability.id,
                            "arguments": _sample(capability.input_schema),
                        }
                    ]
                }
            )
            db.add(
                WorkflowVersion(
                    workflow_id=workflow.id,
                    version=1,
                    spec_json=json.dumps(spec),
                    snapshot_json="{}",
                    created_by_user_id=user.id,
                )
            )
            await db.flush()
            run = await queue_workflow_run(
                db,
                workflow=workflow,
                trigger_type="manual",
                trigger_payload={"issue": 298},
                initiated_by_user_id=user.id,
            )
            run_id = run.id
            user_id = user.id
            workspace_id = workspace.id
            await db.commit()

        async with SessionFactory() as db:
            first = await workflow_engine.execute_run(db, run_id)
            self.assertEqual(first.status, "waiting_approval")
            step = await db.scalar(
                select(WorkflowStepRun).where(WorkflowStepRun.workflow_run_id == run_id)
            )
            self.assertIsNotNone(step)
            self.assertEqual(step.status, "waiting_approval")
            self.assertIsNotNone(step.approval_id)
            approval_id = step.approval_id
            attempt = await db.scalar(
                select(WorkflowStepAttempt).where(WorkflowStepAttempt.step_run_id == step.id)
            )
            self.assertIsNotNone(attempt)
            self.assertEqual(attempt.status, "waiting_approval")
            self.assertEqual(attempt.approval_id, approval_id)

            context = await resolve_execution_context(
                db,
                workspace_id=workspace_id,
                user_id=user_id,
                channel="workflow",
                surface=SurfaceKind.SYSTEM_TASK,
                conversation_id=f"workflow:{run_id}",
                metadata={"workflow_run_id": run_id},
            )
            await decide_approval(
                db,
                context=context,
                approval_id=approval_id,
                approved=True,
                decided_by_user_id=user_id,
            )
            await db.commit()

            resumed = await workflow_engine.execute_run(db, run_id)
            self.assertEqual(resumed.status, "completed")

            step = await db.scalar(
                select(WorkflowStepRun).where(WorkflowStepRun.workflow_run_id == run_id)
            )
            attempt = await db.scalar(
                select(WorkflowStepAttempt).where(WorkflowStepAttempt.step_run_id == step.id)
            )
            approval = await db.get(KernelApproval, approval_id)
            kernel_runs = (
                await db.scalars(
                    select(KernelRun).where(KernelRun.capability_id == capability.id)
                )
            ).all()

            self.assertEqual(step.status, "completed")
            self.assertEqual(step.attempt, 1)
            self.assertEqual(attempt.status, "completed")
            self.assertEqual(attempt.attempt, 1)
            self.assertEqual(attempt.approval_id, approval_id)
            self.assertEqual(approval.status, "consumed")
            self.assertEqual(len(kernel_runs), 2)


if __name__ == "__main__":
    unittest.main()
