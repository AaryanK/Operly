from __future__ import annotations

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
