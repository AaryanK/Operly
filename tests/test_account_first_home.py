from pathlib import Path


def test_account_first_navigation_contract():
    source = Path("apps/web/static/auth.js").read_text(encoding="utf-8")
    assert "/auth/personal-scope" in source
    assert "function installPersonalHomeNavigation()" in source
    assert "#operly-workspace-rail" in source
    assert ".operly-section-nav .operly-nav-scroll" in source
    assert 'preferredScope === "workspace"' in source
    assert 'location.pathname === "/app"' in source
    assert "return enterAuthenticatedPersonal();" in source
    assert "await enterAuthenticatedScope(response.scope);" not in source
