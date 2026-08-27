# OPERLY

**OPERLY is a model-agnostic AI operating system, agent runtime, and capability harness for people and businesses.**

The model is replaceable. The durable product is the layer around the model: identity, context, permissions, capabilities, actions, connectors, software runtimes, artifacts, memory, and execution policy.

OPERLY is built around one principle:

> **Any authorized model should be able to understand the current person or workspace, discover the capabilities available to it, and safely use those capabilities to get work done.**

Today the repository contains a working control plane for Personal and Workspace AI, capability/plugin discovery, approval-backed actions, federated authorized context, multi-provider model routing, Discord and Google integrations, MCP exposure, business state, software construction, isolated generated-code execution, artifacts, and the web product.

---

## Mental model

```text
                    Human / Client / Channel
          Web · Personal AI · Workspace AI · Discord · MCP
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ Identity + Scope        │
                 │ human · account ·       │
                 │ workspace · channel     │
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ Agent Control Plane     │
                 │ objective · stages ·    │
                 │ context · validation    │
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ PluginAgentHarness      │
                 │ capability discovery   │
                 │ + authorization         │
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │ Capability Firewall     │
                 │ actions · approvals ·   │
                 │ policy · provenance     │
                 └────────────┬────────────┘
                              │
             ┌────────────────┼─────────────────┐
             ▼                ▼                 ▼
        Connectors       OPERLY services   Software/runtime
      Google/Discord     business/context  Studio/projects
      MCP/channels       memory/artifacts  coding/runner
             └────────────────┼─────────────────┘
                              ▼
                   Persistent OPERLY state
              database · history · source · events
                              │
                              ▼
                     Model Runtime
        Ollama · OpenRouter · Groq · Gemini · NVIDIA
```

A model never becomes the authority boundary. Identity, current scope, permissions, capability policy, action state, and live authorization remain owned by OPERLY.

---

## Current architecture

### 1. Identity and scope

OPERLY distinguishes the human from the interface being used.

The repository has first-class concepts for:

- authenticated users and sessions;
- account-level identity;
- workspaces and membership roles;
- external/channel identities;
- connector accounts;
- principals and execution context;
- workspace invitations;
- service/runtime bindings;
- user-local temporal context.

`packages/security/human_identity.py` projects the known authentication, external-provider, connector, principal, and membership records into one human identity graph without merging their permissions.

Workspace invitations are durable, expiring, one-time records. Identity linking is account-scoped, while workspace authority is resolved separately.

### 2. Federated authorized context

Personal AI can retrieve across authorized OPERLY history without treating every source as one unrestricted transcript.

`packages/context` contains the context broker, federation layer, provider-history adapters, and materialization rules. Current federation can include authorized Personal conversations, context records, workspace/channel history, business events, Gmail history, and Calendar history where the user has the required account/workspace authority.

Important rule:

> **A context reference is a locator, not a bearer token.**

Search can return a reference, but materialization rechecks current authorization before the underlying data is exposed.

### 3. Capabilities and plugins

Capabilities are OPERLY's common tool abstraction.

`packages/capabilities` contains capability contracts, providers, schemas, semantic discovery, session views, authorization, execution context, and the capability firewall.

A model session starts with a deliberately small discovery kernel. It can search for capabilities and describe exact schemas, but discovery and authorization remain separate. Progressive exposure lets the runtime reveal only the capabilities relevant to the current task instead of sending the entire tool surface to every model call.

First-party capabilities are registered through the plugin runtime. New connectors, model providers, lifecycle hooks, and capability providers should converge on this path rather than creating parallel agent-only registries.

### 4. Actions, approvals, and execution policy

OPERLY separates **reasoning** from **authority**.

Consequential capability calls flow through the capability firewall and Action lifecycle. Policy considers declared capability effects, data egress, external writes, destructive behavior, live authorization, and approval requirements.

A model may propose an operation without receiving permission to execute it. Durable action results and approval outcomes become system evidence and are not silently replayed.

### 5. Agent runtime

