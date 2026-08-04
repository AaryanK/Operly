import json, unittest
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from packages.database.db import Base
from packages.database import models,business_models,agent_models,operations_models,studio_models
from packages.database.models import Tenant,AppUser,TenantMember
from packages.studio.service import StudioService

class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.sessions() as db:
            self.t=Tenant(name="Tenant",slug="tenant");self.other=Tenant(name="Other",slug="other");self.u=AppUser(email="test@example.com",password_hash="x");db.add_all([self.t,self.other,self.u]);await db.flush();db.add(TenantMember(tenant_id=self.t.id,user_id=self.u.id,role="owner"));await db.commit()
    async def asyncTearDown(self):await self.engine.dispose()
    async def test_lifecycle_is_immutable_and_scoped(self):
        async with self.sessions() as db:
            p=await StudioService.create_project(db,self.t.id,self.u.id,"Site","Description");first=p.active_draft_version_id
            with self.assertRaises(LookupError):await StudioService.project(db,self.other.id,p.id)
            v=await StudioService.save_schema(db,self.t.id,p.id,self.u.id,json.loads((await StudioService.version(db,self.t.id,p.id,first)).schema_json),"second")
            deployment,url=await StudioService.publish(db,self.t.id,p.id,v.id,self.u.id);self.assertIn("/sites/",url)
            published=await StudioService.version(db,self.t.id,p.id,v.id);self.assertEqual(published.status,"published")
            rollback=await StudioService.rollback(db,self.t.id,p.id,v.id,self.u.id);self.assertNotEqual(rollback.id,published.id);self.assertEqual(rollback.status,"draft");self.assertEqual(published.status,"published")
    async def test_cannot_publish_cross_tenant(self):
        async with self.sessions() as db:
            p=await StudioService.create_project(db,self.t.id,self.u.id,"Scoped")
            with self.assertRaises(LookupError):await StudioService.publish(db,self.other.id,p.id,p.active_draft_version_id,self.u.id)
if __name__=="__main__":unittest.main()
