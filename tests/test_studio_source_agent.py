from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.database.db import Base
from packages.database.schema import import_all_models
from packages.studio.schema import blank_site
from packages.studio.source_agent import production_html, project_context


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
