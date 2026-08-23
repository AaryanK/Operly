# OPERLY

**OPERLY is a model-agnostic AI operating system and capability harness for people and businesses.**

It gives an AI model persistent context, identity, permissions, tools, external services, software-construction abilities, and controlled ways to act in the real world. The model is replaceable. The harness is the product kernel.

OPERLY can currently combine business state, conversations, memory, CRM data, Google Workspace, Discord, reminders, approvals, websites, generated software, coding agents, and other capabilities behind one authorization and execution layer.

The long-term direction is simple:

> **Any authorized model should be able to understand the user or company, discover the capabilities available to it, and safely use those capabilities to get work done.**

---

## What OPERLY is

Most AI products hard-code a model into a fixed application.

OPERLY separates those concerns:

```text
                     Human / Client / Channel
                  Web · Discord · MCP · API · AI
                              │
                              ▼
                    ┌───────────────────┐
                    │   OPERLY HARNESS  │
                    │ identity · context│
                    │ policy · runtime  │
                    └─────────┬─────────┘
                              │
                     authorized capability set
                              │
                    ┌─────────▼─────────┐
                    │ Capability Layer  │
                    │ registry/firewall │
                    │ discovery/schemas │
                    └─────────┬─────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       ▼                      ▼                      ▼
   Connectors             OPERLY services       Construction
 Google / Discord         business / CRM        Studio
 email / channels         memory / actions      apps / websites
 MCP / external APIs      company state         coding agents
                                                  runners
       └──────────────────────┬──────────────────────┘
                              ▼
                     persistent workspace
                   database · context · history
                              │
                              ▼
                        Model Runtime
              Ollama · OpenRouter · compatible APIs
```

The same capability can be exposed to OPERLY's own AI, an external AI client, a channel such as Discord, or another authorized principal without rebuilding the underlying business logic for every interface.

---

## Core architecture

### 1. Harness

The harness resolves the current principal, workspace, conversation, temporal context, permissions, and available services before a model acts.

The important boundary is not *which model is running*. It is **what that model is authorized to see and do in the current context**.

Relevant code lives primarily in:

- `packages/harness`
- `packages/agents`
- `packages/security`
- `packages/context`
- `packages/workspace`
- `packages/service_bindings`

### 2. Capability system

Capabilities are OPERLY's common tool abstraction.

`packages/capabilities` contains contracts, registration, discovery, authorization, runtime context, capability providers, and the capability firewall. Providers currently cover areas such as:

- actions and approvals;
- business operations;
- context and history;
- CRM reads;
- Gmail drafts and Google Workspace operations;
- reminders and personal utilities;
- model access;
- software projects;
- Solutions;
- Studio;
- websites;
- workspace operations.

Low-risk authorized capabilities can be exposed directly to the agent. Capabilities that require additional discovery, stronger authorization, or approval remain bounded by policy rather than being handed to the model indiscriminately.

`packages/capability_sandbox` provides target-resolution and benchmark tooling for exercising this boundary.

### 3. Model runtime

Models are providers, not the operating system.

`packages/model_runtime` provides:

- a provider-neutral model registry;
- model catalogs and discovery;
- Ollama support;
- OpenRouter support and discovery;
- generic OpenAI-compatible endpoints;
- semantic routing;
- role-based model portfolios;
- fallback and routing policy.

Different roles such as planning, coding, repair, validation, placement, or bounded agent work can use different models without changing the higher-level OPERLY contracts.

### 4. Identity, workspaces, and authorization

OPERLY maintains explicit security context around every action.

The repository contains first-class concepts for:

- users and authenticated sessions;
- principals and external clients;
- workspace membership and RBAC;
- channel identities;
- conversation ownership;
- execution context;
- temporal/user-local context;
- action provenance;
- approvals;
- encrypted connector credentials.

The goal is for capability access to follow the human and workspace rather than whichever interface happens to invoke it.

### 5. Persistent context and memory

OPERLY is designed around durable context rather than isolated prompts.

It stores and reconstructs context from areas including:

- conversations;
- business/company state;
- actions and activity;
- channel messages;
- workspace history;
- human memory;
- software projects;
- Studio runs and model traces.

Context providers decide what should be surfaced into the current model interaction instead of blindly stuffing the entire database into a prompt.

---

## Product layers

The current repository effectively contains three closely related product layers.

### OPERLY AI

The conversational agent layer.

It receives the current context and authorized capabilities, reasons with the configured model runtime, invokes tools, requests approval where necessary, and persists relevant results.

### OPERLY OS

The persistent operating layer underneath the agent.

It contains workspaces, identities, permissions, context, business records, company intelligence, actions, connectors, channels, approvals, memory, and the capability registry.

### OPERLY Studio

The software-construction environment.

It allows an agent to plan, create, inspect, edit, validate, run, repair, preview, and version software rather than merely returning code in chat.

---

## Business and company intelligence

