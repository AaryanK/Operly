import json
import unittest
import tempfile
from datetime import datetime, timedelta
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.intelligence import observe_evidence,synthesize_profile
from packages.database.application_builder_models import ApplicationVersion,ManagedApplication
from packages.database.custom_software_models import GeneratedProject,GeneratedSourceBundle,RunnerBuildRecord,RunnerPreviewRecord,SoftwarePlanRecord
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
 async def test_generated_solution_prefers_live_runner_preview_and_ignores_expired_preview(self):
  plan=SoftwarePlanRecord(tenant_id=self.a.id,prompt="Build a full-stack scheduling app",current_version=1,approved_version=1,status="approved",created_by=self.user.id);self.db.add(plan);await self.db.flush()
  project=GeneratedProject(tenant_id=self.a.id,slug="runner-backed",name="Runner Backed",vertical="general",prompt=plan.prompt,brand_json="{}",artifact_graph_json="{}",plan_id=plan.id,approved_plan_version=1,architecture_pack="general",created_by=self.user.id)
  source=GeneratedSourceBundle(tenant_id=self.a.id,plan_id=plan.id,plan_version=1,source_version=1,application_id=f"plan-{plan.id}",bundle_digest="sha256:solution-preview",manifest_json="{}",files_json="[]",provenance_json=json.dumps({"summary":"Initial full-stack source"}),created_by=self.user.id);self.db.add_all([project,source]);await self.db.flush()
  build=RunnerBuildRecord(tenant_id=self.a.id,plan_id=plan.id,source_bundle_id=source.id,runner_job_id="runner-job",idempotency_key="solution-preview-build",state="preview_ready",runner_implementation="test-runner",isolation_profile="isolated-test",submission_json="{}",result_json="{}",created_by=self.user.id);self.db.add(build);await self.db.flush()
  preview=RunnerPreviewRecord(tenant_id=self.a.id,build_id=build.id,runner_preview_id="runner-preview",state="active",target_url="https://runner-preview.example",expires_at=datetime.utcnow()+timedelta(minutes=10),created_by=self.user.id);self.db.add(preview);await self.db.commit()
  rows=await self.service.list(self.db,self.a.id);solution=next(x for x in rows if x.runtime_type==RuntimeType.GENERATED_PROJECT and x.runtime_reference==project.id)
  self.assertEqual(solution.lifecycle_status,LifecycleStatus.PREVIEW_READY);self.assertEqual(solution.preview_state,"ready");self.assertEqual(await self.service.preview_target(self.db,self.a.id,solution,project),f"/api/custom-software/previews/{preview.id}/")
  self.assertEqual(solution_json(solution)["runtime"],{"kind":"generated","id":project.id})
  versions=await self.service.versions(self.db,self.a.id,solution.id);self.assertEqual(versions[0]["kind"],"source");self.assertEqual(versions[0]["version"],1);self.assertEqual(versions[0]["summary"],"Initial full-stack source")
  preview.expires_at=datetime.utcnow()-timedelta(seconds=1);await self.db.commit();solution=await self.service.get(self.db,self.a.id,solution.id)
  self.assertEqual(solution.lifecycle_status,LifecycleStatus.APPROVED);self.assertEqual(solution.preview_state,"available");self.assertEqual(await self.service.preview_target(self.db,self.a.id,solution,project),f"/api/custom-software/projects/{project.id}/preview")
 async def test_generated_solution_history_only_shows_current_approved_plan_version(self):
  plan=SoftwarePlanRecord(tenant_id=self.a.id,prompt="Build a durable scheduling app",current_version=2,approved_version=2,status="approved",created_by=self.user.id);self.db.add(plan);await self.db.flush()
  project=GeneratedProject(tenant_id=self.a.id,slug="version-scoped",name="Version Scoped",vertical="general",prompt=plan.prompt,brand_json="{}",artifact_graph_json="{}",plan_id=plan.id,approved_plan_version=2,architecture_pack="general",created_by=self.user.id)
  old_source=GeneratedSourceBundle(tenant_id=self.a.id,plan_id=plan.id,plan_version=1,source_version=1,application_id=f"plan-{plan.id}",bundle_digest="sha256:old",manifest_json="{}",files_json="[]",provenance_json=json.dumps({"summary":"Old approved plan source"}),created_by=self.user.id)
  current_source=GeneratedSourceBundle(tenant_id=self.a.id,plan_id=plan.id,plan_version=2,source_version=2,application_id=f"plan-{plan.id}",bundle_digest="sha256:current",manifest_json="{}",files_json="[]",provenance_json=json.dumps({"summary":"Current approved plan source"}),created_by=self.user.id);self.db.add_all([project,old_source,current_source]);await self.db.commit()
  rows=await self.service.list(self.db,self.a.id);solution=next(x for x in rows if x.runtime_type==RuntimeType.GENERATED_PROJECT and x.runtime_reference==project.id)
  versions=await self.service.versions(self.db,self.a.id,solution.id)
  self.assertEqual(len(versions),1);self.assertEqual(versions[0]["version"],2);self.assertEqual(versions[0]["status"],"current");self.assertEqual(versions[0]["summary"],"Current approved plan source")
 async def test_digital_presence_uses_profile_and_continues_same_identity(self):
  await observe_evidence(self.db,self.a.id,"display_name","Acme Plumbing","owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"description","Emergency plumbing across Chicago","owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"products_services",["Drain cleaning","Water heaters"],"owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"contact",{"phones":["312-555-1212"]},"owner",confidence=1,owner_confirmed=True);await synthesize_profile(self.db,self.a.id)
  one=await self.service.create_presence(self.db,self.a.id,self.user.id);two=await self.service.create_presence(self.db,self.a.id,self.user.id);self.assertEqual(one.id,two.id);self.assertEqual(one.lifecycle_status,LifecycleStatus.PREVIEW_READY)
  context=json.loads(one.context_json);self.assertEqual(context["planning_request"]["products_services"],["Drain cleaning","Water heaters"]);self.assertEqual(context["planning_request"]["contact"]["phones"],["312-555-1212"])
  _,runtime=await self.service.resolve(self.db,self.a.id,one.id);self.assertIsInstance(runtime,StudioProject);self.assertEqual(solution_json(one)["preview"]["url"],f"/api/solutions/{one.id}/preview");self.assertEqual(solution_json(one)["runtime"],{"kind":"studio","id":runtime.id})
  job,published=await ProductionService(self.service,ManagedStaticDeploymentProvider(self.deployments.name)).publish(self.db,self.a.id,one.id,self.user.id);self.assertEqual(job.status,"succeeded");self.assertEqual(published.lifecycle_status,LifecycleStatus.LIVE);self.assertEqual(published.production_state,"live");self.assertTrue(published.production_url)
 async def test_digital_presence_falls_back_to_active_workspace_name(self):
  website=await self.service.create_presence(self.db,self.b.id,self.user.id);self.assertEqual(website.name,"B")
  _,runtime=await self.service.resolve(self.db,self.b.id,website.id);self.assertEqual(runtime.name,"B")
 async def test_tenant_and_runtime_reference_isolation(self):
  studio=await StudioService.create_project(self.db,self.a.id,self.user.id,"A Website","");rows=await self.service.list(self.db,self.a.id);solution=next(x for x in rows if x.runtime_reference==studio.id)
  with self.assertRaises(LookupError):await self.service.get(self.db,self.b.id,solution.id)
  forged=SolutionRecord(tenant_id=self.b.id,name="Forged",description="",solution_type=SolutionType.DIGITAL_PRESENCE,lifecycle_status=LifecycleStatus.DRAFT,runtime_type=RuntimeType.STUDIO,runtime_reference=studio.id);self.db.add(forged);await self.db.flush()
  with self.assertRaises(LookupError):await self.service.resolve(self.db,self.b.id,forged.id)