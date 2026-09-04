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
from apps.api.agent_runtime_router import (
    personal_router as personal_agent_runtime_router,
    workspace_router as workspace_agent_runtime_router,
)
from apps.api.artifact_router import router as artifact_router
from apps.api.csrf import CSRFMiddleware
from apps.api.discord_auth_router import router as discord_auth_router
from apps.api.kernel_router import router as kernel_router
from apps.api.mcp_router import router as mcp_router
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from apps.api.request_safety import AuthRequestSafetyMiddleware
from apps.api.security import hash_password
from apps.api.security_headers import SecurityHeadersMiddleware, confined_file
from apps.api.session import router as session_router
from apps.api.workspace_os_router import router as workspace_os_router
from apps.api.workspace_simple_router import router as workspace_simple_router
from packages.agent_runtime.inference import AgentInferenceError, InferenceRoute
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.database.db import init_db, session_scope
from packages.database.models import AppUser, AuthIdentity, Tenant, TenantMember
from packages.personal_modules.connectors import router as personal_connectors_router
from packages.personal_modules.router import router as personal_tools_router
from packages.plugins.egress_router import router as runtime_egress_router
from packages.plugins.event_router import router as plugin_event_router
from packages.plugins.gateway_router import router as capability_gateway_router
from packages.plugins.hosted_public_router import public_router as plugin_hosted_public_router
from packages.plugins.router import router as plugin_platform_router
from packages.plugins.runtime_router import router as plugin_runtime_management_router
from packages.plugins.webhook_router import (
    management_router as plugin_webhook_management_router,
    public_router as plugin_webhook_public_router,
)
from packages.workflow import workflow_event_dispatcher, workflow_scheduler
from packages.workspace_modules.agent_computer.router import router as agent_computer_router
from packages.workspace_modules.integrations.discord.lifecycle import discord_bot_lifecycle
from packages.workspace_modules.integrations.router import router as workspace_integrations_router
from packages.workspace_modules.studio.router import public_router as studio_public_router
from packages.workspace_modules.tools.router import router as workspace_tools_router

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
STUDIO_PUBLIC_HOST = os.getenv("OPERLY_STUDIO_PUBLIC_HOST", "").strip().lower().rstrip(".")
PRODUCTION = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {"production", "prod"}


def _site_suffix_hint(host: str | None) -> str:
    """Conservative cookie-site guard without depending on a public-suffix library.

    Matching the final two labels catches ordinary sibling-subdomain deployments.
    It deliberately over-rejects some multi-label public suffixes (for example co.uk)
    rather than accidentally accepting a content hostname that could receive a broad
    parent-domain Operly cookie.
    """

    labels = [part for part in str(host or "").strip().lower().rstrip(".").split(".") if part]
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


