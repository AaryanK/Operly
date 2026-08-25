import asyncio

from packages.software_projects.coding.opencode_agent import CodingSession, VirtualWorkspace
from packages.software_projects.source_bundle import SourceFile
from packages.studio.agent_runs import (
    VisibleToolRegistry,
    _capability_prompt,
    _relevant_studio_capabilities,
    _source_working_set,
    _studio_budget,
)
from packages.studio.runtime_policy import (
    StudioWebsiteCodingAgent,
    StudioWebsiteToolRegistry,
    _studio_budget as _website_studio_budget,
    apply_studio_runtime_policy,
)


def _capability(identifier: str, name: str, description: str, category: str = "general"):
    return {
        "id": identifier,
        "name": name,
        "description": description,
        "category": category,
        "provider": "operly",
        "risk": "low",
        "approval": "auto",
    }


def test_studio_selects_only_task_relevant_capabilities_without_padding():
    capabilities = [
        _capability("discord.search_messages", "Search Discord", "Search channel history", "messaging"),
        _capability("account.update_profile", "Update account", "Change account settings", "account"),
        _capability("runtime.inspect", "Inspect runtime", "Inspect runtime status", "runtime"),
        _capability("business.search_leads", "Search leads", "Find CRM leads and customer inquiries", "business"),
        _capability("business.create_lead", "Create lead", "Create a CRM lead from a website inquiry", "business"),
        _capability("presence.website_form", "Website form", "Receive public website contact forms", "business"),
    ]
    capabilities.extend(
        _capability(f"misc.capability_{index}", f"Misc {index}", "Unrelated administrative operation")
        for index in range(20)
    )

    selected = _relevant_studio_capabilities(
        capabilities,
        "Add a contact form that captures an inquiry and creates a lead",
        {"selection": {"tag": "section", "text": "Contact us"}},
        limit=4,
    )
    ids = {item["id"] for item in selected}

    assert len(selected) == 3
    assert ids == {"business.search_leads", "business.create_lead", "presence.website_form"}
    assert "discord.search_messages" not in ids


def test_capability_prompt_reports_selected_subset_of_authorized_surface():
    selected = [
        _capability("business.create_lead", "Create lead", "Create a CRM lead", "business"),
        _capability("presence.website_form", "Website form", "Receive contact forms", "business"),
    ]

    prompt = _capability_prompt(selected, total_count=65)

    assert "2 of 65 available" in prompt
    assert "business.create_lead" in prompt
    assert "presence.website_form" in prompt


def test_source_working_set_preloads_complete_small_site_files():
    files = [
        SourceFile("app.js", b"export function boot(){ return true; }\n", "seed"),
        SourceFile("index.html", b"<!doctype html><main><h1>Hello</h1></main>\n", "seed"),
        SourceFile("styles.css", b"body{font-family:system-ui}\n", "seed"),
        SourceFile("tests/app.test.js", b"const test=require('node:test');test('x',()=>{});\n", "seed"),
    ]

    packet, complete, omitted = _source_working_set(files, char_limit=8_000)

    assert complete == ["index.html", "styles.css", "app.js", "tests/app.test.js"]
    assert omitted == []
    assert len(packet) <= 8_000
    assert "CURRENT SOURCE WORKING SET" in packet
    assert "<!doctype html>" in packet
    assert "export function boot" in packet
    assert "Do not list/read a complete unchanged file" in packet


def test_source_working_set_counts_json_escaping_against_budget():
    noisy = ('const markup = "<div class=\\"card\\">\\n";\n' * 500).encode("utf-8")
    files = [
        SourceFile("index.html", noisy, "seed"),
        SourceFile("app.js", b"export const fallback = true;\n", "seed"),
    ]

    packet, complete, omitted = _source_working_set(files, char_limit=4_000)

    assert len(packet) <= 4_000
    assert "index.html" in omitted
    assert "app.js" in complete


def test_preloaded_read_is_short_circuited_until_that_file_changes():
    async def no_op_event(_event):
        return None

    files = [
        SourceFile("index.html", b"<h1>Old</h1>\n", "seed"),
        SourceFile("app.js", b"module.exports={};\n", "seed"),
        SourceFile("tests/app.test.js", b"const test=require('node:test');test('x',()=>{});\n", "seed"),
    ]
    workspace = VirtualWorkspace(files)
    session = CodingSession(
        mode="edit",
        workspace=workspace,
        before=workspace.snapshot(),
        editor_context={},
    )
    registry = VisibleToolRegistry(no_op_event, preloaded_paths=["index.html"])
    read_tool = registry.for_mode("edit", visual=False, web=False)["read"]

    first = asyncio.run(read_tool.execute({"path": "index.html"}, session))
    assert first["preloaded"] is True
    assert first["unchanged"] is True
    assert "content" not in first

    workspace.edit("index.html", "<h1>Old</h1>", "<h1>New</h1>")
    second = asyncio.run(read_tool.execute({"path": "index.html"}, session))
    assert second["ok"] is True
    assert second.get("preloaded") is not True
    assert "<h1>New</h1>" in second["content"]


