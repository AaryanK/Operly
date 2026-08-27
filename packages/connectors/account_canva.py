from __future__ import annotations

from datetime import datetime, timezone

from packages.connectors.account_secrets import read_account_secret, update_account_secret
from packages.connectors.canva_provider import CANVA_SCOPES, finalize_tokens, normalize_scopes, refresh_tokens


async def account_access_token(db, connector) -> str:
    """Resolve or refresh a personal, account-owned Canva credential."""
    secret = await read_account_secret(db, connector.user_id, connector.credential_reference)
    if float(secret.get("expires_at", 0)) > datetime.now(timezone.utc).timestamp() + 60:
        return secret["access_token"]

    refreshed = await refresh_tokens(str(secret.get("refresh_token") or ""))
    if not refreshed.get("refresh_token") and secret.get("refresh_token"):
        refreshed["refresh_token"] = secret["refresh_token"]
    refreshed, _ = finalize_tokens(
        refreshed,
        fallback_scopes=normalize_scopes(secret.get("scope")) or CANVA_SCOPES,
    )
    await update_account_secret(
        db,
        connector.user_id,
        connector.credential_reference,
        refreshed,
    )
    return refreshed["access_token"]
