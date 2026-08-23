from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "apps" / "web" / "static"


def test_runtime_bridge_does_not_load_legacy_global_themes():
    bridge = (STATIC / "ai-assistant-bridge.js").read_text(encoding="utf-8")

    for legacy_asset in (
        "operly-cosmic.css",
        "operly-cosmic.js",
        "operly-modern.css",
        "operly-modern-extras.css",
        "operly-modern.js",
        "viewport-fix.css",
    ):
        assert legacy_asset not in bridge

    assert "account-shell-clean.css" in bridge
    assert "workspace-shell.js" in bridge


def test_personal_route_can_actually_hide_workspace_shell():
    css = (STATIC / "account-shell-clean.css").read_text(encoding="utf-8")

    assert "#dashboard.workspace-shell-ready.hidden" in css
    assert "display: none !important" in css


def test_account_scope_rail_is_the_only_visible_global_workspace_rail():
    css = (STATIC / "account-shell-clean.css").read_text(encoding="utf-8")
    personal = (STATIC / "personal.js").read_text(encoding="utf-8")

    assert 'rail.id = "operly-scope-rail"' in personal
    assert "#dashboard.workspace-shell-ready > .operly-workspace-rail" in css
    assert "grid-template-columns: 252px minmax(0, 1fr)" in css
    assert "margin-left: 72px" in css


def test_frontend_asset_revision_busts_pre_fix_browser_cache():
    main = (ROOT / "apps" / "api" / "main.py").read_text(encoding="utf-8")
    assert 'WEB_ASSET_REVISION = "20260823-account-shell-v2"' in main