async def bootstrap_admin() -> None:
    """Keep deterministic account bootstrap without starting the model runtime."""
    email = os.getenv("ADMIN_EMAIL", "admin@operly.local").strip().lower()
    password = os.getenv("ADMIN_PASSWORD")
    tenant_name = os.getenv("DEFAULT_TENANT_NAME", "My Business").strip()
    if not password:
        return
    async with session_scope() as db:
        user = await db.scalar(select(AppUser).where(AppUser.email == email))
        if user is None:
            user = AppUser(email=email, display_name="Owner", password_hash=hash_password(password), email_verified_at=datetime.utcnow())
            db.add(user)
            await db.flush()
        password_identity = await db.scalar(select(AuthIdentity).where(AuthIdentity.user_id == user.id, AuthIdentity.provider == "password"))
        if user.password_hash and password_identity is None:
            db.add(AuthIdentity(user_id=user.id, provider="password", provider_subject=user.email, provider_email=user.email))
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
    session_secret = os.getenv("SESSION_SECRET", "")
    if not session_secret:
        raise RuntimeError("SESSION_SECRET is missing")
    if not PRODUCTION:
        return
    token_pepper = os.getenv("AUTH_TOKEN_PEPPER", "")
    if len(token_pepper.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_TOKEN_PEPPER must contain at least 32 bytes")
    if token_pepper == session_secret:
        raise RuntimeError("AUTH_TOKEN_PEPPER and SESSION_SECRET must be different")
    mcp_token_secret = (
        os.getenv("OPERLY_MCP_TOKEN_SECRET", "").strip()
        or os.getenv("MCP_OAUTH_SECRET", "").strip()
    )
    if len(mcp_token_secret.encode("utf-8")) < 32:
        raise RuntimeError("A dedicated MCP token signing secret of at least 32 bytes is required")
    if mcp_token_secret in {session_secret, token_pepper}:
        raise RuntimeError("MCP token signing secret must be distinct from session/auth secrets")
    if not PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("PUBLIC_BASE_URL must use HTTPS in production")
    if PUBLIC_BASE_URL == "https://operly.example":
        raise RuntimeError("PUBLIC_BASE_URL still uses the example value")

    if os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip():
        if not STUDIO_PUBLIC_HOST:
            raise RuntimeError(
                "OPERLY_STUDIO_PUBLIC_HOST is required when Studio hosting is enabled in production"
            )
        if any(token in STUDIO_PUBLIC_HOST for token in ("/", "@", "?", "#", ":")):
            raise RuntimeError("OPERLY_STUDIO_PUBLIC_HOST must be a bare hostname")
        app_host = str(urlparse(PUBLIC_BASE_URL).hostname or "").lower().rstrip(".")
        if STUDIO_PUBLIC_HOST == app_host or _site_suffix_hint(STUDIO_PUBLIC_HOST) == _site_suffix_hint(app_host):
            raise RuntimeError(
                "Studio published content must use a separate registrable-style origin from Operly authentication"
            )


def _agent_runtime_status() -> dict[str, object]:
    enabled = AgentRuntimeSettings.from_environment().enabled
    if not enabled:
        return {
            "enabled": False,
            "configured": False,
            "provider": None,
            "model": None,
            "reason": "OPERLY_AGENT_RUNTIME_ENABLED is off",
        }
    try:
        route = InferenceRoute.from_environment()
    except AgentInferenceError as error:
        return {
            "enabled": True,
            "configured": False,
            "provider": None,
            "model": None,
            "reason": str(error),
        }
    return {
        "enabled": True,
        "configured": True,
        "provider": route.provider,
        "model": route.model_id,
        "reason": None,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    validate_runtime_configuration()
    await init_db()
    await bootstrap_admin()
    await discord_bot_lifecycle.start()
    await workflow_scheduler.start()
    await workflow_event_dispatcher.start()
    try:
        yield
    finally:
        await workflow_event_dispatcher.stop()
        await workflow_scheduler.stop()
        await discord_bot_lifecycle.stop()


app = FastAPI(title="OPERLY API", version="0.11.0-agent-runtime-1", lifespan=lifespan)

# AI ingress is still deployment-gated. When enabled, every model-proposed capability
# executes through the same live ExecutionContext + Kernel authority boundary as humans,
# workflows, MCP and plugin runtimes.
app.add_middleware(CSRFMiddleware)
app.add_middleware(AuthRequestSafetyMiddleware)
app.add_middleware(PublicEndpointSafetyMiddleware)

allowed_hosts = {"healthcheck.railway.app"}
public_host = urlparse(PUBLIC_BASE_URL).hostname
if public_host:
    allowed_hosts.add(public_host)
if STUDIO_PUBLIC_HOST:
    allowed_hosts.add(STUDIO_PUBLIC_HOST)
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
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "MCP-Protocol-Version",
        "Mcp-Method",
        "Mcp-Name",
        "X-Operly-Webhook-Signature",
        "X-Operly-Event-Id",
    ],
    expose_headers=["MCP-Protocol-Version", "WWW-Authenticate"],
)

app.include_router(session_router)
app.include_router(discord_auth_router)
app.include_router(personal_agent_runtime_router)
app.include_router(workspace_agent_runtime_router)
app.include_router(personal_connectors_router)
app.include_router(personal_tools_router)
app.include_router(workspace_os_router)
app.include_router(workspace_simple_router)
app.include_router(workspace_integrations_router)
app.include_router(workspace_tools_router)
app.include_router(artifact_router)
app.include_router(plugin_platform_router)
app.include_router(plugin_runtime_management_router)
app.include_router(plugin_hosted_public_router)
app.include_router(plugin_event_router)
app.include_router(plugin_webhook_management_router)
app.include_router(plugin_webhook_public_router)
app.include_router(capability_gateway_router)
app.include_router(runtime_egress_router)
app.include_router(agent_computer_router)
app.include_router(access_router)
app.include_router(mcp_router)
app.include_router(kernel_router)
app.include_router(studio_public_router)


@app.get("/api/health")
async def health():
    discord = discord_bot_lifecycle.status()
    agent = _agent_runtime_status()
    studio_hosting_configured = bool(
        os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip() and STUDIO_PUBLIC_HOST
    )
    return {
        "ok": True,
        "service": "operly",
        "runtime": "operly-kernel-v3+agent-runtime-1",
        "account_access": True,
        "personal_tools_enabled": True,
        "personal_google_connectors_enabled": True,
        "personal_tool_discovery": "search-then-describe",
        "personal_workflows_enabled": True,
        "workspace_os_enabled": True,
        "workspace_tools_enabled": True,
        "workspace_integrations_enabled": True,
        "artifact_store_enabled": True,
        "plugin_platform_enabled": True,
        "plugin_manifest_schema": "operly.plugin/v1",
        "capability_gateway_enabled": True,
        "runtime_egress_broker_enabled": True,
        "plugin_runtime_reconciliation_enabled": True,
        "workspace_plugin_hosting_enabled": True,
        "digital_webhook_ingress_enabled": True,
        "digital_event_delivery_enabled": True,
        "isolated_plugin_validation_enabled": True,
        "untrusted_plugin_execution_in_control_plane": False,
        "agent_computer_enabled": True,
        "workflow_engine_enabled": True,
        "workflow_scheduler": workflow_scheduler.status(),
        "workflow_event_dispatcher": workflow_event_dispatcher.status(),
        "workflow_event_triggers_enabled": True,
        "mcp_enabled": True,
        "mcp_protocol_version": "2026-07-28",
        "mcp_authority_model": "live-workspace-authority-plus-client-narrowing",
        "studio_hosting_configured": studio_hosting_configured,
        "studio_content_host": STUDIO_PUBLIC_HOST or None,
        "discord_bot_configured": discord["configured"],
        "discord_bot_running": discord["task_running"],
        "discord_agent_runtime": True,
        "discord_workspace_plugins": "same-request-local-workspace-runtime",
        "human_workflows_enabled": True,
        "kernel_runtime_enabled": True,
        "ai_runtime_enabled": bool(agent["enabled"] and agent["configured"]),
        "ai_runtime_gate_enabled": agent["enabled"],
        "ai_runtime_configured": agent["configured"],
        "ai_runtime_provider": agent["provider"],
        "ai_runtime_model": agent["model"],
        "ai_runtime_reason": agent["reason"],
        "ai_runtime_surfaces": ["personal_web", "workspace_web", "discord_dm", "discord_guild"],
        "ai_runtime_logging": "structured-stdout-operly-agent",
    }