`packages/agents` contains the generic agent runtime, persistence, verification, capability rescue, run state, and the newer Factory control plane.

The runtime is provider-neutral: model calls, capability observations, durable run state, and verification are separate concerns.

---

## Agent Factory control plane

`packages/agents/control_plane` implements the newer orchestration layer for complex agent work.

The Factory owns root-task truth rather than delegating lifecycle semantics to a single model loop. Its core concepts include:

- immutable objective specifications;
- frozen acceptance contracts;
- stage DAGs;
- stage-specific context capsules;
- capability intents;
- dependency-scoped artifact distribution;
- evidence collection;
- deterministic/provider validation;
- semantic validation fallback;
- structured defects;
- bounded repair budgets;
- durable waiting and exact-stage resume;
- root completion truth.

The important separation is:

```text
Factory decides what must be accomplished
        │
        ▼
Worker receives only stage-relevant context + capabilities
        │
        ▼
PluginAgentHarness authorizes and executes real capabilities
        │
        ▼
Validation decides whether evidence satisfies the stage
        │
        ├── pass -> promote verified outputs
        ├── repairable -> bounded repair
        └── terminal/waiting -> persist exact state
```

Workers do **not** inherit a workspace-wide transcript, sibling-worker history, or failed artifacts. Failed attempt outputs remain audit evidence but are not promoted as trusted dependency inputs.

Approval continuation re-resolves the original run principal's current authority. An approving administrator does not donate broader permissions to the resumed worker.

Workspace AI can be bound to this control plane with `OPERLY_WORKSPACE_AGENT_FACTORY`; deployment enablement remains an explicit operational decision.

---

## Model runtime

Models are providers, not the operating system.

`packages/model_runtime` provides:

- provider-neutral inference contracts;
- model catalogs and resource metadata;
- live model discovery;
- provider/model route identity;
- capability/requirement filtering;
- adaptive scoring;
- reliability and latency feedback;
- cooldown and failure handling;
- role-based selection and fallbacks;
- provider activation policy;
- zero-cost eligibility policy;
- normalized attempt telemetry.

Current live catalog support includes:

- Ollama;
- OpenRouter;
- Groq;
- Gemini;
- NVIDIA.

The adaptive route flow is roughly:

```text
provider catalogs
    -> model-resource index
    -> provider activation policy
    -> cost/eligibility policy
    -> task capability filter
    -> live scorer
    -> ranked route batch
    -> model call
    -> telemetry/result
    -> scorer update
```

Each provider/model pair is a distinct route. The same canonical model hosted by two providers may rank differently because reliability, latency, rate limits, and runtime history differ.

### Current operating policy

The repository currently defaults to conservative model routing:

- `OPERLY_FREE_MODELS_ONLY=1` by default, so paid or unknown-cost routes are excluded from adaptive scoring;
- `OPERLY_ACTIVE_MODEL_PROVIDERS` defaults to `ollama`, providing a hard provider-level activation/hold switch;
- provider discovery may know about more routes than are currently eligible to invoke.

These are operating policies, not architectural limits. The higher-level runtime remains provider-neutral.

---

## Personal AI and Workspace AI

OPERLY has two important user-facing AI scopes.

### Personal Operly

Personal AI is account-scoped. It can use the user's authorized personal connectors and federated history, and may retrieve from multiple workspaces only where current membership and permissions allow it.

Personal attachments remain account-private. Their extracted content is treated as untrusted input before entering model context.

### Workspace Operly

Workspace AI operates inside the selected workspace's authority boundary. Workspace data, capabilities, roles, approvals, activity, and connectors are resolved for that scope.

Changing a frontend route is not enough to change scope: the backend session is explicitly switched before the UI enters another workspace.

---

## Channels, connectors, and external clients

External systems enter the same authorization/runtime architecture rather than receiving separate unrestricted agent implementations.

### Google Workspace

Google capabilities expose authorized account operations, including Gmail and Calendar functionality, through the normal capability system.

### Discord

`packages/connectors/discord` contains Discord lifecycle, secure runtime, commands, artifact/task delivery, identity linking, and workspace-aware channel behavior.

