"""URL handling for authenticated generated-application previews.

The Operly API proxies isolated runner previews so generated applications remain
behind the workspace authorization boundary.  Keep URL construction and redirect
rewriting deterministic here so the proxy never follows an application-controlled
redirect server-side.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse


class PreviewRedirectError(ValueError):
    pass


def preview_request_target(base_url: str, path: str, query: str = "") -> str:
    target = base_url.rstrip("/") + "/" + path.lstrip("/")
    return f"{target}?{query}" if query else target


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def preview_redirect_location(preview_id: str, base_url: str, location: str) -> str:
    """Rewrite a same-runner redirect back through the authenticated proxy.

    Cross-origin redirects are rejected instead of being followed by Operly.  A
    generated application therefore cannot turn the preview proxy into an SSRF
    redirector while normal `/login`, `/items?id=...`, etc. redirects still work.
    """
    resolved = urljoin(base_url.rstrip("/") + "/", location)
    if _origin(resolved) != _origin(base_url):
        raise PreviewRedirectError("Preview redirect escaped the approved runner origin")
    parsed = urlparse(resolved)
    path = parsed.path.lstrip("/")
    proxied = f"/api/custom-software/previews/{preview_id}/{path}"
    if parsed.query:
        proxied += f"?{parsed.query}"
    if parsed.fragment:
        proxied += f"#{parsed.fragment}"
    return proxied
