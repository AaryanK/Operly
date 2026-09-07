from __future__ import annotations

import os


# Personal and workspace Google OAuth intentionally share the one callback URI
# registered with Google. Canonicalize stale/missing Personal callback settings
# before importing the API routers so production can never silently fall back to
# the retired /api/personal-connectors/google/callback route.
public_base_url = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")
configured_google_callback = (os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
if (
    not configured_google_callback
    or configured_google_callback.endswith("/api/personal-connectors/google/callback")
):
    os.environ["GOOGLE_OAUTH_REDIRECT_URI"] = (
        public_base_url + "/api/connectors/google/callback"
    )

from apps.api.account_compat_router import router as account_compat_router
from apps.api.main import app


# main.py ends with a React GET catch-all. Routers added after that catch-all can
# be shadowed for GET requests, so temporarily move it behind the account shell
# compatibility routes. This entrypoint is intentionally small and can disappear
# once these account endpoints are folded into the canonical auth router.
frontend_catch_all = next(
    (route for route in app.router.routes if getattr(route, "path", None) == "/{path:path}"),
    None,
)
if frontend_catch_all is not None:
    app.router.routes.remove(frontend_catch_all)

app.include_router(account_compat_router)

if frontend_catch_all is not None:
    app.router.routes.append(frontend_catch_all)
