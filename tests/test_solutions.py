import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.custom_software_models import (
    GeneratedSourceBundle,
    RunnerBuildRecord,
    RunnerPreviewRecord,
    SoftwarePlanRecord,
)
from packages.database.db import Base
from packages.database.models import AppUser, Tenant
from packages.database.product_models import SolutionRecord
from packages.database.schema import import_all_models
from packages.software_projects import ProjectState, SoftwareProjectService, SoftwareSourceService
from packages.solutions import LifecycleStatus, RuntimeType, SolutionService
from packages.solutions.service import solution_json


class SolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = AppUser(email="owner@solution.test", password_hash="x")
        self.a = Tenant(name="A")
        self.b = Tenant(name="B")
        self.db.add_all([self.user, self.a, self.b])
        await self.db.flush()
        self.service = SolutionService()
        self.projects = SoftwareProjectService()
        self.sources = SoftwareSourceService()
        self.deployments = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()
        self.deployments.cleanup()

    async def _software_solution(self, tenant_id, name="Canonical App", *, state=ProjectState.APPROVED):
        project = await self.projects.create(
            self.db,
            workspace_id=tenant_id,
            user_id=self.user.id,
            name=name,
            description=f"{name} objective",
        )
        source = await self.sources.persist(
            self.db,
            tenant_id=tenant_id,
            project_id=project.id,
            user_id=self.user.id,
            files={
                "index.html": f"<!doctype html><html><body><h1>{name}</h1></body></html>"
            },
            runtime_profile="static-web-js",
            provenance={"summary": "Initial canonical source"},
            change_summary="Initial canonical source",
        )
        await self.projects.set_execution_state(
            self.db,
            workspace_id=tenant_id,
            project_id=project.id,
            source_version_id=source.id,
            runtime_id="static-web-js",
            state=state,
        )
        record = await self.projects.record(self.db, tenant_id, project.id)
        solution = await self.service.create_software_solution(
            self.db,
            tenant_id=tenant_id,
            user_id=self.user.id,
            project=record,
            objective=record.description,
        )
        await self.db.flush()
        return solution, record, source

    async def test_registry_exposes_only_explicit_software_project_solutions(self):
        first, first_project, _ = await self._software_solution(self.a.id, "First")
        second, second_project, _ = await self._software_solution(self.a.id, "Second")
        await self.db.commit()

        rows = await self.service.list(self.db, self.a.id)
        again = await self.service.list(self.db, self.a.id)

        self.assertEqual({row.id for row in rows}, {first.id, second.id})
        self.assertEqual({row.id for row in rows}, {row.id for row in again})
        self.assertEqual(
            {row.runtime_type for row in rows}, {RuntimeType.SOFTWARE_PROJECT}
        )
        self.assertEqual(
            {row.runtime_reference for row in rows},
            {first_project.id, second_project.id},
        )
        self.assertEqual(
            await self.db.scalar(
                select(func.count(SolutionRecord.id)).where(
                    SolutionRecord.tenant_id == self.a.id
                )
            ),
            2,
        )

    async def test_software_solution_uses_live_runner_preview_adapter_without_changing_identity(self):
        solution, project, source = await self._software_solution(
            self.a.id, "Runner Backed"
        )
        plan = SoftwarePlanRecord(
            tenant_id=self.a.id,
            prompt="Build a full-stack scheduling app",
            current_version=1,
            approved_version=1,
            status="approved",
            created_by=self.user.id,
        )
        self.db.add(plan)
        await self.db.flush()
        bundle = GeneratedSourceBundle(
            tenant_id=self.a.id,
            plan_id=plan.id,
            plan_version=1,
            source_version=1,
            application_id=f"software-project-{project.id}",
            bundle_digest="sha256:solution-preview",
            manifest_json="{}",
            files_json="[]",
            provenance_json=json.dumps({"summary": "Runner source adapter"}),
            created_by=self.user.id,
        )
        self.db.add(bundle)
        await self.db.flush()
        build = RunnerBuildRecord(
            tenant_id=self.a.id,
            plan_id=plan.id,
            source_bundle_id=bundle.id,
            runner_job_id="runner-job",
            idempotency_key="solution-preview-build",
            state="preview_ready",
            runner_implementation="test-runner",
            isolation_profile="isolated-test",
            submission_json="{}",
            result_json="{}",
            created_by=self.user.id,
        )
        self.db.add(build)
        await self.db.flush()
        preview = RunnerPreviewRecord(
            tenant_id=self.a.id,
            build_id=build.id,
            runner_preview_id="runner-preview",
            state="active",
            target_url="https://runner-preview.example",
            expires_at=datetime.utcnow() + timedelta(minutes=10),
            created_by=self.user.id,
        )
        self.db.add(preview)
        await self.db.commit()

        with patch.dict(
            "os.environ", {"OPERLY_SANDBOX_PREVIEW_HOSTS": "runner-preview.example"}
        ):
            row = await self.service.get(self.db, self.a.id, solution.id)
            resolved_row, runtime = await self.service.resolve(
                self.db, self.a.id, solution.id
            )
            self.assertEqual(row.lifecycle_status, LifecycleStatus.PREVIEW_READY)
            self.assertEqual(row.preview_state, "ready")
            self.assertEqual(runtime.id, project.id)
            self.assertEqual(
                await self.service.preview_target(
                    self.db, self.a.id, resolved_row, runtime
                ),
                preview.target_url,
            )
            self.assertEqual(
                solution_json(row)["runtime"], {"kind": "software", "id": project.id}
            )
            self.assertEqual(row.current_version_reference, source.id)

    async def test_version_history_comes_only_from_canonical_source_versions(self):
        solution, project, first = await self._software_solution(self.a.id, "Versioned")
        second = await self.sources.persist(
            self.db,
            tenant_id=self.a.id,
            project_id=project.id,
            user_id=self.user.id,
            files={
                "index.html": "<!doctype html><html><body><h1>Version Two</h1></body></html>"
            },
            runtime_profile="static-web-js",
            provenance={"summary": "Version two"},
            change_summary="Version two",
        )
        await self.projects.set_execution_state(
            self.db,
            workspace_id=self.a.id,
            project_id=project.id,
            source_version_id=second.id,
            runtime_id="static-web-js",
            state=ProjectState.APPROVED,
        )
        await self.db.commit()

        versions = await self.service.versions(self.db, self.a.id, solution.id)
        self.assertEqual([item["id"] for item in versions], [second.id, first.id])
        self.assertEqual([item["status"] for item in versions], ["current", "superseded"])
        self.assertEqual(versions[0]["summary"], "Version two")

    async def test_tenant_and_runtime_reference_isolation(self):
        solution, project, _ = await self._software_solution(self.a.id, "A Website")
        await self.db.commit()

        with self.assertRaises(LookupError):
            await self.service.get(self.db, self.b.id, solution.id)

        forged = SolutionRecord(
            tenant_id=self.b.id,
            name="Forged",
            description="",
            solution_type="custom_solution",
            lifecycle_status=LifecycleStatus.DRAFT,
            runtime_type=RuntimeType.SOFTWARE_PROJECT,
            runtime_reference=project.id,
        )
        self.db.add(forged)
        await self.db.flush()
        with self.assertRaises(LookupError):
            await self.service.resolve(self.db, self.b.id, forged.id)


if __name__ == "__main__":
    unittest.main()
