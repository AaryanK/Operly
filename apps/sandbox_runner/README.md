# Operly Sandbox Runner

This is Operly's **execution plane** on Railway. The main API is only the control plane.
Agent Computer shell, Python, filesystem, process, Git, web and browser operations are
executed inside one Railway Sandbox VM per Computer session.

The existing Railway service named **Operly Sandbox Runner** points at this directory,
runs `npm start`, and health-checks `/health`.

## Isolation contract

The runner itself does not execute agent commands in its service container. It uses the
Railway `Sandbox` SDK to create an ephemeral sandbox and invokes tools there. The Operly
API stores only the opaque Railway sandbox ID.

The sandbox receives no Gmail/Calendar/CRM/provider secrets and is never attached to the
Operly private network. Business side effects remain normal Workspace capabilities in the
API control plane.

Network policies:

- `off`: Railway Sandbox network isolation plus a uid-level egress guard.
- `web`: Railway's normal sandbox public-internet access. The sandbox is not joined to the
  project private network; explicit web/browser helpers additionally block private and
  link-local targets.

## Native Computer tools

The execution image provides Python, Bash, Node/npm, Git, ripgrep, jq, curl/wget,
archive/build utilities, Chromium/Playwright and common data/document Python packages.
The API exposes them through the `computer.*` Workspace capability contracts.

## Protocol

Authenticated requests use `OPERLY_RUNNER_TOKEN` plus an HMAC SHA-256 signature over:

`METHOD + "\\n" + PATH + "\\n" + RAW_BODY`

Responses are HMAC-signed over the raw response body.

Endpoints:

- `GET /health`
- `POST /v1/computer/sessions`
- `GET /v1/computer/sessions/{sandbox_id}`
- `DELETE /v1/computer/sessions/{sandbox_id}`
- `POST /v1/computer/sessions/{sandbox_id}/tools/{tool_id}`

## Railway configuration

The service expects the existing Railway variables:

- `OPERLY_RUNNER_TOKEN`
- `RAILWAY_ENVIRONMENT_ID`
- Railway authentication (`RAILWAY_TOKEN`) for Sandbox SDK operations
- `PORT` (Railway normally injects this)

The Operly API points to this service using its existing
`OPERLY_SANDBOX_RUNNER_URL` and `OPERLY_SANDBOX_RUNNER_TOKEN` variables.
