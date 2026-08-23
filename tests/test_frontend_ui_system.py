from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps" / "web" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_authenticated_bridge_does_not_load_legacy_visual_generations():
    bridge = read("ai-assistant-bridge.js")
    assert "/static/authenticated-ui.css?v=20260823-ui-system-v3" in bridge
    assert "/static/authenticated-ui.js?v=20260823-ui-system-v3" in bridge
    assert "/static/frontend-overhaul.css" not in bridge
    assert "/static/settings-scopes.css" not in bridge
    assert "/static/account-shell-clean.css" not in bridge
    assert '"data-operly-frontend-overhaul"' in bridge
    assert '"data-operly-settings-scopes"' in bridge


def test_workspace_assistant_does_not_force_a_solution_picker():
    behavior = read("authenticated-ui.js")
    css = read("authenticated-ui.css")
    assert ".ai-application-picker" in behavior
    assert "node.remove()" in behavior
    assert ".ai-application-picker{display:none!important}" in css


def test_personal_dm_owns_its_composer_and_infers_scope():
    behavior = read("authenticated-ui.js")
    assert "prunePersonalScopePicker" in behavior
    assert 'compose.querySelector(\'label[for="personal-workspace-select"]\')?.remove()' in behavior
    assert "compose.querySelector('#personal-workspace-select')?.remove()" in behavior
    assert "grid-template-rows" in behavior
    assert "position', 'static'" in behavior
    assert "calc(100dvh - 64px)" in behavior


def test_personal_dm_renders_assistant_markdown():
    behavior = read("authenticated-ui.js")
    assert "enhancePersonalMessages" in behavior
    assert "window.operlyChatEnhancements?.renderMarkdown" in behavior
    assert "personal-message-markdown ai-markdown" in behavior
    assert "personalMarkdownRendered" in behavior


def test_ui_system_has_tablet_and_phone_navigation_contracts():
    css = read("authenticated-ui.css")
    assert "@media(max-width:1024px)" in css
    assert "@media(max-width:860px)" in css
    assert "@media(max-width:700px)" in css
    assert "operly-mobile-nav-open" in css
    assert "personal-mobile-nav-open" in css
    assert "grid-template-columns:1fr!important" in css


def test_settings_and_activity_use_light_readable_surfaces():
    css = read("authenticated-ui.css")
    assert ".card.approval" in css
    assert "background:#fff!important" in css
    assert ".operly-setting-row" in css
    assert ".operly-bind-guide" in css
    assert ".shell-modal-card" in css
    assert "width:min(1120px,calc(100vw - 48px))!important" in css


def test_solution_composition_is_responsive_and_full_width():
    css = read("authenticated-ui.css")
    assert ":has(#ss-compose-name)" in css
    assert "#ss-compose-objective" in css
    assert "width:100%!important" in css


def test_frontend_asset_revision_busts_previous_authenticated_shell():
    main = (ROOT / "apps" / "api" / "main.py").read_text(encoding="utf-8")
    assert 'WEB_ASSET_REVISION = "20260823-ui-system-v3"' in main