def test_studio_budget_prefers_fewer_richer_model_turns():
    assert _studio_budget("edit") == (10, 120, 75)
    assert _studio_budget("generate") == (20, 180, 90)


def test_website_runtime_registry_accepts_production_preloaded_paths_and_invalidates_after_edit():
    async def no_op_event(_event):
        return None

    files = [
        SourceFile("index.html", b"<!doctype html><html><body><h1>Old</h1></body></html>\n", "seed"),
        SourceFile("styles.css", b"body{font-family:system-ui}\n", "seed"),
    ]
    workspace = VirtualWorkspace(files)
    session = CodingSession(
        mode="edit",
        workspace=workspace,
        before=workspace.snapshot(),
        editor_context={},
    )
    registry = StudioWebsiteToolRegistry(no_op_event, preloaded_paths=["index.html"])
    tools = registry.for_mode("edit", visual=False, web=False)

    first = asyncio.run(tools["read"].execute({"path": "index.html"}, session))
    assert first["preloaded"] is True
    assert first["unchanged"] is True
    assert "content" not in first

    changed = asyncio.run(
        tools["edit"].execute(
            {
                "path": "index.html",
                "old": "<h1>Old</h1>",
                "new": "<h1>New</h1>",
            },
            session,
        )
    )
    assert changed["ok"] is True
    assert "index.html" not in registry.preloaded_paths

    second = asyncio.run(tools["read"].execute({"path": "index.html"}, session))
    assert second["ok"] is True
    assert second.get("preloaded") is not True
    assert "<h1>New</h1>" in second["content"]


def test_applied_runtime_policy_registry_matches_agent_runs_preload_factory_contract():
    from packages.studio import agent_runs

    async def no_op_event(_event):
        return None

    apply_studio_runtime_policy()
    registry = agent_runs.VisibleToolRegistry(no_op_event, preloaded_paths=["index.html"])

    assert isinstance(registry, StudioWebsiteToolRegistry)
    assert registry.preloaded_paths == {"index.html"}
    assert agent_runs._studio_budget("edit") == (10, 120, 75)
    assert agent_runs._studio_budget("generate") == (20, 180, 90)


def test_website_runtime_budget_stays_aligned_with_context_rich_harness():
    assert _website_studio_budget("edit") == (10, 120, 75)
    assert _website_studio_budget("generate") == (20, 180, 90)


class _InspectTwiceThenEditWebsiteModel:
    def __init__(self):
        self.calls = 0
        self.saw_guardrail = False

    async def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "read", "arguments": {"path": "index.html"}}}],
            }
        if self.calls == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "grep", "arguments": {"query": "Old"}}}],
            }
        if self.calls == 3:
            self.saw_guardrail = any(
                item.get("role") == "user"
                and "two model turns inspecting without changing the website source" in str(item.get("content") or "")
                for item in messages
            )
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "edit",
                            "arguments": {
                                "path": "index.html",
                                "old": "<h1>Old</h1>",
                                "new": "<h1>New</h1>",
                            },
                        }
                    },
                    {"function": {"name": "finish", "arguments": {"summary": "Updated heading."}}},
                ],
            }
        raise AssertionError("unexpected website coding turn")


def test_actual_website_agent_emits_model_input_and_breaks_inspection_only_stall():
    events = []

    async def progress(event):
        events.append(event)

    model = _InspectTwiceThenEditWebsiteModel()
    files = [
        SourceFile("index.html", b"<!doctype html><html><body><h1>Old</h1></body></html>\n", "seed"),
        SourceFile("styles.css", b"body{font-family:system-ui}\n", "seed"),
    ]
    registry = StudioWebsiteToolRegistry(progress, preloaded_paths=["index.html", "styles.css"])
    agent = StudioWebsiteCodingAgent(
        client=model,
        max_steps=6,
        registry=registry,
        progress_callback=progress,
    )

    result = asyncio.run(
        agent.edit(
            "Approved Studio website specification",
            files,
            "Change the heading from Old to New",
        )
    )

    assert model.calls == 3
    assert model.saw_guardrail is True
    assert result.changed_paths == ["index.html"]
    model_input = next(item for item in events if item.get("phase") == "model_input")
    assert model_input["detail"]["systemPrompt"] == "STUDIO_WEBSITE_SYSTEM"
    assert model_input["detail"]["workspaceFiles"] == ["index.html", "styles.css"]
    assert any(item.get("phase") == "guardrail" for item in events)
