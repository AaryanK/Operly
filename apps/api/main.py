import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select

from apps.api.access_router import router as access_router
from apps.api.admin_router import router as admin_router
from apps.api.agent_router import router as agent_router
from apps.api.analytics_router import router as analytics_router
from apps.api.app_identity_router import admin_router as app_identity_admin_router
from apps.api.app_identity_router import runtime_router as app_identity_runtime_router
from apps.api.approvals_router import router as approvals_router
from apps.api.artifact_router import router as artifact_router
from apps.api.business import router as business_router
from apps.api.capability_diagnostics_router import router as capability_diagnostics_router
from apps.api.channel_identity_router import router as channel_identity_router
from apps.api.company_router import router as company_router
from apps.api.connectors_router import router as connectors_router
from apps.api.csrf import CSRFMiddleware
from apps.api.integrations_router import router as integrations_router
from apps.api.mcp_router import router as mcp_router
from apps.api.operations_router import router as operations_router
from apps.api.personal_agent_router import router as personal_agent_router
from apps.api.personal_connectors_router import router as personal_connectors_router
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from apps.api.relational_data_router import router as relational_data_router
from apps.api.request_safety import AuthRequestSafetyMiddleware
from apps.api.runtime_trace_router import router as runtime_trace_router
from apps.api.security import hash_password
from apps.api.security_headers import SecurityHeadersMiddleware
from apps.api.session import router as session_router
from apps.api.software_projects_router import router as software_projects_router
from apps.api.solution_generation_router import router as solution_generation_router
from apps.api.solutions_router import public_router as solutions_public_router
from apps.api.solutions_router import router as solutions_router
from apps.api.studio_source_router import router as studio_source_router
from apps.api.system_router import router as system_router
from apps.api.workspace_entities_router import router as workspace_entities_router
from apps.api.workspace_router import router as workspace_router
from packages.capabilities.defaults import bootstrap_builtin_plugins
from packages.database.db import init_db, session_scope
from packages.database.models import AppUser, AuthIdentity, Tenant, TenantMember
from packages.model_runtime.catalog import provider_is_configured
from packages.model_runtime.discovery import refresh_model_discovery
from packages.plugins import default_plugin_runtime

load_dotenv(override=False)

RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip().strip("/")
RAILWAY_PUBLIC_URL = f"https://{RAILWAY_PUBLIC_DOMAIN}" if RAILWAY_PUBLIC_DOMAIN else ""
RUNNING_ON_RAILWAY = bool(
    RAILWAY_PUBLIC_DOMAIN
    or os.getenv("RAILWAY_ENVIRONMENT_ID")
    or os.getenv("RAILWAY_PROJECT_ID")
    or os.getenv("RAILWAY_SERVICE_ID")
)
PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    or RAILWAY_PUBLIC_URL
    or "http://localhost:8000"
)
PRODUCTION = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {
    "production",
    "prod",
}


async def bootstrap_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "admin@operly.local").strip().lower()
    password = os.getenv("ADMIN_PASSWORD")
    tenant_name = os.getenv("DEFAULT_TENANT_NAME", "My Business").strip()
    if not password:
        return
    async with session_scope() as db:
        user = await db.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(
                email=email,
                display_name="Owner",
                password_hash=hash_password(password),
                email_verified_at=datetime.utcnow(),
            )
            db.add(user)
            await db.flush()
        password_identity = await db.scalar(
            select(AuthIdentity).where(
                AuthIdentity.user_id == user.id,
                AuthIdentity.provider == "password",
            )
        )
        if user.password_hash and password_identity is None:
            db.add(
                AuthIdentity(
                    user_id=user.id,
                    provider="password",
                    provider_subject=user.email,
                    provider_email=user.email,
                )
            )
        if user.email_verified_at is None:
            user.email_verified_at = user.created_at
        membership = await db.scalar(
            select(TenantMember).where(TenantMember.user_id == user.id)
        )
        if membership:
            return
        tenants = (await db.scalars(select(Tenant).order_by(Tenant.created_at))).all()
        if len(tenants) == 1:
            tenant = tenants[0]
        else:
            tenant = Tenant(name=tenant_name, slug="default")
            db.add(tenant)
            await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))


