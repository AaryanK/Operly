from __future__ import annotations

import os
from datetime import datetime, timezone

import aiohttp

from packages.connectors.account_secrets import read_account_secret, update_account_secret
from packages.connectors.google_provider import ProviderRejected


async def account_access_token(db, connector) -> str:
    """Resolve/refresh an account-owned Google credential without tenant lookup."""
    secret = await read_account_secret(db, connector.user_id, connector.credential_reference)
    if float(secret.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp() + 60:
        return secret["access_token"]

    refresh_token = secret.get("refresh_token")
    if not refresh_token:
        raise ProviderRejected("Google authorization expired and no refresh token is available")
    data = {
        "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        async with session.post("https://oauth2.googleapis.com/token", data=data) as response:
            body = await response.json()
    if response.status != 200:
        raise ProviderRejected("Google authorization expired or refresh was rejected")
    secret.update(body)
    secret["expires_at"] = datetime.now(timezone.utc).timestamp() + int(body.get("expires_in", 3600))
    await update_account_secret(db, connector.user_id, connector.credential_reference, secret)
    return secret["access_token"]
