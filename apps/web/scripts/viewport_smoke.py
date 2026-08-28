from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

WEB_ROOT = Path(__file__).resolve().parents[1]
DIST_ROOT = WEB_ROOT / "dist"

PUBLIC_ROUTES = {
    "/": "Give AI somewhere to",
    "/login": "Welcome back",
    "/signup": "Create your Operly account",
    "/verify-email": "Check your inbox",
    "/forgot-password": "Reset your password",
    "/reset-password": "Choose a new password",
    "/onboarding": "Welcome to OPERLY",
    "/privacy": "Privacy Policy",
    "/terms": "Terms of Service",
}

VIEWPORTS = [
    (320, 800),
    (360, 800),
    (390, 844),
    (430, 932),
    (768, 1024),
    (1024, 768),
    (1440, 900),
]


class SmokeServer(ThreadingHTTPServer):
    scope = "public"


class Handler(BaseHTTPRequestHandler):
    server: SmokeServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _react_shell(self) -> None:
        self._file(DIST_ROOT / "index.html", "text/html; charset=utf-8")

    def _api_get(self, path: str) -> None:
        if path == "/api/auth/bootstrap":
            self._json({"google_client_id": None, "google_nonce": "viewport-smoke"})
            return
        if path in {"/api/me", "/api/auth/workspaces"} and self.server.scope == "public":
            self._json({"detail": "not authenticated"}, status=401)
            return
        if path == "/api/personal-agent/me":
            self._json({
                "id": "user-1",
                "email": "mobile-smoke@operly.test",
                "display_name": "Mobile Smoke",
                "current_workspace_id": "workspace-1" if self.server.scope == "workspace" else None,
            })
            return
        if path == "/api/personal-agent/workspaces":
            self._json([{
                "id": "workspace-1",
                "name": "Demo Workspace",
                "role": "owner",
                "current": self.server.scope == "workspace",
                "slug": "demo",
                "logo_url": None,
                "timezone": "America/Chicago",
            }])
            return
        if path == "/api/personal-agent/conversations":
            self._json([])
            return
        if path in {"/api/tasks", "/api/approvals", "/api/approvals/personal", "/api/solutions"}:
            self._json([])
            return
        self._json([])

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._api_get(path)
            return
        if path.startswith("/assets/"):
            self._file(DIST_ROOT / path.removeprefix("/"))
            return
        if path == "/operly-logo.png":
            self._file(DIST_ROOT / "operly-logo.png", "image/png")
            return
        if path.startswith("/channels/"):
            self.server.scope = "personal" if path.startswith("/channels/@me") else "workspace"
            self._react_shell()
            return
        if path in PUBLIC_ROUTES:
            self.server.scope = "public"
            self._react_shell()
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        if path == "/api/auth/personal-scope":
            self.server.scope = "personal"
        elif path == "/api/auth/switch-workspace":
            self.server.scope = "workspace"
            try:
                json.loads(body or b"{}")
            except json.JSONDecodeError:
                pass
        self._json({"ok": True})


def assert_no_horizontal_overflow(page: Page, label: str) -> None:
    dimensions = page.evaluate(
        """() => ({
          htmlScroll: document.documentElement.scrollWidth,
          htmlClient: document.documentElement.clientWidth,
          bodyScroll: document.body.scrollWidth,
          bodyClient: document.body.clientWidth
        })"""
    )
    if dimensions["htmlScroll"] > dimensions["htmlClient"] + 1 or dimensions["bodyScroll"] > dimensions["bodyClient"] + 1:
        raise AssertionError(f"{label}: horizontal overflow detected: {dimensions}")


def assert_min_target(page: Page, selector: str, minimum: float, label: str) -> None:
    boxes = page.locator(selector).evaluate_all(
        """els => els.filter(el => {
          const s = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || 1) > 0 && r.width > 0 && r.height > 0;
        }).map(el => { const r = el.getBoundingClientRect(); return ({w:r.width,h:r.height}); })"""
    )
    if not boxes:
        raise AssertionError(f"{label}: expected visible touch target {selector}")
    for box in boxes:
        if box["w"] < minimum or box["h"] < minimum:
            raise AssertionError(f"{label}: {selector} target below {minimum}px: {box}")


def assert_full_viewport(page: Page, selector: str, viewport: tuple[int, int], label: str) -> None:
    box = page.locator(selector).bounding_box()
    if not box:
        raise AssertionError(f"{label}: {selector} is not visible")
    width, height = viewport
    if box["x"] > 1 or box["y"] > 1 or box["width"] < width - 2 or box["height"] < height - 2:
        raise AssertionError(f"{label}: {selector} did not take over the viewport: {box}")


