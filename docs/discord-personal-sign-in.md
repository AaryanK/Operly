# Discord personal sign-in

Operly uses Discord OAuth2 for personal identity. The OAuth callback creates or resolves the Operly account and binds the same Discord user ID as an `ExternalIdentity`, so Discord DMs resolve to the authenticated personal user.

## Discord application configuration

Configure the Discord application with this OAuth2 redirect URI:

```text
${PUBLIC_BASE_URL}/api/identities/discord/callback
```

Set these deployment variables:

```text
DISCORD_AUTH_CLIENT_ID=
DISCORD_AUTH_CLIENT_SECRET=
# Optional. Defaults to ${PUBLIC_BASE_URL}/api/identities/discord/callback.
DISCORD_AUTH_REDIRECT_URI=
```

The flow requests only the `identify` and `email` scopes. Operly requires Discord to return a verified email. The access token is used only to fetch `/users/@me` during the callback and is not persisted.

## Identity behavior

- Discord sign-in always creates a personal Operly session (`tenant_id=None`).
- The Discord user ID is also bound through Operly's channel identity graph, allowing Discord DMs to resolve to that personal user.
- Workspace/server authority remains separate. A Discord server is bound to an existing Operly workspace with the governed workspace-binding flow.
- The old Discord pairing-code and web-claim identity flows are retired; non-Discord provider linking remains unchanged.