`packages/business`, `packages/business_brain`, and `packages/company` provide the company-aware layer of OPERLY.

Current systems include:

- business profiles and operational records;
- contacts, leads, products, inventory, orders, quotations, appointments, team members, and documents;
- business induction and context loading;
- operational scans, alerts, briefs, audits, and operating plans;
- company state and event history;
- company attention/intelligence;
- research and web-research hooks;
- attachment processing for documents, images, archives, and multimodal inputs.

This allows the model to reason over a persistent company rather than treating each conversation as a blank session.

---

## Connectors and channels

External systems enter the same harness instead of becoming separate agent implementations.

Current connector/channel work includes:

### Google Workspace

The Google provider exposes authorized Workspace operations through the capability layer, including Gmail and calendar-related functionality.

### Discord

`packages/connectors/discord` contains the bot lifecycle, secure runtime, provider, slash commands, channel integration, and harness-facing behavior.

Discord messages can participate in OPERLY context while remaining subject to workspace identity and authorization boundaries.

### Email

OPERLY has a transactional email subsystem with SMTP, Zoho Mail API, development-memory providers, and HTML templates for verification, password reset, welcome, password-change, and security notifications.

### MCP

`packages/mcp` provides an MCP gateway, OAuth support, and policy enforcement so external AI clients can access approved OPERLY capabilities through the same security model rather than receiving unrestricted backend access.

### Channels and identity

`packages/channels` handles channel envelopes, identity resolution/linking, guest chat, service routing, and space/workspace bindings.

---

## Software construction

OPERLY contains both higher-level application generation and a lower-level coding harness.

The intended construction lifecycle is roughly:

```text
request
  -> context and requirements analysis
  -> capability/dependency graph
  -> clarification only when materially necessary
  -> plan and acceptance criteria
  -> persistent coding-agent tool loop
  -> immutable source snapshot
  -> isolated build/test/start/health checks
  -> validation and bounded repair
  -> preview
  -> iterative source-aware editing
```

Major components include:

- `packages/application_builder` — schema-driven managed applications;
- `packages/custom_software` — planning, graph coverage, dependency resolution, scope convergence, source generation, runner orchestration, validation and repair;
- `packages/coding_harness` — persistent coding-agent execution, context-window management, source tools, model resolution, build service and execution loop;
- `packages/software_projects` — durable project abstraction and persistence;
- `packages/solutions` — solution registry, production lifecycle and deployment-oriented state;
- `packages/studio` — source agent, agent runs, model traces, rendering, design/runtime policy and terminal recovery.

Generated code is not executed directly inside the FastAPI control plane. Production-grade generation is designed to use an isolated runner with bounded resources and explicit validation.

See [`packages/coding_harness/ARCHITECTURE.md`](packages/coding_harness/ARCHITECTURE.md) for the coding/runtime design.

---

## Solutions

A **Solution** is the user-facing unit for software or operational functionality launched through OPERLY.

A Solution may contain any combination of:

- a website or public digital presence;
- an internal application;
- a customer portal;
- backend services;
- workflows;
- agents;
- integrations;
- generated software;
- operational capabilities.

The Solution layer is intentionally above the lower-level runtime implementations so a business outcome does not have to be defined by whether it happened to be built as a website, managed application, generated project, workflow, or agent.

---

## Actions, approvals, and safety

OPERLY distinguishes reasoning from authority.

The model can propose or plan an operation without automatically receiving permission to perform every consequential action.

Important safety boundaries include:

- workspace- and principal-scoped data access;
- capability authorization before tool exposure;
- capability firewall and runtime validation;
- explicit approvals for consequential actions where required;
- action provenance and history;
- CSRF protection, secure sessions, security headers, and request safety;
- encrypted connector credentials;
- immutable/versioned generated source;
- isolated execution of generated software;
- fail-closed behavior when required production infrastructure is unavailable.

The objective is **maximum useful capability with explicit authority boundaries**, not an artificially weak agent and not an unrestricted one.

---

## Current web application

The backend is a FastAPI application under `apps/api`.

The repository currently contains two frontend generations:

- `apps/web/static` — the current browser interface served by FastAPI;
- `apps/web/src` — a newer React/Vite application that is being developed alongside the existing static shell.

The static application contains accumulated product surfaces and compatibility layers from several stages of OPERLY's development. Consolidating these frontend generations is an active architectural cleanup opportunity.

---

## Repository map

