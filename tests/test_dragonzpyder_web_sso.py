"""Independent first-party web OIDC relying party with its own host-only session."""
import asyncio
import os
from contextlib import asynccontextmanager
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from apps.api.dependencies import get_db
from apps.api.federated_login import (
    router, dragonzpyder_web_login_available,
)
from packages.database.db import Base
from packages.database.federated_login_models import FederatedLoginAttempt

ISSUER = "https://api.dragonzpyder.xyz"
WEB = "https://dragonzpyder.xyz"
OPERLY = "https://operly.dragonzpyder.xyz"

def test_web_client_requires_distinct_registered_client(monkeypatch):
    with patch.dict(os.environ, {"OPERLY_DRAGONZPYDER_SSO_ENABLED": "0"}, clear=True):
        assert not dragonzpyder_web_login_available()
    # In this contract an incomplete issuer key must never expose web login.
    with patch.dict(os.environ, {
        "OPERLY_DRAGONZPYDER_SSO_ENABLED": "1",
        "OPERLY_DRAGONZPYDER_WEB_SSO_ENABLED": "1",
        "OPERLY_FEDERATED_DRAGONZPYDER_WEB_CLIENT_ID": "dragonzpyder-web",
        "PUBLIC_BASE_URL": OPERLY,
    }, clear=True):
        assert not dragonzpyder_web_login_available()

def test_web_callback_uses_separate_client_and_redirect(monkeypatch):
    # This test does not depend on any live provider or database.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_SSO_ENABLED", "1")
    monkeypatch.setenv("OPERLY_ENV", "test")
    monkeypatch.setenv("PUBLIC_BASE_URL", OPERLY)
    monkeypatch.setenv("OPERLY_FEDERATED_ISSUER", ISSUER)
    monkeypatch.setenv("OPERLY_FEDERATED_CLIENT_ID", "operly-web")
    monkeypatch.setenv("OPERLY_FEDERATED_RESOURCE_AUDIENCE", "operly-api")
    monkeypatch.setenv("OPERLY_FEDERATED_SIGNING_KID", "synthetic-kid")
    monkeypatch.setenv("OPERLY_FEDERATED_PUBLIC_KEY_PEM", pem)
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "synthetic-one-login-pepper-" + "z" * 48)
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_WEB_SSO_ENABLED", "1")
    monkeypatch.setenv("OPERLY_FEDERATED_DRAGONZPYDER_WEB_CLIENT_ID", "dragonzpyder-web")
    assert dragonzpyder_web_login_available()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async def run():
        import base64
        import hashlib
        import time
        import jwt
        import httpx
        from uuid import uuid4
        from sqlalchemy import select
        from packages.database.models import AppUser, AuthSession
        from packages.database.federated_identity_models import FederatedIdentityMap
        user_id = str(uuid4())
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                FederatedLoginAttempt.__table__, AppUser.__table__,
                AuthSession.__table__, FederatedIdentityMap.__table__,
            ])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            db.add(AppUser(id=user_id, email="web-test@example.test", display_name="Web Test", active=True))
            db.add(FederatedIdentityMap(
                issuer=ISSUER, subject=user_id, operly_user_id=user_id, status="active",
            ))
            await db.commit()
        async def db_session():
            async with maker() as db:
                yield db
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = db_session
        nonce_holder = {}
        class Issuer:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, uri, *, data, headers):
                assert uri == ISSUER + "/identity/v1/token"
                assert data["client_id"] == "dragonzpyder-web"
                assert data["redirect_uri"] == WEB + "/api/auth/dragonzpyder/web/callback"
                now = int(time.time())
                access = jwt.encode({
                    "iss": ISSUER, "sub": user_id, "aud": "operly-api",
                    "iat": now, "nbf": now, "exp": now + 300,
                    "jti": "synthetic-jti", "scope": "openid operly:access",
                    "token_use": "access", "typ": "at+jwt",
                }, key, algorithm="RS256", headers={"kid": "synthetic-kid", "typ": "at+jwt"})
                at_hash = base64.urlsafe_b64encode(
                    hashlib.sha256(access.encode("ascii")).digest()[:16]
                ).rstrip(b"=").decode("ascii")
                identity = jwt.encode({
                    "iss": ISSUER, "sub": user_id, "aud": "dragonzpyder-web",
                    "iat": now, "exp": now + 300,
                    "nonce": nonce_holder["nonce"], "at_hash": at_hash, "token_use": "id",
                }, key, algorithm="RS256", headers={"kid": "synthetic-kid", "typ": "JWT"})
                return httpx.Response(200, json={"access_token": access, "id_token": identity})
        monkeypatch.setattr("apps.api.federated_login.httpx.AsyncClient",
                            lambda **kwargs: Issuer())
        with TestClient(app, base_url=WEB) as client:
            result = client.get("/api/auth/dragonzpyder/web/start", follow_redirects=False)
            assert result.status_code == 302
            assert "__Host-operly_oidc" in result.headers["set-cookie"]
            params = parse_qs(urlsplit(result.headers["location"]).query)
            assert params["client_id"] == ["dragonzpyder-web"]
            assert params["redirect_uri"] == [WEB + "/api/auth/dragonzpyder/web/callback"]
            assert params["scope"] == ["openid operly:access"]
            assert params["code_challenge_method"] == ["S256"]
            nonce_holder["nonce"] = params["nonce"][0]
            # Cross-client callback must not consume a legitimate Web attempt.
            wrong = client.get(
                "/api/auth/dragonzpyder/callback?code=" + "c" * 64
                + "&state=" + params["state"][0], follow_redirects=False,
            )
            assert wrong.status_code in (400,403)
            callback_path = ("/api/auth/dragonzpyder/web/callback?code="
                             + "c" * 64 + "&state=" + params["state"][0])
            success = client.get(callback_path, follow_redirects=False)
            assert success.status_code == 303
            assert success.headers["location"] == "/app"
            assert "operly_session" in success.headers["set-cookie"]
            assert client.get(callback_path, follow_redirects=False).status_code in (400,403)
        async with maker() as db:
            sessions = (await db.execute(select(AuthSession))).scalars().all()
            assert len(sessions) == 1 and sessions[0].user_id == user_id
        await engine.dispose()
    asyncio.run(run())
