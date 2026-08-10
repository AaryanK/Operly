import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# Local and self-hosted launches should behave the same as CLI diagnostics.
# Existing process variables keep precedence, so managed deployments remain in
# control of their injected secrets and configuration.
load_dotenv(override=False)

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext, get_auth_context, get_db
from apps.api.operations_router import router as operations_router
from apps.api.studio_router import router as studio_router
from apps.api.dashboard_studio_router import router as dashboard_studio_router
from apps.api.application_builder_router import router as application_builder_router
from apps.api.agent_router import router as agent_router
from apps.api.custom_software_router import router as custom_software_router
from apps.api.architecture_pack_router import router as architecture_pack_router
from apps.api.coding_harness_router import router as coding_harness_router
from apps.api.csrf import CSRFMiddleware
from apps.api.security_headers import SecurityHeadersMiddleware
from apps.api.public_safety import PublicEndpointSafetyMiddleware
from apps.api.session import router as session_router
from apps.api.business import router as business_router
from apps.api.schemas import (
    ApprovalDecision,
    LoginInput,
    MemoryCreate,
    TaskCreate,
    TenantUpdate,
)
from apps.api.security import create_token, hash_password, verify_password
from packages.database.db import init_db, session_scope
from packages.database.models import (
    AppUser,
    Approval,
    DiscordGuild,
    Integration,
    Memory,
    Message,
    Task,
    Tenant,
    TenantMember,
)


async def bootstrap_admin() -> None:
    email = os.getenv("ADMIN_EMAIL", "admin@operly.local").strip().lower()
    password = os.getenv("ADMIN_PASSWORD")
    tenant_name = os.getenv("DEFAULT_TENANT_NAME", "My Business").strip()

    if not password:
        raise RuntimeError("ADMIN_PASSWORD is missing")

    async with session_scope() as db:
        user = await db.scalar(select(AppUser).where(AppUser.email == email))

        if user is None:
            user = AppUser(
                email=email,
                display_name="Owner",
                password_hash=hash_password(password),
            )
            db.add(user)
            await db.flush()

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

        db.add(
            TenantMember(
                tenant_id=tenant.id,
                user_id=user.id,
                role="owner",
            )
        )


def validate_runtime_configuration() -> None:
    if not os.getenv("SESSION_SECRET"):
        raise RuntimeError("SESSION_SECRET is missing")
    if production:
        if not public_base_url.startswith("https://"):
            raise RuntimeError("PUBLIC_BASE_URL must use HTTPS in production")
        if public_base_url == "https://operly.example":
            raise RuntimeError("PUBLIC_BASE_URL still uses the example value")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration()
    await init_db()
    await bootstrap_admin()
    yield


app = FastAPI(
    title="OPERLY API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(CSRFMiddleware)
app.add_middleware(PublicEndpointSafetyMiddleware)

public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
production = os.getenv("OPERLY_ENV", os.getenv("APP_ENV", "development")).lower() in {"production", "prod"}
deployed_commit_sha = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or os.getenv("SOURCE_VERSION") or "unknown").strip()[:64]
allowed_origins = [public_base_url]
if not production:
    allowed_origins.append("http://localhost:5173")
trusted_host = urlparse(public_base_url).hostname or "localhost"
railway_health_host = "healthcheck.railway.app"
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[trusted_host,railway_health_host] if production else [trusted_host,railway_health_host,"localhost","127.0.0.1","testserver"])
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "operly", "commit_sha": deployed_commit_sha}


@app.post("/api/auth/login", include_in_schema=False)
async def legacy_login_disabled():
    raise HTTPException(
        status_code=410,
        detail="Use /api/session/login",
    )


@app.get("/api/me")
async def me(auth: AuthContext = Depends(get_auth_context)):
    return {
        "user": {
            "id": auth.user.id,
            "email": auth.user.email,
            "display_name": auth.user.display_name,
        },
        "tenant": {
            "id": auth.tenant.id,
            "name": auth.tenant.name,
            "timezone": auth.tenant.timezone,
        },
        "role": auth.role,
    }


