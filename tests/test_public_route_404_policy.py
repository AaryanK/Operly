from apps.api.security_headers import _is_known_frontend_fallback


def test_arbitrary_extensionless_site_path_is_not_a_valid_frontend_fallback():
    assert _is_known_frontend_fallback("/fdfdsfsafsafsaff") is False
    assert _is_known_frontend_fallback("/totally/made/up/path") is False


def test_known_operly_shell_routes_remain_valid_frontend_fallbacks():
    for path in (
        "/",
        "/login",
        "/signup",
        "/admin",
        "/privacy",
        "/terms",
        "/channels",
        "/channels/@me",
        "/channels/workspace-id/activity",
        "/assets/index.js",
        "/favicon.ico",
    ):
        assert _is_known_frontend_fallback(path) is True


def test_trailing_slashes_on_known_routes_are_tolerated():
    assert _is_known_frontend_fallback("/admin/") is True
    assert _is_known_frontend_fallback("/login/") is True
