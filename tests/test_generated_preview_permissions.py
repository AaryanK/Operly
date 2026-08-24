from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from apps.api.security_headers import SecurityHeadersMiddleware


async def ok(request):
    return PlainTextResponse("ok")


def client():
    app = Starlette(routes=[Route("/{path:path}", ok)])
    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_solution_studio_can_delegate_same_origin_browser_permissions():
    response = client().get("/channels/workspace-1/solutions")

    policy = response.headers["Permissions-Policy"]
    assert "camera=(self)" in policy
    assert "microphone=(self)" in policy
    assert "geolocation=(self)" in policy
    assert "payment=(self)" in policy
    assert "usb=(self)" in policy


def test_generated_preview_can_use_same_origin_browser_permissions():
    response = client().get("/api/custom-software/previews/preview-1/")

    policy = response.headers["Permissions-Policy"]
    assert "camera=(self)" in policy
    assert "microphone=(self)" in policy
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_other_operly_surfaces_keep_device_permissions_denied():
    for path in ("/", "/apps/app-1/preview", "/api/studio/projects/studio-1/preview"):
        response = client().get(path)
        assert response.headers["Permissions-Policy"] == (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