@app.get("/api/dashboard")
async def dashboard(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = auth.tenant.id

    message_count = await db.scalar(
        select(func.count(Message.id)).where(Message.tenant_id == tenant_id)
    )
    open_tasks = await db.scalar(
        select(func.count(Task.id)).where(
            Task.tenant_id == tenant_id,
            Task.status == "open",
        )
    )
    memory_count = await db.scalar(
        select(func.count(Memory.id)).where(Memory.tenant_id == tenant_id)
    )
    pending_approvals = await db.scalar(
        select(func.count(Approval.id)).where(
            Approval.tenant_id == tenant_id,
            Approval.status == "pending",
        )
    )

    recent = (
        await db.scalars(
            select(Message)
            .where(Message.tenant_id == tenant_id)
            .order_by(desc(Message.created_at))
            .limit(8)
        )
    ).all()

    return {
        "stats": {
            "messages": message_count or 0,
            "open_tasks": open_tasks or 0,
            "memories": memory_count or 0,
            "pending_approvals": pending_approvals or 0,
        },
        "recent_messages": [
            {
                "id": row.id,
                "author_name": row.author_name,
                "content": row.content,
                "is_bot": row.is_bot,
                "channel_id": str(row.channel_id),
                "created_at": row.created_at.isoformat(),
            }
            for row in recent
        ],
    }


@app.get("/api/messages")
async def list_messages(
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=250),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    query = select(Message).where(Message.tenant_id == auth.tenant.id)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Message.content.ilike(pattern),
                Message.author_name.ilike(pattern),
            )
        )

    rows = (
        await db.scalars(
            query.order_by(desc(Message.created_at)).limit(limit)
        )
    ).all()

    return [
        {
            "id": row.id,
            "message_id": str(row.message_id),
            "channel_id": str(row.channel_id),
            "author_name": row.author_name,
            "content": row.content,
            "is_bot": row.is_bot,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.get("/api/tasks")
async def list_tasks(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Task)
            .where(Task.tenant_id == auth.tenant.id)
            .order_by(desc(Task.created_at))
        )
    ).all()

    return [
        {
            "id": row.id,
            "title": row.title,
            "status": row.status,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/api/tasks")
async def create_task(
    payload: TaskCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = Task(
        tenant_id=auth.tenant.id,
        title=payload.title.strip(),
        due_at=payload.due_at,
        status="open",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "created_at": row.created_at.isoformat(),
    }


@app.patch("/api/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")

    row.status = "completed"
    await db.commit()
    return {"ok": True}


@app.get("/api/memories")
async def list_memories(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Memory)
            .where(Memory.tenant_id == auth.tenant.id)
            .order_by(desc(Memory.created_at))
        )
    ).all()

    return [
        {
            "id": row.id,
            "kind": row.kind,
            "content": row.content,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.post("/api/memories")
async def create_memory(
    payload: MemoryCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    row = Memory(
        tenant_id=auth.tenant.id,
        kind=payload.kind.strip() or "fact",
        content=payload.content.strip(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "id": row.id,
        "kind": row.kind,
        "content": row.content,
        "created_at": row.created_at.isoformat(),
    }


@app.get("/api/approvals")
async def list_approvals(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(Approval)
            .where(Approval.tenant_id == auth.tenant.id)
            .order_by(desc(Approval.created_at))
        )
    ).all()

    return [
        {
            "id": row.id,
            "action": row.action,
            "status": row.status,
            "details": json.loads(row.payload_json or "{}"),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@app.patch("/api/approvals/{approval_id}")
async def decide_approval(
    approval_id: str,
    payload: ApprovalDecision,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    if payload.status not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid decision")

    row = await db.scalar(
        select(Approval).where(
            Approval.id == approval_id,
            Approval.tenant_id == auth.tenant.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    row.status = payload.status
    await db.commit()
    return {"ok": True}


@app.get("/api/integrations")
async def integrations(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    guilds = (
        await db.scalars(
            select(DiscordGuild).where(
                DiscordGuild.tenant_id == auth.tenant.id,
                DiscordGuild.enabled.is_(True),
            )
        )
    ).all()

    connected = (
        await db.scalars(
            select(Integration).where(
                Integration.tenant_id == auth.tenant.id
            )
        )
    ).all()
    status_map = {row.provider: row.status for row in connected}

    return [
        {
            "provider": "discord",
            "label": "Discord",
            "status": "connected" if guilds else status_map.get("discord", "disconnected"),
            "detail": guilds[0].guild_name if guilds else None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "reminders", "workflow_triggers", "approvals", "controlled_solution_updates"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "whatsapp",
            "label": "WhatsApp",
            "status": status_map.get("whatsapp", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "reminders", "workflow_triggers", "controlled_solution_updates"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "instagram",
            "label": "Instagram",
            "status": status_map.get("instagram", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "facebook",
            "label": "Facebook",
            "status": status_map.get("facebook", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
        {
            "provider": "x",
            "label": "X",
            "status": status_map.get("x", "coming_soon"),
            "detail": None,
            "role": "event_and_action_channel",
            "capabilities": ["messages", "workflow_triggers", "publishing"],
            "frontendAuthority": "controlled_updates_only",
        },
    ]


@app.patch("/api/settings/tenant")
async def update_tenant(
    payload: TenantUpdate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    tenant = await db.get(Tenant, auth.tenant.id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    tenant.name = payload.name.strip()
    tenant.timezone = payload.timezone.strip() or "UTC"
    await db.commit()

    return {
        "id": tenant.id,
        "name": tenant.name,
        "timezone": tenant.timezone,
    }

app.include_router(business_router)
app.include_router(session_router)
app.include_router(agent_router)
app.include_router(operations_router)
app.include_router(studio_router)
app.include_router(dashboard_studio_router)
app.include_router(application_builder_router)
app.include_router(custom_software_router)
app.include_router(architecture_pack_router)
app.include_router(coding_harness_router)

WEB_STATIC = Path(__file__).resolve().parents[1] / "web" / "static"

app.mount(
    "/static",
    StaticFiles(directory=WEB_STATIC),
    name="static",
)

@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    requested = WEB_STATIC / path
    if path and requested.is_file():
        return FileResponse(requested)
    return FileResponse(WEB_STATIC / "index.html")
