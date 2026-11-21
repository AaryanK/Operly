"""Real two-repository, HTTP-level SSO acceptance with disposable databases.

No mocks for password authentication, PKCE, authorization-code consumption,
JWT signatures, ID-token nonce/at_hash, Operly mapping or local sessions.
Only the network transport is in-process; the same FastAPI endpoints run.
DRAGONZPYDER_IDENTITY_TEST_DSN must target a disposable identity_test DB.
"""
from __future__ import annotations

import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from types import SimpleNamespace

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.dependencies import get_auth_context, get_db, AuthContext
from apps.api.federated_login import router as operly_sso_router
import apps.api.federated_login as operly_federation
from packages.database.db import Base
from packages.database.federated_identity_models import FederatedIdentityMap
from packages.database.federated_login_models import FederatedLoginAttempt
from packages.database.models import AppUser, Tenant, TenantMember, AuthSession

ISSUER = "https://api.dragonzpyder.xyz"
OPERLY = "https://operly.dragonzpyder.xyz"
CALLBACK = OPERLY + "/api/auth/dragonzpyder/callback"


@pytest.mark.asyncio
async def test_full_cross_repo_browser_sso_lifecycle(monkeypatch):
    dsn = os.getenv("DRAGONZPYDER_IDENTITY_TEST_DSN", "")
    api_path = os.getenv("DRAGONZPYDER_IDENTITY_API_PATH", "")
    if not dsn or not api_path:
        pytest.skip("Requires checked-out DragonZpyder API and disposable PostgreSQL")
    from urllib.parse import urlsplit as split_dsn
    if split_dsn(dsn).path != "/identity_test":
        raise RuntimeError("Refusing to run cross-repo SSO test outside identity_test DB")
    dzroot = Path(api_path).resolve()
    if not (dzroot / "identity" / "schema_staging.sql").is_file():
        raise RuntimeError("DragonZpyder source checkout not found")
    sys.path.insert(0, str(dzroot))
    import identity.http_bridge as bridge
    import identity.login_http as login_http
    import identity.browser_authorize as browser
    from identity.router import router as metadata_router
    from identity.passwords import hash_password

    account_id = str(uuid4())  # canonical UUID is unchanged across both systems
    tenant_id = str(uuid4())
    email = "synthetic-sso@example.test"
    password = "CI-Ephemeral-Passphrase-Only!" + secrets.token_hex(8)  # pragma: allowlist secret -- random test input
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = signing_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_ISSUER", ISSUER)
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_HTTP_ENABLED", "1")
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_JWKS_ENABLED", "1")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.delenv("DRAGONZPYDER_IDENTITY_ALLOW_PRODUCTION", raising=False)
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_SIGNING_KEY_PEM", pem)
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_SIGNING_KID", "ci-only-sso-key")
    monkeypatch.setenv("DRAGONZPYDER_IDENTITY_GRANT_HASH_KEY", secrets.token_urlsafe(48))
    monkeypatch.setenv("OPERLY_DRAGONZPYDER_SSO_ENABLED", "1")
    monkeypatch.setenv("OPERLY_ENV", "test")
    monkeypatch.setenv("PUBLIC_BASE_URL", OPERLY)
    monkeypatch.setenv("OPERLY_FEDERATED_ISSUER", ISSUER)
    monkeypatch.setenv("OPERLY_FEDERATED_CLIENT_ID", "operly-web")
    monkeypatch.setenv("OPERLY_FEDERATED_RESOURCE_AUDIENCE", "operly-api")
    monkeypatch.setenv("OPERLY_FEDERATED_SIGNING_KID", "ci-only-sso-key")
    monkeypatch.setenv("OPERLY_FEDERATED_PUBLIC_KEY_PEM", public)
    monkeypatch.setenv("AUTH_TOKEN_PEPPER", secrets.token_urlsafe(48))

    admin = await asyncpg.connect(dsn)
    schema = "synthetic_sso_" + uuid4().hex
    await admin.execute(f"CREATE SCHEMA {schema}")
    operly_db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        await admin.execute(f"SET search_path TO {schema}")
        for filename in ("schema_staging.sql", "schema_sessions_staging.sql",
                         "schema_oauth_staging.sql", "schema_login_staging.sql"):
            await admin.execute((dzroot / "identity" / filename).read_text("utf-8"))
        # Safely simulate activation *only* inside this randomly named disposable
        # schema. The real migration still prohibits ACTIVE status everywhere.
        await admin.execute(
            "ALTER TABLE identity_accounts DROP CONSTRAINT identity_account_pending_or_disabled"
        )
        await admin.execute(
            """INSERT INTO identity_accounts
               (subject,source_operly_user_id,email,password_hash,display_name,
                source_active,status,email_verified_at)
               VALUES ($1,$1,$2,$3,'Synthetic',TRUE,'active',now())""",
            account_id, email, hash_password(password),
        )
        await admin.execute(
            """INSERT INTO identity_oauth_clients
               (client_id,redirect_uri,audience,allowed_scope,enabled)
               VALUES ('operly-web',$1,'operly-api','openid operly:access',TRUE)""",
            CALLBACK,
        )

        @asynccontextmanager
        async def identity_connection():
            conn = await asyncpg.connect(dsn, server_settings={"search_path": schema})
            try:
                yield conn
            finally:
                await conn.close()

        # All real DragonZpyder identity routes, with only DB connection
        # scoped to the isolated schema (no real account or network access).
        monkeypatch.setattr(bridge, "_identity_db", identity_connection)
        monkeypatch.setattr(login_http, "_identity_db", identity_connection)
        dz_app = FastAPI()
        dz_app.include_router(bridge.router)
        dz_app.include_router(login_http.router)
        dz_app.include_router(browser.router)
        dz_app.include_router(metadata_router)

        tables = [AppUser.__table__, Tenant.__table__, TenantMember.__table__,
                  AuthSession.__table__, FederatedIdentityMap.__table__,
                  FederatedLoginAttempt.__table__]
        async with operly_db.begin() as c:
            await c.run_sync(Base.metadata.create_all, tables=tables)
        session_factory = async_sessionmaker(operly_db, expire_on_commit=False)
        async with session_factory() as db:
            db.add(AppUser(id=account_id, email=email, display_name="Existing", active=True))
            db.add(Tenant(id=tenant_id, name="Existing Workspace"))
            db.add(TenantMember(tenant_id=tenant_id, user_id=account_id, role="owner"))
            db.add(FederatedIdentityMap(
                issuer=ISSUER, subject=account_id, operly_user_id=account_id,
                status="active",
            ))
            await db.commit()

        async def operly_session():
            async with session_factory() as session:
                yield session

        operly_app = FastAPI()
        operly_app.include_router(operly_sso_router)
        operly_app.dependency_overrides[get_db] = operly_session

        @operly_app.get("/test/private")
        async def private_workspace(auth: AuthContext = Depends(get_auth_context)):
            return {"subject": auth.user.id, "tenant": auth.tenant.id, "role": auth.role}

        # Replace only the *network* transport that Operly uses for its
        # backend code exchange. DragonZpyder still verifies real DB code
        # and issues real signed tokens, not synthetic shortcut responses.
        real_httpx = httpx
        def issuer_http_client(**kwargs):
            return real_httpx.AsyncClient(
                transport=real_httpx.ASGITransport(app=dz_app),
                base_url=ISSUER, **kwargs,
            )
        monkeypatch.setattr(operly_federation, "httpx",
                            SimpleNamespace(AsyncClient=issuer_http_client,
                                            HTTPError=real_httpx.HTTPError))

        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=operly_app),
                              base_url=OPERLY, follow_redirects=False) as operly,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=dz_app),
                              base_url=ISSUER, follow_redirects=False) as identity,
        ):
            discovery = await identity.get("/.well-known/openid-configuration")
            assert discovery.status_code == 200
            assert discovery.json()["issuer"] == ISSUER
            assert discovery.json()["authorization_endpoint"] == ISSUER + "/identity/v1/authorize"
            assert (await identity.get("/.well-known/jwks.json")).status_code == 200

            start = await operly.get("/api/auth/dragonzpyder/start")
            assert start.status_code == 302
            authorize_url = start.headers["location"]
            params = parse_qs(urlsplit(authorize_url).query)
            assert params["scope"] == ["openid operly:access"]
            assert params["code_challenge_method"] == ["S256"]
            assert params["redirect_uri"] == [CALLBACK]
            assert "__Host-operly_oidc" in operly.cookies

            before_login = await identity.get(authorize_url)
            assert before_login.status_code == 200
            assert 'id="login-form"' in before_login.text
            assert 'id="consent" hidden' in before_login.text
            no_login_grant = await identity.post(
                "/identity/v1/authorize", json={k:v[0] for k,v in params.items()},
                headers={"Origin": ISSUER, "Accept": "application/json",
                         "x-dz-csrf": "not-a-session"},
            )
            assert no_login_grant.status_code == 403
            wrong_password = await identity.post(
                "/identity/v1/password/login",
                json={"email": email, "password": "incorrect"},  # pragma: allowlist secret -- invalid test credential
                headers={"Origin": ISSUER},
            )
            assert wrong_password.status_code == 401

            login = await identity.post(
                "/identity/v1/password/login",
                json={"email": email, "password": password},
                headers={"Origin": ISSUER},
            )
            assert login.status_code == 200, login.text
            assert "__Host-dz_identity" in identity.cookies
            assert "__Host-dz_identity" not in operly.cookies
            displayed = await identity.get(authorize_url)
            assert displayed.status_code == 200
            assert 'id="consent" ' in displayed.text
            assert 'id="consent" hidden' not in displayed.text

            wrong_csrf = await identity.post(
                "/identity/v1/authorize", json={k:v[0] for k,v in params.items()},
                headers={"Origin": ISSUER, "x-dz-csrf": "wrong", "Accept": "application/json"},
            )
            assert wrong_csrf.status_code == 403
            consent = await identity.post(
                "/identity/v1/authorize", json={k:v[0] for k,v in params.items()},
                headers={"Origin": ISSUER,
                         "x-dz-csrf": identity.cookies["__Host-dz_csrf"],
                         "Accept": "application/json"},
            )
            assert consent.status_code == 200, consent.text
            callback_url = consent.json()["redirect_url"]
            assert callback_url.startswith(CALLBACK + "?")
            callback_query = parse_qs(urlsplit(callback_url).query)
            assert callback_query["state"] == params["state"]
            assert "id_token" not in consent.json()
            assert "access_token" not in consent.json()

            # A forged state cannot establish a local session.
            forged = await operly.get(CALLBACK + "?code=" + callback_query["code"][0]
                                      + "&state=" + ("forged" * 11))
            assert forged.status_code == 403
            assert "operly_session" not in operly.cookies

            # Actual Operly callback exchanges code with DragonZpyder ASGI
            # endpoint and verifies signed ID/access tokens, nonce and at_hash.
            complete = await operly.get(callback_url)
            assert complete.status_code == 303, complete.text
            assert complete.headers["location"] == "/"
            assert "operly_session" in operly.cookies
            assert "operly_session" not in identity.cookies
            workspace = await operly.get("/test/private")
            assert workspace.status_code == 200, workspace.text
            assert workspace.json() == {
                "subject": account_id, "tenant": tenant_id, "role": "owner",
            }

            replay = await operly.get(callback_url)
            assert replay.status_code in (400, 403)
            async with identity_connection() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM identity_authorization_codes WHERE consumed_at IS NOT NULL"
                ) == 1
                assert await conn.fetchval(
                    "SELECT count(*) FROM identity_browser_sessions WHERE revoked_at IS NULL"
                ) == 1

            # Chromium uses an independent Operly session; close previous
            # SQLite reader before the browser callback creates a new one.
            await _chromium_browser_acceptance(
                operly_app=operly_app, dz_app=dz_app,
                email=email, password=password,
                account_id=account_id, tenant_id=tenant_id,
            )

            async with session_factory() as db:
                sessions = (await db.scalars(select(AuthSession))).all()
                assert len(sessions) == 2
                assert all(s.user_id == account_id for s in sessions)
                assert all(s.tenant_id == tenant_id for s in sessions)


                # Live membership is rechecked even with a valid browser session.
                member = await db.scalar(select(TenantMember))
                await db.delete(member)
                await db.commit()
            denied_workspace = await operly.get("/test/private")
            assert denied_workspace.status_code == 403
    finally:
        await operly_db.dispose()
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


