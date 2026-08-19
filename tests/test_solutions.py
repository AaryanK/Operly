import json
import unittest
import tempfile
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.intelligence import observe_evidence,synthesize_profile
from packages.database.application_builder_models import ApplicationVersion,ManagedApplication
from packages.database.custom_software_models import GeneratedProject
from packages.database.db import Base
from packages.database.models import AppUser,Tenant
from packages.database.product_models import SolutionRecord
from packages.database.schema import import_all_models
from packages.database.studio_models import StudioProject
from packages.solutions import LifecycleStatus,RuntimeType,SolutionService,SolutionType
from packages.solutions.service import solution_json
from packages.solutions.deployment import ManagedStaticDeploymentProvider
from packages.solutions.production import ProductionService
from packages.studio.service import StudioService

class SolutionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import_all_models();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:",poolclass=StaticPool)
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.db=async_sessionmaker(self.engine,expire_on_commit=False)();self.user=AppUser(email="owner@solution.test",password_hash="x");self.a=Tenant(name="A");self.b=Tenant(name="B");self.db.add_all([self.user,self.a,self.b]);await self.db.flush();self.service=SolutionService();self.deployments=tempfile.TemporaryDirectory()
 async def asyncTearDown(self):await self.db.close();await self.engine.dispose();self.deployments.cleanup()
 async def test_backfill_all_runtimes_stable_and_version_mapping(self):
  studio=await StudioService.create_project(self.db,self.a.id,self.user.id,"Legacy Website","Public site")
  app=ManagedApplication(tenant_id=self.a.id,slug="ops",name="Operations",description="Internal",created_by=self.user.id);self.db.add(app);await self.db.flush();version=ApplicationVersion(tenant_id=self.a.id,application_id=app.id,version_number=1,manifest_json="{}",summary="Initial",created_by=self.user.id,active=True);self.db.add(version);await self.db.flush();app.active_version_id=version.id
  project=GeneratedProject(tenant_id=self.a.id,slug="generated-a",name="Generated A",vertical="service",prompt="Generated project",brand_json="{}",artifact_graph_json="{}",created_by=self.user.id);self.db.add(project);await self.db.commit()
  first=await self.service.list(self.db,self.a.id);second=await self.service.list(self.db,self.a.id);self.assertEqual(len(first),3);self.assertEqual({x.id for x in first},{x.id for x in second});self.assertEqual(await self.db.scalar(select(func.count(SolutionRecord.id)).where(SolutionRecord.tenant_id==self.a.id)),3)
  mapped={x.runtime_type:x for x in first};self.assertEqual(mapped[RuntimeType.STUDIO].solution_type,SolutionType.DIGITAL_PRESENCE);self.assertEqual(mapped[RuntimeType.MANAGED_APP].current_version_reference,version.id);self.assertEqual(mapped[RuntimeType.GENERATED_PROJECT].current_version_reference,"1")
  versions=await self.service.versions(self.db,self.a.id,mapped[RuntimeType.STUDIO].id);self.assertEqual(versions[0]["version"],1)
 async def test_digital_presence_uses_profile_and_continues_same_identity(self):
  await observe_evidence(self.db,self.a.id,"display_name","Acme Plumbing","owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"description","Emergency plumbing across Chicago","owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"products_services",["Drain cleaning","Water heaters"],"owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"contact",{"phones":["312-555-1212"]},"owner",confidence=1,owner_confirmed=True);await synthesize_profile(self.db,self.a.id)
  one=await self.service.create_presence(self.db,self.a.id,self.user.id);two=await self.service.create_presence(self.db,self.a.id,self.user.id);self.assertEqual(one.id,two.id);self.assertEqual(one.lifecycle_status,LifecycleStatus.PREVIEW_READY)
  context=json.loads(one.context_json);self.assertEqual(context["planning_request"]["products_services"],["Drain cleaning","Water heaters"]);self.assertEqual(context["planning_request"]["contact"]["phones"],["312-555-1212"])
  _,runtime=await self.service.resolve(self.db,self.a.id,one.id);self.assertIsInstance(runtime,StudioProject);self.assertEqual(solution_json(one)["preview"]["url"],f"/api/solutions/{one.id}/preview")
  job,published=await ProductionService(self.service,ManagedStaticDeploymentProvider(self.deployments.name)).publish(self.db,self.a.id,one.id,self.user.id);self.assertEqual(job.status,"succeeded");self.assertEqual(published.lifecycle_status,LifecycleStatus.LIVE);self.assertEqual(published.production_state,"live");self.assertTrue(published.production_url)
 async def test_tenant_and_runtime_reference_isolation(self):
  studio=await StudioService.create_project(self.db,self.a.id,self.user.id,"A Website","");rows=await self.service.list(self.db,self.a.id);solution=next(x for x in rows if x.runtime_reference==studio.id)
  with self.assertRaises(LookupError):await self.service.get(self.db,self.b.id,solution.id)
  forged=SolutionRecord(tenant_id=self.b.id,name="Forged",description="",solution_type=SolutionType.DIGITAL_PRESENCE,lifecycle_status=LifecycleStatus.DRAFT,runtime_type=RuntimeType.STUDIO,runtime_reference=studio.id);self.db.add(forged);await self.db.flush()
  with self.assertRaises(LookupError):await self.service.resolve(self.db,self.b.id,forged.id)
