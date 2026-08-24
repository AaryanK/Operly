from __future__ import annotations

import copy

import pytest

from packages.agents.runtime import AgentRuntime
from packages.model_runtime.contracts import InferenceResult, ModelTraits
from packages.model_runtime.ollama_client import _ollama_messages
from packages.model_runtime.openai_compatible_client import (
    _GEMINI_IMPORTED_THOUGHT_SIGNATURE,
    _compatible_messages,
)


def _tool_call(name: str, arguments, *, call_id: str | None = "call-1", extra_content=None):
    call = {
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
    if call_id is not None:
        call["id"] = call_id
    if extra_content is not None:
        call["extra_content"] = extra_content
    return call


def test_gemini_preserves_native_thought_signature_without_mutating_history():
    messages = [
        {"role": "user", "content": "Read the page."},
        {
            "role": "assistant",
            "tool_calls": [
                _tool_call(
                    "web.read_url",
                    {"url": "https://example.com"},
                    extra_content={
                        "google": {"thought_signature": "native-signature"}
                    },
                )
            ],
        },
        {"role": "tool", "tool_name": "web.read_url", "content": "{}"},
    ]
    original = copy.deepcopy(messages)

    encoded = _compatible_messages(messages, provider="gemini")

    call = encoded[1]["tool_calls"][0]
    assert call["extra_content"]["google"]["thought_signature"] == "native-signature"
    assert call["function"]["arguments"] == '{"url":"https://example.com"}'
    assert messages == original


def test_gemini_supplies_transfer_signature_for_foreign_tool_history():
    messages = [
        {"role": "user", "content": "Read the page."},
        {
            "role": "assistant",
            "tool_calls": [
                _tool_call("web.read_url", '{"url":"https://example.com"}')
            ],
        },
        {"role": "tool", "tool_name": "web.read_url", "content": "{}"},
    ]

    encoded = _compatible_messages(messages, provider="gemini")

    assert (
        encoded[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        == _GEMINI_IMPORTED_THOUGHT_SIGNATURE
    )


def test_gemini_parallel_transfer_marks_only_first_function_call():
    messages = [
        {"role": "user", "content": "Read both."},
        {
            "role": "assistant",
            "tool_calls": [
                _tool_call("web.read_url", {"url": "https://a.example"}, call_id="a"),
                _tool_call("web.read_url", {"url": "https://b.example"}, call_id="b"),
            ],
        },
    ]

    encoded = _compatible_messages(messages, provider="gemini")
    calls = encoded[1]["tool_calls"]

    assert calls[0]["extra_content"]["google"]["thought_signature"] == _GEMINI_IMPORTED_THOUGHT_SIGNATURE
    assert "extra_content" not in calls[1]


def test_non_gemini_openai_compatible_history_does_not_gain_google_metadata():
    messages = [
        {"role": "user", "content": "Read it."},
        {
            "role": "assistant",
            "tool_calls": [_tool_call("web.read_url", {"url": "https://example.com"})],
        },
    ]

    encoded = _compatible_messages(messages, provider="groq")

    assert "extra_content" not in encoded[1]["tool_calls"][0]


def test_ollama_encodes_foreign_tool_history_into_native_shape():
    messages = [
        {"role": "user", "content": "Read it."},
        {
            "role": "assistant",
            "content": None,
            "_operly_task_route": {"role": "business_agent"},
            "tool_calls": [
                _tool_call(
                    "web.read_url",
                    '{"url":"https://example.com"}',
                    extra_content={
                        "google": {"thought_signature": "gemini-only"}
                    },
                )
            ],
        },
        {
            "role": "tool",
            "tool_name": "web.read_url",
            "tool_call_id": "call-1",
            "content": '{"ok":true}',
        },
    ]
    original = copy.deepcopy(messages)

    encoded = _ollama_messages(messages)

    assert encoded[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "type": "function",
                "function": {
                    "name": "web.read_url",
                    "arguments": {"url": "https://example.com"},
                },
            }
        ],
    }
    assert encoded[2] == {
        "role": "tool",
        "content": '{"ok":true}',
        "tool_name": "web.read_url",
    }
    assert messages == original


def test_ollama_replays_malformed_foreign_arguments_as_effective_empty_object():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [_tool_call("web.read_url", '{"url":')],
        }
    ]

    encoded = _ollama_messages(messages)

    assert encoded[0]["tool_calls"][0]["function"]["arguments"] == {}


class _IdlessThenDoneModel:
    id = "idless-test-model"
    provider = "ollama"
    tags = frozenset({"test"})
    capabilities = frozenset({"text", "tools"})
    traits = ModelTraits()

    def __init__(self):
        self.calls = 0
        self.second_request = None

    async def infer(self, request):
        self.calls += 1
        if self.calls == 1:
            return InferenceResult(
                message={
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "web.read_url",
                            {"url": "https://example.com"},
                            call_id=None,
                        )
                    ],
                },
                model_resource_id=self.id,
                provider=self.provider,
                provider_model_id=self.id,
                latency_ms=1,
            )
        self.second_request = request
        return InferenceResult(
            message={"role": "assistant", "content": "done"},
            model_resource_id=self.id,
            provider=self.provider,
            provider_model_id=self.id,
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_agent_runtime_correlates_idless_provider_tool_calls_for_future_adapters():
    model = _IdlessThenDoneModel()
    runtime = AgentRuntime(max_steps=2)
    seen_call_ids = []

    async def schemas():
        return [
            {
                "type": "function",
                "function": {
                    "name": "web.read_url",
                    "parameters": {"type": "object"},
                },
            }
        ]

    async def invoke(name, arguments, call_id):
        assert name == "web.read_url"
        assert arguments == {"url": "https://example.com"}
        seen_call_ids.append(call_id)
        return {"ok": True}

    result = await runtime.run(
        model=model,
        messages=[{"role": "user", "content": "Read the page and summarize it."}],
        schemas=schemas,
        invoke=invoke,
    )

    assert result["message"] == "done"
    assert len(seen_call_ids) == 1
    assert seen_call_ids[0].startswith("operly-call-")
    assert model.second_request is not None
    history = list(model.second_request.messages)
    assistant_call = history[1]["tool_calls"][0]
    tool_result = history[2]
    assert assistant_call["id"] == seen_call_ids[0]
    assert tool_result["tool_call_id"] == seen_call_ids[0]

    # The same canonical history can now be transferred into Gemini safely: it has
    # a correlation id and the Gemini adapter supplies only its own opaque signature.
    gemini_history = _compatible_messages(history, provider="gemini")
    assert gemini_history[1]["tool_calls"][0]["id"] == seen_call_ids[0]
    assert gemini_history[2]["tool_call_id"] == seen_call_ids[0]
    assert (
        gemini_history[1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"]
        == _GEMINI_IMPORTED_THOUGHT_SIGNATURE
    )
