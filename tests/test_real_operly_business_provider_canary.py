"""Actual Operly Kernel + Workspace provider reads against an isolated DB.

Unlike a mocked broker HTTP contract, these tests execute native business
providers and real SQLAlchemy queries with synthetic Workspace records.
No production DB, external network, customer data or paid inference.
"""
import unittest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.business_models import Contact, ActivityEvent
from packages.database.workspace_module_models import WorkspaceModule
from packages.kernel.contracts import CapabilityRisk, RuntimeRequest
from packages.security.execution_context import resolve_execution_context, ExecutionContextError
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


class RealWorkspaceReadCanaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.sessions() as db:
            self.user=AppUser(email="staging-owner@example.test",display_name="Synthetic Owner")
            self.tenant=Tenant(name="Isolated Business Test",slug="isolated-business-test")
            self.other=Tenant(name="Other Workspace",slug="other-workspace")
            db.add_all([self.user,self.tenant,self.other])
            await db.flush()
            self.member=TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner")
            self.contact=Contact(tenant_id=self.tenant.id,name="Synthetic Alice",
                                 email="alice@example.test")
            self.other_contact=Contact(tenant_id=self.other.id,name="Synthetic Bob",
                                       email="bob@example.test")
            db.add_all([
                self.member,self.contact,self.other_contact,
                WorkspaceModule(tenant_id=self.tenant.id,module_key="crm",enabled=True),
                ActivityEvent(tenant_id=self.tenant.id,event_type="synthetic.canary",
                              entity_type="contact",entity_id=self.contact.id,
                              summary="Synthetic activity",actor="test"),
            ])
            await db.commit()
            self.owner_id,self.tenant_id,self.other_id=(self.user.id,self.tenant.id,self.other.id)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _context(self,db,tenant_id=None):
        return await resolve_execution_context(
            db,workspace_id=tenant_id or self.tenant_id,
            user_id=self.owner_id,channel="web",
            surface=SurfaceKind.WORKSPACE_PRIVATE,require_membership=True,
        )

    async def _read(self,db,context,capability,args=None):
        runtime=build_workspace_runtime()
        specs=await runtime.available_capabilities(
            db,context=context,query=capability,limit=50,
        )
        exact=[item for item in specs if item.id==capability]
        self.assertEqual(len(exact),1,capability)
        self.assertEqual(exact[0].risk,CapabilityRisk.READ_ONLY)
        response=await runtime.execute(
            db,context=context,
            request=RuntimeRequest(
                goal="Read-only isolated staging business health check",
                capability_id=capability,arguments=args or {},
                request_id="business-canary-"+capability,
            ),
        )
        self.assertTrue(response.done)
        return response.result

    async def test_real_native_business_providers_return_scoped_rows(self):
        async with self.sessions() as db:
            ctx=await self._context(db)
            summary=await self._read(db,ctx,"workspace.summary.read")
            self.assertEqual(summary["members"],1)
            activity=await self._read(db,ctx,"workspace.activity.list",{"limit":10})
            self.assertEqual(len(activity["events"]),1)
            self.assertEqual(activity["events"][0]["event_type"],"synthetic.canary")
            found=await self._read(db,ctx,"workspace.search",{"query":"Synthetic","limit":10})
            ids={item["id"] for item in found["items"]}
            self.assertIn(self.contact.id,ids)
            self.assertNotIn(self.other_contact.id,ids)
            self.assertFalse(any("bob@example.test" in str(x) for x in found["items"]))
            # An actual database query, independent of the provider report.
            expected=await db.scalars(
                select(Contact).where(Contact.tenant_id==self.tenant_id)
            )
            self.assertEqual(len(expected.all()),1)

    async def test_other_tenant_access_and_revocation_fail_closed(self):
        async with self.sessions() as db:
            with self.assertRaises(ExecutionContextError):
                await self._context(db,tenant_id=self.other_id)
            membership=await db.scalar(select(TenantMember).where(
                TenantMember.tenant_id==self.tenant_id,
                TenantMember.user_id==self.owner_id,
            ))
            await db.delete(membership)
            await db.commit()
            with self.assertRaises(ExecutionContextError):
                await self._context(db)

    async def test_business_mutations_are_not_advertised_as_read_only(self):
        specs={spec.id:spec for spec in build_workspace_runtime().registry.all()}
        for capability in (
            "workspace.sales.complete",
            "workspace.finance.invoice.create_simple",
            "workspace.finance.payment.record",
        ):
            with self.subTest(capability=capability):
                self.assertNotEqual(specs[capability].risk,CapabilityRisk.READ_ONLY)
                self.assertTrue(specs[capability].approval_required)


if __name__=="__main__":
    unittest.main()
