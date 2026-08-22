from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.coding_harness.opencode_agent import VirtualWorkspace
from packages.custom_software.source_bundles import SourceFile
from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.studio.agent_runs import _context_summary
from packages.studio.preview_assets import inline_local_preview_assets
from packages.studio.runtime_policy import (
    StudioWebsiteContractError,
    _safe_fuzzy_edit,
    validate_studio_website,
)
from packages.studio.schema import blank_site
from packages.studio.source_agent import production_html, project_context
from packages.studio.terminal_recovery import (
    _EXTRA_RUNTIME_RULE,
    recover_verified_terminal_session,
)


class _Rows:
    def all(self):
        return []


class _DB:
    async def get(self, model, key):
        return None

    async def scalars(self, statement):
        return _Rows()


@pytest.mark.asyncio
async def test_source_agent_context_is_point_form_and_selection_aware():
    project = SimpleNamespace(id="project-1", name="Antu Hill Travels", description="Nepal travel company")
    text = await project_context(
        _DB(),
        "tenant-1",
        project,
        editor_context={
            "route": "/",
            "viewport": "mobile",
            "selection": {"tag": "h1", "text": "Explore Nepal"},
            "conversation": [{"role": "user", "content": "Keep the header"}],
        },
    )

    assert "PROJECT" in text
    assert "BUSINESS CONTEXT" in text
    assert "CURRENT STUDIO CONTEXT" in text
    assert "Selected element" in text
    assert "Explore Nepal" in text
    assert "Keep the header" in text
    assert "mobile" in text
    assert "Pydantic" not in text
    assert "SiteSchema" not in text
    assert "Return JSON" not in text


def test_studio_context_summary_surfaces_authorized_capability_groups():
    summary, detail = _context_summary(
        {
            "route": "/",
            "viewport": "desktop",
            "conversation": [{"role": "user", "content": "Build the booking page"}],
            "_operly_capabilities": [
                {"id": "business.search_leads", "category": "business", "provider": "operly"},
                {"id": "gmail.search", "category": "messaging", "provider": "google"},
            ],
        },
        SimpleNamespace(source_version=4),
    )

    assert "2 authorized capabilities" in summary
    assert "business" in summary
    assert "messaging" in summary
    assert detail["capabilityContext"]["count"] == 2
    assert detail["capabilityContext"]["ids"] == ["business.search_leads", "gmail.search"]
    assert detail["source"] == "S4"


def test_legacy_blank_site_is_only_a_neutral_compatibility_snapshot():
    site = blank_site("Example Co", "A useful business")
    assert len(site.pages) == 1
    assert len(site.pages[0].sections) == 1
    assert site.pages[0].sections[0].id == "legacy-placeholder"
    assert site.theme.primary == "#4b96ff"
    assert site.theme.mode == "dark"


def test_source_production_flattens_css_blocks_generated_js_and_wires_contact_form():
    row = SimpleNamespace(
        files_json=(
            '[{"path":"index.html","content":"<!doctype html><html><head>'
            '<link rel=\\"stylesheet\\" href=\\"styles.css\\"></head><body>'
            '<form method=\\"post\\" action=\\"__OPERLY_FORM_ACTION__\\"></form>'
            '<script src=\\"app.js\\"></script></body></html>","generatedBy":"agent"},'
            '{"path":"styles.css","content":"body{background:#050610;color:#fff}","generatedBy":"agent"},'
            '{"path":"app.js","content":"console.log(1)","generatedBy":"agent"}]'
        )
    )
    html = production_html(row, "solution-123")
    assert "body{background:#050610;color:#fff}" in html
    assert "<script" not in html.lower()
    assert "/api/public/presence/solution-123/forms/contact" in html
    assert "__OPERLY_FORM_ACTION__" not in html


def test_sandboxed_preview_inlines_local_css_and_js_without_same_origin_credentials():
    html = """<!doctype html><html><head><link rel='stylesheet' href='./styles.css'></head>
    <body><h1>Antu Hill</h1><script src='app.js'></script></body></html>"""
    bundled = inline_local_preview_assets(
        html,
        {
            "index.html": html,
            "styles.css": "body{background:#050610;color:#fff}",
            "app.js": "document.documentElement.dataset.ready='1';",
        },
    )
    assert "href='./styles.css'" not in bundled
    assert "src='app.js'" not in bundled
    assert "body{background:#050610;color:#fff}" in bundled
    assert "document.documentElement.dataset.ready='1'" in bundled
    assert 'data-operly-inline-source="styles.css"' in bundled
    assert 'data-operly-inline-source="app.js"' in bundled


def test_studio_website_contract_accepts_native_navigation_forms_and_css_toggle():
    files = [
        SourceFile(
            "index.html",
            b"""<!doctype html><html><body>
            <nav><a href='#about'>About</a><input type='checkbox' id='menu'></nav>
            <form method='post' action='__OPERLY_FORM_ACTION__'>
              <input name='name'><input type='email' name='email'>
              <textarea name='message'></textarea><button type='submit'>Send</button>
            </form></body></html>""",
            "test",
        )
    ]
    report = validate_studio_website(files, '- Known facts: {"location":"Kathmandu, Nepal"}')
    assert report["nativeBrowserBehavior"] is True
    assert report["groundingChecked"] is True


