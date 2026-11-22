# One DragonZpyder identity login, two secure application sessions

DragonZpyder's **existing Web** currently uses Operly as its Personal
data-plane through a fixed same-origin proxy. Thus the two public sites
must each obtain their own scoped session from the same central identity
issuer; parent-domain shared cookies and email-based matching are forbidden.

The Opt-in second OIDC client is `dragonzpyder-web`, with an exact registered
HTTPS redirect `https://dragonzpyder.xyz/api/auth/dragonzpyder/web/callback`.
The Web uses its same-origin /api proxy to Operly's protected OIDC relying
party and receives an independent **host-only** Personal session on the
DragonZpyder domain. Operly uses `operly-web` and its existing callback.
Both rely on the same DragonZpyder issuer browser session. The Operly
business Kernel independently enforces tenant and workspace authority.

No two sites share a browser cookie, token, login endpoint, or untrusted
redirect. The second client is disabled unless ALL are configured:
- `OPERLY_DRAGONZPYDER_SSO_ENABLED=1`
- `OPERLY_DRAGONZPYDER_SSO_PRODUCTION=1` in production
- `OPERLY_DRAGONZPYDER_WEB_SSO_ENABLED=1`
- `OPERLY_FEDERATED_DRAGONZPYDER_WEB_CLIENT_ID=dragonzpyder-web`
- the same valid issuer RSA verification configuration as the primary client
- registered `dragonzpyder-web` client with the exact callback in issuer DB.

This solves repeated password entry after issuer sign-in; it is **not**
global sign-out or removal of legacy registration. Real identity DB import,
consent, signing keys, revoked-user checks, and restored-backup verification
are mandatory before flags go live. Browser feature detection must present
the link only when the server says this relying party is available.
