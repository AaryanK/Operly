from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.artifacts.delivery import _strip_model_artifact_links
from packages.channels.envelope import ChannelResponse
from packages.connectors.discord.artifact_delivery import (
    response_artifact_scope,
    send_discord_response,
)


class FakeSentMessage:
    pass


class FakeChannel:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.files = []

    async def send(self, content=None, *, file=None, allowed_mentions=None):
        del allowed_mentions
        if content is not None:
            self.messages.append(str(content))
        if file is not None:
            self.files.append(file)
        return FakeSentMessage()


class FakeMessage:
    def __init__(self, *, upload_limit: int = 8 * 1024 * 1024) -> None:
        self.guild = SimpleNamespace(filesize_limit=upload_limit)
        self.channel = FakeChannel()
        self.replies: list[str] = []

    async def reply(self, content, **kwargs):
        del kwargs
        self.replies.append(str(content))
        return FakeSentMessage()


class FakeArtifactService:
    def __init__(self, db) -> None:
        del db

    async def get(self, scope, artifact_id):
        assert scope.kind == "workspace"
        assert scope.tenant_id == "tenant-a"
        assert artifact_id == "artifact-1"
        return SimpleNamespace(filename="contacts.xlsx", size_bytes=8)

    async def read_bytes(self, scope, artifact_id):
        assert scope.kind == "workspace"
        assert artifact_id == "artifact-1"
        return b"PK\x03\x04xlsx"


@asynccontextmanager
async def fake_session_scope():
    yield object()


def test_model_artifact_uuid_is_not_treated_as_a_navigation_target():
    message = "I exported it here: [contacts.xlsx](artifact-1)"
    cleaned = _strip_model_artifact_links(
        message,
        [{"artifact_id": "artifact-1", "filename": "contacts.xlsx"}],
    )
    assert cleaned == "I exported it here: contacts.xlsx"


def test_channel_response_keeps_base_message_for_rich_adapter():
    response = ChannelResponse(
        message="Created the spreadsheet.",
        tenant_id="tenant-a",
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "filename": "contacts.xlsx",
                "download_url": "https://operly.test/api/artifacts/artifact-1/download",
            }
        ],
    )

    assert response.base_message == "Created the spreadsheet."
    assert "Files:" in response.message
    assert response_artifact_scope(response).tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_discord_sends_verified_artifact_as_native_file_without_duplicate_link_text():
    response = ChannelResponse(
        message="Created the spreadsheet.",
        tenant_id="tenant-a",
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "filename": "contacts.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "size_bytes": 8,
                "download_url": "https://operly.test/api/artifacts/artifact-1/download",
            }
        ],
    )
    message = FakeMessage()

    with patch(
        "packages.connectors.discord.artifact_delivery.ArtifactService",
        FakeArtifactService,
    ), patch(
        "packages.connectors.discord.artifact_delivery.session_scope",
        fake_session_scope,
    ):
        await send_discord_response(message, response)

    assert message.replies == ["Created the spreadsheet."]
    assert len(message.channel.files) == 1
    assert message.channel.files[0].filename == "contacts.xlsx"
    assert not any("operly.test" in text for text in message.channel.messages)


@pytest.mark.asyncio
async def test_discord_uses_canonical_link_only_when_native_upload_is_too_large():
    response = ChannelResponse(
        message="Created the spreadsheet.",
        tenant_id="tenant-a",
        artifacts=[
            {
                "artifact_id": "artifact-1",
                "filename": "contacts.xlsx",
                "size_bytes": 20,
                "download_url": "https://operly.test/api/artifacts/artifact-1/download",
            }
        ],
    )
    message = FakeMessage(upload_limit=10)

    await send_discord_response(message, response)

    assert message.replies == ["Created the spreadsheet."]
    assert message.channel.files == []
    assert any("https://operly.test/api/artifacts/artifact-1/download" in text for text in message.channel.messages)
