# OPERLY

**OPERLY is a governed execution platform for people, workspaces, plugins, durable workflows, integrations, MCP clients, and future AI agents.**

The current repository is intentionally centered on a deterministic Kernel and capability runtime. Models are not the authority boundary, and in the current production boot path the AI runtime is deliberately disabled. The platform underneath it—identity, scope, permissions, capability discovery, approvals, idempotency, plugin execution, workflows, events, connectors, artifacts, audit, and runtime isolation—is the product foundation.

> **The long-term goal is simple: any authorized model or client should be able to understand the current person or workspace, discover only the capabilities available to it, and safely use those capabilities through the same governed runtime.**

This README describes the repository **as it exists on `main` today**. Older subsystem names still appear in migrations and database models because Operly has gone through several architecture generations; those names should not be mistaken for currently booted runtime packages.

---

## Current snapshot

The FastAPI application identifies itself as:

```text
0.10.0-universal-workflows
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

The current boot path explicitly reports:

```text
kernel_runtime_enabled = true
ai_runtime_enabled     = false
```

That distinction matters. The repository already contains much of the infrastructure an AI runtime will need, but the currently booted system does not pretend that a model-driven agent loop is live when it is not.

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
     direct route     Workflow       future AI
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

A web route, MCP client, scheduled workflow, Discord action, hosted plugin, and future AI agent should not each grow its own security model.

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

# What is deliberately not live yet

The current repository should not be described as if all historical AI architecture is already active.

Today:

- the deterministic Kernel is active;
- capability discovery/execution is active;
- plugins are active;
- workflows are active;
- Personal and Workspace tool surfaces are active;
- MCP and integrations are active;
- Agent Computer is active;
- **the general AI runtime is disabled in the booted application.**

That is a feature of the current stabilization phase, not a statement that AI is no longer part of Operly's direction.

---

# Future directions

The following directions preserve the strongest ideas from earlier Operly architecture generations without restoring their old parallel implementations.

## 1. Model-driven Operly agent on top of the Kernel

Reintroduce a provider-neutral AI runtime only after it can behave as an ordinary Kernel client.

The future agent should:

```text
user request
  -> authenticated scope
  -> bounded context
  -> capability search
  -> capability describe
  -> model reasoning
  -> Kernel invocation
  -> approval / execution / verification
  -> trace + durable output
```

The model must never own permission, credentials, approval or completion truth.

## 2. Persistent Personal AI identity

Build Personal AI as a durable identity layer rather than a stateless chatbot session.

Future work should include:

- stable Personal AI identity across surfaces;
- human-controlled preferences;
- durable private conversations;
- persistent memory with provenance;
- explicit forget/retain controls;
- separation between private Personal context and Workspace context;
- cross-workspace reasoning only under the human's own authorization.

## 3. Memory and context architecture

Bring back the earlier strong distinction between short-term working context and durable memory, but implement it over the current Kernel/security model.

The target architecture should support:

- bounded recent conversation context;
- long-term memory retrieval instead of prompt replay;
- semantic and structured retrieval;
- reference-first context materialization;
- reauthorization when materializing a stored reference;
- memory compression and summarization;
- token-efficient context packing;
- provenance, confidence and staleness;
- explicit user deletion/forgetting.

The goal is to avoid gigantic prompts while still giving the agent continuity.

## 4. Long-horizon agent orchestration

Reintroduce the useful ideas from the previous Agent Controller / Agent Factory work as a Kernel-native orchestration layer:

- objective and acceptance contracts;
- planning only when complexity justifies it;
- stage/DAG execution;
- specialist workers;
- resumable checkpoints;
- minimum stage-specific context;
- evidence promotion;
- deterministic validators;
- bounded repair;
- waiting/approval state;
- exact-stage resume;
- no replay of already-resolved external effects.

This should be implemented as orchestration over the current capability runtime, not as a second capability system.

## 5. Dynamic tool discovery at scale

Expand the current search-then-describe direction into a universal capability discovery protocol.

A model should not receive every installed tool schema. It should receive a tiny permanent kernel and discover relevant capabilities on demand.

Future discovery should span:

- Personal tools;
- workspace tools;
- installed plugins;
- external MCP capabilities;
- generated-runtime service bindings;
- future third-party providers.

## 6. Richer Personal-to-Workspace orchestration

Personal Operly should eventually operate naturally across all scopes the human is authorized to use:

```text
"From ANHITRA, email the supplier."
"Now compare that with NaySchool."
"Use my personal calendar for availability."
```

The resolver should preserve which scope owns each resource and capability and ask for disambiguation when necessary.

The reverse direction must remain denied by default: a workspace does not inherit a member's Personal memory, connectors or other workspaces.

## 7. Software construction and Studio

The current repository retains Studio surfaces and historical software-generation data, but the earlier multi-package software stack is no longer the active architecture.

Future Studio should be rebuilt as a workspace module/plugin family over the same Kernel contracts:

- persistent software project identity;
- source versions;
- planning and coding sessions;
- build/test/repair loops;
- visual editing;
- artifact generation;
- deployment contracts;
- generated-app identity;
- project-scoped data bindings;
- verified preview/production lifecycle;
- genuine isolated execution.

Generated code must not receive Operly database, connector or model credentials directly.

## 8. Third-party plugin ecosystem

Grow `operly.plugin/v1` into a stable developer ecosystem with:

- versioned manifests;
- typed capability contracts;
- install/uninstall lifecycle;
- configuration schemas;
- secret/credential bindings;
- permissions and risk metadata;
- webhook/event declarations;
- runtime health;
- isolated execution profiles;
- compatibility negotiation;
- plugin publishing/distribution.

A future marketplace should remain a discovery/distribution layer, not a security bypass.

## 9. Agent and system interoperability

MCP is already a first interoperability boundary. Future work should extend that philosophy toward:

- agent-to-agent communication;
- A2A-style task exchange;
- capability negotiation;
- delegated credentials without raw secret transfer;
- workload identity;
- scoped callbacks/events;
- resumable cross-agent tasks;
- provenance across agent boundaries.

## 10. More channels and external surfaces

Add new channels as thin adapters into the same runtime:

- Slack;
- WhatsApp Business;
- SMS/voice;
- email-native workflows;
- mobile clients;
- additional social/business surfaces.

No channel should implement its own business-agent security model.

## 11. Workflow platform maturity

Continue hardening the newly consolidated Workflow runtime with:

- larger multi-worker HA testing;
- contention/failover testing;
- event-storm backpressure;
- dead-worker recovery;
- concurrency queues and cancellation semantics;
- richer branching/fan-out/fan-in;
- workflow observability and replay diagnostics;
- workflow templates;
- human-in-the-loop tasks;
- distributed execution where needed.

## 12. Runtime observability and AI Debug

Build one explainable trace across:

```text
request
-> scope resolution
-> capability discovery
-> policy
-> approval
-> provider/plugin call
-> workflow continuation
-> delivery
-> verification
```

When AI runtime returns, model/provider calls should fit into that same trace rather than create a separate debugging system.

## 13. Repository and release governance

Continue tightening the non-runtime side of reliability:

- required regression CI before merge;
- protected `main`;
- dependency/security update discipline;
- migration verification;
- deployment evidence separated from test evidence;
- explicit break-glass procedures.

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

**OPERLY resolves a human and scope, exposes only governed capabilities, executes them through a deterministic Kernel with policy, approvals, idempotency and audit, coordinates durable workflows and plugins around that boundary, and leaves model-driven intelligence as a replaceable layer that can be added without weakening the system underneath it.**
