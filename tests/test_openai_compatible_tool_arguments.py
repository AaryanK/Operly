import copy
import json

from packages.model_runtime.openai_compatible_client import _compatible_messages


def test_replayed_parsed_tool_arguments_are_json_strings_without_mutation():
    messages = [
        {"role": "user", "content": "Find the account"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "account.list_workspaces",
                        "arguments": {"z": 2, "a": [1, True]},
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_name": "account.list_workspaces",
            "content": '{"workspaces":[]}',
        },
        {"role": "user", "content": "continue"},
    ]
    original = copy.deepcopy(messages)

    wire = _compatible_messages(messages)

    arguments = wire[1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(arguments, str)
    assert arguments == '{"a":[1,true],"z":2}'
    assert json.loads(arguments) == {"a": [1, True], "z": 2}
    assert wire[2]["tool_call_id"] == "call-1"
    assert messages == original


def test_existing_string_tool_arguments_are_preserved_exactly():
    raw = '{ "query": "ANHITRA" }'
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "workspace.search", "arguments": raw},
                }
            ],
        }
    ]

    wire = _compatible_messages(messages)

    assert wire[0]["tool_calls"][0]["function"]["arguments"] == raw


def test_legacy_function_call_arguments_are_normalized_for_compatible_wire():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "function_call": {
                "name": "legacy.lookup",
                "arguments": {"id": 7},
            },
        }
    ]

    wire = _compatible_messages(messages)

    assert wire[0]["function_call"]["name"] == "legacy.lookup"
    assert wire[0]["function_call"]["arguments"] == '{"id":7}'


def test_non_json_serializable_arguments_are_still_wire_safe_strings():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-3",
                    "type": "function",
                    "function": {"name": "legacy.bad", "arguments": {1, 2}},
                }
            ],
        }
    ]

    wire = _compatible_messages(messages)
    arguments = wire[0]["tool_calls"][0]["function"]["arguments"]

    assert isinstance(arguments, str)
    assert isinstance(json.loads(arguments), str)