def test_studio_website_contract_rejects_invented_metrics_and_testimonials():
    facts = '- Known facts: {"name":"Antu Hill Travels","location":"Kathmandu, Nepal"}'
    metric = [
        SourceFile(
            "index.html",
            b"<!doctype html><html><body><b>15k+</b><p>Happy Travelers</p></body></html>",
            "test",
        )
    ]
    with pytest.raises(StudioWebsiteContractError, match="Unsupported business metric"):
        validate_studio_website(metric, facts)

    testimonial = [
        SourceFile(
            "index.html",
            b"<!doctype html><html><body><blockquote class='testimonial'>Best trip ever</blockquote></body></html>",
            "test",
        )
    ]
    with pytest.raises(StudioWebsiteContractError, match="Unsupported testimonial"):
        validate_studio_website(testimonial, facts)


def test_studio_fuzzy_edit_recovers_from_whitespace_only_source_drift():
    workspace = VirtualWorkspace(
        [
            SourceFile(
                "index.html",
                b"<section>\n  <h2>Explore Nepal</h2>\n  <p>Plan your journey.</p>\n</section>",
                "test",
            )
        ]
    )
    _safe_fuzzy_edit(
        workspace,
        "index.html",
        "<section><h2>Explore Nepal</h2><p>Plan your journey.</p></section>",
        "<section><h2>Explore Nepal</h2><p>Start in Kathmandu and travel across Nepal.</p></section>",
    )
    assert "Start in Kathmandu" in workspace.raw("index.html")


def test_terminal_guard_preserves_a_changed_workspace_only_after_studio_validation_passes():
    workspace = VirtualWorkspace(
        [SourceFile("index.html", b"<!doctype html><html><body><h1>Old</h1></body></html>", "test")]
    )
    before = workspace.snapshot()
    workspace.write("index.html", "<!doctype html><html><body><h1>Explore Nepal</h1></body></html>")

    recovered = recover_verified_terminal_session(
        mode="edit",
        specification='- Known facts: {"location":"Kathmandu, Nepal"}',
        workspace=workspace,
        before=before,
        require_change=True,
        editor_context={"viewport": "desktop"},
        error=RuntimeError("Coding model did not respond within the bounded website-edit window."),
    )

    assert recovered is not None
    assert recovered.finished is True
    assert "verified" in recovered.summary.lower()
    assert recovered.changed_paths() == ["index.html"]


def test_terminal_guard_does_not_preserve_an_invalid_or_unchanged_workspace():
    invalid = VirtualWorkspace(
        [SourceFile("index.html", b"<!doctype html><html><body><script src='https://evil.example/x.js'></script></body></html>", "test")]
    )
    invalid_before = {"index.html": "<!doctype html><html><body><h1>Old</h1></body></html>"}
    assert recover_verified_terminal_session(
        mode="edit",
        specification="- Known facts: {}",
        workspace=invalid,
        before=invalid_before,
        require_change=True,
        editor_context={},
        error=RuntimeError("Studio website agent exhausted its bounded model-turn budget."),
    ) is None

    unchanged = VirtualWorkspace(
        [SourceFile("index.html", b"<!doctype html><html><body><h1>Old</h1></body></html>", "test")]
    )
    unchanged_before = unchanged.snapshot()
    assert recover_verified_terminal_session(
        mode="edit",
        specification="- Known facts: {}",
        workspace=unchanged,
        before=unchanged_before,
        require_change=True,
        editor_context={},
        error=RuntimeError("Studio website agent exhausted its bounded model-turn budget."),
    ) is None


def test_terminal_recovery_runtime_rule_warns_model_about_remote_scripts_upfront():
    assert "third-party remote script URLs" in _EXTRA_RUNTIME_RULE
    assert "Studio rejects them" in _EXTRA_RUNTIME_RULE


def test_studio_agent_run_models_are_registered_for_durable_progress():
    import_all_models()
    assert "studio_agent_runs" in Base.metadata.tables
    assert "studio_agent_events" in Base.metadata.tables


def test_studio_browser_uses_durable_source_runs_and_visible_trace():
    source = (Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "unified-solution-studio.js").read_text()
    bridge = (Path(__file__).resolve().parents[1] / "apps" / "web" / "static" / "ai-assistant-bridge.js").read_text()
    assert "/source/runs" in source
    assert "/source/runs/latest" in source
    assert "ss-run-trace" in source
    assert "Agent activity" in source
    assert "/source/edits" not in source
    assert "/source/generate" not in source
    assert "/ai/revise" not in source
    assert "SELECTED ELEMENT CONTEXT" not in source
    assert "Redesign page" not in source
    assert "setInterval(" not in source
    assert "studio-product-overhaul.js" not in bridge
    assert "studio-product-overhaul.css" not in bridge
    assert "createModal" not in source
    assert "Website name" not in source
    assert 'id="ss-open-website"' in source
    assert 'solution_type:"digital_presence"' in source
