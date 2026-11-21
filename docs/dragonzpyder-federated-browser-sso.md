# Operly federated browser callback — opt-in (no cutover)

The existing Operly login/session system remains authoritative until a controlled cutover. This adds **one** parallel SSO path.

## Entry and callback
- `GET /api/auth/dragonzpyder/start` creates a five-minute one-time server-stored nonce, OAuth state, PKCE verifier and browser-binding cookie; redirects the browser to the pinned DragonZpyder issuer `/identity/v1/authorize`.
- `GET /api/auth/dragonzpyder/callback` consumes state before network token exchange, validates browser-binding, performs server-side code + PKCE exchange, validates issuer-signed ID and access tokens (RS256 pinned key ID, issuer, audience, nonce, at_hash and matching subject), and resolves an **activated** `(issuer,subject) → existing AppUser` mapping.
- Creates a **new separate Operly AuthSession** with existing `_create_session` and `set_session_cookies`. Workspace membership is selected from existing TenantMember and all workspace checks remain unchanged.
- Never matches users by email, never reuses DragonZpyder browser cookies, and never trusts provider/subject from the browser.

## Feature flags and configuration
- `OPERLY_DRAGONZPYDER_SSO_ENABLED=1` opts in (default off).
- Production requires an additional `OPERLY_DRAGONZPYDER_SSO_PRODUCTION=1`; **do not set until security review**.
- `OPERLY_FEDERATED_ISSUER`, `OPERLY_FEDERATED_CLIENT_ID`, `OPERLY_FEDERATED_RESOURCE_AUDIENCE`, `OPERLY_FEDERATED_SIGNING_KID`, `OPERLY_FEDERATED_PUBLIC_KEY_PEM`, and an exact HTTPS `PUBLIC_BASE_URL`.
- The public key is currently explicitly pinned via env. JWKS rollout/key rotation is future work; never pull keys from JWT-provided `jku` or from an arbitrary remote URL.

## Required before canary
1. Merge/rebase Phase B mapping + Phase C token verifier only after CI, Alembic upgrade and rollback tests.
2. Register and enable the exact Operly callback URI, scopes and `operly-web` client in the dedicated DragonZpyder identity database.
3. Apply canonical mapping only after audited account migration; default pending must be kept until proof-of-ownership tests pass.
4. Run true issuer ↔ client integration in an isolated HTTPS environment, including revoked sessions, corrupt state, wrong nonce, wrong audience/key, replay, parallel redemption, and expiration.
5. Keep legacy Operly login available as immediate rollback; do not merge accounts by email or upgrade Discord channel links to login identities.
6. Review key rotation, metadata compliance, cookie/site isolation and privacy controls.

This is a **draft integration contract**, not operational SSO in production.
