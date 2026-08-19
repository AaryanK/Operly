import json
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.events import query_events
from packages.company.intelligence import observe_evidence,synthesize_profile
from packages.database.db import Base
from packages.database.models import AppUser,Tenant
from packages.database.product_models import SolutionDeployment,SolutionJob
from packages.database.schema import import_all_models
from packages.solutions import LifecycleStatus,SolutionService
from packages.solutions.deployment import DeploymentResult,ManagedStaticDeploymentProvider,UnconfiguredDeploymentProvider
from packages.solutions.production import JobStatus,JobType,ProductionService,transition
from packages.studio.service import StudioService

class FailedHealthProvider(ManagedStaticDeploymentProvider):
 async def health(self,result):return False,{"reason":"deliberate_health_failure"}

class ProductionLifecycleTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import_all_models();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:",poolclass=StaticPool)
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.db=async_sessionmaker(self.engine,expire_on_commit=False)();self.user=AppUser(email="publish@test",password_hash="x");self.a=Tenant(name="A");self.b=Tenant(name="B");self.db.add_all([self.user,self.a,self.b]);await self.db.flush();self.solutions=SolutionService();self.tmp=tempfile.TemporaryDirectory()
  await observe_evidence(self.db,self.a.id,"display_name","Live Acme","owner",confidence=1,owner_confirmed=True);await observe_evidence(self.db,self.a.id,"description","A real public business presence","owner",confidence=1,owner_confirmed=True);await synthesize_profile(self.db,self.a.id);self.solution=await self.solutions.create_presence(self.db,self.a.id,self.user.id)
 async def asyncTearDown(self):await self.db.close();await self.engine.dispose();self.tmp.cleanup()
 async def test_job_transitions_reject_invalid_state(self):
  job=SolutionJob(tenant_id=self.a.id,solution_id=self.solution.id,source_version_reference="v1",job_type=JobType.PUBLISH,status=JobStatus.QUEUED,idempotency_key="transition-test")
  with self.assertRaisesRegex(ValueError,"Invalid Solution job transition"):transition(job,JobStatus.SUCCEEDED)
  transition(job,JobStatus.RUNNING);transition(job,JobStatus.SUCCEEDED);self.assertIsNotNone(job.started_at);self.assertIsNotNone(job.ended_at)
 async def test_full_real_static_publish_idempotency_and_events(self):
  production=ProductionService(self.solutions,ManagedStaticDeploymentProvider(self.tmp.name));job,row=await production.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="publish-once")
  self.assertEqual((job.status,row.lifecycle_status,row.production_state),(JobStatus.SUCCEEDED,LifecycleStatus.LIVE,"live"));deployment=await self.db.scalar(select(SolutionDeployment).where(SolutionDeployment.job_id==job.id));self.assertTrue(Path(deployment.artifact_reference).is_file());html=Path(deployment.artifact_reference).read_text(encoding="utf-8");self.assertIn("Live Acme",html);self.assertEqual(deployment.health_state,"healthy")
  same_job,same_row=await production.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="publish-once");self.assertEqual(same_job.id,job.id);self.assertEqual(await self.db.scalar(select(func.count(SolutionDeployment.id))),1)
  job_types=set((await self.db.scalars(select(SolutionJob.job_type).where(SolutionJob.solution_id==self.solution.id))).all());self.assertTrue({JobType.BUILD,JobType.VERIFY,JobType.PUBLISH}<=job_types)
  events={x.event_type for x in await query_events(self.db,self.a.id,limit=100)};self.assertTrue({"solution.publish.requested","solution.publish.started","solution.publish.succeeded"}<=events)
 async def test_failed_health_preserves_previous_live_and_bounds_redacted_logs(self):
  good=ProductionService(self.solutions,ManagedStaticDeploymentProvider(self.tmp.name));first,row=await good.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="good")
  runtime=await self.db.get(__import__("packages.database.studio_models",fromlist=["StudioProject"]).StudioProject,self.solution.runtime_reference);old_url=row.production_url
  version=await StudioService.save_schema(self.db,self.a.id,runtime.id,self.user.id,json.loads((await self.db.get(__import__("packages.database.studio_models",fromlist=["StudioVersion"]).StudioVersion,runtime.active_draft_version_id)).schema_json),"Second version")
  failed,row=await ProductionService(self.solutions,FailedHealthProvider(self.tmp.name)).publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="bad-health")
  self.assertEqual(failed.failure_classification,"health_check_failure");self.assertEqual(row.lifecycle_status,LifecycleStatus.LIVE);self.assertEqual(row.production_url,old_url);self.assertEqual(await self.db.scalar(select(func.count(SolutionDeployment.id))),1)
  transition(failed,failed.status,log="token=super-secret-value "+("x"*100000));self.assertNotIn("super-secret-value",failed.log_json);self.assertLessEqual(len(failed.log_json),32000)
 async def test_unconfigured_is_truthful_and_tenant_scoped(self):
  job,row=await ProductionService(self.solutions,UnconfiguredDeploymentProvider()).publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="unconfigured");self.assertEqual(job.status,JobStatus.FAILED);self.assertEqual(job.failure_classification,"provider_unconfigured");self.assertEqual(row.lifecycle_status,LifecycleStatus.FAILED)
  with self.assertRaises(LookupError):await ProductionService(self.solutions,ManagedStaticDeploymentProvider(self.tmp.name)).publish(self.db,self.b.id,self.solution.id,self.user.id)
 async def test_rollback_redeploys_previous_verified_version(self):
  production=ProductionService(self.solutions,ManagedStaticDeploymentProvider(self.tmp.name));first,row=await production.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="v1");first_deployment=await self.db.scalar(select(SolutionDeployment).where(SolutionDeployment.job_id==first.id));runtime=(await self.solutions.resolve(self.db,self.a.id,self.solution.id))[1];old=await self.db.get(__import__("packages.database.studio_models",fromlist=["StudioVersion"]).StudioVersion,runtime.active_draft_version_id);schema=json.loads(old.schema_json);schema["site"]["title"]="Version Two";second_version=await StudioService.save_schema(self.db,self.a.id,runtime.id,self.user.id,schema,"Version two");second,row=await production.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="v2");self.assertEqual(row.current_version_reference,second_version.id)
  rollback,row=await production.rollback(self.db,self.a.id,self.solution.id,self.user.id);self.assertEqual(rollback.status,JobStatus.SUCCEEDED);self.assertEqual(row.current_version_reference,first_deployment.version_reference);events={x.event_type for x in await query_events(self.db,self.a.id,limit=100)};self.assertIn("solution.rollback.succeeded",events)