def validate_runtime_configuration() -> None:
    if not os.getenv("SESSION_SECRET"):
        raise RuntimeError("SESSION_SECRET is missing")
    if not PRODUCTION:
        return
    token_pepper = os.getenv("AUTH_TOKEN_PEPPER", "")
    if len(token_pepper.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_TOKEN_PEPPER must contain at least 32 bytes")
    if token_pepper == os.getenv("SESSION_SECRET"):
        raise RuntimeError("AUTH_TOKEN_PEPPER and SESSION_SECRET must be different")
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("PUBLIC_BASE_URL must use HTTPS in production")
    if PUBLIC_BASE_URL == "https://operly.example":
        raise RuntimeError("PUBLIC_BASE_URL still uses the example value")


async def warm_model_discovery() -> None:
    """Bounded catalog warmup so the first worker can see small tool-capable models."""
    if not provider_is_configured("openrouter"):
        return
    try:
        timeout = float(
            os.getenv("OPERLY_STARTUP_MODEL_DISCOVERY_TIMEOUT_SECONDS", "4")
        )
    except ValueError:
        timeout = 4.0
    try:
        await asyncio.wait_for(
            refresh_model_discovery(provider="openrouter", ttl_seconds=0.0, force=True),
            timeout=max(1.0, min(timeout, 10.0)),
        )
    except (asyncio.TimeoutError, RuntimeError):
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration()
    await init_db()
    await bootstrap_admin()
    bootstrap_builtin_plugins()
    await warm_model_discovery()
    plugin_runtime = default_plugin_runtime()
    await plugin_runtime.start()
    try:
        yield
    finally:
        await plugin_runtime.stop()


app = FastAPI(title="OPERLY API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthRequestSafetyMiddleware)
app.add_middleware(PublicEndpointSafetyMiddleware)

allowed_hosts = {"healthcheck.railway.app"}
public_host = urlparse(PUBLIC_BASE_URL).hostname
if public_host:
    allowed_hosts.add(public_host)
if RAILWAY_PUBLIC_DOMAIN:
    allowed_hosts.add(RAILWAY_PUBLIC_DOMAIN)
if RUNNING_ON_RAILWAY:
    allowed_hosts.add("*.up.railway.app")
if not PRODUCTION:
    allowed_hosts.update({"localhost", "127.0.0.1", "testserver"})
app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(allowed_hosts))
app.add_middleware(SecurityHeadersMiddleware)

allowed_origins = {PUBLIC_BASE_URL}
if RAILWAY_PUBLIC_URL:
    allowed_origins.add(RAILWAY_PUBLIC_URL)
if not PRODUCTION:
    allowed_origins.add("http://localhost:5173")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

for router in (
    system_router,
    session_router,
    analytics_router,
    admin_router,
    personal_agent_router,
    personal_connectors_router,
    workspace_router,
    access_router,
    mcp_router,
    approvals_router,
    artifact_router,
    integrations_router,
    business_router,
    agent_router,
    runtime_trace_router,
    relational_data_router,
    company_router,
    connectors_router,
    capability_diagnostics_router,
    channel_identity_router,
    operations_router,
    studio_source_router,
    software_projects_router,
    solutions_router,
    solution_generation_router,
    solutions_public_router,
):
    app.include_router(router)

app.include_router(workspace_entities_router)
app.include_router(app_identity_runtime_router)
app.include_router(app_identity_admin_router)

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
WEB_DIST = WEB_ROOT / "dist"
KNOWN_REACT_ROUTES = {
    "",
    "login",
    "signup",
    "join",
    "verify-email",
    "forgot-password",
    "reset-password",
    "onboarding",
    "personal",
    "app",
    "admin",
    "privacy",
    "terms",
}


def react_shell(*, status_code: int = 200) -> HTMLResponse:
    index = WEB_DIST / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>OPERLY frontend build is unavailable.</h1>",
            status_code=503,
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if status_code == 404:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(
        index.read_text(encoding="utf-8"), status_code=status_code, headers=headers
    )


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    route = path.strip("/")
    built_asset = WEB_DIST / route
    if route and built_asset.is_file():
        return FileResponse(built_asset)
    if (
        route in KNOWN_REACT_ROUTES
        or route == "channels"
        or route.startswith("channels/")
    ):
        return react_shell()
    return react_shell(status_code=404)