Discord identities are linked to OPERLY principals, but provider identity linking does not merge workspace permissions.

### MCP

`packages/mcp` exposes approved OPERLY capabilities to external AI clients through an MCP gateway with OAuth and policy enforcement. MCP is an interface to the capability system, not a bypass around it.

### Channels

`packages/channels` handles provider/channel envelopes, identity resolution, service routing, guest/channel bindings, and cross-interface delivery semantics.

---

## Business and company state

`packages/business`, `packages/business_brain`, and `packages/company` provide persistent business-aware state.

Current functionality includes business profiles, contacts, leads, products, inventory, orders, quotations, appointments, team members, documents, operational events, business context, scans, alerts, briefs, research hooks, attachments, and agent-facing business operations.

This lets OPERLY reason over a durable organization instead of treating every chat as a blank session.

---

## Artifacts and agent computer

Complex agent work is not limited to text messages.

`packages/artifacts` provides durable artifact storage/delivery semantics, while `packages/agent_computer` and related capability providers expose controlled file/computer/runtime operations.

Artifacts are first-class evidence for agent and Factory workflows. Verified outputs can be promoted and delivered across supported surfaces; failed or unverified attempt outputs remain isolated from downstream dependency trust.

---

## Software construction

OPERLY contains a source-aware software construction stack rather than a chat-only code generator.

The intended lifecycle is:

```text
request
  -> requirements/context analysis
  -> objective + acceptance contract
  -> plan/stage graph
  -> source-aware coding loop
  -> immutable source version
  -> isolated build/test/start/health checks
  -> validation + bounded repair
  -> preview
  -> iterative edits
  -> production lifecycle
```

Major components:

- `packages/software_projects` — canonical durable software-project identity and source/runtime state;
- `packages/solutions` — user-facing Solution registry and production lifecycle;
- `packages/studio` — source agent, Studio runs, rendering/design/runtime policy, traces and recovery;
- `packages/coding_harness` — persistent coding-agent execution, source tools, context management and build loop;
- `packages/custom_software` — requirements, planning, dependency/scope convergence and compatibility orchestration;
- `packages/application_builder` — older managed/schema-driven application generation retained for compatibility;
- `packages/service_bindings` — scoped project/service bindings without storing raw provider credentials;
- `packages/runtime_plugins` — trusted runtime contracts and registry.

Generated code does not execute inside the FastAPI control plane.

---

## Isolated runner

`apps/runner` is the production isolated execution service for generated full-stack software.

It is intentionally separate from the OPERLY API. Each build receives fresh container/network isolation. Generated software receives no Docker socket, host mounts, OPERLY database credentials, session secrets, model keys, connector credentials, or raw service-binding secrets.

The production runner uses a dedicated Docker host and constrained egress during dependency installation. Generated runtime traffic is exposed through an opaque preview boundary rather than directly publishing the generated container.

`apps/sandbox_runner` and local runner paths support development/test workflows, but the production isolation contract belongs to the dedicated runner service.

See [`apps/runner/README.md`](apps/runner/README.md) for the security and deployment contract.

---

## Web application

The web product is split by **route ownership**, not by two competing authenticated frontends.

### Canonical authenticated UI

`apps/web/src` is the React/Vite application and owns authenticated `/channels/**` routes, including:

- Personal Operly;
- workspace Home;
- Workspace Operly chat;
- CRM;
- Operations;
- Activity/approvals/tasks;
- Presence;
- Solutions;
- Connections;
- Plugins;
- Members;
- Access/MCP exposure.

The production image builds `apps/web/dist`, and FastAPI serves the React application for authenticated product routes.

### Compatibility shell

`apps/web/static` remains temporarily for public/authentication flows and protected legacy Studio paths that have not yet completed migration. It is **not** the canonical owner of normal authenticated `/channels/**` product development.

See [`apps/web/FRONTEND_MIGRATION.md`](apps/web/FRONTEND_MIGRATION.md).

---

## Repository map

