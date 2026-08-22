import json

from packages.coding_harness.context_window import (
    COMPACTION_MARKER,
    SOURCE_WORKING_SET_HEADER,
    _latest_source_observations,
    compact_messages,
)


def _head():
    return [
        {"role": "system", "content": "coding policy"},
        {"role": "user", "content": "approved specification and task"},
    ]


def _read_turn(path: str, content: str):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read", "arguments": {"path": path}}}],
        },
        {
            "role": "tool",
            "tool_name": "read",
            "content": json.dumps(
                {
                    "ok": True,
                    "path": path,
                    "offset": 1,
                    "limit": 400,
                    "totalLines": 20,
                    "content": content,
                    "truncated": False,
                }
            ),
        },
    ]


def _noise_turn(index: int):
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "grep", "arguments": {"query": f"noise-{index}"}}}
            ],
        },
        {
            "role": "tool",
            "tool_name": "grep",
            "content": json.dumps({"ok": True, "matches": ["x" * 2600]}),
        },
    ]


def test_compaction_keeps_latest_source_reads_as_a_durable_working_set():
    messages = _head()
    messages += _read_turn("index.html", "1: <main>hero-source</main>\n2: <script src='app.js'></script>")
    messages += _read_turn("app.js", "1: export function handler(){ return 'handler-source'; }")
    for index in range(7):
        messages += _noise_turn(index)
    messages.append({"role": "assistant", "content": "Ready to make the source edit."})

    compacted = compact_messages(messages, limit_chars=14_000, recent_messages=4)

    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[2]["content"] == COMPACTION_MARKER
    durable = next(
        item for item in compacted if str(item.get("content") or "").startswith(SOURCE_WORKING_SET_HEADER)
    )
    assert durable["role"] == "user"
    assert "index.html" in durable["content"]
    assert "hero-source" in durable["content"]
    assert "app.js" in durable["content"]
    assert "handler-source" in durable["content"]
    assert "noise-0" not in str(compacted)
    assert compacted[-1] == messages[-1]


def test_latest_reread_replaces_an_older_observation_for_the_same_range():
    messages = _head()
    messages += _read_turn("index.html", "1: OLD SOURCE")
    messages += _read_turn("index.html", "1: NEW SOURCE")

    observations = _latest_source_observations(messages)

    assert len(observations) == 1
    assert observations[0]["path"] == "index.html"
    assert observations[0]["content"] == "1: NEW SOURCE"


def test_exact_edit_invalidates_pre_edit_source_observation():
    messages = _head()
    messages += _read_turn("index.html", "1: <h1>Old title</h1>")
    messages += [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit",
                        "arguments": {
                            "path": "index.html",
                            "old": "<h1>Old title</h1>",
                            "new": "<h1>New title</h1>",
                        },
                    }
                }
            ],
        },
        {"role": "tool", "tool_name": "edit", "content": json.dumps({"ok": True, "path": "index.html"})},
    ]

    observations = _latest_source_observations(messages)

    assert observations == []


def test_failed_edit_keeps_last_confirmed_source_observation():
    messages = _head()
    messages += _read_turn("index.html", "1: <h1>Still current</h1>")
    messages += [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "edit",
                        "arguments": {
                            "path": "index.html",
                            "old": "missing text",
                            "new": "replacement",
                        },
                    }
                }
            ],
        },
        {
            "role": "tool",
            "tool_name": "edit",
            "content": json.dumps({"ok": False, "error": "Exact edit requires one match; found 0"}),
        },
    ]

    observations = _latest_source_observations(messages)

    assert len(observations) == 1
    assert observations[0]["path"] == "index.html"
    assert "Still current" in observations[0]["content"]


def test_full_write_becomes_the_new_durable_source_without_an_extra_read():
    messages = _head()
    messages += _read_turn("app.js", "1: old source")
    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "write",
                        "arguments": {"path": "app.js", "content": "export const current = true;\n"},
                    }
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_name": "write", "content": json.dumps({"ok": True, "path": "app.js"})})

    observations = _latest_source_observations(messages)

    assert len(observations) == 1
    assert observations[0]["path"] == "app.js"
    assert observations[0]["source"] == "write"
    assert observations[0]["content"] == "export const current = true;\n"


def test_failed_write_never_poison_durable_source_state():
    messages = _head()
    messages += _read_turn("app.js", "1: export const current = 'old';")
    messages += [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "write",
                        "arguments": {"path": "app.js", "content": "export const current = 'uncommitted';\n"},
                    }
                }
            ],
        },
        {
            "role": "tool",
            "tool_name": "write",
            "content": json.dumps({"ok": False, "error": "Workspace size limit exceeded"}),
        },
    ]

    observations = _latest_source_observations(messages)

    assert len(observations) == 1
    assert observations[0]["source"] == "read"
    assert "old" in observations[0]["content"]
    assert "uncommitted" not in observations[0]["content"]