def wait_for_visible_text(page: Page, expected: str) -> None:
    page.wait_for_function(
        """expected => Array.from(document.querySelectorAll('body *')).some(el => {
          if (!(el.textContent || '').includes(expected)) return false;
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
        })""",
        arg=expected,
    )


def open_page(page: Page, base_url: str, route: str, expected: str, viewport: tuple[int, int]) -> None:
    failures: list[str] = []

    def on_page_error(error: object) -> None:
        failures.append(str(error))

    page.on("pageerror", on_page_error)
    try:
        response = page.goto(f"{base_url}{route}", wait_until="networkidle")
        if response is None or response.status >= 400:
            raise AssertionError(f"{route} at {viewport}: HTTP {None if response is None else response.status}")
        wait_for_visible_text(page, expected)
        assert_no_horizontal_overflow(page, f"{route} at {viewport}")
        if failures:
            raise AssertionError(f"{route} at {viewport}: page error(s): {failures}")
    finally:
        page.remove_listener("pageerror", on_page_error)


def run() -> None:
    if not (DIST_ROOT / "index.html").is_file():
        raise SystemExit("Run `npm run build` before viewport_smoke.py")

    server = SmokeServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for viewport in VIEWPORTS:
                    width, height = viewport
                    page = browser.new_page(viewport={"width": width, "height": height})
                    try:
                        for route, expected in PUBLIC_ROUTES.items():
                            open_page(page, base_url, route, expected, viewport)

                        open_page(page, base_url, "/channels/@me", "Messages", viewport)
                        if width <= 680:
                            rail = page.locator(".scope-rail")
                            rail.wait_for(state="visible")
                            rail_box = rail.bounding_box()
                            if not rail_box or rail_box["height"] < height - 2 or rail_box["width"] > 74:
                                raise AssertionError(f"Personal at {viewport}: scope rail must remain a vertical mobile rail: {rail_box}")
                            page.locator(".personal-conversation-search").wait_for(state="visible")
                            assert_min_target(page, ".personal-message-list-head button[aria-label='New conversation']", 44, f"Personal at {viewport}")
                            page.locator(".personal-message-list-head button[aria-label='New conversation']").click()
                            page.wait_for_timeout(120)
                            assert_full_viewport(page, ".mobile-personal-thread .personal-surface", viewport, f"Personal at {viewport}")
                            assert_min_target(page, ".personal-mobile-content-header > button", 44, f"Personal thread at {viewport}")
                            page.locator(".personal-mobile-content-header > button").click()
                            page.locator(".personal-conversation-search").wait_for(state="visible")
                        else:
                            page.locator(".personal-history").wait_for(state="visible")
                            page.locator(".personal-surface").wait_for(state="visible")

                        open_page(page, base_url, "/channels/workspace-1", "Demo Workspace", viewport)
                        if width <= 680:
                            page.locator(".workspace-nav").wait_for(state="visible")
                            page.locator(".workspace-nav-search").wait_for(state="visible")
                            if page.locator(".workspace-mobile-nav").count():
                                raise AssertionError(f"Workspace at {viewport}: retired bottom navigation returned")
                            assert_min_target(page, ".nav-group-items > button", 44, f"Workspace navigation at {viewport}")
                            home = page.locator(".nav-group-items > button").filter(has_text="Home").first
                            home.click()
                            page.wait_for_timeout(120)
                            assert_full_viewport(page, ".mobile-content-open .workspace-content-frame", viewport, f"Workspace at {viewport}")
                            assert_min_target(page, ".workspace-mobile-content-header > button", 44, f"Workspace content at {viewport}")
                            page.locator(".workspace-mobile-content-header > button").click()
                            page.locator(".workspace-nav-search").wait_for(state="visible")
                            first_group = page.locator(".nav-group").first
                            first_group.locator(".nav-group-heading").click()
                            if first_group.locator(".nav-group-items").evaluate("el => getComputedStyle(el).display") != "none":
                                raise AssertionError(f"Workspace at {viewport}: collapsible navigation group did not collapse")
                        else:
                            page.locator(".workspace-nav").wait_for(state="visible")
                            page.locator(".workspace-content-frame").wait_for(state="visible")
                    finally:
                        page.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    print(f"Viewport smoke passed: {len(VIEWPORTS)} viewports × {len(PUBLIC_ROUTES) + 2} surfaces.")


if __name__ == "__main__":
    run()
