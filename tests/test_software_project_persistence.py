import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.capabilities.contracts import CapabilityDefinition, CapabilityResult
from packages.capabilities.providers import BaseProvider
from packages.capabilities.registry import CapabilityRegistry
from packages.database.db import Base
from packages.database.models import Tenant
from packages.database.schema import import_all_models
from packages.database.software_project_models import ServiceBindingRecord, SoftwareProjectRecord
from packages.database.studio_models import StudioProject
from packages.service_bindings import ServiceBindingStore
from packages.software_projects import SoftwareProjectService


class DemoProvider(BaseProvider):
    name = "demo-project-bindings"
    capabilities = (
        CapabilityDefinition(
            "demo.lookup",
            "demo_lookup",
            "Look up a demo business record",
            {"type": "object", "properties": {}, "additionalProperties": False},
            {"type": "object"},
            risk_level="read_only",
            plugin_id="demo",
            category="business",
            semantic_operations=frozenset({"look up business record"}),
        ),
    )

    async def execute(self, context, capability_name, arguments):
        return CapabilityResult(True, False, {"ok": True})

    async def verify(self, context, capability_name, arguments, result):
        return result


class SoftwareProjectPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.sessions()
        self.tenant = Tenant(name="Universal Studio")
        self.db.add(self.tenant)
        await self.db.flush()
        self.projects = SoftwareProjectService()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def test_create_canonical_project_without_legacy_runtime(self):
        project = await self.projects.create(
            self.db,
            workspace_id=self.tenant.id,
            user_id="owner-a",
            name="Operations Console",
            description="One project identity independent of its runtime.",
        )
        await self.db.commit()

        stored = await self.db.get(SoftwareProjectRecord, project.id)
        self.assertIsNotNone(stored)
        self.assertIsNone(stored.legacy_runtime_type)
        self.assertEqual(project.name, "Operations Console")
        self.assertEqual(project.state.value, "draft")

    async def test_legacy_studio_project_materializes_stable_canonical_identity(self):
        legacy = StudioProject(
            tenant_id=self.tenant.id,
            name="Public Website",
            slug="public-website",
            description="Existing website runtime",
            status="draft",
            created_by="owner-a",
        )
        self.db.add(legacy)
        await self.db.flush()

        first = await self.projects.list(self.db, self.tenant.id)
        await self.db.commit()
        second = await self.projects.list(self.db, self.tenant.id)

        matched = [
            row
            for row in first
            if row.metadata.get("runtime_reference") == legacy.id
        ]
        self.assertEqual(len(matched), 1)
        canonical_id = matched[0].id
        self.assertNotEqual(canonical_id, legacy.id)
        self.assertTrue(
            any(
                row.id == canonical_id
                and row.metadata.get("compatibility_runtime") == "studio"
                for row in second
            )
        )
        stored = await self.db.scalar(
            select(SoftwareProjectRecord).where(
                SoftwareProjectRecord.id == canonical_id
            )
        )
        self.assertEqual(stored.legacy_runtime_type, "studio")
        self.assertEqual(stored.legacy_runtime_reference, legacy.id)

    async def test_service_binding_persists_capability_identity_without_credentials(self):
        project = await self.projects.create(
            self.db,
            workspace_id=self.tenant.id,
            user_id="owner-a",
            name="Internal Tool",
        )
        registry = CapabilityRegistry()
        registry.register(DemoProvider())
        store = ServiceBindingStore(registry)

        with self.assertRaisesRegex(PermissionError, "explicit current authority"):
            await store.create(
                self.db,
                workspace_id=self.tenant.id,
                project_id=project.id,
                user_id="owner-a",
                semantic_name="business.lookup",
                capability_id="demo.lookup",
                configuration={"allowed_argument_fields": ["query"]},
            )

        binding = await store.create(
            self.db,
            workspace_id=self.tenant.id,
            project_id=project.id,
            user_id="owner-a",
            semantic_name="business.lookup",
            capability_id="demo.lookup",
            configuration={"allowed_argument_fields": ["query"]},
            authority=set(),
        )
        await self.db.commit()

        stored = await self.db.get(ServiceBindingRecord, binding.id)
        self.assertEqual(stored.capability_id, "demo.lookup")
        self.assertEqual(stored.project_id, project.id)
        self.assertNotIn("token", stored.configuration_json.lower())
        self.assertNotIn("secret", stored.configuration_json.lower())

        rows = await store.list(
            self.db,
            workspace_id=self.tenant.id,
            project_id=project.id,
        )
        self.assertEqual([row.id for row in rows], [binding.id])


if __name__ == "__main__":
    unittest.main()
