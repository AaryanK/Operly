# Operly Agent Computer runner

This service implements the `/v1` Agent Computer runtime protocol used by
`packages.workspace_modules.agent_computer.sandbox.ComputerRunnerClient`.

It is a **development/reference runner**. It intentionally refuses to boot when
`OPERLY_ENV=production`, because processes in this implementation share one OS
namespace. Production must place the same protocol behind a backend that gives
**each Agent Computer session its own container or microVM** with CPU, memory,
disk, lifetime and egress controls.

## Native tools

The protocol exposes the compute tools a modern coding/research agent expects:

- Bash/terminal commands, foreground and background
- Python 3 execution
- filesystem list/read/write/mkdir/remove/move/search
- background process list/kill
- Git status/diff and an allowlisted Git command surface
- public HTTP fetch/download with private-network blocking
- Playwright/Chromium open, navigate, snapshot, click, type, press, evaluate,
  screenshot and close

The runner never receives Operly connector credentials. Gmail, Calendar, CRM,
deployment and other business side effects remain Workspace capabilities and are
executed by the Operly control plane after normal permission/approval checks.

## Local run

Set a random shared runner token and run this service separately from the Operly
API process:

```bash
export OPERLY_ENV=development
export OPERLY_AGENT_COMPUTER_DEV_RUNNER=1
export OPERLY_AGENT_COMPUTER_RUNNER_TOKEN='replace-with-a-random-secret'
uvicorn apps.computer_runner.main:app --host 127.0.0.1 --port 8092
```

Then point Operly at it:

```bash
export OPERLY_AGENT_COMPUTER_RUNNER_URL=http://127.0.0.1:8092
export OPERLY_AGENT_COMPUTER_RUNNER_TOKEN='replace-with-the-same-random-secret'
```

The Dockerfile installs Chromium and the required Playwright runtime. The normal
Operly API image does not install a browser and never runs agent shell/Python
commands itself.

## Production contract

A production backend must implement:

- `GET /v1/health`
- `POST /v1/sessions`
- `GET /v1/sessions/{id}`
- `DELETE /v1/sessions/{id}`
- `POST /v1/sessions/{id}/tools/{tool_id}`

It must also enforce per-session isolation, TTL cleanup, resource quotas, no
metadata/private-network access, scoped egress, no host secret inheritance, and
complete teardown of processes/browser state when the session stops.
