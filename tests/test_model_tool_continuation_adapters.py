from __future__ import annotations

import copy

from packages.model_runtime.ollama_client import _ollama_messages
from packages.model_runtime.openai_compatible_client import (
    _GEMINI_IMPORTED_THOUGHT_SIGNATURE,
    _compatible_messages,
)


def _tool_call(name: str, arguments, *, call_id: str = "call-1", extra_content=None):
    call = {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
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
