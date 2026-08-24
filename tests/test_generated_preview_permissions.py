from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from apps.api.security_headers import SecurityHeadersMiddleware
from packages.solutions.service import _approved_runner_preview_target


async def ok(request):
    return PlainTextResponse("ok")


def client():
    app = Starlette(routes=[Route("/{path:path}", ok)])
    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_solution_studio_delegates_device_permissions_only_to_configured_runner_origin(monkeypatch):
    monkeypatch.setenv("OPERLY_SANDBOX_PREVIEW_HOSTS", "preview.runner.example")
    response = client().get("/channels/workspace-1/solutions")

    policy = response.headers["Permissions-Policy"]
    delegated = '"https://preview.runner.example"'
    assert f"camera=({delegated})" in policy
    assert f"microphone=({delegated})" in policy
    assert f"geolocation=({delegated})" in policy
    assert f"payment=({delegated})" in policy
    assert f"usb=({delegated})" in policy
    assert "https://preview.runner.example" in response.headers["Content-Security-Policy"]


def test_solution_studio_keeps_device_permissions_denied_without_runner_allowlist(monkeypatch):
    monkeypatch.delenv("OPERLY_SANDBOX_PREVIEW_HOSTS", raising=False)
    response = client().get("/channels/workspace-1/solutions")
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )


def test_generated_preview_redirect_requires_exact_allowlisted_https_host(monkeypatch):
    monkeypatch.setenv("OPERLY_ENV", "production")
    monkeypatch.setenv("OPERLY_SANDBOX_PREVIEW_HOSTS", "preview.runner.example")

    assert _approved_runner_preview_target(
        "https://preview.runner.example/preview/random-token/"
    )
    assert not _approved_runner_preview_target(
        "https://evil.example/preview/random-token/"
    )
    assert not _approved_runner_preview_target(
        "http://preview.runner.example/preview/random-token/"
    )
    assert not _approved_runner_preview_target(
        "https://preview.runner.example.evil.test/preview/random-token/"
    )
    assert not _approved_runner_preview_target(
        "https://preview.runner.example/preview/random-token/?next=https://evil.example"
    )
    assert not _approved_runner_preview_target(
        "https://user@preview.runner.example/preview/random-token/"
    )


def test_operly_same_origin_generated_proxy_never_receives_device_authority(monkeypatch):
    monkeypatch.setenv("OPERLY_SANDBOX_PREVIEW_HOSTS", "preview.runner.example")
    response = client().get("/api/custom-software/previews/preview-1/")

    # New generated Solutions execute on the separate runner origin. The legacy
    # same-origin proxy must not gain camera/mic authority, or arbitrary generated
    # JavaScript would share Operly's browser origin and session boundary.
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_other_operly_surfaces_keep_device_permissions_denied(monkeypatch):
    monkeypatch.setenv("OPERLY_SANDBOX_PREVIEW_HOSTS", "preview.runner.example")
    for path in ("/", "/apps/app-1/preview", "/api/studio/projects/studio-1/preview"):
        response = client().get(path)
        assert response.headers["Permissions-Policy"] == (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
