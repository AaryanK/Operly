import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.database.db import Base
from packages.database.models import AppUser, Tenant, TenantMember
from packages.database.schema import import_all_models
from packages.security.scope_resolver import (
    ResolvedScopeKind,
    authorized_scopes,
    resolve_authorized_scope,
)


class AccountScopeResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            user = AppUser(email="scope@example.test", display_name="Scope User", active=True)
            other_user = AppUser(email="other-scope@example.test", display_name="Other", active=True)
            anhitra = Tenant(name="ANHITRA", slug="anhitra")
            nayschool = Tenant(name="NaySchool", slug="nayschool")
            hidden = Tenant(name="Hidden Workspace", slug="hidden")
            db.add_all([user, other_user, anhitra, nayschool, hidden])
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=anhitra.id, user_id=user.id, role="owner"),
                    TenantMember(tenant_id=nayschool.id, user_id=user.id, role="employee"),
                    TenantMember(tenant_id=hidden.id, user_id=other_user.id, role="owner"),
                ]
            )
            await db.commit()
            self.user_id = user.id
            self.anhitra_id = anhitra.id
            self.nayschool_id = nayschool.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_inventory_contains_personal_and_only_authorized_workspaces(self):
        async with self.sessions() as db:
            scopes = await authorized_scopes(db, user_id=self.user_id)

        self.assertEqual(scopes[0].kind, ResolvedScopeKind.PERSONAL)
        self.assertEqual(scopes[0].id, f"personal:{self.user_id}")
        self.assertEqual({item.name for item in scopes[1:]}, {"ANHITRA", "NaySchool"})
        self.assertNotIn("Hidden Workspace", {item.name for item in scopes})

    async def test_explicit_personal_reference_wins_over_workspace_focus(self):
        async with self.sessions() as db:
            resolution = await resolve_authorized_scope(
                db,
                user_id=self.user_id,
                reference="my personal",
                focus_workspace_id=self.anhitra_id,
            )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.resolved.kind, ResolvedScopeKind.PERSONAL)
        self.assertEqual(resolution.resolved.id, f"personal:{self.user_id}")

    async def test_explicit_workspace_name_resolves_regardless_of_other_focus(self):
        async with self.sessions() as db:
            resolution = await resolve_authorized_scope(
                db,
                user_id=self.user_id,
                reference="ANHITRA",
                focus_workspace_id=self.nayschool_id,
            )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.resolved.kind, ResolvedScopeKind.WORKSPACE)
        self.assertEqual(resolution.resolved.id, self.anhitra_id)

    async def test_generic_workspace_reference_uses_focus_only_as_hint(self):
        async with self.sessions() as db:
            resolution = await resolve_authorized_scope(
                db,
                user_id=self.user_id,
                reference="my workspace",
                focus_workspace_id=self.nayschool_id,
            )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.resolved.id, self.nayschool_id)

    async def test_generic_workspace_without_focus_is_ambiguous(self):
        async with self.sessions() as db:
            resolution = await resolve_authorized_scope(
                db,
                user_id=self.user_id,
                reference="my workspace",
            )

        self.assertEqual(resolution.status, "ambiguous")
        self.assertEqual({item.id for item in resolution.matches}, {self.anhitra_id, self.nayschool_id})


if __name__ == "__main__":
    unittest.main()
