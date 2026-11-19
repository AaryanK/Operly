"""Genuine Operly Kernel/provider calls on disposable SQLAlchemy business records.

This uses the actual business providers and runtime, NOT a mocked HTTP
broker or pre-filled fake response. No production database or human data.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.business_models import ActivityEvent, CatalogItem
from packages.kernel.contracts import RuntimeRequest
from packages.security.execution_context import (
    ExecutionContextError, resolve_execution_context,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime

import_all_models()


class RealBusinessKernelReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.workspace = "7b06e0d6-d389-4546-a32a-b46e138a4c2c"
        self.other_workspace = "370229b5-f7e6-49c4-887a-5b8823b77c26"
        self.user = "b9a95f89-b5e3-47fb-a66e-01591eb349a5"
        async with self.sessions() as db:
            db.add_all([
                Tenant(id=self.workspace, name="Synthetic Coffee Store", timezone="UTC"),
                Tenant(id=self.other_workspace, name="Other Business", timezone="UTC"),
                AppUser(id=self.user, email="canary-owner@example.test",
                        display_name="Canary Owner", active=True),
            ])
            await db.flush()
            db.add(TenantMember(tenant_id=self.workspace, user_id=self.user, role="owner"))
            db.add_all([
                CatalogItem(tenant_id=self.workspace, name="Synthetic Latte",
                            stock_qty=2, reorder_level=5, active=True),
                CatalogItem(tenant_id=self.other_workspace, name="Other Tenant Product",
                            stock_qty=1, reorder_level=2, active=True),
                ActivityEvent(tenant_id=self.workspace, event_type="created",
                              entity_type="catalog_item", entity_id="fixture",
                              summary="Synthetic stock item created", actor="Canary Owner"),
                ActivityEvent(tenant_id=self.other_workspace, event_type="private",
                              entity_type="customer", entity_id="other-fixture",
                              summary="Other tenant confidential event", actor="Other"),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def context(self, db):
        return await resolve_execution_context(
            db, workspace_id=self.workspace, user_id=self.user,
            channel="web", surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id="synthetic-canary", require_membership=True,
        )

    async def call(self, db, context, cap, args=None):
        return await build_workspace_runtime().execute(
            db, context=context,
            request=RuntimeRequest(
                goal="Read synthetic disposable business data",
                capability_id=cap,
                arguments=args or {},
                request_id=f"operly-real-canary:{cap}",
            ),
        )

    async def test_real_kernel_read_tools_and_cross_tenant_separation(self):
        async with self.sessions() as db:
            ctx = await self.context(db)
            steps = [
                ("workspace.describe", {}),
                ("workspace.summary.read", {}),
                ("workspace.activity.list", {"limit": 10}),
                ("workspace.attention.list", {}),
            ]
            results = {}
            for capability, arguments in steps:
                response = await self.call(db, ctx, capability, arguments)
                self.assertTrue(response.done, capability)
                self.assertEqual(response.capability_id, capability)
                self.assertEqual(response.status, "completed")
                self.assertIsInstance(response.result, dict)
                results[capability] = response.result

            self.assertEqual(
                results["workspace.describe"]["name"], "Synthetic Coffee Store"
            )
            summary = results["workspace.summary.read"]
            self.assertEqual(summary["catalog_items"], 1)
            self.assertEqual(summary["low_stock"], 1)
            events = results["workspace.activity.list"]["events"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["summary"], "Synthetic stock item created")
            serialized = json.dumps(results, default=str)
            self.assertNotIn("Other Tenant Product", serialized)
            self.assertNotIn("Other tenant confidential event", serialized)

    async def test_non_member_cannot_read_disposable_other_workspace(self):
        async with self.sessions() as db:
            with self.assertRaises(ExecutionContextError):
                await resolve_execution_context(
                    db, workspace_id=self.other_workspace, user_id=self.user,
                    channel="web", surface=SurfaceKind.WORKSPACE_PRIVATE,
                    require_membership=True,
                )

    async def test_business_writes_are_approval_gated(self):
        specs = {s.id: s for s in build_workspace_runtime().registry.all()}
        for id in (
            "workspace.sales.complete",
            "workspace.finance.invoice.create_simple",
            "workspace.finance.payment.record",
        ):
            self.assertTrue(specs[id].approval_required)
            self.assertNotEqual(specs[id].risk.value, "read_only")


if __name__ == "__main__":
    unittest.main()
