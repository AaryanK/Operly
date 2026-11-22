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
    from apps.api.federated_login import _settings
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
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                FederatedLoginAttempt.__table__,
            ])
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async def db_session():
            async with maker() as db:
                yield db
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = db_session
        with TestClient(app, base_url=WEB) as client:
            result = client.get("/api/auth/dragonzpyder/web/start", follow_redirects=False)
            assert result.status_code == 302
            assert "__Host-operly_oidc" in result.headers["set-cookie"]
            params = parse_qs(urlsplit(result.headers["location"]).query)
            assert params["client_id"] == ["dragonzpyder-web"]
            assert params["redirect_uri"] == [WEB + "/api/auth/dragonzpyder/web/callback"]
            assert params["scope"] == ["openid operly:access"]
            assert params["code_challenge_method"] == ["S256"]
            wrong = client.get("/api/auth/dragonzpyder/callback?code=bad&state=bad")
            assert wrong.status_code in (400,403)
        await engine.dispose()
    asyncio.run(run())
