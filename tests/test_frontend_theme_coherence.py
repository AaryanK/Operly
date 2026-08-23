from pathlib import Path


STATIC = Path("apps/web/static")


def test_authenticated_workspace_loads_canonical_coherence_layer():
    regression = (STATIC / "regression.css").read_text(encoding="utf-8")
    assert "@import url('/static/operly-coherence.css?v=20260823-coherence-v2');" in regression


def test_coherence_layer_protects_text_contrast_and_mobile_navigation():
    css = (STATIC / "operly-coherence.css").read_text(encoding="utf-8")
    assert "#dashboard h2" in css
    assert "color: var(--shell-ink) !important" in css
    assert "#dashboard .sidebar.open" in css
    assert "body.mobile-nav-open #dashboard .sidebar" in css
    assert "#dashboard .operly-section-nav" in css
    assert "display: flex !important" in css


def test_legacy_mobile_hide_rule_is_overridden_with_higher_specificity():
    legacy = (STATIC / "workspace-shell.css").read_text(encoding="utf-8")
    coherence = (STATIC / "operly-coherence.css").read_text(encoding="utf-8")
    assert ".operly-section-nav{display:none}" in legacy.replace(" ", "")
    assert "#dashboard .operly-section-nav" in coherence
    assert "display: flex !important" in coherence
