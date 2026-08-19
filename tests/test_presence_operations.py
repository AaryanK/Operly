import json,os,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.pool import StaticPool
from packages.actions.service import ActionService,ActionStatus
from packages.capabilities.agent_harness import ROLE_AUTHORITY
from packages.capabilities.providers import default_registry
from packages.company.events import query_events
from packages.company.intelligence import observe_evidence,synthesize_profile
from packages.database.company_models import BusinessActionRecord
from packages.database.db import Base
from packages.database.models import AppUser,ScheduledJob,Tenant
from packages.database.product_models import PresenceObservation,SolutionDeployment,SolutionImprovementProposal
from packages.database.schema import import_all_models
from packages.solutions.deployment import ManagedStaticDeploymentProvider
from packages.solutions.operations import PresenceOperationsService,run_due_observations
from packages.solutions.production import ProductionService
from packages.solutions.service import SolutionService

class FailedHealthProvider(ManagedStaticDeploymentProvider):
 async def health(self,result):return False,{"reason":"deliberate post-change failure"}

class PresenceOperationsTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import_all_models();self.engine=create_async_engine("sqlite+aiosqlite:///:memory:",poolclass=StaticPool)
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.db=async_sessionmaker(self.engine,expire_on_commit=False)();self.user=AppUser(email="owner@care.test",password_hash="x");self.a=Tenant(name="A");self.b=Tenant(name="B");self.db.add_all([self.user,self.a,self.b]);await self.db.flush();self.solutions=SolutionService();self.tmp=tempfile.TemporaryDirectory();self.old_provider=os.environ.get("OPERLY_DEPLOYMENT_PROVIDER");self.old_root=os.environ.get("OPERLY_DEPLOYMENT_ROOT");os.environ["OPERLY_DEPLOYMENT_PROVIDER"]="managed_static";os.environ["OPERLY_DEPLOYMENT_ROOT"]=self.tmp.name
  for field,value in (("display_name","Acme Roofing"),("description","Roofing specialists"),("products_services",["Service A"]),("contact",{"phones":["312-555-0100"]})):
   await observe_evidence(self.db,self.a.id,field,value,"owner",confidence=1,owner_confirmed=True)
  await synthesize_profile(self.db,self.a.id);self.solution=await self.solutions.create_presence(self.db,self.a.id,self.user.id);self.production=ProductionService(self.solutions,ManagedStaticDeploymentProvider(self.tmp.name));await self.production.publish(self.db,self.a.id,self.solution.id,self.user.id,idempotency_key="initial")
 async def asyncTearDown(self):
  if self.old_provider is None:os.environ.pop("OPERLY_DEPLOYMENT_PROVIDER",None)
  else:os.environ["OPERLY_DEPLOYMENT_PROVIDER"]=self.old_provider
  if self.old_root is None:os.environ.pop("OPERLY_DEPLOYMENT_ROOT",None)
  else:os.environ["OPERLY_DEPLOYMENT_ROOT"]=self.old_root
  await self.db.close();await self.engine.dispose();self.tmp.cleanup()
 async def test_noop_then_real_profile_drift_approval_publish_and_audit(self):
  operations=PresenceOperationsService(self.solutions,self.production);first=await operations.observe(self.db,self.a.id,self.solution.id);self.assertEqual(first["proposals"],[])
  await observe_evidence(self.db,self.a.id,"products_services",["Service A","Commercial Roofing"],"owner",confidence=1,owner_confirmed=True);await synthesize_profile(self.db,self.a.id)
  detected=await operations.observe(self.db,self.a.id,self.solution.id);self.assertEqual(len(detected["proposals"]),1);proposal_id=detected["proposals"][0]["id"];self.assertIn("Commercial Roofing",detected["proposals"][0]["issue"])
  repeated=await operations.observe(self.db,self.a.id,self.solution.id);self.assertEqual(repeated["proposals"][0]["id"],proposal_id);self.assertEqual(await self.db.scalar(select(func.count(SolutionImprovementProposal.id))),1)
  actions=ActionService(self.db,default_registry(),authority=set(ROLE_AUTHORITY["owner"]),actor_id=self.user.id);action=await actions.propose(tenant_id=self.a.id,objective="Keep website accurate",capability="solution.apply_improvement",arguments={"proposal_id":proposal_id},rationale="Owner-confirmed service is absent from live site",expected_outcome="Website includes Commercial Roofing",risk_level="medium",idempotency_key="approve-drift")
  proposal=await self.db.get(SolutionImprovementProposal,proposal_id);proposal.action_id=action.id;self.assertEqual(action.status,ActionStatus.WAITING_APPROVAL);self.assertIsNotNone(action.approval_id);self.assertIsNone(proposal.after_version_reference)
  await actions.approve(self.a.id,action.id);self.assertEqual(action.status,ActionStatus.VERIFIED);self.assertEqual(proposal.status,"verified");self.assertNotEqual(proposal.before_version_reference,proposal.after_version_reference)
  deployment=await self.db.get(SolutionDeployment,proposal.deployment_id);html=Path(deployment.artifact_reference).read_text(encoding="utf-8");self.assertIn("Commercial Roofing",html);verification=json.loads(proposal.verification_json);self.assertTrue(verification["passed"]);self.assertTrue(verification["site_healthy"])
  event_types={x.event_type for x in await query_events(self.db,self.a.id,limit=200)};self.assertTrue({"presence.profile_mismatch","solution.change.proposed","solution.change.applied","solution.change.verified"}<=event_types)
  self.assertIsNone(await self.db.scalar(select(SolutionImprovementProposal).where(SolutionImprovementProposal.tenant_id==self.b.id)))
 async def test_rejection_no_mutation_and_failed_verification_preserves_live(self):
  await observe_evidence(self.db,self.a.id,"products_services",["Service A","Service B"],"owner",confidence=1,owner_confirmed=True);await synthesize_profile(self.db,self.a.id);operations=PresenceOperationsService(self.solutions,self.production);proposal_id=(await operations.observe(self.db,self.a.id,self.solution.id))["proposals"][0]["id"]
  actions=ActionService(self.db,default_registry(),authority=set(ROLE_AUTHORITY["owner"]),actor_id=self.user.id);action=await actions.propose(tenant_id=self.a.id,objective="Update",capability="solution.apply_improvement",arguments={"proposal_id":proposal_id},rationale="drift",expected_outcome="accurate",risk_level="medium");before=(await self.solutions.get(self.db,self.a.id,self.solution.id)).current_version_reference;await actions.reject(self.a.id,action.id);self.assertEqual((await self.solutions.get(self.db,self.a.id,self.solution.id)).current_version_reference,before)
  proposal=await self.db.get(SolutionImprovementProposal,proposal_id);failed_ops=PresenceOperationsService(self.solutions,ProductionService(self.solutions,FailedHealthProvider(self.tmp.name)));failed=await failed_ops.apply(self.db,self.a.id,proposal.id,self.user.id);self.assertEqual(failed.status,"verification_failed");self.assertEqual((await self.solutions.get(self.db,self.a.id,self.solution.id)).current_version_reference,before)
 async def test_durable_schedule_duplicate_suppression_and_due_execution(self):
  operations=PresenceOperationsService(self.solutions,self.production,interval_minutes=15);one,created=await operations.schedule(self.db,self.a.id,self.solution.id);two,again=await operations.schedule(self.db,self.a.id,self.solution.id);self.assertTrue(created);self.assertFalse(again);self.assertEqual(one.id,two.id)
  one.run_at=datetime.utcnow()-timedelta(seconds=1);result=await run_due_observations(self.db,self.solutions);self.assertEqual(len(result),1);self.assertEqual(one.status,"completed");pending=await self.db.scalar(select(ScheduledJob).where(ScheduledJob.tenant_id==self.a.id,ScheduledJob.status=="pending"));self.assertIsNotNone(pending)
