"""DragonZpyder SSO client tests with isolated synthetic users and signing keys."""
import asyncio
import base64
import hashlib
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import get_db
from apps.api.federated_login import (
    SSORejected, router, verify_callback_tokens, federated_login_available,
)
from packages.database.db import Base
from packages.database.models import AppUser, AuthSession, Tenant, TenantMember
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.database.federated_login_models import FederatedLoginAttempt

ISSUER = "https://api.dragonzpyder.xyz"
OPERLY_URL = "https://operly.dragonzpyder.xyz"
CLIENT = "operly-web"
AUDIENCE = "operly-api"
SUBJECT = "existing-operly-uuid"


@pytest.fixture
def signing():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return key, pem


def configure(monkeypatch, pem):
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_SSO_ENABLED", "1")
    monkeypatch.setenv("OPERLY_ENV", "test")
    monkeypatch.setenv("PUBLIC_BASE_URL", OPERLY_URL)
    monkeypatch.setenv("OPERLY_FEDERATED_ISSUER", ISSUER)
    monkeypatch.setenv("OPERLY_FEDERATED_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OPERLY_FEDERATED_RESOURCE_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("OPERLY_FEDERATED_SIGNING_KID", "synthetic-kid")
    monkeypatch.setenv("OPERLY_FEDERATED_PUBLIC_KEY_PEM", pem)
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", "test-only-auth-token-pepper-" + "z" * 40)


def signed_pair(key, nonce, *, subject=SUBJECT, id_audience=CLIENT, access_audience=AUDIENCE):
    now = int(time.time())
    access = jwt.encode({
        "iss": ISSUER, "sub": subject, "aud": access_audience,
        "iat": now, "nbf": now, "exp": now + 300,
        "jti": "synthetic-jti", "scope": "openid operly:access",
        "token_use": "access", "typ": "at+jwt",
    }, key, algorithm="RS256", headers={"kid": "synthetic-kid", "typ": "at+jwt"})
    at_hash = base64.urlsafe_b64encode(
        hashlib.sha256(access.encode("ascii")).digest()[:16]
    ).rstrip(b"=").decode("ascii")
    id_token = jwt.encode({
        "iss": ISSUER, "sub": subject, "aud": id_audience,
        "iat": now, "exp": now + 300, "nonce": nonce,
        "at_hash": at_hash, "token_use": "id",
    }, key, algorithm="RS256", headers={"kid": "synthetic-kid", "typ": "JWT"})
    return access, id_token


def test_reject_bad_nonce_wrong_client_and_subject(signing):
    key, pem = signing
    public = key.public_key()
    nonce = "n" * 40
    access, identity = signed_pair(key, nonce)
    common = dict(issuer=ISSUER, client_id=CLIENT, audience=AUDIENCE,
                  key_id="synthetic-kid", public_key=public)
    assert verify_callback_tokens(
        access_token=access, id_token=identity, nonce=nonce, **common
    ) == SUBJECT
    with pytest.raises(SSORejected):
        verify_callback_tokens(
            access_token=access, id_token=identity, nonce="wrong" * 8, **common
        )
    with pytest.raises(SSORejected):
        verify_callback_tokens(
            access_token=access, id_token=signed_pair(key, nonce, id_audience="other-client")[1],
            nonce=nonce, **common
        )
    with pytest.raises(SSORejected):
        verify_callback_tokens(
            access_token=access, id_token=signed_pair(key, nonce, subject="other-user")[1],
            nonce=nonce, **common
        )
    other = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    with pytest.raises(SSORejected):
        verify_callback_tokens(
            access_token=access, id_token=signed_pair(other, nonce)[1], nonce=nonce, **common
        )


def test_readiness_gate_requires_complete_production_config(monkeypatch, signing):
    _, pem = signing
    configure(monkeypatch, pem)
    assert federated_login_available()
    monkeypatch.setenv("OPERLY_ENV", "production")
    monkeypatch.delenv("OPERLY_DRAGONZPYDER_SSO_PRODUCTION", raising=False)
    assert not federated_login_available()
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_SSO_PRODUCTION", "1")
    assert federated_login_available()
    monkeypatch.setenv("OPERLY_FEDERATED_CLIENT_ID", "clïent")
    assert not federated_login_available()
    monkeypatch.setenv("OPERLY_FEDERATED_CLIENT_ID", CLIENT)
    monkeypatch.setenv("OPERLY_FEDERATED_PUBLIC_KEY_PEM", "invalid")
    assert not federated_login_available()


def test_login_routes_are_invisible_until_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("OPERLY_DRAGONZPYDER_SSO_ENABLED", raising=False)
    assert not federated_login_available()
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/auth/dragonzpyder/start").status_code == 404
        assert client.get("/api/auth/dragonzpyder/callback").status_code == 404


@pytest.mark.asyncio
async def test_browser_callback_maps_existing_user_and_establishes_operly_session(monkeypatch, signing):
    key, pem = signing
    configure(monkeypatch, pem)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [AppUser.__table__, Tenant.__table__, TenantMember.__table__,
              AuthSession.__table__, FederatedIdentityMap.__table__,
              FederatedLoginAttempt.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(AppUser(id=SUBJECT, email="existing@example.test", display_name="Existing", active=True))
        db.add(FederatedIdentityMap(issuer=ISSUER, subject=SUBJECT, operly_user_id=SUBJECT,
                                    status="active"))
        await db.commit()

    async def session_provider():
        async with maker() as db:
            yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = session_provider
    state_nonce = {}
    class FakeIssuerClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def post(self, uri, *, data, headers):
            assert uri == ISSUER + "/identity/v1/token"
            assert data["grant_type"] == "authorization_code"
            assert data["code"] == "c" * 64
            assert data["client_id"] == CLIENT
            assert data["redirect_uri"] == OPERLY_URL + "/api/auth/dragonzpyder/callback"
            access, identity = signed_pair(key, state_nonce["nonce"])
            return httpx.Response(200, json={"access_token": access, "id_token": identity})
    monkeypatch.setattr("apps.api.federated_login.httpx.AsyncClient",
                        lambda **kwargs: FakeIssuerClient())
    with TestClient(app, base_url=OPERLY_URL) as client:
        started = client.get("/api/auth/dragonzpyder/start", follow_redirects=False)
        assert started.status_code == 302
        assert "__Host-operly_oidc" in started.headers["set-cookie"]
        assert "Secure" in started.headers["set-cookie"]
        target = urlsplit(started.headers["location"])
        assert target.scheme == "https"
        assert target.netloc == "api.dragonzpyder.xyz"
        data = parse_qs(target.query)
        assert data["code_challenge_method"] == ["S256"]
        assert data["scope"] == ["openid operly:access"]
        state_nonce["nonce"] = data["nonce"][0]
        callback = client.get(
            "/api/auth/dragonzpyder/callback?state=" + data["state"][0]
            + "&code=" + "c" * 64, follow_redirects=False
        )
        assert callback.status_code == 303
        assert callback.headers["location"] == "/"
        assert "operly_session" in callback.headers["set-cookie"]
        replay = client.get(
            "/api/auth/dragonzpyder/callback?state=" + data["state"][0]
            + "&code=synthetic-code", follow_redirects=False
        )
        assert replay.status_code in (400, 403)
    async with maker() as db:
        assert await db.scalar(select(AuthSession.user_id)) == SUBJECT
        attempt = await db.scalar(select(FederatedLoginAttempt))
        assert attempt.consumed_at is not None
    await engine.dispose()
