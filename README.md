# Operly

**Governed business execution, powered by DragonZpyder intelligence.**

Operly is the business application and workspace capability platform: it owns business tenants, memberships, data, integrations, application-specific workflows, policies, approvals, idempotency, audit and deterministic execution through its Kernel. DragonZpyder is the intended shared intelligence layer for agent reasoning, model routing, native computing, Personal identity and general-purpose agent objectives.

A human or authorized agent should be able to express an objective and discover only the business capabilities permitted in the current workspace. The model does not grant itself authority; Operly independently enforces live scope, schema, approval and execution policy. Web, Discord, MCP and workflow entry points should converge on these same contracts rather than create separate AI engines.

**[Platform boundary and migration rules](docs/dragonzpyder-platform-boundary.md) · [Repository instructions](AGENTS.md) · [DragonZpyder universal harness](https://github.com/AaryanK/DragonZpyder/blob/develop/docs/universal-harness-v4.md)**

> The intended target is one DragonZpyder intelligence harness with Operly as a governed business client and capability provider. This does not assert that legacy Operly intelligence routes have been retired or that identity migration is complete. The implementation notes below describe existing and transitional structures; inspect current source, CI, flags and deployed SHAs for actual availability.

---

## Start here — contributors and coding agents

This README explains Operly's purpose and implementation. Before changing the repository, understand the shared product vision and the business authority boundary.

| Read in this order | What you will learn |
| --- | --- |
| 1. [AGENTS.md](AGENTS.md) | Mandatory repository-level development instructions and guardrails. |
| 2. [Platform boundary](docs/dragonzpyder-platform-boundary.md) | Operly's business responsibilities, DragonZpyder's intelligence responsibilities and safe migration rules. |
| 3. [DragonZpyder Universal Harness V4](https://github.com/AaryanK/DragonZpyder/blob/develop/docs/universal-harness-v4.md) | Shared product vision, target intelligence architecture, roadmap and acceptance criteria. |
| 4. [Kernel](packages/kernel/), [security](packages/security/), [API entry point](apps/api/main.py) | Current implementation and authority enforcement. |

Inspect the relevant code, tests and current pull requests before making changes. Consult current upstream documentation and relevant open-source implementations when changing foundational infrastructure. Reuse shared DragonZpyder intelligence rather than introduce competing agent runtimes; preserve Operly's business authority. Distinguish planned, implemented, tested and deployed capabilities, and update the documentation when verified behavior or architectural decisions change.

## Current snapshot

The FastAPI application identifies itself as:

```text
0.11.0-agent-runtime-1
```

The current booted platform includes:

- account and workspace authentication;
- Personal tools and account-owned Google connectors;
- workspace OS routes and workspace tool discovery;
- plugin manifests, bindings, hosted plugins, events and webhooks;
- a governed capability gateway and egress broker;
- durable Personal and Workspace workflows;
- schedule, manual and same-scope semantic-event triggers;
- per-workflow concurrency policy;
- approvals and resumable workflow execution;
- MCP access through live workspace authority plus client narrowing;
- Discord integration;
- artifacts;
- Agent Computer / sandbox execution;
- Studio hosting surfaces;
- audit and route traceability;
- a deterministic Kernel runtime.

The `/api/health` and `/api/rebuild-status` endpoints report agent runtime status from configuration. `ai_runtime_enabled` is true only when `OPERLY_AGENT_RUNTIME_ENABLED=1` and an inference route is configured; the flag defaults to off. The implementation lives in `packages/agent_runtime` and uses the governed Kernel. Repository support does not establish whether a particular deployment has enabled or configured inference.

---

## Repository shape

The current top-level application surfaces are:

```text
apps/
├── api/                 trusted FastAPI control plane
├── web/                 React/Vite product UI
└── sandbox_runner/      isolated sandbox / Agent Computer runtime
```

The current first-class Python package areas are:

```text
packages/
├── artifacts/
├── connectors/
├── database/
├── email/
├── kernel/
├── mcp/
├── personal_modules/
├── plugins/
├── security/
├── workflow/
└── workspace_modules/
```

This compact structure is deliberate. Earlier versions split the platform into many more packages—agents, capabilities, coding harnesses, custom-software runtimes, Solutions, Studio layers, model runtime, context federation, and others. Those responsibilities are being reconverged around Kernel + plugins + workflows + scoped modules rather than preserved as permanent parallel architectures.

---

# Architecture in one picture

```text
Browser / API / Discord / MCP / Webhook / Workflow
                         │
                         ▼
                authenticated principal
                         │
                         ▼
                    scope resolution
                 personal | workspace
                         │
                         ▼
                  ExecutionContext
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     direct route     Workflow       Agent runtime
          │              │              │
          └──────────────┴──────┬───────┘
                                ▼
                         Operly Kernel
               registry / policy / approvals
              idempotency / audit / traceability
                                │
                                ▼
                       capability registry
                    search / describe / invoke
                                │
                                ▼
                         Plugin runtime
              bindings / credentials / budgets
                 events / deliveries / builds
                                │
                                ▼
                governed provider execution
             integrations / tools / sandbox work
                                │
                                ▼
                 artifacts / events / database
```

The most important rule is:

> **Every surface should converge on the same authority and capability semantics.**

A web route, MCP client, scheduled workflow, Discord action, hosted plugin, and AI agent should not each grow its own security model.

---

# Kernel

`packages/kernel` is the current center of gravity.

Important files include:

| File | Responsibility |
| --- | --- |
| `contracts.py` | Kernel capability and runtime contracts |
| `registry.py` | Capability/provider registry |
| `providers.py` | Provider composition |
| `runtime.py` | Governed runtime execution |
| `approvals.py` | Approval lifecycle integration |
| `policy.py` | Execution policy |
| `idempotency.py` | Replay and duplicate-effect protection |
| `audit.py` | Durable execution audit |
| `ingress.py` | Ingress normalization |
| `schema_validation.py` | Capability argument/schema validation |
| `runtime_availability.py` | Runtime availability checks |
| `route_traceability.py` | Route-to-capability traceability |
| `bootstrap.py` | Kernel bootstrap/composition |

The Kernel owns the durable invariants that should survive any future model or agent implementation.

A model may eventually choose an operation. It must not be able to:

- create its own permission;
- bypass scope resolution;
- bypass schema validation;
- bypass approval policy;
- bypass idempotency;
- inject credentials directly;
- claim provider success as verified completion without application evidence.

---

# Identity, scopes and security

`packages/security` keeps human identity separate from runtime authority.

Current responsibilities include:

- human identity projection;
- principals;
- Personal vs Workspace `ExecutionContext`;
- scope resolution;
- workspace permissions;
- delegation and delegation context;
- explicit personal-to-workspace delegation;
- guest workspace authority;
- surface policy;
- temporal context;
- workspace invitations.

The intended asymmetry is:

```text
Personal scope -> may intentionally operate across resources the human is authorized to use
Workspace scope -> does not inherit Personal data or other workspaces
```

Delegation may narrow authority. It must never widen it.

---

# Personal modules

`packages/personal_modules` owns account-scoped abilities.

Current files include:

- `connectors.py` — account-owned connector management;
- `google_provider.py` — Personal Google capability provider;
- `router.py` — Personal tool APIs;
- `runtime.py` — Personal capability runtime composition.

Personal connector ownership is account-level rather than a fake default workspace. This is an important architectural boundary for future Personal AI.

The Personal capability flow is designed around **authorization-aware search, then describe**, rather than injecting every available tool schema into every request.

---

# Workspace modules

`packages/workspace_modules` contains workspace-installed abilities rather than turning each workspace into a different hardcoded agent.

Current areas are:

```text
workspace_modules/
├── agent_computer/
├── integrations/
├── studio/
├── tools/
└── catalog.py
```

`catalog.py` is the shared workspace ability catalog. The long-term model is that a workspace is a governed capability scope: adding a plugin or integration should expand what can be discovered there without rewriting the core runtime.

Current workspace surfaces include:

- workspace OS APIs;
- workspace tools;
- integrations;
- Agent Computer;
- Studio/public hosting behavior.

---

# Plugin platform

`packages/plugins` is the extensibility layer around the Kernel.

The package currently contains contracts and runtime machinery for areas such as:

- plugin contracts and manifests;
- capability sources and bindings;
- credential binding;
- budgets;
- builds;
- runtime reconciliation;
- concurrent workers;
- egress routing;
- events and event routing;
- deliveries;
- hosted/public plugin routes;
- capability gateway access;
- webhooks;
- runtime-management APIs.

The current platform health contract describes plugin execution as:

```text
plugin_manifest_schema      = operly.plugin/v1
plugin_runtime_policy       = isolated-workload-only
capability_gateway          = short-lived-runtime-identity + live workspace authority
runtime_egress_broker       = grant-scoped credential injection
```

The design rule is:

> **Installing a plugin composes capability; it does not bypass security.**

Plugin code and external runtimes still need a valid scope, valid bindings, appropriate credentials, policy permission and the normal execution boundary.

---

# Durable workflows

`packages/workflow` is now a first-class orchestration subsystem.

Current files are:

```text
workflow/
├── access.py
├── concurrency.py
├── engine.py
├── models.py
├── provider.py
├── scheduler.py
├── spec.py
├── tracing.py
└── triggers.py
```

Workflows exist in both Personal and Workspace scopes and execute capabilities through the normal Kernel runtime. A workflow does not become a privileged provider client simply because it is scheduled.

The runtime supports:

- durable workflow definitions;
- immutable workflow versioning;
- scheduled execution;
- manual runs;
- same-scope semantic Kernel event triggers;
- durable run state;
- approval blocking and resume;
- retry/recovery semantics;
- access checks at execution time;
- tracing;
- per-workflow concurrency policy;
- HA-aware claim/concurrency behavior.

A core invariant is:

```text
Workflow -> resolve fresh authority -> Kernel -> capability
```

not:

```text
Workflow -> provider directly
```

Approval resolution is persisted before continuation so a resume failure cannot casually replay an already-resolved side effect.

---

# Integrations

## Google

Personal Google connectors are account-owned and exposed through Personal modules. Workspace Google and other workspace integrations live under workspace capability/integration semantics.

Authentication identity and connector authorization are separate concerns. Signing into Operly with Google does not by itself grant Gmail or Calendar execution.

## Discord

The current API boots the Discord lifecycle from:

```text
packages/workspace_modules/integrations/discord
```

Discord remains an ingress/egress surface, not an alternate business-runtime authority system.

## Webhooks and digital events

The plugin platform exposes both management and public webhook routes, plus same-scope digital event delivery. Incoming events must still map into governed capability/workflow semantics.

---

# MCP

`packages/mcp` is the external AI/client interoperability surface.

The current server reports MCP protocol version:

```text
2026-07-28
```

The authority model is:

```text
live workspace authority + client narrowing
```

An MCP grant cannot invent permissions that the current user/workspace does not have. The client boundary narrows the live Operly authority rather than replacing it.

---

# Agent Computer and sandbox execution

`apps/sandbox_runner` and `packages/workspace_modules/agent_computer` provide bounded computer/sandbox execution.

This boundary exists so tool execution that should not occur inside the trusted FastAPI control plane can be isolated.

The security direction is to keep:

- untrusted execution out of the control plane;
- credentials out of generated/untrusted code;
- network access explicit;
- leases and runtime lifetimes bounded;
- provider/runtime effects attributable.

The current repository no longer contains the old `apps/runner` tree that earlier READMEs described. Any future full generated-software runner should build on the current isolation contracts rather than reintroducing a second hidden execution architecture.

---

# Artifacts, email and persistence

## Artifacts

`packages/artifacts` is the durable output layer for generated or user-facing files and runtime deliverables.

## Email

`packages/email` provides application email/provider abstraction for account/security and platform messaging needs.

## Database

`packages/database` contains the SQLAlchemy persistence layer and many domain-specific model modules.

The database intentionally retains historical model families such as old Application Builder, custom software, Agent, Studio and product records. They are schema/data history and compatibility surfaces; they do **not** mean their former Python runtime packages are currently active.

`alembic/versions` remains the authoritative migration history.

---

# FastAPI control plane

`apps/api/main.py` is the trusted application entrypoint.

It currently boots:

- database initialization and account bootstrap;
- Discord lifecycle;
- Workflow scheduler;
- Workflow event dispatcher;
- authentication/session routes;
- Personal connectors and Personal tools;
- workspace OS and workspace-simple routes;
- workspace integrations and workspace tools;
- artifacts;
- plugin platform/runtime/hosting/events/webhooks;
- capability gateway;
- runtime egress broker;
- Agent Computer;
- access/client grants;
- MCP;
- Kernel routes;
- Studio public routes;
- React frontend serving.

Production configuration validates separate secrets, HTTPS assumptions, trusted hosts, CORS boundaries and request/security middleware before serving traffic.

---

# Web application

`apps/web` is a React/Vite frontend.

Current package versions on `main` are:

```text
React      18.2.0
React DOM  18.2.0
Vite       8.2.1
```

The source tree currently contains account, admin, auth, workspace, workspace-lite, flow, public, legal, minimal and shared UI areas.

Frontend contract scripts cover the general application contract plus workspace tools, Agent Computer, workflow and bundle behavior.

---

# Local development

Start from `.env.example`.

Backend:

```bash
python -m venv .venv
# activate the environment
pip install -r requirements.txt
alembic upgrade head
uvicorn apps.api.main:app --reload --env-file .env
```

Frontend:

```bash
cd apps/web
npm install
npm run dev
```

Frontend checks:

```bash
npm run test:contracts
npm run test:workspace-tools
npm run test:agent-computer
npm run test:workflow
npm run test:bundle
```

General tests:

```bash
pytest -q
```

---

# Deployment

The repository includes:

- root `Dockerfile` for the control plane/web build;
- `railway.toml`;
- `docker-compose.yml` for local composition;
- Caddy/Nginx material under `deploy/`;
- a separate `apps/sandbox_runner` runtime.

Production should preserve a hard distinction between trusted control-plane code and untrusted/isolated runtime execution.

---

# Runtime availability

The current repository should not be described as if all historical AI architecture is already active.

Today:

- the deterministic Kernel is active;
- capability discovery/execution is active;
- plugins are active;
- workflows are active;
- Personal and Workspace tool surfaces are active;
- MCP and integrations are active;
- Agent Computer is active;
- Personal and Workspace agent routes are mounted; model execution requires the opt-in flag and inference configuration described above.

Plugin runtime support is narrower than the historical manifest vocabulary:

| Workspace plugin mode | Current runtime support |
| --- | --- |
| `remote_http` | Reconciles a separately operated HTTPS endpoint and executes governed remote capabilities. |
| `sandbox_job` | Reconciles validated artifacts for isolated per-invocation Sandbox Runner execution. |
| `web_service`, `worker`, `static_site` | No end-to-end reconciliation adapters; unsupported for new publication, installation, activation and reconciliation. |

The manifest parser retains all historical modes, including reserved `platform_native`. API foundation, profiles, installations and runtime status expose `runtime_support`; declared modes are not a promise of deployment support. Historical static artifact serving remains available under its existing checks. See [the runtime support review](docs/plugin-runtime-support-2026-09-12.md) for lifecycle and compatibility details.

# Future directions

The accepted direction is documented in [the platform boundary](docs/dragonzpyder-platform-boundary.md) and [DragonZpyder V4](https://github.com/AaryanK/DragonZpyder/blob/develop/docs/universal-harness-v4.md). Operly should deepen governed business capabilities, plugin interoperability, workspace workflows and auditability while migrating general agent reasoning, model routing, Personal intelligence and native computer execution to DragonZpyder. Preserve business policy and compatibility during migration. Do not revive a competing agent runtime.

---

# Compatibility and historical architecture

Operly has changed architecture rapidly. Old names still appear in migrations and database models, including earlier Agent, Application Builder, custom-software, Studio and generated-project concepts.

Treat those as history unless a current boot path imports them.

The rule for future development is:

> **Reuse the valuable contract or product idea; do not revive an obsolete parallel runtime merely because its old tables or migrations still exist.**

When documentation disagrees with the current tree, inspect:

1. `apps/api/main.py` — what actually boots;
2. `packages/kernel` — the authority/execution core;
3. `packages/plugins` — capability extensibility;
4. `packages/workflow` — durable orchestration;
5. `packages/security` — scope and authority;
6. current tests/CI — enforced architecture.

---

# Design invariants

1. **The model is replaceable.**
2. **The model is not the authority boundary.**
3. **Human identity and runtime principal are separate concepts.**
4. **Personal and Workspace scopes are explicit.**
5. **Identity linking never merges authorization.**
6. **Delegation can narrow authority, never widen it.**
7. **Capability discovery does not grant capability execution.**
8. **Every invocation rechecks current authority.**
9. **Schemas and policy are application controlled.**
10. **Consequential effects may require durable approval.**
11. **Idempotency is part of correctness, not an optional convenience.**
12. **Provider success is not automatically verified completion.**
13. **Credentials belong to governed bindings, not prompts or generated code.**
14. **Workflows execute through the Kernel rather than bypassing it.**
15. **Plugins compose capability rather than create a privileged side door.**
16. **Events and webhooks preserve scope and provenance.**
17. **MCP/client access narrows live authority rather than replacing it.**
18. **Untrusted execution stays outside the FastAPI control plane.**
19. **Historical compatibility data must not regain architectural authority.**
20. **Future AI, software construction and agent-to-agent systems must reuse the same Kernel/security contracts.**

---

## One-sentence architecture

**Operly provides governed business capabilities and durable application workflows through a deterministic Kernel; DragonZpyder supplies the shared agent intelligence, and Operly independently enforces business authority for every effect.**