| Path | Responsibility |
| --- | --- |
| `apps/api` | FastAPI control plane, auth/session APIs, routers, security middleware and browser delivery |
| `apps/web/src` | Canonical React/Vite authenticated product UI |
| `apps/web/static` | Public/auth and temporary legacy compatibility surfaces |
| `apps/runner` | Production isolated generated-software runner |
| `apps/sandbox_runner` | Sandbox/development execution support |
| `packages/agents` | Generic agent runtime, persistence, verification and Factory control plane |
| `packages/agents/control_plane` | Objective/stage DAG orchestration, context distribution, evidence, validation and repair |
| `packages/capabilities` | Capability contracts, discovery, providers, session exposure and firewall |
| `packages/capability_sandbox` | Capability-resolution benchmarks and safety tests |
| `packages/actions` | Durable action policy/lifecycle support |
| `packages/model_runtime` | Provider-neutral inference, discovery, scoring, provider/cost policy and routing |
| `packages/security` | Principals, roles, authorization, human identity, invitations and execution context |
| `packages/context` | Durable context, federation, provider-history adapters and retrieval |
| `packages/workspace` | Workspace-level services |
| `packages/channels` | Cross-channel identity, envelopes, routing and bindings |
| `packages/connectors` | Google Workspace, Discord and connector runtime |
| `packages/mcp` | MCP gateway, OAuth and capability policy |
| `packages/artifacts` | Durable agent artifacts and delivery |
| `packages/agent_computer` | Controlled computer/file/runtime execution bridge |
| `packages/business` | Core business data/services |
| `packages/business_brain` | Business-aware agent/runtime, Factory binding, tools and attachments |
| `packages/company` | Company state, events, research and intelligence |
| `packages/software_projects` | Canonical software-project persistence |
| `packages/solutions` | Solution registry and production lifecycle |
| `packages/studio` | Source-aware Studio agent and software editing runtime |
| `packages/coding_harness` | Persistent coding-agent/source execution loop |
| `packages/custom_software` | General software planning/build/repair compatibility orchestration |
| `packages/application_builder` | Managed application generation compatibility layer |
| `packages/service_bindings` | Project/workspace semantic service bindings |
| `packages/plugins` | Plugin manifests and plugin support |
| `packages/runtime_plugins` | Runtime plugin contracts and registry |
| `packages/database` | SQLAlchemy models, services and migration helpers |
| `alembic/versions` | Authoritative schema history |
| `tests` | Unit, integration, security, model, connector, Factory, harness and runner tests |

---

## Local setup

Requirements:

- Python 3.11+;
- Node.js for the React frontend build/development workflow;
- SQLite for simple local development or PostgreSQL for production-like use.

### Backend

```powershell
Copy-Item .env.example .env
# Configure at minimum the session/admin/model settings you need.

uv venv
uv pip install -r requirements.txt
uv run python -m packages.database.migrate upgrade
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open `http://localhost:8000`.

### React frontend development

```powershell
cd apps/web
npm install
npm run dev
```

Production builds the frontend into the main image; the Vite dev server is only for frontend development.

### Discord connector

```powershell
uv run python -m packages.connectors.discord.bot_harness
```

### Local generated-software runner

Use the separate development runner sidecar when testing the production control-plane protocol locally. See [`apps/runner/README.md`](apps/runner/README.md) for the required environment and safety restrictions.

---

## Configuration

Start with [`.env.example`](.env.example).

