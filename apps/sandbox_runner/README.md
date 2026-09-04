# Operly Sandbox Runner

This is Operly's **execution plane** on Railway. The main API is only the control plane.
Agent Computer shell, Python, filesystem, process, Git, web and browser operations are
executed inside one Railway Sandbox VM per Computer session.

The existing Railway service named **Operly Sandbox Runner** points at this directory,
runs `npm start`, and health-checks `/health`.

## Isolation contract

The Sandbox Runner service may be reachable from the Operly control plane, but it does
not execute agent commands in its own service container. It uses Railway's `Sandbox`
SDK to allocate an ephemeral VM and invokes the native Computer tools there.

The **agent sandbox itself is not attached to Operly's private service network** and
receives no Gmail, Calendar, CRM, database, model-provider, deployment, connector or
other Operly credentials. The API stores only the opaque Railway sandbox ID.

Business side effects remain normal Workspace capabilities in the API control plane.
Giving an agent `computer:execute` therefore does not give it Gmail-send, CRM-write,
Studio-deploy or other Workspace authority.

Network policies:

- `off`: Railway Sandbox isolation plus uid-level IPv4/IPv6 outbound egress guards.
- `web`: public-web sandbox networking only. The sandbox is not joined to the project
  private network; link-local/metadata destinations are blocked at the untrusted uid
  layer, and explicit web/browser helpers additionally reject private, loopback,
  link-local and metadata targets.

## Native Computer tools

The sandbox image provides Python, Bash, Node/npm, Git, ripgrep, jq, curl/wget,
archive/build utilities, Chromium/Playwright and common data/document Python packages.
The API exposes the approved subset through the `computer.*` Workspace capability
contracts. The runner also contains bounded artifact import/export primitives so the
control plane can later stage source and collect generated artifacts without exposing a
shared host filesystem.

## Protocol

Runner authentication deliberately uses **two different secrets**:

- `OPERLY_RUNNER_TOKEN` authenticates the control plane with a Bearer credential.
- `OPERLY_RUNNER_SIGNING_KEY` authenticates request/response integrity with HMAC
  SHA-256. The runner refuses to start if the two values are equal.

Every authenticated request carries `X-Operly-Timestamp` and a random
`X-Operly-Nonce`. The HMAC input is:

`METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + RAW_BODY`

The runner rejects timestamps outside its bounded freshness window and rejects a nonce
that has already been accepted during the replay window. Responses are HMAC-signed over
the raw response body with the signing key.

Endpoints:

- `GET /health`
- `POST /v1/computer/sessions`
- `GET /v1/computer/sessions/{sandbox_id}`
- `DELETE /v1/computer/sessions/{sandbox_id}`
- `POST /v1/computer/sessions/{sandbox_id}/tools/{tool_id}`

## Railway configuration

The Runner service requires:

- `OPERLY_RUNNER_TOKEN` — random transport/Bearer secret
- `OPERLY_RUNNER_SIGNING_KEY` — a different random HMAC signing secret
- `RAILWAY_ENVIRONMENT_ID`
- Railway authentication (`RAILWAY_TOKEN`) for Sandbox SDK operations
- `PORT` (Railway normally injects this)

The Operly API points to this service with:

- `OPERLY_SANDBOX_RUNNER_URL`
- `OPERLY_SANDBOX_RUNNER_TOKEN` — same value as the Runner's `OPERLY_RUNNER_TOKEN`
- `OPERLY_SANDBOX_RUNNER_SIGNING_KEY` — same value as the Runner's
  `OPERLY_RUNNER_SIGNING_KEY`

Rotate the Bearer token and signing key independently. During a coordinated rotation,
update both services before enabling Agent Computer traffic again; the boundary fails
closed when either value is absent or when the two secrets are the same.