async def _chromium_browser_acceptance(*, operly_app, dz_app, email, password,
                                       account_id, tenant_id):
    """Real Chromium page / JS / origin + cookie semantics; in-process HTTP IO.

    Network interception prevents contacting the public DragonZpyder and Operly
    domains. Only the HTTP transport is virtualized. Auth, DB, cryptography,
    browser navigation, DOM, and JavaScript remain real.
    """
    from http.cookies import SimpleCookie
    from playwright.async_api import async_playwright, expect

    async with async_playwright() as play:
        browser = await play.chromium.launch(headless=True)
        try:
            context = await browser.new_context(ignore_https_errors=True, offline=True, service_workers="block")

            async def intercept(route):
                req = route.request
                parsed = urlsplit(req.url)
                origin = parsed.scheme + "://" + parsed.netloc
                if parsed.scheme != "https" or origin not in (ISSUER, OPERLY):
                    await route.abort("blockedbyclient")
                    return
                target = dz_app if origin == ISSUER else operly_app
                headers = dict(await req.all_headers())
                for key in ("host", "content-length", "accept-encoding"):
                    headers.pop(key, None)
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=target),
                    base_url=origin,
                    follow_redirects=False,
                ) as proxy:
                    response = await proxy.request(
                        req.method, req.url, headers=headers,
                        content=req.post_data_buffer,
                    )
                # Playwright's route.fulfill accepts a flat header mapping,
                # not multiple Set-Cookie headers; materialize each cookie in
                # the actual Chromium cookie store with its original origin
                # and security flags. Never weaken the issuer's cookie values.
                for item in response.headers.get_list("set-cookie"):
                    jar = SimpleCookie()
                    jar.load(item)
                    additions = []
                    for name, cookie in jar.items():
                        if cookie["max-age"] == "0":
                            await context.clear_cookies(name=name)
                            continue
                        same_site = cookie["samesite"].capitalize() if cookie["samesite"] else "Lax"
                        additions.append({
                            "name": name, "value": cookie.value,
                            "url": origin, "secure": bool(cookie["secure"]),
                            "httpOnly": bool(cookie["httponly"]),
                            "sameSite": same_site,
                        })
                    if additions:
                        await context.add_cookies(additions)
                out_headers = {
                    key: value for key, value in response.headers.items()
                    if key.lower() not in (
                        "set-cookie", "content-length", "content-encoding",
                        "transfer-encoding",
                    )
                }
                # Chromium's mocked 302 responses may follow redirects
                # outside the interception route; never allow a live request
                # to production. The actual backend 303 is already asserted
                # in the HTTP integration test. Return only a test landing
                # document for the completed callback in this browser test.
                if (origin == OPERLY
                        and parsed.path == "/api/auth/dragonzpyder/callback"
                        and response.status_code == 303):
                    await route.fulfill(
                        status=200,
                        headers={"Content-Type": "text/html",
                                 "Cache-Control": "no-store"},
                        body="<html><body>SSO callback completed</body></html>",
                    )
                else:
                    await route.fulfill(
                        status=response.status_code,
                        headers=out_headers,
                        body=response.content,
                    )

            await context.route("**/*", intercept)
            page = await context.new_page()
            # Resolve the FIRST 302 in-process because Playwright fulfill()
            # redirects may bypass request interception. Preserve the actual
            # Operly browser-binding cookie and navigate Chromium to the
            # registered issuer authorization URL; offline mode prohibits
            # any unintended request to the real public production hosts.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=operly_app),
                base_url=OPERLY, follow_redirects=False,
            ) as start_client:
                started = await start_client.get("/api/auth/dragonzpyder/start")
            assert started.status_code == 302
            starter_cookies = SimpleCookie()
            for item in started.headers.get_list("set-cookie"):
                starter_cookies.load(item)
            await context.add_cookies([
                {"name": name, "value": cookie.value, "url": OPERLY,
                 "secure": bool(cookie["secure"]),
                 "httpOnly": bool(cookie["httponly"]),
                 "sameSite": "Lax"}
                for name, cookie in starter_cookies.items()
            ])
            start_destination = started.headers["location"]
            assert start_destination.startswith(ISSUER + "/identity/v1/authorize?")
            await page.goto(start_destination, wait_until="domcontentloaded")
            assert await page.locator("#signin").count() == 1, (
                "Browser did not reach issuer UI: "
                + page.url + " " + (await page.content())[:1200]
            )
            await expect(page.locator("#signin")).to_be_visible()
            await expect(page.locator("#consent")).to_be_hidden()
            await page.get_by_label("Email").fill(email)
            await page.get_by_label("Password").fill(password)
            await page.get_by_role("button", name="Sign in").click()
            await expect(page.locator("#consent")).to_be_visible(timeout=20000)
            await expect(page.locator("#signin")).to_be_hidden()
            await page.get_by_role("button", name="Continue").click()
            await page.wait_for_url(CALLBACK + "?*", timeout=20000)
            await expect(page.locator("body")).to_contain_text("SSO callback completed")
            cookies = await context.cookies()
            by_name = {cookie["name"]: cookie for cookie in cookies}
            assert "operly_session" in by_name
            assert by_name["operly_session"]["domain"] == "operly.dragonzpyder.xyz"
            assert by_name["operly_session"]["httpOnly"] is True
            assert by_name["operly_session"]["secure"] is True
            assert by_name["__Host-dz_identity"]["domain"] == "api.dragonzpyder.xyz"
            assert by_name["__Host-dz_identity"]["httpOnly"] is True
            assert by_name["__Host-dz_identity"]["secure"] is True
            await page.goto(OPERLY + "/test/private")
            await expect(page.locator("body")).to_contain_text(account_id)
            assert tenant_id in await page.locator("body").inner_text()
            await context.close()
        finally:
            await browser.close()
