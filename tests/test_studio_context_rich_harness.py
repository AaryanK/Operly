import asyncio

from packages.coding_harness.opencode_agent import CodingSession, VirtualWorkspace
from packages.custom_software.source_bundles import SourceFile
from packages.studio.agent_runs import (
    VisibleToolRegistry,
    _capability_prompt,
    _relevant_studio_capabilities,
    _source_working_set,
    _studio_budget,
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
