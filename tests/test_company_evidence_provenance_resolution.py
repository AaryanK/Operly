import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.company.intelligence import observe_evidence, synthesize_profile
from packages.company.provenance import (
    get_subject,
    list_subjects,
    mark_evidence_inactive,
    resolve_conflict,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.schema import import_all_models
from packages.database.scope_models import ScopedCompanyEvidence


class CompanyEvidenceProvenanceResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@provenance.test", password_hash="x")
        self.tenant = Tenant(name="Provenance Test")
        self.db.add_all([self.user, self.tenant])
        await self.db.flush()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_unresolved_subject_conflict_is_explainable_then_owner_resolvable(self):
        first = await observe_evidence(
            self.db,
            self.tenant.id,
            "description",
            "Student attendance system",
            "research",
            confidence=0.55,
            owner_initiated=True,
            owner_confirmed=False,
            subject_kind="solution",
            subject_reference="solution-1",
            subject_name="Attendance",
            actor_user_id=self.user.id,
            conversation_id="conversation-1",
            action_id="action-1",
            research_run_id="research-1",
        )
        second = await observe_evidence(
            self.db,
            self.tenant.id,
            "description",
            "Unrelated astrology website",
            "research",
            confidence=0.55,
            owner_initiated=True,
            owner_confirmed=False,
            subject_kind="solution",
            subject_reference="solution-1",
            subject_name="Attendance",
            actor_user_id=self.user.id,
            conversation_id="conversation-2",
            action_id="action-2",
            research_run_id="research-2",
        )
        profile = await synthesize_profile(
            self.db,
            self.tenant.id,
            subject_kind="solution",
            subject_reference="solution-1",
            subject_name="Attendance",
        )
        self.assertNotIn("description", profile["profile"])
        self.assertIn("description", profile["conflicts"])

        subjects = await list_subjects(self.db, self.tenant.id)
        subject = next(row for row in subjects if row["reference_id"] == "solution-1")
        detail = await get_subject(self.db, self.tenant.id, subject["id"])
        provenance = {row["id"]: row for row in detail["provenance"]}
        self.assertEqual(provenance[first.id]["conversation_id"], "conversation-1")
        self.assertEqual(provenance[first.id]["action_id"], "action-1")
        self.assertEqual(provenance[first.id]["research_run_id"], "research-1")
        self.assertTrue(provenance[first.id]["owner_initiated"])
        self.assertFalse(provenance[first.id]["owner_confirmed"])

        resolved = await resolve_conflict(
            self.db,
            self.tenant.id,
            subject["id"],
            "description",
            first.id,
            actor_user_id=self.user.id,
        )
        self.assertEqual(resolved["profile"]["profile"]["description"], "Student attendance system")
        self.assertNotIn("description", resolved["profile"]["conflicts"])
        self.assertTrue(resolved["winner"]["owner_confirmed"])
        self.assertIn(second.id, resolved["superseded_evidence_ids"])

        stored_second = await self.db.scalar(
            select(ScopedCompanyEvidence).where(ScopedCompanyEvidence.id == second.id)
        )
        self.assertTrue(stored_second.superseded)

    async def test_explicit_stale_workflow_removes_obsolete_fact_from_subject_profile(self):
        row = await observe_evidence(
            self.db,
            self.tenant.id,
            "website",
            "https://old.example",
            "owner",
            confidence=1,
            owner_initiated=True,
            owner_confirmed=True,
            subject_kind="solution",
            subject_reference="solution-2",
            subject_name="Old Website",
            actor_user_id=self.user.id,
        )
        await synthesize_profile(
            self.db,
            self.tenant.id,
            subject_kind="solution",
            subject_reference="solution-2",
            subject_name="Old Website",
        )
        subject = next(
            item for item in await list_subjects(self.db, self.tenant.id)
            if item["reference_id"] == "solution-2"
        )
        await mark_evidence_inactive(
            self.db,
            self.tenant.id,
            subject["id"],
            row.id,
            actor_user_id=self.user.id,
            state="stale",
        )
        detail = await get_subject(self.db, self.tenant.id, subject["id"])
        self.assertNotIn("website", detail["profile"])
        stored = await self.db.scalar(
            select(ScopedCompanyEvidence).where(ScopedCompanyEvidence.id == row.id)
        )
        self.assertTrue(stored.stale)


if __name__ == "__main__":
    unittest.main()
