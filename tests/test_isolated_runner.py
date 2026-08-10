import json,os,unittest
from unittest.mock import patch
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine

from packages.custom_software.generated_sources import generated_files,prompt_digest
from packages.custom_software.planner import build_software_plan
from packages.custom_software.plan_service import approve,create_plan
from packages.custom_software.runner_adapters import FakeRunnerAdapter,LocalSubprocessTestRunner
from packages.custom_software.runner_contracts import BuildSubmission,HealthCheck,NetworkPolicy
from packages.custom_software.runner_service import RunnerStateError,_event,active_preview,build_events,owned_build,request_repair,stop_preview,submit_build
from packages.custom_software.source_bundles import BundlePolicyError,SourceFile,build_bundle
from packages.database.db import Base
from packages.database.models import AppUser,Tenant,TenantMember
from packages.database import models,custom_software_models
from apps.api.custom_software_router import _validated_preview_target

PROMPT="Build a football club and match intelligence platform with leagues, seasons, clubs, squads, players, tactical formations, live match events, automatic standings, player statistics, and analytics."

class BundleAndPolicyTests(unittest.TestCase):
 def test_bundle_is_deterministic_versioned_and_traceable(self):
  plan=build_software_plan(PROMPT);files=generated_files(plan)
  a=build_bundle(files,"w1","a1","p1",1,1,prompt_digest(plan));b=build_bundle(list(reversed(files)),"w1","a1","p1",1,1,prompt_digest(plan))
  self.assertEqual(a.digest,b.digest);self.assertEqual(a.manifest["files"],b.manifest["files"]);self.assertTrue(all(x["generatedBy"] for x in a.manifest["files"]))
 def test_traversal_absolute_hidden_duplicate_size_and_secret_rejected(self):
  base=("w","a","p",1,1,"sha256:"+"0"*64)
  for path in ("../escape","/host","C:/host",".ssh/key","x\\y"):
   with self.subTest(path=path),self.assertRaises(BundlePolicyError):build_bundle([SourceFile(path,b"x","test")],*base)
  with self.assertRaises(BundlePolicyError):build_bundle([SourceFile("a",b"x","t"),SourceFile("a",b"y","t")],*base)
  with self.assertRaises(BundlePolicyError):build_bundle([SourceFile("key.txt",b"BEGIN PRIVATE KEY","t")],*base)
 def test_command_port_network_dependency_and_result_policies(self):
  common=dict(workspaceId="w",applicationId="a",planVersion=1,sourceVersion=1,stackId="python-stdlib-web",sourceBundleDigest="sha256:"+"a"*64,operations=["build"],healthCheck=HealthCheck(),idempotencyKey="abcdefgh")
  with self.assertRaises(ValidationError):BuildSubmission(**common,requiredPorts=[22])
  with self.assertRaises(ValidationError):NetworkPolicy(mode="approved_hosts",approvedHosts=["169.254.169.254"])
  with self.assertRaises(ValidationError):BuildSubmission(**common,dependencies=[{"name":"../evil","version":"1.0"}])
 def test_preview_target_requires_approved_runner_origin(self):
  from fastapi import HTTPException
  with patch.dict(os.environ,{"OPERLY_ENV":"production","OPERLY_SANDBOX_PREVIEW_HOSTS":"preview.runner.example"},clear=True):
   self.assertEqual(_validated_preview_target("https://preview.runner.example/app"),"https://preview.runner.example/app")
   for value in ("http://preview.runner.example","https://169.254.169.254/latest","https://evil.example"):
    with self.subTest(value=value),self.assertRaises(HTTPException):_validated_preview_target(value)

class RunnerServiceTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions() as db:
   self.tenant=Tenant(name="Runner",slug="runner");self.other=Tenant(name="Other",slug="other");self.user=AppUser(email="runner@test.local",password_hash="x",display_name="Runner");db.add_all([self.tenant,self.other,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()
 async def asyncTearDown(self):await self.engine.dispose()
 async def plan(self,db):
  row,_,plan=await create_plan(db,self.tenant.id,self.user.id,PROMPT);await approve(db,row,1);return row,plan
 async def test_fake_success_persists_lifecycle_and_cross_workspace_denied(self):
  async with self.sessions() as db:
   row,plan=await self.plan(db);build=await submit_build(db,self.tenant.id,self.user.id,row,plan,"fake-success",FakeRunnerAdapter());self.assertEqual(build.state,"preview_ready")
   events=await build_events(db,build);self.assertEqual(events[-1].state,"preview_ready");self.assertGreater(len(events),8)
   with self.assertRaises(LookupError):await owned_build(db,self.other.id,build.id)
   await db.commit();identity=build.id
  async with self.sessions() as restarted:self.assertEqual((await owned_build(restarted,self.tenant.id,identity)).state,"preview_ready")
 async def test_failure_never_exposes_preview_and_invalid_transition_is_audited_out(self):
  async with self.sessions() as db:
   row,plan=await self.plan(db);build=await submit_build(db,self.tenant.id,self.user.id,row,plan,"fake-failure",FakeRunnerAdapter("test_failure"));self.assertEqual(build.state,"tests_failed");self.assertFalse(json.loads(build.result_json)["previewAvailable"])
   with self.assertRaises(RunnerStateError):await _event(db,build,"preview_ready")
 async def test_idempotent_submission(self):
  async with self.sessions() as db:
   row,plan=await self.plan(db);runner=FakeRunnerAdapter();first=await submit_build(db,self.tenant.id,self.user.id,row,plan,"same-build-key",runner);second=await submit_build(db,self.tenant.id,self.user.id,row,plan,"same-build-key",runner);self.assertEqual(first.id,second.id);self.assertEqual(len(runner.jobs),1)
 async def test_cancel_timeout_cleanup_transitions(self):
  from packages.database.custom_software_models import RunnerBuildRecord
  async with self.sessions() as db:
   row,plan=await self.plan(db);build=await submit_build(db,self.tenant.id,self.user.id,row,plan,"cancel-build",FakeRunnerAdapter());await _event(db,build,"cancel_requested");await _event(db,build,"cancelled");await _event(db,build,"cleaning");await _event(db,build,"cleaned");await db.commit();self.assertEqual(build.state,"cleaned")
   timed=RunnerBuildRecord(tenant_id=self.tenant.id,plan_id=row.id,source_bundle_id=build.source_bundle_id,idempotency_key="timed-build",state="building",runner_implementation="fake",isolation_profile="none",submission_json="{}",created_by=self.user.id);db.add(timed);await db.flush();await _event(db,timed,"timed_out");await db.commit();self.assertEqual(timed.failure_classification,"resource_violation")

class LocalRunnerAcceptance(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.env=patch.dict(os.environ,{"OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER":"1","OPERLY_ENV":"test"});self.env.start();self.runner=LocalSubprocessTestRunner();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions() as db:
   self.tenant=Tenant(name="Local",slug="local");self.user=AppUser(email="local@test",password_hash="x",display_name="Local");db.add_all([self.tenant,self.user]);await db.flush();db.add(TenantMember(tenant_id=self.tenant.id,user_id=self.user.id,role="owner"));await db.commit()
 async def asyncTearDown(self):
  for job in list(self.runner.jobs):await self.runner.cleanup(job)
  await self.engine.dispose();self.env.stop()
 async def setup_plan(self,db):
  row,_,plan=await create_plan(db,self.tenant.id,self.user.id,PROMPT);await approve(db,row,1);return row,plan
 async def test_real_generated_process_health_domain_persistence_and_cleanup(self):
  async with self.sessions() as db:
   row,plan=await self.setup_plan(db);build=await submit_build(db,self.tenant.id,self.user.id,row,plan,"local-runner-ok",self.runner);self.assertEqual(build.state,"preview_ready");result=json.loads(build.result_json);self.assertTrue(result["healthCheckSuccess"]);self.assertTrue(result["acceptanceCheckSuccess"]);self.assertTrue(result["testReport"]["acceptance"]["passed"])
   from sqlalchemy import select
   from packages.database.custom_software_models import RunnerPreviewRecord
   preview=await db.scalar(select(RunnerPreviewRecord).where(RunnerPreviewRecord.build_id==build.id));same,_=await active_preview(db,self.tenant.id,preview.id);self.assertEqual(same.id,preview.id)
   with self.assertRaises(LookupError):await active_preview(db,"another-workspace",preview.id)
   job_id=build.runner_job_id;await stop_preview(db,preview,build,self.runner);self.assertEqual(build.state,"cleaned");self.assertNotIn(job_id,self.runner.jobs)
 async def test_deliberate_defect_repair_creates_new_source_and_passes(self):
  async with self.sessions() as db:
   row,plan=await self.setup_plan(db);failed=await submit_build(db,self.tenant.id,self.user.id,row,plan,"local-defect",self.runner,defect=True);self.assertEqual(failed.state,"tests_failed")
   repaired,record=await request_repair(db,self.tenant.id,self.user.id,failed,row,plan,"local-repaired",self.runner);self.assertEqual(record.status,"applied");self.assertEqual(repaired.state,"preview_ready");self.assertEqual(repaired.attempt,2);self.assertNotEqual(repaired.source_bundle_id,failed.source_bundle_id);self.assertIn("smallest source-only patch",record.repair_prompt)

@unittest.skipUnless(os.getenv("OPERLY_REAL_ISOLATION_RUNNER")=="1","real container or microVM runner is not available in this environment")
class RealIsolationAcceptance(unittest.TestCase):
 def test_external_isolation_boundary(self):self.fail("Configure the real runner integration harness before enabling this gate")
