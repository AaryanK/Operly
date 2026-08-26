import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.channels.identity import IdentityService
from packages.context.broker import ContextBroker
from packages.database.company_models import BusinessEventRecord
from packages.database.db import Base
from packages.database.models import AppUser, Message, Tenant, TenantMember
from packages.database.principal_models import Principal, PrincipalConversation, PrincipalMessage
from packages.database.schema import import_all_models
from packages.security.execution_context import PERSONAL_EXECUTION_PERMISSIONS
from packages.security.human_identity import HumanIdentityService
from packages.security.surfaces import SurfaceKind
from packages.security.workspace_invitations import WorkspaceInvitationError, WorkspaceInvitationService


class HumanIdentityInviteHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_workspace_invite_accepts_new_human_once(self):
        async with self.sessions() as db:
            owner = AppUser(email="owner@example.test", display_name="Owner", active=True)
            invitee = AppUser(email="raju@example.test", display_name="Raju", active=True)
            workspace = Tenant(name="Research")
            db.add_all([owner, invitee, workspace])
            await db.flush()
            db.add(TenantMember(tenant_id=workspace.id, user_id=owner.id, role="owner"))
            invite, token = await WorkspaceInvitationService.create(
                db,
                tenant_id=workspace.id,
                role="employee",
                invited_by_user_id=owner.id,
                target_email=invitee.email,
            )
            await db.commit()
            invite_id = invite.id
            invitee_id = invitee.id
            workspace_id = workspace.id

        async with self.sessions() as db:
            info = await WorkspaceInvitationService.inspect(db, token=token)
            self.assertIsNotNone(info)
            self.assertEqual(info.workspace_id, workspace_id)
            membership = await WorkspaceInvitationService.accept(
                db,
                token=token,
                user_id=invitee_id,
            )
            await db.commit()
            self.assertEqual(membership.role, "employee")
            self.assertEqual(membership.tenant_id, workspace_id)

        async with self.sessions() as db:
            row = await db.get(type(invite), invite_id)
            self.assertEqual(row.status, "accepted")
            membership = await db.scalar(
                select(TenantMember).where(
                    TenantMember.tenant_id == workspace_id,
                    TenantMember.user_id == invitee_id,
                )
            )
            self.assertIsNotNone(membership)
            with self.assertRaises(WorkspaceInvitationError):
                await WorkspaceInvitationService.accept(db, token=token, user_id=invitee_id)

    async def test_targeted_workspace_invite_cannot_be_claimed_by_other_email(self):
        async with self.sessions() as db:
            owner = AppUser(email="owner@example.test", display_name="Owner", active=True)
            wrong = AppUser(email="wrong@example.test", display_name="Wrong", active=True)
            workspace = Tenant(name="Research")
            db.add_all([owner, wrong, workspace])
            await db.flush()
            _, token = await WorkspaceInvitationService.create(
                db,
                tenant_id=workspace.id,
                role="employee",
                invited_by_user_id=owner.id,
                target_email="expected@example.test",
            )
            await db.commit()
            wrong_id = wrong.id

        async with self.sessions() as db:
            with self.assertRaises(WorkspaceInvitationError):
                await WorkspaceInvitationService.accept(db, token=token, user_id=wrong_id)

    async def test_provider_identity_and_runtime_binding_resolve_to_same_human(self):
        async with self.sessions() as db:
            user = AppUser(email="human@example.test", display_name="Human", active=True)
            db.add(user)
            await db.flush()
            user_id = user.id
            await IdentityService.link_external_identity(
                db,
                user_id=user_id,
                provider="discord",
                external_user_id="discord-42",
                display_name="Human on Discord",
                metadata={"guild": "research"},
            )
            await db.commit()

        async with self.sessions() as db:
            snapshot = await HumanIdentityService.snapshot(db, user_id=user_id)
            payload = snapshot.as_dict()
            self.assertEqual(payload["human"]["user_id"], user_id)
            self.assertTrue(
                any(
                    row["provider"] == "discord"
                    and row["provider_subject"] == "discord-42"
                    for row in payload["external_identities"]
                )
            )
            bindings = [
                binding
                for principal in payload["principals"]
                for binding in principal["external_bindings"]
            ]
            self.assertTrue(
                any(
                    row["provider"] == "discord"
                    and row["provider_subject"] == "discord-42"
                    and row["verified"] is True
                    for row in bindings
                )
            )

    async def test_personal_history_federates_only_currently_authorized_workspaces(self):
        async with self.sessions() as db:
            user = AppUser(email="human@example.test", display_name="Human", active=True)
            allowed = Tenant(name="Allowed")
            denied = Tenant(name="Denied")
            db.add_all([user, allowed, denied])
            await db.flush()
            user_id = user.id
            allowed_id = allowed.id
            denied_id = denied.id
            db.add(TenantMember(tenant_id=allowed_id, user_id=user_id, role="owner"))

            principal = Principal(
                kind="human",
                user_id=user_id,
                display_name="Human",
                status="active",
            )
            db.add(principal)
            await db.flush()
            conversation = PrincipalConversation(
                principal_id=principal.id,
                provider="operly_web",
                external_conversation_id="personal-1",
                title="Personal",
                status="active",
            )
            db.add(conversation)
            await db.flush()
            db.add(
                PrincipalMessage(
                    conversation_id=conversation.id,
                    role="user",
                    content="Personal falcon project note",
                )
            )
            db.add_all(
                [
                    Message(
                        tenant_id=allowed_id,
                        channel_id=100,
                        message_id=101,
                        author_id=1,
                        author_name="Allowed User",
                        content="Authorized falcon workspace discussion",
                    ),
                    Message(
                        tenant_id=denied_id,
                        channel_id=200,
                        message_id=201,
                        author_id=2,
                        author_name="Denied User",
                        content="ULTRASECRET denied workspace content",
                    ),
                ]
            )
            db.add_all(
                [
                    BusinessEventRecord(
                        scope_kind="workspace",
                        tenant_id=allowed_id,
                        owner_user_id=None,
                        event_type="project.updated",
                        source="operly",
                        payload_json='{"project":"falcon"}',
                        metadata_json="{}",
                    ),
                    BusinessEventRecord(
                        scope_kind="personal",
                        tenant_id=None,
                        owner_user_id=user_id,
                        event_type="personal.reminder",
                        source="operly",
                        payload_json='{"topic":"falcon"}',
                        metadata_json="{}",
                    ),
                ]
            )
            await db.commit()

        async with self.sessions() as db:
            refs = await ContextBroker.search(
                db,
                tenant_id=f"personal:{user_id}",
                user_id=user_id,
                conversation_id=None,
                authority=set(PERSONAL_EXECUTION_PERMISSIONS),
                surface=SurfaceKind.PERSONAL_PRIVATE,
                query="falcon",
                limit=20,
            )
            sources = {ref.source for ref in refs}
            self.assertIn("personal_conversation", sources)
            self.assertIn("workspace_message", sources)
            self.assertIn("business_event", sources)
            self.assertTrue(
                any(ref.scope == f"workspace:{allowed_id}" for ref in refs)
            )
            self.assertFalse(
                any(ref.scope == f"workspace:{denied_id}" for ref in refs)
            )

            materialized = await ContextBroker.materialize(
                db,
                refs=[ref.id for ref in refs],
                tenant_id=f"personal:{user_id}",
                user_id=user_id,
                conversation_id=None,
                authority=set(PERSONAL_EXECUTION_PERMISSIONS),
                surface=SurfaceKind.PERSONAL_PRIVATE,
            )
            serialized = str(materialized)
            self.assertIn("Authorized falcon workspace discussion", serialized)
            self.assertNotIn("ULTRASECRET", serialized)
