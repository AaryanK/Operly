import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import select

from packages.database.db import Base, SessionFactory, engine
from packages.database.kernel_models import KernelEventRecord
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.plugin_platform_models import DigitalEventOutboxRecord
from packages.database.schema import import_all_models
from packages.plugins.events import DigitalEventService
from packages.workflow.models import (
    WorkflowDefinition,
    WorkflowEventCursor,
    WorkflowEventTrigger,
    WorkflowRun,
    WorkflowVersion,
)
from packages.workflow.spec import validate_workflow_spec
from packages.workflow.triggers import workflow_event_dispatcher


class DigitalEventWorkflowBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def _workspace_workflow(self, db, *, workspace, owner, name):
        spec = validate_workflow_spec(
            {
                "steps": [
                    {
                        "id": "read",
                        "capability_id": "workspace.summary.read",
                        "arguments": {
                            "artifact_id": "{{trigger.event.payload.trigger.artifact_id}}",
                            "digital_event_id": "{{trigger.event.payload.digital_event_id}}",
                        },
                    }
                ]
            }
        )
        workflow = WorkflowDefinition(
            scope_kind="workspace",
            workspace_id=workspace.id,
            owner_user_id=owner.id,
            name=name,
            description="Digital event bridge test",
            status="enabled",
            current_version=1,
        )
        db.add(workflow)
        await db.flush()
        db.add(
            WorkflowVersion(
                workflow_id=workflow.id,
                version=1,
                spec_json=json.dumps(spec),
                snapshot_json="{}",
                created_by_user_id=owner.id,
            )
        )
        db.add(
            WorkflowEventTrigger(
                workflow_id=workflow.id,
                event_pattern="external.order.created",
                condition_json=json.dumps(
                    {
                        "ref": "event.payload.trigger.artifact_id",
                        "op": "eq",
                        "value": "artifact-1",
                    }
                ),
                enabled=True,
                created_by_user_id=owner.id,
            )
        )
        return workflow

    async def test_digital_event_becomes_minimized_same_scope_kernel_trigger_exactly_once(self):
        baseline = datetime.utcnow() - timedelta(seconds=2)
        async with SessionFactory() as db:
            owner = AppUser(email="bridge-owner@example.com", display_name="Bridge Owner")
            other_owner = AppUser(email="bridge-other@example.com", display_name="Other Owner")
            workspace = Tenant(name="Bridge Workspace", slug="bridge-workspace")
            other_workspace = Tenant(name="Other Workspace", slug="other-workspace")
            db.add_all([owner, other_owner, workspace, other_workspace])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=workspace.id, user_id=owner.id, role="owner"),
                    TenantMember(tenant_id=other_workspace.id, user_id=other_owner.id, role="owner"),
                    WorkflowEventCursor(id="kernel", last_created_at=baseline, last_event_id=""),
                ]
            )
            workflow = await self._workspace_workflow(
                db, workspace=workspace, owner=owner, name="Webhook workflow"
            )
            await self._workspace_workflow(
                db, workspace=other_workspace, owner=other_owner, name="Other workspace workflow"
            )

            digital_event = await DigitalEventService().emit(
                db,
                tenant_id=workspace.id,
                event_type="External.Order.Created",
                source_kind="webhook",
                source_id="endpoint-1",
                subject_type="workspace",
                subject_id=workspace.id,
                payload={
                    "artifact_id": "artifact-1",
                    "receipt_id": "receipt-1",
                    "private_customer_email": "must-not-enter-kernel@example.com",
                    "raw_provider_payload": {"token": "must-not-enter-kernel"},
                },
                trigger_payload={
                    "artifact_id": "artifact-1",
                    "receipt_id": "receipt-1",
                    "body_sha256": "abc123",
                    "content_type": "application/json",
                },
            )
            await db.commit()
            digital_event_id = digital_event.id
            workspace_id = workspace.id
            workflow_id = workflow.id

        async with SessionFactory() as db:
            outbox = await db.get(DigitalEventOutboxRecord, digital_event_id)
            self.assertIsNotNone(outbox)
            full_payload = json.loads(outbox.payload_json)
            self.assertEqual(full_payload["private_customer_email"], "must-not-enter-kernel@example.com")

            kernel_event = await db.scalar(
                select(KernelEventRecord).where(
                    KernelEventRecord.resource_type == "digital_event",
                    KernelEventRecord.resource_id == digital_event_id,
                )
            )
            self.assertIsNotNone(kernel_event)
            self.assertEqual(kernel_event.event_type, "external.order.created")
            self.assertEqual(kernel_event.scope_kind, "workspace")
            self.assertEqual(kernel_event.workspace_id, workspace_id)
            self.assertEqual(kernel_event.actor_type, "external")
            kernel_payload = json.loads(kernel_event.payload_json)
            encoded_kernel_payload = json.dumps(kernel_payload, sort_keys=True)
            self.assertEqual(kernel_payload["digital_event_id"], digital_event_id)
            self.assertEqual(kernel_payload["trigger"]["artifact_id"], "artifact-1")
            self.assertNotIn("private_customer_email", encoded_kernel_payload)
            self.assertNotIn("must-not-enter-kernel", encoded_kernel_payload)

        queued = await workflow_event_dispatcher.tick()
        self.assertEqual(queued, 1)

        async with SessionFactory() as db:
            runs = (
                await db.scalars(
                    select(WorkflowRun).where(WorkflowRun.trigger_type == "event")
                )
            ).all()
            self.assertEqual(len(runs), 1)
            run = runs[0]
            self.assertEqual(run.workflow_id, workflow_id)
            self.assertEqual(run.scope_kind, "workspace")
            self.assertEqual(run.workspace_id, workspace_id)
            trigger = json.loads(run.trigger_payload_json)
            self.assertEqual(trigger["event"]["payload"]["trigger"]["artifact_id"], "artifact-1")
            self.assertEqual(trigger["event"]["payload"]["digital_event_id"], digital_event_id)

        # Cursor advancement + per-event trigger dedupe mean the same DigitalEvent
        # cannot silently enqueue a second run.
        self.assertEqual(await workflow_event_dispatcher.tick(), 0)
        async with SessionFactory() as db:
            self.assertEqual(len((await db.scalars(select(WorkflowRun))).all()), 1)

    async def test_semantic_projection_is_bounded_and_can_be_explicitly_delivery_only(self):
        async with SessionFactory() as db:
            owner = AppUser(email="bridge-bounds@example.com", display_name="Bounds Owner")
            workspace = Tenant(name="Bounds Workspace", slug="bounds-workspace")
            db.add_all([owner, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=owner.id, role="owner"))

            with self.assertRaises(ValueError):
                await DigitalEventService().emit(
                    db,
                    tenant_id=workspace.id,
                    event_type="external.too-large",
                    source_kind="webhook",
                    trigger_payload={"value": "x" * 9000},
                )
            await db.rollback()

        # Re-open after rollback so this proves a producer may intentionally keep an
        # event on the delivery plane without exposing it to Workflow triggers.
        async with SessionFactory() as db:
            workspace = await db.scalar(select(Tenant).where(Tenant.slug == "bounds-workspace"))
            if workspace is None:
                owner = AppUser(email="bridge-bounds-2@example.com", display_name="Bounds Owner 2")
                workspace = Tenant(name="Bounds Workspace", slug="bounds-workspace")
                db.add_all([owner, workspace])
                await db.flush()
                db.add(TenantMember(tenant_id=workspace.id, user_id=owner.id, role="owner"))
            row = await DigitalEventService().emit(
                db,
                tenant_id=workspace.id,
                event_type="delivery.only",
                source_kind="system",
                payload={"private": "outbox-only"},
                bridge_to_kernel=False,
            )
            await db.commit()
            row_id = row.id

        async with SessionFactory() as db:
            self.assertIsNotNone(await db.get(DigitalEventOutboxRecord, row_id))
            mirrored = await db.scalar(
                select(KernelEventRecord).where(KernelEventRecord.resource_id == row_id)
            )
            self.assertIsNone(mirrored)


if __name__ == "__main__":
    unittest.main()
