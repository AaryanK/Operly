# Operly production isolated runner

This service is the production execution host for `operly-fullstack-v1`. It is **not**
the Operly API process and must run on a dedicated Docker host whose only sensitive
credential is the runner HMAC token.

## Security model

For every build the gateway creates a fresh internal Docker network and fresh
containers. Generated software receives:

- no Docker socket;
- no host bind mounts;
- no Operly database/session/provider credentials;
- no raw service-binding credentials;
- no network shared with another generated job;
- all Linux capabilities dropped;
- `no-new-privileges`;
- CPU, memory, PID and file-descriptor limits;
- a non-root UID (`10001`);
- a read-only root filesystem once build/tests pass.

Dependency installation uses a separate trusted CONNECT sidecar. The generated job
is attached only to an **internal** Docker network, so direct internet access is not
available. The sidecar accepts TLS CONNECT only to the registries required by the
submission (`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`) and rejects
IP literals/non-global DNS results. The egress sidecar is deleted before generated
build scripts, tests, workers, or the application runtime execute.

Preview traffic uses a second tokenless/credentialless sidecar that bridges only the
job network to the runner control network. The public gateway exposes an opaque
preview token and proxies to that sidecar. The generated runtime is never directly
published on the host.

## Why this is not deployed inside Railway

Railway services are non-privileged and do not provide Docker-in-Docker/container
mounting suitable for launching one container per untrusted job. Running generated
software as a subprocess in a second Railway service would separate it from the
Operly API but would **not** provide the per-job OS isolation this contract claims.

Use Railway for the Operly control plane. Run this service on a dedicated Linux host
(or replace `DockerIsolationBackend` with another genuine microVM/container backend)
and point Operly at its HTTPS origin.

## Dedicated host install

Requirements:

- Linux host dedicated to generated-code execution;
- Docker Engine + Docker Compose v2;
- DNS A/AAAA record for a runner-only hostname;
- inbound TCP 80/443 only; Docker daemon must not be remotely exposed;
- current Operly repository checkout.

Build the two trusted images used by generated jobs:

```bash
docker compose -f apps/runner/docker-compose.runner.yml --profile images build job-image proxy-image
```

Create a strong HMAC secret and start the gateway/TLS edge:

```bash
export RUNNER_DOMAIN=runner.example.com
export OPERLY_RUNNER_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose -f apps/runner/docker-compose.runner.yml up -d runner caddy
```

The gateway will report HTTP 503 from `/health` rather than advertise capabilities
if Docker, the trusted job/proxy images, or the isolated control network are absent.

## Wire Operly to the runner

After the runner's `/health` is ready, configure the Operly control-plane service:

```text
OPERLY_SANDBOX_RUNNER_URL=https://runner.example.com
OPERLY_SANDBOX_RUNNER_TOKEN=<same HMAC secret>
OPERLY_SANDBOX_RUNNER_HOSTS=runner.example.com
OPERLY_SANDBOX_PREVIEW_HOSTS=runner.example.com
```

Do not put model-provider keys, Gmail credentials, database URLs, session secrets,
or connector secrets on the runner host. Future runtime capabilities must continue
to use scoped semantic service bindings/capability-gateway credentials rather than
copying provider secrets into generated containers.

## Runner protocol

Authenticated control endpoints:

```text
GET    /v1/capabilities
POST   /v1/builds
GET    /v1/builds/{job_id}
POST   /v1/builds/{job_id}/cancel
POST   /v1/builds/{job_id}/cleanup
DELETE /v1/previews/{preview_id}
```

All requests use the #116 bearer + HMAC request signature contract and all control
responses are HMAC-signed. `POST /v1/builds` is idempotent and returns `queued`
quickly; execution proceeds outside the request timeout and is polled durably.

`supportsDeploy` remains false. This runner proves isolated preview execution only;
production deployment/rollback is a later platform layer.
