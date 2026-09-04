import json
import unittest
from collections import Counter
from datetime import datetime, timedelta

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
    WorkflowStepRun,
    WorkflowVersion,
)
from packages.workflow.spec import validate_workflow_spec
from packages.workflow.triggers import workflow_event_dispatcher
from packages.workspace_modules.tools.runtime import build_workspace_runtime
from packages.database.kernel_models import KernelEventRecord


TRIGGER_FLAVORS = ("exact", "namespace", "global", "condition")
BATCH_SIZE = 24


def _sample(schema):
    """Build a deterministic value for the JSON-schema subset enforced by Kernel."""
    schema = dict(schema or {})
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    expected = schema.get("type")
    if isinstance(expected, list):
        non_null = [item for item in expected if item != "null"]
        expected = non_null[0] if non_null else "null"
    if expected is None:
        if isinstance(schema.get("properties"), dict):
            expected = "object"
        elif isinstance(schema.get("items"), dict):
            expected = "array"

    if expected == "object":
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        return {
            key: _sample(properties.get(key) or {})
            for key in required
            if key in properties
        }
    if expected == "array":
        minimum = schema.get("minItems")
        count = minimum if isinstance(minimum, int) else 0
        return [_sample(schema.get("items") or {}) for _ in range(count)]
    if expected == "string":
        minimum = schema.get("minLength")
        length = max(1, minimum if isinstance(minimum, int) else 1)
        maximum = schema.get("maxLength")
        if isinstance(maximum, int):
            length = min(length, maximum)
        return "x" * max(0, length)
    if expected == "integer":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        value = int(minimum) if isinstance(minimum, (int, float)) else 0
        if isinstance(maximum, (int, float)):
            value = min(value, int(maximum))
        return value
    if expected == "number":
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        value = float(minimum) if isinstance(minimum, (int, float)) else 0.0
        if isinstance(maximum, (int, float)):
            value = min(value, float(maximum))
        return value
    if expected == "boolean":
        return True
    if expected == "null":
        return None
    return {}


class SchemaContractProvider:
    """Side-effect-free provider that still executes through the real Kernel."""

    def __init__(self):
        self.calls = []

    async def execute(
        self,
        db,
        *,
        context,
        capability,
        arguments,
        minimum_context,
    ):
        del db, minimum_context
        self.calls.append((context.scope_kind.value, capability.id, dict(arguments)))
        value = _sample(capability.output_schema)
        if not isinstance(value, dict):
            raise AssertionError(f"Capability output must be an object: {capability.id}")
        return CapabilityExecutionResult(
            value=value,
            resource_type="stress_capability",
            resource_id=capability.id,
            event_payload={"stress_matrix": True, "capability_id": capability.id},
        )


def _catalog(scope_kind):
    runtime = build_personal_runtime() if scope_kind == "personal" else build_workspace_runtime()
    return tuple(spec for spec in runtime.registry.all() if scope_kind in spec.scopes)


def _test_runtime(specs):
    registry = CapabilityRegistry(specs)
    providers = ProviderRegistry()
    provider = SchemaContractProvider()
    for provider_id in sorted({spec.provider_id for spec in specs}):
        providers.register(provider_id, provider)
    return OperlyKernelRuntime(registry=registry, providers=providers), provider


def _steps(specs):
    rows = []
    for index, capability in enumerate(specs):
        step = {
            "id": f"s{index:02d}",
            "capability_id": capability.id,
            "arguments": _sample(capability.input_schema),
        }
        if index:
            # A branching DAG: odd nodes depend on the prior node; even nodes join
            # the two immediately preceding branches. Every dependency points backward.
            if index >= 2 and index % 2 == 0:
                step["depends_on"] = [f"s{index - 1:02d}", f"s{index - 2:02d}"]
            else:
                step["depends_on"] = [f"s{index - 1:02d}"]
        if index % 5 == 0:
            step["when"] = {
                "ref": "trigger.event.payload.run_all",
                "op": "eq",
                "value": True,
            }
        rows.append(step)
    return rows


def _batches(specs):
    return [specs[index : index + BATCH_SIZE] for index in range(0, len(specs), BATCH_SIZE)]


class CapabilityCompoundWorkflowMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def _create_trigger_workflows(
        self,
        db,
        *,
        scope_kind,
        owner_user_id,
        workspace_id,
        specs,
    ):
        created = []
        for batch_index, batch in enumerate(_batches(specs)):
            validated = validate_workflow_spec({"steps": _steps(batch)})
            for flavor in TRIGGER_FLAVORS:
                workflow = WorkflowDefinition(
                    scope_kind=scope_kind,
                    workspace_id=workspace_id,
                    owner_user_id=owner_user_id,
                    name=f"Stress {scope_kind} {batch_index} {flavor}",
                    description="Capability-wide compound workflow matrix",
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
                    "exact": "stress.matrix.fire",
                    "namespace": "stress.matrix.*",
                    "global": "*",
                    "condition": "stress.matrix.*",
                }[flavor]
                condition = (
                    {"ref": "event.payload.cohort", "op": "eq", "value": "capability-matrix"}
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
                created.append(workflow)
        await db.flush()
        return created

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

    async def _execute_to_terminal(self, db, workflow_engine, run):
        approvals = 0
        for _ in range(200):
            current = await workflow_engine.execute_run(db, run.id)
            if current.status == "waiting_approval":
                step = await db.scalar(
                    select(WorkflowStepRun).where(
                        WorkflowStepRun.workflow_run_id == current.id,
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
                msg=f"Workflow {current.id} terminated as {current.status}: {current.error_code} {current.error_message}",
            )
            return approvals
        self.fail(f"Workflow {run.id} did not reach a terminal state")

    async def test_every_registered_capability_runs_in_compound_event_workflows(self):
        workspace_specs = _catalog("workspace")
        personal_specs = _catalog("personal")
        self.assertTrue(workspace_specs)
        self.assertTrue(personal_specs)
        self.assertEqual(len({spec.id for spec in workspace_specs}), len(workspace_specs))
        self.assertEqual(len({spec.id for spec in personal_specs}), len(personal_specs))

        workspace_runtime, workspace_provider = _test_runtime(workspace_specs)
        personal_runtime, personal_provider = _test_runtime(personal_specs)
        workflow_engine = WorkflowEngine()
        workflow_engine._workspace_runtime = workspace_runtime
        workflow_engine._personal_runtime = personal_runtime

        baseline = datetime.utcnow() - timedelta(seconds=2)
        async with SessionFactory() as db:
            personal_user = AppUser(
                email="capability-matrix-personal@example.com",
                display_name="Capability Matrix Personal",
            )
            workspace_user = AppUser(
                email="capability-matrix-workspace@example.com",
                display_name="Capability Matrix Workspace",
            )
            workspace = Tenant(name="Capability Matrix", slug="capability-matrix")
            db.add_all([personal_user, workspace_user, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=workspace_user.id, role="owner"))
            db.add(WorkflowEventCursor(id="kernel", last_created_at=baseline, last_event_id=""))

            workspace_workflows = await self._create_trigger_workflows(
                db,
                scope_kind="workspace",
                owner_user_id=workspace_user.id,
                workspace_id=workspace.id,
                specs=workspace_specs,
            )
            personal_workflows = await self._create_trigger_workflows(
                db,
                scope_kind="personal",
                owner_user_id=personal_user.id,
                workspace_id=None,
                specs=personal_specs,
            )

            db.add(
                KernelEventRecord(
                    event_type="stress.matrix.fire",
                    scope_kind="workspace",
                    workspace_id=workspace.id,
                    owner_user_id=None,
                    principal_id=f"user:{workspace_user.id}",
                    actor_type="system",
                    actor_id="stress-matrix",
                    initiator_principal_id=f"user:{workspace_user.id}",
                    executor_principal_id="stress-matrix",
                    capability_id="stress.matrix.seed",
                    resource_type="stress_matrix",
                    resource_id="workspace-seed",
                    payload_json=json.dumps(
                        {"run_all": True, "cohort": "capability-matrix", "seed": "workspace"}
                    ),
                )
            )
            db.add(
                KernelEventRecord(
                    event_type="stress.matrix.fire",
                    scope_kind="personal",
                    workspace_id=None,
                    owner_user_id=personal_user.id,
                    principal_id=f"user:{personal_user.id}",
                    actor_type="system",
                    actor_id="stress-matrix",
                    initiator_principal_id=f"user:{personal_user.id}",
                    executor_principal_id="stress-matrix",
                    capability_id="stress.matrix.seed",
                    resource_type="stress_matrix",
                    resource_id="personal-seed",
                    payload_json=json.dumps(
                        {"run_all": True, "cohort": "capability-matrix", "seed": "personal"}
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
            for run in runs:
                approvals_consumed += await self._execute_to_terminal(db, workflow_engine, run)

            completed_runs = (
                await db.scalars(select(WorkflowRun).where(WorkflowRun.status == "completed"))
            ).all()
            self.assertEqual(len(completed_runs), expected_runs)

            consumed_approvals = (
                await db.scalars(select(KernelApproval).where(KernelApproval.status == "consumed"))
            ).all()
            self.assertEqual(len(consumed_approvals), approvals_consumed)

            kernel_runs = (await db.scalars(select(KernelRun))).all()

        workspace_counts = Counter(capability_id for scope, capability_id, _ in workspace_provider.calls if scope == "workspace")
        personal_counts = Counter(capability_id for scope, capability_id, _ in personal_provider.calls if scope == "personal")

        for spec in workspace_specs:
            self.assertEqual(
                workspace_counts[spec.id],
                len(TRIGGER_FLAVORS),
                msg=f"Workspace capability did not receive complete trigger coverage: {spec.id}",
            )
        for spec in personal_specs:
            self.assertEqual(
                personal_counts[spec.id],
                len(TRIGGER_FLAVORS),
                msg=f"Personal capability did not receive complete trigger coverage: {spec.id}",
            )

        total_executions = len(workspace_provider.calls) + len(personal_provider.calls)
        expected_executions = (len(workspace_specs) + len(personal_specs)) * len(TRIGGER_FLAVORS)
        self.assertEqual(total_executions, expected_executions)
        self.assertEqual(len(kernel_runs), expected_executions)

        print(
            "CAPABILITY_MATRIX_SUMMARY="
            + json.dumps(
                {
                    "workspace_capabilities": len(workspace_specs),
                    "personal_capabilities": len(personal_specs),
                    "unique_capability_ids": len(
                        {spec.id for spec in workspace_specs} | {spec.id for spec in personal_specs}
                    ),
                    "workspace_workflows": len(workspace_workflows),
                    "personal_workflows": len(personal_workflows),
                    "workflow_runs": expected_runs,
                    "trigger_flavors": list(TRIGGER_FLAVORS),
                    "capability_executions": total_executions,
                    "kernel_runs": len(kernel_runs),
                    "approvals_consumed": approvals_consumed,
                    "workspace_ids": [spec.id for spec in workspace_specs],
                    "personal_ids": [spec.id for spec in personal_specs],
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
