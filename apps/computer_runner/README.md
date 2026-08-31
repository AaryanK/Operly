# Operly Agent Computer runner

This service implements the `/v1` Agent Computer runtime protocol used by
`packages.workspace_modules.agent_computer.sandbox.ComputerRunnerClient`.

It is a **development/reference runner**. It intentionally refuses to boot when
`OPERLY_ENV=production`, because sessions in this implementation share one runner
container/process namespace. Production must place the same protocol behind a
backend that gives **each Agent Computer session its own container or microVM**
with CPU, memory, disk, lifetime and egress controls.

## Agent workbench

The reference image contains the normal local tools a coding/research agent needs:
Python 3 + pip, Bash, Node.js + npm, Git, ripgrep, jq, curl/wget, zip/unzip,
compiler/build tooling, Chromium and Playwright. The image runs as a dedicated
non-root `computer` user with a dedicated `/runtime` workspace.

The protocol exposes these computer-native operations:

- Bash/terminal commands, foreground and background
- Python 3 execution
- filesystem list/read/write/mkdir/remove/move/search
- background process list/kill
- Git status/diff and an allowlisted Git command surface
- public HTTP fetch/download with redirect and private-network validation
- Playwright/Chromium open, navigate, accessibility/text snapshot, click, type,
  key press, page JavaScript evaluation, screenshot and close

Package managers and other engineering commands run through the terminal inside
the sandbox; they are not special Operly business capabilities.

The runner never receives Operly connector credentials. Gmail, Calendar, CRM,
deployment and other business side effects remain Workspace capabilities and are
executed by the Operly control plane after normal permission/approval checks.

## Network boundary

The reference runner blocks private/link-local destinations for its explicit
`web.*` tools, validates each redirect target, and installs a browser request guard.
Its `network_policy=off` also disables those explicit web/browser operations and
networked Git helpers.

This reference implementation **cannot reliably firewall arbitrary Bash/Python
sockets**. Its health response therefore reports `network_policy_enforcement` as
`tool-level-reference-only`. A production container/microVM backend must enforce
egress at the OS/network layer so the policy applies to every process, not merely
to Operly's helper tools.

## Development run

Prefer the provided Dockerfile even for development so agent commands are kept in
a separate container rather than executed directly on the developer workstation:

```bash
docker build -f apps/computer_runner/Dockerfile -t operly-computer .
docker run --rm -p 127.0.0.1:8092:8092 \
  -e OPERLY_ENV=development \
  -e OPERLY_AGENT_COMPUTER_DEV_RUNNER=1 \
  -e OPERLY_AGENT_COMPUTER_RUNNER_TOKEN='replace-with-a-random-secret' \
  operly-computer
```

Then point Operly at it:

```bash
export OPERLY_AGENT_COMPUTER_RUNNER_URL=http://127.0.0.1:8092
export OPERLY_AGENT_COMPUTER_RUNNER_TOKEN='replace-with-the-same-random-secret'
```

Direct `uvicorn apps.computer_runner.main:app` execution exists only for trusted
runner development. Do not expose untrusted agent commands to a runner process on
the workstation host.

The normal Operly API image does not install a browser and never runs agent
shell/Python commands itself.

## Production contract

A production backend must implement:

- `GET /v1/health`
- `POST /v1/sessions`
- `GET /v1/sessions/{id}`
- `DELETE /v1/sessions/{id}`
- `POST /v1/sessions/{id}/tools/{tool_id}`

It must also enforce per-session isolation, TTL cleanup, CPU/memory/disk/process
quotas, no cloud-metadata/private-network access, scoped egress, no host secret
inheritance, and complete teardown of processes/browser state when the session
stops. The production runtime may use containers, microVMs, Kubernetes jobs or a
specialized sandbox provider as long as it obeys this protocol and isolation
contract.