Important configuration families include:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLite/PostgreSQL connection |
| `SESSION_SECRET` | Authenticated session signing secret |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Bootstrap owner account |
| `PUBLIC_BASE_URL` | Canonical public origin |
| `MAIL_PROVIDER`, `ZOHO_MAIL_*` | Transactional email |
| `OLLAMA_*` | Ollama endpoint/auth/model compatibility settings |
| `OPENROUTER_*` | OpenRouter access/discovery |
| `GROQ_API_KEY` | Groq model catalog/runtime access |
| `GEMINI_API_KEY` | Gemini model catalog/runtime access |
| `NVIDIA_API_KEY` | NVIDIA model catalog/runtime access |
| `OPERLY_ACTIVE_MODEL_PROVIDERS` | Hard allowlist of providers currently permitted to invoke |
| `OPERLY_FREE_MODELS_ONLY` | Fail-closed zero-cost route policy; defaults on |
| `OPERLY_MODEL_DISCOVERY_TTL_SECONDS` | Live provider catalog refresh TTL |
| `OPERLY_MODEL_ROUTE_BATCH_SIZE` | Bounded adaptive candidate batch |
| `OPERLY_MODEL_<ROLE>` | Optional role-specific provider/model assignment |
| `OPERLY_MODEL_<ROLE>_FALLBACKS` | Role-specific fallback routes |
| `OPERLY_WORKSPACE_AGENT_FACTORY` | Workspace Agent Factory deployment switch |
| `OPERLY_SANDBOX_RUNNER_URL` | External isolated runner origin |
| `OPERLY_SANDBOX_RUNNER_TOKEN` | Runner HMAC secret |
| `DISCORD_BOT_TOKEN` | Discord connector |
| `OPERLY_CONNECTOR_SECRET_KEY` | Connector credential encryption |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth application |

Do not copy OPERLY database credentials, model-provider keys, Gmail credentials, session secrets, or connector secrets onto the generated-code runner host.

---

## Database, tests, and CI

Alembic revisions are the authoritative database history.

```powershell
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run pytest -q
```

The repository has focused CI for application flow, runtime hardening, relational data, task/workflow contracts, the unified agent runtime, capability sandbox behavior, and coding-harness/runner paths.

Before releasing, follow [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

Useful architecture notes:

- [`docs/TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md)
- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
- [`docs/MULTI_PROVIDER_MODELS.md`](docs/MULTI_PROVIDER_MODELS.md)
- [`docs/PLUGIN_HARNESS.md`](docs/PLUGIN_HARNESS.md)
- [`docs/model-runtime.md`](docs/model-runtime.md)
- [`packages/coding_harness/ARCHITECTURE.md`](packages/coding_harness/ARCHITECTURE.md)
- [`apps/web/FRONTEND_MIGRATION.md`](apps/web/FRONTEND_MIGRATION.md)
- [`apps/runner/README.md`](apps/runner/README.md)

---

## Current migration boundaries

OPERLY is moving quickly, and `main` still contains compatibility layers from earlier product generations.

The important boundaries today are:

- React/Vite is canonical for authenticated `/channels/**`, while the static shell remains for public/auth and protected legacy Studio migration;
- Agent Factory is implemented, but Workspace deployment is controlled explicitly by `OPERLY_WORKSPACE_AGENT_FACTORY`;
- the universal capability system is canonical, while some older coding and software-generation registries remain as compatibility bridges;
- `SoftwareProject` is the canonical software identity, while Studio/managed/generated legacy records still exist for compatibility;
- production generated-code execution depends on the separately operated isolated runner;
- model discovery spans multiple providers, but invocation is additionally constrained by active-provider and cost policy;
- provider and connector maturity is not uniform;
- not every historical subsystem has completed plugin/runtime convergence yet.

New architecture should strengthen the common identity → context → capability → action → runtime path rather than creating another special-case agent stack.

---

## Design principles

1. **Models are replaceable.** Provider choice must not define product architecture.
2. **Context is authorized, reference-first, and revalidated.** Retrieval is not authority.
3. **Capabilities are composable.** Interfaces should reuse the same underlying operations.
4. **Discovery is not permission.** A model may know a capability exists without being allowed to invoke it.
5. **Consequential effects are durable actions.** Approvals and terminal outcomes are system truth.
6. **Workers do not own completion truth.** The control plane validates evidence against the objective.
7. **Generated code is untrusted.** It executes outside the OPERLY control plane.
8. **Identity follows the human; authority follows the current scope.** Linking identities never silently merges permissions.
9. **Everything should converge on plugins/capabilities instead of parallel special-case runtimes.**

**Models are replaceable. Capabilities are composable. Context is persistent and authorized. Authority is explicit. Execution is verifiable.**