@app.get("/api/rebuild-status")
async def rebuild_status():
    studio_hosting_configured = bool(
        os.getenv("OPERLY_DEPLOYMENT_ROOT", "").strip() and STUDIO_PUBLIC_HOST
    )
    agent = _agent_runtime_status()
    return {
        "state": "agent-runtime-1-testable",
        "deterministic_core": True,
        "account_access": True,
        "personal_tools_enabled": True,
        "personal_tool_discovery": "authorization-aware-search-then-describe",
        "personal_google_connector_ownership": "account",
        "personal_workflows_enabled": True,
        "workspace_os_enabled": True,
        "workspace_tools_enabled": True,
        "workspace_integrations_enabled": True,
        "artifact_store_enabled": True,
        "plugin_platform_enabled": True,
        "plugin_manifest_schema": "operly.plugin/v1",
        "plugin_runtime_policy": "isolated-workload-only",
        "capability_gateway": "short-lived-runtime-identity-plus-live-workspace-authority",
        "runtime_egress_broker": "grant-scoped-credential-injection",
        "plugin_runtime_reconciliation": "queued-health-verified-no-direct-health-override",
        "workspace_plugin_hosting": "validated-artifact-over-dedicated-content-origin",
        "digital_webhook_ingress_enabled": True,
        "digital_event_delivery_enabled": True,
        "isolated_plugin_validation_enabled": True,
        "agent_computer_enabled": True,
        "agent_computer_planner": "deterministic",
        "workflow_engine_enabled": True,
        "workflow_scheduler": workflow_scheduler.status(),
        "workflow_event_dispatcher": workflow_event_dispatcher.status(),
        "workflow_event_triggers": "kernel-semantic-events-same-scope-only",
        "mcp_enabled": True,
        "mcp_protocol_version": "2026-07-28",
        "mcp_gateway": "canonical-workspace-capability-runtime",
        "studio_hosting_configured": studio_hosting_configured,
        "studio_content_host": STUDIO_PUBLIC_HOST or None,
        "discord_bot": discord_bot_lifecycle.status(),
        "discord_agent_runtime": True,
        "discord_plugin_discovery": "request-local-installed-workspace-capabilities",
        "human_workflows_enabled": True,
        "kernel_runtime_enabled": True,
        "ai_runtime_enabled": bool(agent["enabled"] and agent["configured"]),
        "ai_runtime_gate_enabled": agent["enabled"],
        "ai_runtime_configured": agent["configured"],
        "ai_runtime_provider": agent["provider"],
        "ai_runtime_model": agent["model"],
        "message": (
            "Operly Runtime 1.0 now shares one objective/context/capability loop across Personal AI, Workspace AI, and Discord. "
            "Workspace Discord requests discover the same authorized installed plugin capabilities as Workspace tools."
        ),
    }


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
WEB_DIST = WEB_ROOT / "dist"
KNOWN_REACT_ROUTES = {
    "", "login", "signup", "join", "verify-email", "forgot-password", "reset-password",
    "onboarding", "account", "personal", "app", "privacy", "terms",
}


def react_shell(*, status_code: int = 200) -> HTMLResponse:
    index = WEB_DIST / "index.html"
    if not index.is_file():
        return HTMLResponse("<h1>OPERLY frontend build is unavailable.</h1>", status_code=503, headers={"Cache-Control": "no-store, max-age=0"})
    headers = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
    if status_code == 404:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(index.read_text(encoding="utf-8"), status_code=status_code, headers=headers)


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    route = path.strip("/")
    built_asset = confined_file(WEB_DIST, route) if route else None
    if built_asset is not None:
        return FileResponse(built_asset)
    if route in KNOWN_REACT_ROUTES or route == "channels" or route.startswith("channels/"):
        return react_shell()
    return react_shell(status_code=404)
