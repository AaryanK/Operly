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
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from apps.api.access_router import router as access_router
from apps.api.agent_router import router as agent_router
from apps.api.application_builder_router import router as application_builder_router
from apps.api.approvals_router import router as approvals_router
from apps.api.architecture_pack_router import router as architecture_pack_router
from apps.api.business import router as business_router
from apps.api.capability_diagnostics_router import router as capability_diagnostics_router
from apps.api.channel_identity_router import router as channel_identity_router
from apps.api.coding_harness_router import router as coding_harness_router
from apps.api.company_router import router as company_router
from apps.api.connectors_router import router as connectors_router
from apps.api.csrf import CSRFMiddleware
from apps.api.custom_software_router import router as custom_software_router
from apps.api.dashboard_studio_router import router as dashboard_studio_router
from apps.api.integrations_router import router as integrations_router
from apps.api.mcp_router import router as mcp_router
from apps.api.operations_router import router as operations_router
from apps.api.personal_agent_router import router as personal_agent_router
from apps.api.personal_connectors_router import router as personal_connectors_router
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from apps.api.request_safety import AuthRequestSafetyMiddleware
from apps.api.runtime_trace_router import router as runtime_trace_router
from apps.api.security import hash_password
from apps.api.security_headers import SecurityHeadersMiddleware
from apps.api.session import router as session_router
from apps.api.software_projects_router import router as software_projects_router
from apps.api.solution_generation_router import router as solution_generation_router
from apps.api.solutions_router import public_router as solutions_public_router
from apps.api.solutions_router import router as solutions_router
from apps.api.studio_debug_router import router as studio_debug_router
from apps.api.studio_router import router as studio_router
from apps.api.studio_run_history_router import router as studio_run_history_router
from apps.api.studio_source_router import router as studio_source_router
from apps.api.system_router import router as system_router
from apps.api.workspace_router import router as workspace_router
from packages.capabilities.defaults import bootstrap_builtin_plugins
from packages.database.db import init_db, session_scope
from packages.database.models import AppUser, AuthIdentity, Tenant, TenantMember
from packages.plugins import default_plugin_runtime
from packages.studio.agent_resume import resume_interrupted_studio_runs

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
WEB_ASSET_REVISION = "20260823-pending-fixes-v1"


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
        membership = await db.scalar(select(TenantMember).where(TenantMember.user_id == user.id))
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration()
    await init_db()
    await bootstrap_admin()
    bootstrap_builtin_plugins()
    plugin_runtime = default_plugin_runtime()
    await plugin_runtime.start()
    await resume_interrupted_studio_runs()
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
    personal_agent_router,
    personal_connectors_router,
    workspace_router,
    access_router,
    mcp_router,
    approvals_router,
    integrations_router,
    business_router,
    agent_router,
    runtime_trace_router,
    company_router,
    connectors_router,
    capability_diagnostics_router,
    channel_identity_router,
    operations_router,
    studio_router,
    studio_source_router,
    studio_run_history_router,
    studio_debug_router,
    dashboard_studio_router,
    application_builder_router,
    custom_software_router,
    architecture_pack_router,
    coding_harness_router,
    software_projects_router,
    solutions_router,
    solution_generation_router,
    solutions_public_router,
):
    app.include_router(router)

WEB_STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
app.mount("/static", StaticFiles(directory=WEB_STATIC), name="static")


def frontend_shell() -> HTMLResponse:
    html = (WEB_STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        "/static/app.js?v=20260819-auth-v4",
        f"/static/app.js?v={WEB_ASSET_REVISION}",
    ).replace(
        "/static/auth.js?v=20260819-auth-v4",
        f"/static/auth.js?v={WEB_ASSET_REVISION}",
    ).replace(
        "/static/ai-assistant-bridge.js",
        f"/static/ai-assistant-bridge.js?v={WEB_ASSET_REVISION}",
    ).replace(
        "/static/unified-solution-studio.js",
        f"/static/unified-solution-studio.js?v={WEB_ASSET_REVISION}",
    ).replace(
        "/static/time-sync.js",
        f"/static/time-sync.js?v={WEB_ASSET_REVISION}",
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    requested = WEB_STATIC / path
    if path and requested.is_file():
        return FileResponse(requested)
    return frontend_shell()
