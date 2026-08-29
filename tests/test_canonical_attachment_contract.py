from dataclasses import fields
from pathlib import Path

from packages.business_brain.types import AgentInput
from packages.channels.envelope import ChannelEnvelope


def test_raw_image_transport_contract_is_removed():
    assert "images" not in {item.name for item in fields(ChannelEnvelope)}
    assert "images" not in {item.name for item in fields(AgentInput)}


def test_workspace_channel_handoff_uses_artifact_context_only():
    root = Path(__file__).resolve().parents[1]
    channel_source = (root / "packages/channels/service.py").read_text(encoding="utf-8")
    agent_source = (root / "packages/business_brain/agent.py").read_text(encoding="utf-8")

    assert "envelope.images" not in channel_source
    assert "request.images" not in agent_source
    assert "attachment_context=attachment_prompt" in channel_source
