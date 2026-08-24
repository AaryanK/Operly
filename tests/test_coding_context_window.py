import asyncio
import json

from packages.coding_harness.context_window import (
    COMPACTION_MARKER,
    ContextBoundCodingClient,
    compact_messages,
    request_char_estimate,
)


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
    compacted = compact_messages(
        messages,
        limit_chars=16_000,
        recent_messages=8,
        output_reserve_chars=4_000,
    )

    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[2]["content"] == COMPACTION_MARKER
    assert compacted[-1] == messages[-1]
    assert len(compacted) < len(messages)
    assert "stale-0" not in str(compacted)
    assert "stale-11" in str(compacted)
    assert compacted[3]["role"] != "tool"


def test_request_estimate_counts_tool_schemas_and_output_reserve():
    messages = [{"role": "user", "content": "hello"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "description": "Read a project file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    estimate = request_char_estimate(messages, tools, output_reserve_chars=1234)

    assert estimate["messageChars"] > 0
    assert estimate["toolSchemaChars"] > 0
    assert estimate["outputReserveChars"] == 1234
    assert estimate["estimatedRequestChars"] == (
        estimate["messageChars"] + estimate["toolSchemaChars"] + 1234
    )


def test_two_message_initial_packet_can_drop_reproducible_machine_contract_bulk():
    specification = {
        "projectName": "Attendance",
        "objective": "Employees clock in and out",
        "requirements": [{"id": "R-1", "requirement": "Preserve owner intent"}],
        "operlyExecutionContract": {
            "contractAuthority": "canonical validators are authoritative",
            "machineContracts": {"hugeSchema": "x" * 20_000},
        },
    }
    packet = {
        "approvedSpecification": json.dumps(specification),
        "task": "Build the application",
        "workspaceFiles": [],
    }
    messages = [
        {"role": "system", "content": "coding policy"},
        {"role": "user", "content": json.dumps(packet)},
    ]

    compacted = compact_messages(
        messages,
        limit_chars=12_000,
        output_reserve_chars=4_000,
    )

    assert len(compacted) == 2
    decoded_packet = json.loads(compacted[1]["content"])
    decoded_spec = json.loads(decoded_packet["approvedSpecification"])
    assert decoded_spec["objective"] == "Employees clock in and out"
    assert decoded_spec["requirements"][0]["id"] == "R-1"
    machine = decoded_spec["operlyExecutionContract"]["machineContracts"]
    assert machine["compacted"] is True
    assert "hugeSchema" not in machine


class CapturingClient:
    def __init__(self):
        self.messages = None
        self.tools = None

    async def chat(self, messages, tools=None):
        self.messages = messages
        self.tools = tools
        return {"role": "assistant", "content": "ok"}


def test_context_bound_client_compacts_before_provider_call(monkeypatch):
    monkeypatch.setenv("OPERLY_CODING_CONTEXT_CHARS", "16000")
    monkeypatch.setenv("OPERLY_CODING_OUTPUT_RESERVE_CHARS", "4000")
    monkeypatch.setenv("OPERLY_CODING_CONTEXT_RECENT_MESSAGES", "8")
    inner = CapturingClient()
    client = ContextBoundCodingClient(inner)

    asyncio.run(client.chat(_messages(), []))

    assert inner.messages is not None
    assert any(message.get("content") == COMPACTION_MARKER for message in inner.messages)
    assert "stale-0" not in str(inner.messages)
