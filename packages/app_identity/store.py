"""Application-data store for generated-app users, invitations and sessions."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.app_identity.contracts import InvitationCreateRequest, RegisterRequest
from packages.app_identity.crypto import (
    IdentityCredentialError,
    hash_password,
    hash_token,
    new_token,
    normalize_email,
    validate_password,
    verify_password,
)
from packages.relational_data.store import configured_app_data_url


class AppIdentityError(ValueError):
    pass


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _namespace(workspace_id: str, application_id: str) -> str:
    raw = f"{workspace_id}\0{application_id}".encode("utf-8")
    return "aid_" + hashlib.sha256(raw).hexdigest()[:20]


def _physical(workspace_id: str, application_id: str, suffix: str) -> str:
    return f"{_namespace(workspace_id, application_id)}__{suffix}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class AppIdentityStore:
    """Own generated-app credentials without mixing them with Operly account auth."""

    def __init__(self, database_url: str | None = None, *, token_secret: str | None = None):
        self.database_url = configured_app_data_url(database_url)
        self.engine: AsyncEngine = create_async_engine(self.database_url, future=True)
        self.token_secret = token_secret
        self._initialized: set[tuple[str, str]] = set()

    async def close(self) -> None:
        await self.engine.dispose()

    async def initialize(self, workspace_id: str, application_id: str) -> None:
        key = (workspace_id, application_id)
        if key in self._initialized:
            return
        users = _physical(workspace_id, application_id, "users")
        sessions = _physical(workspace_id, application_id, "sessions")
        invites = _physical(workspace_id, application_id, "invites")
        async with self.engine.begin() as conn:
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {_q(users)} (
                  id VARCHAR(120) PRIMARY KEY,
                  email VARCHAR(320) NOT NULL UNIQUE,
                  password_hash TEXT NOT NULL,
                  display_name VARCHAR(200) NOT NULL,
                  role VARCHAR(40) NOT NULL,
                  entity_kind VARCHAR(30),
                  entity_id VARCHAR(120),
                  status VARCHAR(30) NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {_q(sessions)} (
                  id VARCHAR(120) PRIMARY KEY,
                  user_id VARCHAR(120) NOT NULL,
                  token_hash VARCHAR(64) NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  revoked_at TEXT,
                  created_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {_q(invites)} (
                  id VARCHAR(120) PRIMARY KEY,
                  email VARCHAR(320) NOT NULL,
                  display_name VARCHAR(200) NOT NULL,
                  role VARCHAR(40) NOT NULL,
                  entity_kind VARCHAR(30),
                  entity_id VARCHAR(120),
                  token_hash VARCHAR(64) NOT NULL UNIQUE,
                  expires_at TEXT NOT NULL,
                  consumed_at TEXT,
                  created_at TEXT NOT NULL
                )
            """))
        self._initialized.add(key)

    def _session_seconds(self) -> int:
        import os
        try:
            configured = int(os.getenv("OPERLY_APP_IDENTITY_SESSION_SECONDS", "604800"))
        except ValueError:
            configured = 604800
        return max(300, min(configured, 2_592_000))

    async def _create_session(self, conn, workspace_id: str, application_id: str, user_id: str) -> str:
        token = new_token()
        now = _now()
        await conn.execute(
            text(
                f"INSERT INTO {_q(_physical(workspace_id, application_id, 'sessions'))} "
                "(id,user_id,token_hash,expires_at,revoked_at,created_at,last_seen_at) "
                "VALUES (:id,:user_id,:token_hash,:expires_at,NULL,:created_at,:last_seen_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "token_hash": hash_token(token, purpose="session", secret=self.token_secret),
                "expires_at": _iso(now + timedelta(seconds=self._session_seconds())),
                "created_at": _iso(now),
                "last_seen_at": _iso(now),
            },
        )
        return token

    @staticmethod
    def _public_user(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "email": row["email"],
            "displayName": row["display_name"],
            "role": row["role"],
            "entityKind": row.get("entity_kind"),
            "entityId": row.get("entity_id"),
            "status": row["status"],
            "createdAt": row["created_at"],
        }

    async def register(self, workspace_id: str, application_id: str, request: RegisterRequest) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        try:
            email = normalize_email(request.email)
            validate_password(request.password, email=email)
        except IdentityCredentialError as error:
            raise AppIdentityError(str(error)) from error
        now = _iso(_now())
        user_id = str(uuid.uuid4())
        try:
            async with self.engine.begin() as conn:
                await conn.execute(
                    text(
                        f"INSERT INTO {_q(_physical(workspace_id, application_id, 'users'))} "
                        "(id,email,password_hash,display_name,role,entity_kind,entity_id,status,created_at,updated_at) "
                        "VALUES (:id,:email,:password_hash,:display_name,'user',NULL,NULL,'active',:created_at,:updated_at)"
                    ),
                    {
                        "id": user_id,
                        "email": email,
                        "password_hash": hash_password(request.password),
                        "display_name": request.displayName.strip(),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                session = await self._create_session(conn, workspace_id, application_id, user_id)
        except IntegrityError as error:
            raise AppIdentityError("An account with that email already exists") from error
        user = await self.get_user(workspace_id, application_id, user_id)
        return {"user": user, "sessionToken": session}

    async def login(self, workspace_id: str, application_id: str, email_value: str, password: str) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        try:
            email = normalize_email(email_value)
        except IdentityCredentialError as error:
            raise AppIdentityError("Invalid email or password") from error
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT * FROM {_q(_physical(workspace_id, application_id, 'users'))} WHERE email=:email"),
                    {"email": email},
                )
            ).mappings().first()
            if row is None or row["status"] != "active" or not verify_password(password, row["password_hash"]):
                raise AppIdentityError("Invalid email or password")
            token = await self._create_session(conn, workspace_id, application_id, row["id"])
            user = self._public_user(dict(row))
        return {"user": user, "sessionToken": token}

    async def verify_session(self, workspace_id: str, application_id: str, token: str) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        token_digest = hash_token(token, purpose="session", secret=self.token_secret)
        sessions = _physical(workspace_id, application_id, "sessions")
        users = _physical(workspace_id, application_id, "users")
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT s.id AS session_id,s.expires_at,s.revoked_at,u.* "
                        f"FROM {_q(sessions)} s JOIN {_q(users)} u ON u.id=s.user_id "
                        "WHERE s.token_hash=:token_hash"
                    ),
                    {"token_hash": token_digest},
                )
            ).mappings().first()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["status"] != "active"
                or _parse(row["expires_at"]) <= _now()
            ):
                raise AppIdentityError("Session is no longer valid")
            await conn.execute(
                text(f"UPDATE {_q(sessions)} SET last_seen_at=:now WHERE id=:id"),
                {"now": _iso(_now()), "id": row["session_id"]},
            )
        return {"user": self._public_user(dict(row)), "expiresAt": row["expires_at"]}

    async def logout(self, workspace_id: str, application_id: str, token: str) -> dict[str, bool]:
        await self.initialize(workspace_id, application_id)
        digest = hash_token(token, purpose="session", secret=self.token_secret)
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    f"UPDATE {_q(_physical(workspace_id, application_id, 'sessions'))} "
                    "SET revoked_at=:now WHERE token_hash=:token_hash AND revoked_at IS NULL"
                ),
                {"now": _iso(_now()), "token_hash": digest},
            )
        return {"ok": True}

    async def get_user(self, workspace_id: str, application_id: str, user_id: str) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT * FROM {_q(_physical(workspace_id, application_id, 'users'))} WHERE id=:id"),
                    {"id": user_id},
                )
            ).mappings().first()
        if row is None:
            raise AppIdentityError("App user not found")
        return self._public_user(dict(row))

    async def list_users(self, workspace_id: str, application_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        await self.initialize(workspace_id, application_id)
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT * FROM {_q(_physical(workspace_id, application_id, 'users'))} "
                        "ORDER BY created_at,id LIMIT :limit"
                    ),
                    {"limit": max(1, min(limit, 500))},
                )
            ).mappings().all()
        return [self._public_user(dict(row)) for row in rows]

    async def create_invitation(
        self,
        workspace_id: str,
        application_id: str,
        request: InvitationCreateRequest,
    ) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        try:
            email = normalize_email(request.email)
        except IdentityCredentialError as error:
            raise AppIdentityError(str(error)) from error
        token = new_token()
        now = _now()
        invite_id = str(uuid.uuid4())
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(
                    text(f"SELECT id FROM {_q(_physical(workspace_id, application_id, 'users'))} WHERE email=:email"),
                    {"email": email},
                )
            ).first()
            if existing is not None:
                raise AppIdentityError("An app user with that email already exists")
            await conn.execute(
                text(
                    f"INSERT INTO {_q(_physical(workspace_id, application_id, 'invites'))} "
                    "(id,email,display_name,role,entity_kind,entity_id,token_hash,expires_at,consumed_at,created_at) "
                    "VALUES (:id,:email,:display_name,:role,:entity_kind,:entity_id,:token_hash,:expires_at,NULL,:created_at)"
                ),
                {
                    "id": invite_id,
                    "email": email,
                    "display_name": request.displayName.strip(),
                    "role": request.role,
                    "entity_kind": request.entityKind,
                    "entity_id": request.entityId,
                    "token_hash": hash_token(token, purpose="invite", secret=self.token_secret),
                    "expires_at": _iso(now + timedelta(seconds=request.expiresInSeconds)),
                    "created_at": _iso(now),
                },
            )
        return {
            "id": invite_id,
            "email": email,
            "role": request.role,
            "entityKind": request.entityKind,
            "entityId": request.entityId,
            "expiresAt": _iso(now + timedelta(seconds=request.expiresInSeconds)),
            "invitationToken": token,
        }

    async def accept_invitation(
        self,
        workspace_id: str,
        application_id: str,
        invitation_token: str,
        password: str,
    ) -> dict[str, Any]:
        await self.initialize(workspace_id, application_id)
        digest = hash_token(invitation_token, purpose="invite", secret=self.token_secret)
        invites = _physical(workspace_id, application_id, "invites")
        users = _physical(workspace_id, application_id, "users")
        async with self.engine.begin() as conn:
            invite = (
                await conn.execute(text(f"SELECT * FROM {_q(invites)} WHERE token_hash=:token_hash"), {"token_hash": digest})
            ).mappings().first()
            if invite is None or invite["consumed_at"] is not None or _parse(invite["expires_at"]) <= _now():
                raise AppIdentityError("Invitation is no longer valid")
            try:
                validate_password(password, email=invite["email"])
            except IdentityCredentialError as error:
                raise AppIdentityError(str(error)) from error
            existing = (
                await conn.execute(text(f"SELECT id FROM {_q(users)} WHERE email=:email"), {"email": invite["email"]})
            ).first()
            if existing is not None:
                raise AppIdentityError("An app user with that email already exists")
            user_id = str(uuid.uuid4())
            now = _iso(_now())
            await conn.execute(
                text(
                    f"INSERT INTO {_q(users)} "
                    "(id,email,password_hash,display_name,role,entity_kind,entity_id,status,created_at,updated_at) "
                    "VALUES (:id,:email,:password_hash,:display_name,:role,:entity_kind,:entity_id,'active',:created_at,:updated_at)"
                ),
                {
                    "id": user_id,
                    "email": invite["email"],
                    "password_hash": hash_password(password),
                    "display_name": invite["display_name"],
                    "role": invite["role"],
                    "entity_kind": invite["entity_kind"],
                    "entity_id": invite["entity_id"],
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await conn.execute(text(f"UPDATE {_q(invites)} SET consumed_at=:now WHERE id=:id"), {"now": now, "id": invite["id"]})
            session = await self._create_session(conn, workspace_id, application_id, user_id)
        user = await self.get_user(workspace_id, application_id, user_id)
        return {"user": user, "sessionToken": session}


__all__ = ["AppIdentityError", "AppIdentityStore"]
