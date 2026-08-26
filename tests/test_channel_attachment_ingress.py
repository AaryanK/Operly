import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.artifacts.service import ArtifactScope, ArtifactService
from packages.channels.attachment_ingress import ingest_channel_attachments
from packages.channels.envelope import ChannelAttachment, ChannelEnvelope
from packages.database.db import Base
from packages.database.models import AppUser
from packages.database.schema import import_all_models


class ChannelAttachmentIngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_discord_dm_attachment_becomes_personal_artifact(self):
        async with self.sessions() as db:
            user = AppUser(email="person@example.test", display_name="Person", active=True)
            db.add(user)
            await db.flush()
            scope = ArtifactScope("personal", f"personal:{user.id}", owner_user_id=user.id)
            envelope = ChannelEnvelope(
                provider="discord",
                external_user_id="123",
                external_conversation_id="456",
                actor_name="Person",
                text="summarize this",
                is_direct=True,
                attachments=[
                    ChannelAttachment(
                        filename="notes.txt",
                        content_type="text/plain",
                        size_bytes=5,
                        content_bytes=b"hello",
                    )
                ],
            )
            prompt, names = await ingest_channel_attachments(
                db,
                envelope=envelope,
                scope=scope,
                created_by=user.id,
            )
            await db.commit()

            rows = await ArtifactService(db).list(scope, limit=10)

        self.assertEqual(names, ["notes.txt"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].scope_kind, "personal")
        self.assertEqual(rows[0].owner_user_id, user.id)
        self.assertIsNone(rows[0].tenant_id)
        self.assertIn(rows[0].id, prompt)
        self.assertIn("files.process", prompt)

    async def test_url_only_attachment_is_not_fetched_by_core_ingress(self):
        async with self.sessions() as db:
            user = AppUser(email="person2@example.test", display_name="Person", active=True)
            db.add(user)
            await db.flush()
            scope = ArtifactScope("personal", f"personal:{user.id}", owner_user_id=user.id)
            envelope = ChannelEnvelope(
                provider="discord",
                external_user_id="123",
                external_conversation_id="456",
                actor_name="Person",
                text="read this",
                is_direct=True,
                attachments=[
                    ChannelAttachment(
                        filename="remote.txt",
                        content_type="text/plain",
                        url="https://example.invalid/private",
                        content_bytes=None,
                    )
                ],
            )
            prompt, names = await ingest_channel_attachments(
                db,
                envelope=envelope,
                scope=scope,
                created_by=user.id,
            )
            rows = await ArtifactService(db).list(scope, limit=10)

        self.assertEqual(prompt, "")
        self.assertEqual(names, [])
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
