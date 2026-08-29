from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_discord_runtime_has_no_legacy_pipeline():
    source = _source("packages/connectors/discord/secure_runtime.py")
    assert "process_discord_attachments" not in source
    assert "schedule_new_pending_jobs" not in source
    assert "bot_shared as legacy" not in source
    assert "artifact_delivery" not in source
    assert "DiscordGuild" not in source
    assert "collect_discord_attachments" in source
    assert "ingest_channel_attachments" in source


@pytest.mark.parametrize(
    "path",
    [
        "packages/connectors/discord/bot_shared.py",
        "packages/connectors/discord/artifact_delivery.py",
        "packages/connectors/discord/bot.py",
        "packages/connectors/discord/bot_harness.py",
        "packages/connectors/discord/scheduled_tasks.py",
    ],
)
def test_legacy_discord_files_are_deleted(path: str):
    assert not (ROOT / path).exists()


def test_workspace_agent_is_runtime_v2_only():
    source = _source("packages/business_brain/agent.py")
    assert "AgentRunController" not in source
    assert "model_for_role" not in source
    assert "load_conversation_messages" not in source
    assert "run_workspace_runtime_v2" in source
