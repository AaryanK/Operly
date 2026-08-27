import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from packages.artifacts.service import ArtifactScope
from packages.business_brain.conversation_artifacts import artifact_context, recent_artifacts
from packages.channels.attachment_ingress import ingest_channel_attachments
from packages.channels.envelope import ChannelAttachment, ChannelEnvelope
from packages.database.db import Base
from packages.database.schema import import_all_models


ROOT = Path(__file__).resolve().parents[1]


class DiscordRuntimeArchitectureTests(unittest.TestCase):
    def test_live_runtime_has_no_legacy_membership_attachment_gate(self):
        source = (ROOT / "packages/connectors/discord/secure_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("authorized workspace member before processing business files", source)
        self.assertNotIn("process_discord_attachments", source)
        self.assertNotIn("schedule_new_pending_jobs", source)
        self.assertNotIn("bot_shared as legacy", source)
        self.assertIn("collect_discord_attachments", source)
        self.assertIn("ingest_channel_attachments", source)

    def test_legacy_bot_module_is_only_a_compatibility_export(self):
        source = (ROOT / "packages/connectors/discord/bot_shared.py").read_text(encoding="utf-8")
        self.assertNotIn("AttachmentIngestionPlugin", source)
        self.assertNotIn("AsyncIOScheduler", source)
        self.assertNotIn("def process_discord_attachments", source)
        self.assertIn("canonical Discord connector", source)

    def test_duplicate_artifact_delivery_is_retired(self):
        source = (ROOT / "packages/connectors/discord/artifact_delivery.py").read_text(encoding="utf-8")
        self.assertIn("from packages.connectors.discord.transport import send_discord_response", source)
        self.assertNotIn("ArtifactService", source)


class WorkspaceAttachmentIngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import_all_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_workspace_ingress_is_retained_for_channel_service_context(self):
        async with self.sessions() as db:
            scope = ArtifactScope("workspace", "workspace-1", tenant_id="workspace-1")
            envelope = ChannelEnvelope(
                provider="discord",
                external_user_id="123",
                external_space_id="guild-1",
                external_conversation_id="channel-1",
                actor_name="Guest",
                text="convert these to pdf",
                is_direct=False,
                metadata={"external_message_id": "message-1", "has_attachments": True},
                attachments=[
                    ChannelAttachment(
                        filename="page.png",
                        content_type="image/png",
                        size_bytes=3,
                        content_bytes=b"png",
                    )
                ],
            )
            prompt, names = await ingest_channel_attachments(
                db,
                envelope=envelope,
                scope=scope,
                created_by=None,
            )
            await db.commit()
            rows = await recent_artifacts(
                db,
                tenant_id="workspace-1",
                user_id=None,
                actor_external_id="123",
                channel="discord",
                conversation_id="channel-1",
                is_direct=False,
            )
            retained, retained_names = artifact_context(rows)

        self.assertEqual(names, ["page.png"])
        self.assertIn("files.process", prompt)
        self.assertEqual(len(rows), 1)
        self.assertIn("artifact_id=", retained)
        self.assertIn("page.png", retained_names)


if __name__ == "__main__":
    unittest.main()