| Path | Responsibility |
| --- | --- |
| `apps/api` | FastAPI control plane, auth, API routers, sessions, security and browser delivery |
| `apps/web/static` | Current browser application and legacy/transition UI surfaces |
| `apps/web/src` | React/Vite frontend source |
| `packages/capabilities` | Capability contracts, providers, discovery, registry and firewall |
| `packages/capability_sandbox` | Capability resolution benchmarks and tests |
| `packages/harness` | Core agent/tool harness abstractions |
| `packages/agents` | Agent runtime |
| `packages/model_runtime` | Provider-neutral model discovery, routing and execution |
| `packages/security` | Principals, permissions and execution context |
| `packages/context` | Durable context service |
| `packages/workspace` | Workspace-level services |
| `packages/service_bindings` | Service-to-workspace/principal bindings |
| `packages/channels` | Cross-channel identity, envelopes and workspace bindings |
| `packages/connectors` | Google Workspace, Discord and connector runtime |
| `packages/mcp` | MCP gateway, OAuth and policy |
| `packages/email` | Transactional email service and templates |
| `packages/business` | Core business services |
| `packages/business_brain` | Business-aware agent, tools, attachments and operations |
| `packages/company` | Company state, events, context, research and intelligence |
| `packages/application_builder` | Managed application generation |
| `packages/custom_software` | General software planning/build/runner/repair orchestration |
| `packages/coding_harness` | Persistent coding-agent and source execution loop |
| `packages/software_projects` | Universal software project persistence |
| `packages/solutions` | Solution registry and production lifecycle |
| `packages/studio` | Source-aware AI software editing and agent runs |
| `packages/plugins` | Plugin manifests and plugin runtime |
| `packages/runtime_plugins` | Runtime plugin contracts and registry |
| `packages/database` | SQLAlchemy models, database services and migration helpers |
| `alembic/versions` | Authoritative schema history |
| `tests` | Unit, integration, security, model, connector, planning, harness and runner tests |

---

## Local setup

Requirements: **Python 3.11+**. PostgreSQL is recommended for production; SQLite is supported for local development.

```powershell
Copy-Item .env.example .env
# Configure SESSION_SECRET, ADMIN_PASSWORD, and model settings.

uv venv
uv pip install -r requirements.txt
uv run python -m packages.database.migrate upgrade
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open `http://localhost:8000`.

To run the Discord connector separately:

```powershell
uv run python -m packages.connectors.discord.bot_harness
```

---

## Configuration

Start with [`.env.example`](.env.example).

Important settings include:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLite development database or PostgreSQL connection |
| `SESSION_SECRET` | Authenticated session signing secret |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Bootstrap owner account |
| `PUBLIC_BASE_URL` | Canonical production origin |
| `MAIL_PROVIDER` | Transactional email provider |
| `ZOHO_MAIL_*` | Zoho Mail API/OAuth configuration |
| `OLLAMA_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL` | Ollama provider configuration |
| `OPENROUTER_API_KEY` | OpenRouter provider access when enabled |
| `OPERLY_MODEL_<ROLE>` | Provider/model assignment for an OPERLY role |
| `OPERLY_MODEL_<ROLE>_FALLBACKS` | Role-specific fallback portfolio |
| `OPERLY_PLANNING_MODE` | Planning implementation selection |
| `OPERLY_SANDBOX_RUNNER_URL`, `OPERLY_SANDBOX_RUNNER_TOKEN` | External isolated runner |
| `OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER` | Local/test runner only |
| `DISCORD_BOT_TOKEN` | Discord connector |
| `OPERLY_CONNECTOR_SECRET_KEY` | Connector credential encryption |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth application |
| `GOOGLE_OAUTH_REDIRECT_URI` | Registered Google OAuth callback |

Production additionally requires HTTPS, strong unique secrets, PostgreSQL, completed migrations, verified backups, and an isolated runner when generated-software execution is enabled.

---

## Database and tests

Alembic revisions are the authoritative database history.

```powershell
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run pytest -q
```

CI currently includes application-flow, capability-sandbox, and coding-harness smoke workflows.

Before releasing, follow [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

Architecture and implementation notes are under [`docs/`](docs/), including:

- [`docs/TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md)
- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
- [`docs/MULTI_PROVIDER_MODELS.md`](docs/MULTI_PROVIDER_MODELS.md)
- [`docs/PLUGIN_HARNESS.md`](docs/PLUGIN_HARNESS.md)
- [`docs/model-runtime.md`](docs/model-runtime.md)

---

## Current limitations

OPERLY is evolving quickly and the repository still contains architectural layers from earlier product iterations.

Notable current boundaries:

- the frontend is split between the current static application and a newer React/Vite implementation;
- some older routers, patch scripts, compatibility surfaces, and generation paths remain in the repository;
- capability coverage is broad but not every service has been normalized behind the capability layer yet;
- production generated-software execution depends on separately operated isolated-runner infrastructure;
- successful source generation and preview do not automatically imply production deployment;
- connectors currently have different levels of maturity and capability coverage;
- the plugin architecture exists, but further consolidation is still needed before every subsystem is truly interchangeable as a plugin.

These are implementation gaps, not changes to the architectural direction.

---

## Design principle

OPERLY should not need a special-case architecture for every new model, integration, application, channel, or business tool.

**Models are replaceable. Capabilities are composable. Context is persistent. Authority is explicit. Everything else should plug into the harness.**
