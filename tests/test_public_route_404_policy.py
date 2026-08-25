from apps.api.public_safety import _unknown_public_route


def test_arbitrary_extensionless_site_path_is_not_a_valid_shell_route():
    assert _unknown_public_route("/fdfdsfsafsafsaff") is True
    assert _unknown_public_route("/totally/made/up/path") is True


def test_known_operly_routes_still_reach_their_real_handlers():
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
        "/api/approvals/personal",
        "/static/operly-logo.png",
    ):
        assert _unknown_public_route(path) is False


def test_trailing_slashes_on_known_routes_are_tolerated():
    assert _unknown_public_route("/admin/") is False
    assert _unknown_public_route("/login/") is False
