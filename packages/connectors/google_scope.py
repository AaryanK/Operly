from __future__ import annotations

import json

from sqlalchemy import select

from packages.database.account_connector_models import AccountConnector
from packages.database.connector_models import TenantConnector


class ScopedGoogleConnectorRequired(LookupError):
    pass


def connector_scopes(connector) -> set[str]:
    try:
        return set(json.loads(connector.granted_scopes_json or "[]"))
    except (TypeError, json.JSONDecodeError):
        return set()


async def _workspace_connectors(context):
    if not context.tenant_id:
        raise ScopedGoogleConnectorRequired("Workspace Google scope is unavailable")
    return (
        await context.db.scalars(
            select(TenantConnector).where(
                TenantConnector.tenant_id == context.tenant_id,
                TenantConnector.provider == "google",
                TenantConnector.enabled.is_(True),
                TenantConnector.status == "connected",
            )
        )
    ).all()


async def _personal_connectors(context):
    owner_user_id = str(context.owner_user_id or "").strip()
    if not owner_user_id or context.actor_id != owner_user_id:
        raise ScopedGoogleConnectorRequired("Personal Google authority is unavailable")
    return (
        await context.db.scalars(
            select(AccountConnector).where(
                AccountConnector.user_id == owner_user_id,
                AccountConnector.provider == "google",
                AccountConnector.enabled.is_(True),
                AccountConnector.status == "connected",
            )
        )
    ).all()


async def google_connectors_for_context(context):
    """Resolve Google connectors from durable provider scope, never request metadata."""
    if getattr(context, "scope_kind", "workspace") == "personal":
        return await _personal_connectors(context)
    return await _workspace_connectors(context)


async def google_connector_for_context(context, required_scope):
    required = {required_scope} if isinstance(required_scope, str) else set(required_scope)
    for connector in await google_connectors_for_context(context):
        if required.issubset(connector_scopes(connector)):
            return connector
    raise ScopedGoogleConnectorRequired("Connect Google and grant the required scope")


async def google_connector_any_for_context(context, acceptable_scopes):
    acceptable = set(acceptable_scopes)
    for connector in await google_connectors_for_context(context):
        if connector_scopes(connector) & acceptable:
            return connector
    raise ScopedGoogleConnectorRequired(
        "Reconnect Google with the full assistant permission tier"
    )


async def google_access_token_for_context(context, connector) -> str:
    """Resolve a token according to the connector's trusted durable owner type."""
    if getattr(context, "scope_kind", "workspace") == "personal":
        if not isinstance(connector, AccountConnector):
            raise ScopedGoogleConnectorRequired("Personal Google connector ownership mismatch")
        if connector.user_id != context.owner_user_id or context.actor_id != context.owner_user_id:
            raise ScopedGoogleConnectorRequired("Personal Google connector ownership mismatch")
        # Lazy import avoids a module cycle: account_google imports ProviderRejected
        # from google_provider for its refresh failure contract.
        from packages.connectors.account_google import account_access_token

        return await account_access_token(context.db, connector)

    if not isinstance(connector, TenantConnector) or connector.tenant_id != context.tenant_id:
        raise ScopedGoogleConnectorRequired("Workspace Google connector ownership mismatch")
    # Lazy import keeps this helper independent from the legacy workspace token code.
    from packages.connectors.google_provider import access_token

    return await access_token(context.db, connector)
