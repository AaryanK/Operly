import asyncio

from apps.api.main import frontend


def response_for(path: str):
    return asyncio.run(frontend(path))


def test_arbitrary_site_paths_return_a_real_404():
    for path in (
        "fdfdsfsafsafsaff",
        "totally/made/up/path",
        "made-up.js",
        "admin/not-a-real-page",
    ):
        response = response_for(path)
        assert response.status_code == 404
        assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_known_public_and_authenticated_shell_routes_still_load():
    for path in (
        "",
        "login",
        "login/",
        "signup",
        "privacy",
        "terms",
        "admin",
        "channels",
        "channels/@me",
        "channels/workspace-id/activity",
    ):
        assert response_for(path).status_code == 200
