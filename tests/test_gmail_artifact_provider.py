from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.capabilities.contracts import CapabilityResult
import packages.capabilities.gmail_artifact_provider as gmail_artifacts


def _provider_draft(*, filenames: list[str]) -> dict:
    return {
        "id": "draft-1",
        "message": {
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "filename": filename,
                        "body": {"attachmentId": f"att-{index}"},
                    }
                    for index, filename in enumerate(filenames, 1)
                ],
            }
        },
    }


@pytest.mark.asyncio
async def test_gmail_artifact_verification_rejects_missing_provider_attachment(monkeypatch):
    provider = gmail_artifacts.GmailArtifactProvider()
    context = SimpleNamespace(db=object())
    result = CapabilityResult(
        True,
        True,
        {
            "draft_id": "draft-1",
            "attachment_artifact_ids": ["artifact-1"],
            "attachments": [{"artifact_id": "artifact-1", "filename": "briefing.pdf"}],
            "attachment_count": 1,
        },
        "draft-1",
    )
    monkeypatch.setattr(gmail_artifacts, "google_connector_for_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(gmail_artifacts, "google_access_token_for_context", AsyncMock(return_value="token"))
    monkeypatch.setattr(gmail_artifacts, "request_json", AsyncMock(return_value=_provider_draft(filenames=[])))

    verified = await provider.verify(
        context,
        "gmail.create_draft_with_artifacts",
        {"to": ["me@example.com"], "subject": "Briefing", "artifact_ids": ["artifact-1"]},
        result,
    )
    assert verified.success is False
    assert verified.evidence["attachments_persisted_by_provider"] is False
    assert verified.evidence["attachment_count"] == 0


@pytest.mark.asyncio
async def test_gmail_artifact_verification_requires_matching_provider_filename(monkeypatch):
    provider = gmail_artifacts.GmailArtifactProvider()
    context = SimpleNamespace(db=object())
    result = CapabilityResult(
        True,
        True,
        {
            "draft_id": "draft-1",
            "attachment_artifact_ids": ["artifact-1"],
            "attachments": [{"artifact_id": "artifact-1", "filename": "briefing.pdf"}],
            "attachment_count": 1,
        },
        "draft-1",
    )
    monkeypatch.setattr(gmail_artifacts, "google_connector_for_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(gmail_artifacts, "google_access_token_for_context", AsyncMock(return_value="token"))
    monkeypatch.setattr(
        gmail_artifacts,
        "request_json",
        AsyncMock(return_value=_provider_draft(filenames=["briefing.pdf"])),
    )

    verified = await provider.verify(
        context,
        "gmail.create_draft_with_artifacts",
        {"to": ["me@example.com"], "subject": "Briefing", "artifact_ids": ["artifact-1"]},
        result,
    )
    assert verified.success is True
    assert verified.evidence["attachments_persisted_by_provider"] is True
    assert verified.evidence["attachment_filenames"] == ["briefing.pdf"]


def test_personal_google_catalog_exposes_artifact_backed_draft():
    from packages.capabilities.personal_google_provider import PersonalGoogleCapabilityProvider

    ids = {definition.id for definition in PersonalGoogleCapabilityProvider.capabilities}
    assert "gmail.create_draft_with_artifacts" in ids
