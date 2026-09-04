import json
import unittest
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import select

from packages.database.db import Base, SessionFactory, engine
from packages.database.kernel_models import KernelApproval, KernelEventRecord, KernelRun
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.kernel.approvals import decide_approval
from packages.kernel.contracts import CapabilityExecutionResult
from packages.kernel.providers import ProviderRegistry
from packages.kernel.registry import CapabilityRegistry
from packages.kernel.runtime import OperlyKernelRuntime
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    resolve_execution_context,
    resolve_personal_execution_context,
)
from packages.security.surfaces import SurfaceKind
from packages.workflow.engine import WorkflowEngine
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowRun,
    WorkflowStepAttempt,
    WorkflowStepRun,
    WorkflowVersion,
)
from packages.workflow.spec import validate_workflow_spec
from packages.workflow.triggers import workflow_event_dispatcher
from packages.workspace_modules.tools.runtime import build_workspace_runtime


TRIGGER_FLAVORS = ("exact", "namespace", "global", "condition")


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
        value = int(schema.get("minimum") or 0)
        if isinstance(schema.get("maximum"), (int, float)):
            value = min(value, int(schema["maximum"]))
        return value
    if expected == "number":
        value = float(schema.get("minimum") or 0)
        if isinstance(schema.get("maximum"), (int, float)):
            value = min(value, float(schema["maximum"]))
        return value
    if expected == "boolean":
        return True
    if expected == "null":
        return None
    return {}


class SchemaContractProvider:
    def __init__(self):
        self.calls = []

    async def execute(self, db, *, context, capability, arguments, minimum_context):
        del db, minimum_context
        self.calls.append((context.scope_kind.value, capability.id, dict(arguments)))
        value = _sample(capability.output_schema)
        if not isinstance(value, dict):
            raise AssertionError(f"Capability output must be an object: {capability.id}")
        return CapabilityExecutionResult(
            value=value,
            resource_type="native_approval_matrix",
            resource_id=capability.id,
            event_payload={"native_approval_matrix": True, "capability_id": capability.id},
        )


def _catalog(scope_kind):
    runtime = build_personal_runtime() if scope_kind == "personal" else build_workspace_runtime()
    return tuple(
        spec
        for spec in runtime.registry.all()
        if scope_kind in spec.scopes
        and spec.approval_required
        and not spec.id.startswith("workflow.")
    )


def _test_runtime(scope_kind, approval_specs):
    source = build_personal_runtime() if scope_kind == "personal" else build_workspace_runtime()
    all_specs = tuple(spec for spec in source.registry.all() if scope_kind in spec.scopes)
    registry = CapabilityRegistry(all_specs)
    providers = ProviderRegistry()
    provider = SchemaContractProvider()
    for provider_id in sorted({spec.provider_id for spec in all_specs}):
        providers.register(provider_id, provider)
    expected = {spec.id for spec in approval_specs}
    assert expected <= {spec.id for spec in all_specs}
    return OperlyKernelRuntime(registry=registry, providers=providers), provider


def _compound_steps(specs):
    steps = []
    for index, capability in enumerate(specs):
        step = {
            "id": f"approval_{index:02d}",
            "capability_id": capability.id,
            "arguments": _sample(capability.input_schema),
        }
        if index:
            step["depends_on"] = [f"approval_{index - 1:02d}"]
        if index % 7 == 0:
            step["when"] = {
                "ref": "trigger.event.payload.run_all",
                "op": "eq",
                "value": True,
            }
        steps.append(step)
    return steps


class WorkflowNativeApprovalMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def _create_workflows(
        self,
        db,
        *,
        scope_kind,
        owner_user_id,
        workspace_id,
        specs,
    ):
        validated = validate_workflow_spec({"steps": _compound_steps(specs)})
        workflows = []
        for flavor in TRIGGER_FLAVORS:
            workflow = WorkflowDefinition(
                scope_kind=scope_kind,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                name=f"Native approval {scope_kind} {flavor}",
                description="Issue #298 native approval stress matrix",
                status="enabled",
                current_version=1,
            )
            db.add(workflow)
            await db.flush()
            db.add(
                WorkflowVersion(
                    workflow_id=workflow.id,
                    version=1,
                    spec_json=json.dumps(validated),
                    snapshot_json="{}",
                    created_by_user_id=owner_user_id,
                )
            )
            pattern = {
                "exact": "stress.native.approval",
                "namespace": "stress.native.*",
                "global": "*",
                "condition": "stress.native.*",
            }[flavor]
            condition = (
                {"ref": "event.payload.cohort", "op": "eq", "value": "native-approval"}
                if flavor == "condition"
                else {}
            )
            db.add(
                WorkflowEventTrigger(
                    workflow_id=workflow.id,
                    event_pattern=pattern,
                    condition_json=json.dumps(condition),
                    enabled=True,
                    created_by_user_id=owner_user_id,
                )
            )
            workflows.append(workflow)
        await db.flush()
        return workflows

    async def _approval_context(self, db, run):
        metadata = {"workflow_id": run.workflow_id, "workflow_run_id": run.id}
        if run.scope_kind == "personal":
            return await resolve_personal_execution_context(
                db,
                user_id=run.authority_user_id,
                channel="workflow",
                surface=SurfaceKind.SYSTEM_TASK,
                conversation_id=f"workflow:{run.id}",
                metadata=metadata,
            )
        return await resolve_execution_context(
            db,
            workspace_id=run.workspace_id,
            user_id=run.authority_user_id,
            channel="workflow",
            surface=SurfaceKind.SYSTEM_TASK,
            conversation_id=f"workflow:{run.id}",
            metadata=metadata,
        )

    async def _execute_to_terminal(self, db, workflow_engine, run_id):
        approvals = 0
        for _ in range(200):
            current = await workflow_engine.execute_run(db, run_id)
            if current.status == "waiting_approval":
                step = await db.scalar(
                    select(WorkflowStepRun).where(
                        WorkflowStepRun.workflow_run_id == run_id,
                        WorkflowStepRun.status == "waiting_approval",
                    )
                )
                self.assertIsNotNone(step)
                self.assertIsNotNone(step.approval_id)
                context = await self._approval_context(db, current)
                await decide_approval(
                    db,
                    context=context,
                    approval_id=step.approval_id,
                    approved=True,
                    decided_by_user_id=current.authority_user_id,
                )
                await db.commit()
                approvals += 1
                continue
            self.assertEqual(
                current.status,
                "completed",
                msg=f"Workflow {run_id} ended as {current.status}: {current.error_code} {current.error_message}",
            )
            return approvals
        self.fail(f"Workflow {run_id} did not reach terminal state")

    async def test_all_approval_required_non_workflow_capabilities_resume_natively(self):
        workspace_specs = _catalog("workspace")
        personal_specs = _catalog("personal")
        self.assertTrue(workspace_specs)
        self.assertTrue(personal_specs)

        workspace_runtime, workspace_provider = _test_runtime("workspace", workspace_specs)
        personal_runtime, personal_provider = _test_runtime("personal", personal_specs)
        workflow_engine = WorkflowEngine()
        workflow_engine._workspace_runtime = workspace_runtime
        workflow_engine._personal_runtime = personal_runtime

        baseline = datetime.utcnow() - timedelta(seconds=2)
        async with SessionFactory() as db:
            workspace_user = AppUser(
                email="native-approval-workspace@example.com",
                display_name="Native Approval Workspace",
            )
            personal_user = AppUser(
                email="native-approval-personal@example.com",
                display_name="Native Approval Personal",
            )
            workspace = Tenant(name="Native Approval", slug="native-approval")
            db.add_all([workspace_user, personal_user, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=workspace_user.id, role="owner"))
            db.add(WorkflowEventCursor(id="kernel", last_created_at=baseline, last_event_id=""))

            workspace_workflows = await self._create_workflows(
                db,
                scope_kind="workspace",
                owner_user_id=workspace_user.id,
                workspace_id=workspace.id,
                specs=workspace_specs,
            )
            personal_workflows = await self._create_workflows(
                db,
                scope_kind="personal",
                owner_user_id=personal_user.id,
                workspace_id=None,
                specs=personal_specs,
            )

            for scope_kind, user_id, workspace_id, seed in (
                ("workspace", workspace_user.id, workspace.id, "workspace"),
                ("personal", personal_user.id, None, "personal"),
            ):
                db.add(
                    KernelEventRecord(
                        event_type="stress.native.approval",
                        scope_kind=scope_kind,
                        workspace_id=workspace_id,
                        owner_user_id=user_id if scope_kind == "personal" else None,
                        principal_id=f"user:{user_id}",
                        actor_type="system",
                        actor_id="native-approval-matrix",
                        initiator_principal_id=f"user:{user_id}",
                        executor_principal_id="native-approval-matrix",
                        capability_id="stress.native.approval.seed",
                        resource_type="stress_matrix",
                        resource_id=f"{seed}-seed",
                        payload_json=json.dumps(
                            {"run_all": True, "cohort": "native-approval", "seed": seed}
                        ),
                    )
                )
            await db.commit()

        expected_runs = len(workspace_workflows) + len(personal_workflows)
        queued = await workflow_event_dispatcher.tick()
        self.assertEqual(queued, expected_runs)

        approvals_consumed = 0
        async with SessionFactory() as db:
            runs = (
                await db.scalars(select(WorkflowRun).order_by(WorkflowRun.created_at, WorkflowRun.id))
            ).all()
            self.assertEqual(len(runs), expected_runs)
            # Kernel approval requests intentionally roll back the shared session and
            # therefore expire every ORM object in it. Capture all run identities
            # before starting execution; the public Workflow API is scalar-ID based.
            run_ids = [run.id for run in runs]
            for run_id in run_ids:
                approvals_consumed += await self._execute_to_terminal(
                    db, workflow_engine, run_id
                )

            completed = (
                await db.scalars(select(WorkflowRun).where(WorkflowRun.status == "completed"))
            ).all()
            self.assertEqual(len(completed), expected_runs)

            consumed = (
                await db.scalars(select(KernelApproval).where(KernelApproval.status == "consumed"))
            ).all()
            attempts = (await db.scalars(select(WorkflowStepAttempt))).all()
            kernel_runs = (await db.scalars(select(KernelRun))).all()

        expected_approvals = (
            len(workspace_specs) + len(personal_specs)
        ) * len(TRIGGER_FLAVORS)
        self.assertEqual(approvals_consumed, expected_approvals)
        self.assertEqual(len(consumed), expected_approvals)
        self.assertEqual(len(attempts), expected_approvals)
        self.assertTrue(all(attempt.status == "completed" for attempt in attempts))
        self.assertTrue(all(attempt.attempt == 1 for attempt in attempts))
        self.assertEqual(len(kernel_runs), expected_approvals * 2)

        workspace_counts = Counter(
            capability_id
            for scope, capability_id, _ in workspace_provider.calls
            if scope == "workspace"
        )
        personal_counts = Counter(
            capability_id
            for scope, capability_id, _ in personal_provider.calls
            if scope == "personal"
        )
        for spec in workspace_specs:
            self.assertEqual(workspace_counts[spec.id], len(TRIGGER_FLAVORS), msg=spec.id)
        for spec in personal_specs:
            self.assertEqual(personal_counts[spec.id], len(TRIGGER_FLAVORS), msg=spec.id)

        print(
            "NATIVE_APPROVAL_MATRIX_SUMMARY="
            + json.dumps(
                {
                    "workspace_capabilities": len(workspace_specs),
                    "personal_capabilities": len(personal_specs),
                    "workflow_runs": expected_runs,
                    "trigger_flavors": list(TRIGGER_FLAVORS),
                    "approvals_consumed": approvals_consumed,
                    "workflow_attempts": len(attempts),
                    "kernel_runs": len(kernel_runs),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
