import asyncio

from packages.coding_harness.context_window import COMPACTION_MARKER, ContextBoundCodingClient, compact_messages


def _messages():
    rows = [
        {"role": "system", "content": "coding policy"},
        {"role": "user", "content": "approved specification and task"},
    ]
    for index in range(12):
        rows.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read", "arguments": {"path": f"file-{index}.py"}}}]})
        rows.append({"role": "tool", "tool_name": "read", "content": (f"stale-{index}-" + "x" * 3000)})
    rows.append({"role": "assistant", "content": "I will edit the current implementation."})
    return rows


def test_compaction_preserves_authority_and_recent_turns_but_drops_stale_observations():
    messages = _messages()
    compacted = compact_messages(messages, limit_chars=16_000, recent_messages=8)

    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[2]["content"] == COMPACTION_MARKER
    assert compacted[-1] == messages[-1]
    assert len(compacted) < len(messages)
    assert "stale-0" not in str(compacted)
    assert "stale-11" in str(compacted)
    assert compacted[3]["role"] != "tool"


class CapturingClient:
    def __init__(self):
        self.messages = None

    async def chat(self, messages, tools=None):
        self.messages = messages
        return {"role": "assistant", "content": "ok"}


def test_context_bound_client_compacts_before_provider_call(monkeypatch):
    monkeypatch.setenv("OPERLY_CODING_CONTEXT_CHARS", "16000")
    monkeypatch.setenv("OPERLY_CODING_CONTEXT_RECENT_MESSAGES", "8")
    inner = CapturingClient()
    client = ContextBoundCodingClient(inner)

    asyncio.run(client.chat(_messages(), []))

    assert inner.messages is not None
    assert any(message.get("content") == COMPACTION_MARKER for message in inner.messages)
    assert "stale-0" not in str(inner.messages)
