# OPERLY

**OPERLY is a model-agnostic AI operating system, agent runtime, capability fabric, and software execution platform for people and businesses.**

The model is replaceable. The durable product is the layer around the model: human identity, principals, scope, permissions, context, capabilities, actions, approvals, events, connectors, tasks, software projects, generated runtimes, artifacts, traces, and policy.

> **Any authorized model should be able to understand the current person or workspace, discover the capabilities available to it, and safely use those capabilities to get work done.**

This README is intentionally long. OPERLY is now large enough that a short product summary is actively misleading. The goal here is to explain the repository **as it exists on `main`**, including the canonical paths, compatibility seams, execution boundaries, and the responsibility of every top-level package.

---

## Table of contents

1. [What OPERLY actually is](#what-operly-actually-is)
2. [The executable services](#the-executable-services)
3. [End-to-end request flow](#end-to-end-request-flow)
4. [Core truth boundaries](#core-truth-boundaries)
5. [Identity, scope, delegation, and provenance](#identity-scope-delegation-and-provenance)
6. [Agent runtime and Agent Factory](#agent-runtime-and-agent-factory)
7. [Capabilities, plugins, actions, and approvals](#capabilities-plugins-actions-and-approvals)
8. [Context, memory, and federated history](#context-memory-and-federated-history)
9. [Model runtime and adaptive routing](#model-runtime-and-adaptive-routing)
10. [Tasks and workflow runtime](#tasks-and-workflow-runtime)
11. [Business and company intelligence](#business-and-company-intelligence)
12. [Software construction, Solutions, and Studio](#software-construction-solutions-and-studio)
13. [Generated-runtime data, identity, and service bindings](#generated-runtime-data-identity-and-service-bindings)
14. [Artifacts, files, and Agent Computer](#artifacts-files-and-agent-computer)
15. [Channels, Discord, Google, and MCP](#channels-discord-google-and-mcp)
16. [Tracing and AI Debug](#tracing-and-ai-debug)
17. [Frontend architecture](#frontend-architecture)
18. [FastAPI control plane](#fastapi-control-plane)
19. [Production runner and sandbox runtime](#production-runner-and-sandbox-runtime)
20. [Repository anatomy: every top-level package](#repository-anatomy-every-top-level-package)
21. [Database and migrations](#database-and-migrations)
22. [Scripts](#scripts)
23. [CI workflows](#ci-workflows)
24. [Tests](#tests)
25. [Deployment files](#deployment-files)
26. [Local development](#local-development)
27. [Configuration](#configuration)
28. [Compatibility and migration seams](#compatibility-and-migration-seams)
29. [Architecture documents](#architecture-documents)
30. [Design invariants](#design-invariants)

---

# What OPERLY actually is

OPERLY is not one chatbot, one CRM, one website builder, or one model wrapper. It is a shared operating layer that tries to make all of those things use the same identity, scope, capability, policy, event, runtime, and persistence contracts.

At a high level:

```text
Human / external identity / generated app / scheduled task / AI client
                              │
                              ▼
                  identity + authenticated scope
                              │
                              ▼
                    ExecutionContext
          personal | workspace | guest workspace
                              │
                              ▼
                agent / workflow / direct API
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
       Agent control plane                 direct service
   objective / stages / context                 │
             │                                  │
             └──────────────┬───────────────────┘
                            ▼
                  capability session view
                search / describe / expose
                            │
                            ▼
                   PluginAgentHarness
                            │
                            ▼
                  CapabilityFirewall
     authority / schema / effect / egress / approval
                            │
                            ▼
                      ActionService
          proposed -> approved -> executed -> verified
                            │
       ┌────────────────────┼────────────────────────┐
       ▼                    ▼                        ▼
 internal services      connectors             isolated runner
 business/context       Google/Discord         generated software
 tasks/projects         external APIs          Agent Computer
       └────────────────────┼────────────────────────┘
                            ▼
                 events / artifacts / traces
                            │
                            ▼
                    persistent database
```

Two principles explain most of the repo:

1. **The model is never the authority boundary.**
2. **A thing being discoverable does not mean it is authorized or executable.**

---

# The executable services

The repository contains four application/runtime surfaces under `apps/`.

| Path | What runs there | Trust boundary |
| --- | --- | --- |
| `apps/api` | Main FastAPI control plane and browser/API server | Trusted OPERLY application process |
| `apps/web` | React/Vite product UI plus temporary static compatibility shell | Browser client |
| `apps/runner` | Production isolated runner for generated software | Separate dedicated Docker host / untrusted-job boundary |
| `apps/sandbox_runner` | Node-based sandbox/computer runtime and contract tests | Agent-computer / sandbox boundary; not the same as production full-stack isolation |

The API process owns authentication, authorization, orchestration, persistence, capability execution, connector access, and the web application. Generated software is deliberately kept out of that process.

---

# End-to-end request flow

A normal Workspace Operly request roughly follows this path:

```text
React / Discord / API
  -> authenticated identity
  -> scope resolution
  -> ExecutionContext
  -> conversation + bounded recent messages
  -> governed context retrieval
  -> Workspace agent
  -> optional Agent Factory
  -> model selection
  -> capability discovery/exposure
  -> PluginAgentHarness
  -> CapabilityFirewall
  -> ActionService
  -> provider/service execution
  -> verification
  -> evidence + events + traces
  -> model continuation / Factory validation
  -> persisted assistant output
```

More concretely:

1. **Ingress resolves who is acting.** The application resolves an OPERLY user, a linked external principal, or a guest principal.
2. **Scope is explicit.** Personal authority and workspace authority are different `ScopeKind` values. Merely focusing a workspace in Personal AI does not silently turn that workspace into authority.
3. **Recent conversation is bounded.** Durable history is not blindly replayed into every prompt.
4. **Longer history is retrieved through context services.** Search returns authorized references; materialization rechecks authorization.
5. **The agent runtime gets a model and a capability view.** It does not receive every backend function.
6. **Complex work may enter the Factory.** The Factory freezes an objective/acceptance contract and distributes stage-specific context.
7. **A capability call crosses the harness and firewall.** Schema, permission, surface, delegation, effect, egress, and approval policy are application-controlled.
8. **Consequential work becomes a durable Action.** The model does not directly mutate the outside world.
9. **Execution is verified.** The action state machine distinguishes provider success from verified completion.
10. **Events preserve attribution.** Initiator, entry surface, AI/direct mediation, executor, delegation, action, and timestamp can be reconstructed.
11. **Traces record what happened.** Model packets, routing events, capability events, approvals, connector calls, delivery, and workflow completion feed AI Debug.
12. **The response is persisted.** The UI/channel sees the final assistant output, artifacts, waiting state, or failure truth.

---

# Core truth boundaries

These distinctions are intentional and show up repeatedly in the code.

## Human is not principal

An `AppUser` is the canonical OPERLY account human. A runtime principal may be that human, a guest, a generated-app user, a workflow, a software project, an agent run, or another delegated execution identity.

External identities can be linked to a human, but **identity linking does not merge permissions**.

## Personal scope is not workspace scope

`packages/security/execution_context.py` defines explicit `PERSONAL` and `WORKSPACE` authority namespaces.

Personal AI can search authorized information across the account and workspaces, but a workspace focus is not equivalent to workspace execution authority.

## Discovery is not authority

`capability.search` or `capability.describe` can tell an agent that a capability exists without granting the right to invoke it.

Availability, configuration, health, permissions, surface policy, delegation, and action policy are checked separately.

## Provider success is not verified completion

A connector returning HTTP 200 or a provider returning a successful payload does not automatically make an action `VERIFIED`.

The Action lifecycle moves through explicit states and verification.

## Context references are not bearer tokens

Search returns locators. `context.get`/materialization must reauthorize the underlying source.

## Generated code is not trusted application code

Generated source is built and run outside the FastAPI control plane. It does not receive OPERLY credentials or the Docker socket.

## SoftwareProject is the canonical constructed-software identity

`SoftwareProjectService` treats `SoftwareProject` as the only product-level identity for constructed software.

Compatibility adapters for historical Studio/ManagedApplication/GeneratedProject rows still exist, but the canonical service does not discover them as alternate product identities.

## Solution is the user-facing lifecycle, not another runtime generation

`SolutionService` accepts `software_project` as the supported runtime. Historical enum values remain parseable so old rows can age out, but they are not exposed as current product runtimes.

---

# Identity, scope, delegation, and provenance

The security model is concentrated in `packages/security`, `packages/channels`, identity-related database models, and API authentication/session code.

## Canonical human identity

`packages/security/human_identity.py` projects the known account identities into one human graph across:

- password/Google authentication identities;
- external/channel identities;
- account connectors;
- principal bindings;
- workspace memberships.

The graph answers **which identities belong to the same person**. It does not answer **what that person can do in a particular workspace**.

## Principals

`packages/security/principals.py` manages runtime principals:

- canonical user/human principals;
- linked external provider subjects;
- guest principals;
- principal conversations.

Guest principals have bounded lifetimes and can later be claimed by a verified OPERLY human.

## ExecutionContext

`packages/security/execution_context.py` is the trusted authority object for one operation. It contains the resolved scope, user, workspace, membership, role, permissions, surface/channel information, and principal identity.

Personal execution permissions are explicit rather than pretending the user is an owner of a fake workspace.

## Delegation

`packages/security/delegation.py` narrows existing human/workspace authority to an application-created delegated principal such as:

- workflow;
- software project;
- production runtime;
- agent run;
- app user;
- public session.

Delegation is fail-closed and capability-allowlisted. It can **never widen** the underlying human authority.

`delegation_context.py` carries the specific action/capability delegation use through execution.

## Guest workspaces

`guest_workspace.py` resolves authority for provisional/external-platform workspace participation while retaining `ScopeKind.WORKSPACE` so resource ownership, events, actions, and workflows remain in the same workspace namespace.

## Surfaces

`surfaces.py` defines where a capability is being requested: Personal private, workspace shared/private, Discord DM/guild, scheduled/system tasks, Studio-like surfaces, and related policy categories.

A capability may be authorized for a user but still disallowed on a particular surface.

## Workspace invitations

`workspace_invitations.py` implements durable one-time, expiring invitations. Invitations can be targeted and do not silently replace an existing member's role.

## Provenance

Actions and events now distinguish **initiator** from **executor**.

`packages/actions/attributed_service.py` retains the mature Action state machine while adding:

- initiator type/id;
- executor type/id;
- delegation chain.

`packages/company/events/service.py` stores the same distinction and builds a canonical execution path:

```text
initiator
  -> entry surface/client
  -> AI mediation or direct execution
  -> executor
  -> action/capability/status
  -> timestamp
```

This is what lets OPERLY distinguish a human doing something directly from that human's OPERLY agent doing it on their behalf.

---

# Agent runtime and Agent Factory

There are two related orchestration layers under `packages/agents`.

## Generic AgentRuntime

`packages/agents/runtime.py` owns the provider-neutral model/capability micro-loop.

Responsibilities include:

- inference requests and budgets;
- capability schema loading;
- capability invocation;
- observations;
- tool-call trace entries;
- adaptive step/tool budgets;
- working-context compaction;
- runtime trace scopes.

`compaction.py` shrinks old working tool payloads without deleting the raw durable trace/evidence.

## Long-horizon controller

`controller.py` sits above the micro-loop and adds:

- adaptive planning;
- resumable run state;
- checkpoints;
- capability rescue;
- objective verification;
- partial-completion truth.

Related files:

| File | Responsibility |
| --- | --- |
| `planning.py` | Adaptive plan construction/revision |
| `run_state.py` | Compact durable run/task/specialist state |
| `persistence.py` | Run checkpoint/load/resume |
| `verification.py` | Objective/evidence verification |
| `capability_rescue.py` | Attempts recovery when expected execution evidence is missing |
| `compaction.py` | Deterministic working-context compaction |

## Agent Factory control plane

`packages/agents/control_plane` is the newer stage/DAG orchestration architecture.

The Factory does not replace the capability security boundary. It changes **how work is decomposed, distributed, validated, repaired, paused, and resumed**.

Core objects include:

- `ObjectiveSpec`;
- `AcceptanceContract`;
- `ValidatorSpec`;
- `StageGraph`;
- `StageSpec`;
- `ContextCapsule`;
- `Defect`;
- `RepairBudget`;
- `StageWorkerResult`.

Key files:

| File | Responsibility |
| --- | --- |
| `contracts.py` | Frozen Factory domain contracts |
| `compiler.py` | Converts literal request + tiny ingress metadata into a bounded blueprint |
| `bindings.py` | Resolves authorized context/capability intents |
| `context_injector.py` | Stage-specific minimum context distribution |
| `factory.py` | Root control-plane orchestration |
| `stage_runner.py` | DAG/stage execution and attempts |
| `worker_adapter.py` | Disposable AgentRuntime worker adapter |
| `evidence.py` | Evidence ledger and promotion truth |
| `validation.py` | Control-plane validation |
| `semantic_validation.py` | Evidence-bounded semantic validation fallback |
| `sandbox_validation.py` | Sandboxed Python validation hook |
| `repair.py` | Defect-driven bounded repair |

Important Factory invariants:

- the root objective and acceptance contract are frozen;
- workers do not inherit arbitrary prior-worker transcripts;
- context is reference-first and stage-scoped;
- only declared dependency outputs flow downstream;
- failed attempt artifacts remain audit evidence but are not promoted;
- validated artifacts can become trusted dependency inputs;
- workers do not own root completion truth;
- waiting state is durable;
- resume happens at the exact stage;
- an already resolved external action is not replayed on resume;
- current authority is rechecked at continuation time;
- terminal rejection/denial/cancellation/verification failure does not enter automatic repair.

`packages/business_brain/factory_runtime.py` binds Workspace AI to this control plane while explicitly preserving `PluginAgentHarness` + `CapabilityFirewall` as the real authorization/execution boundary.

The deployment switch is `OPERLY_WORKSPACE_AGENT_FACTORY`.

---

# Capabilities, plugins, actions, and approvals

This is the central execution fabric.

## Capability contracts

`packages/capabilities/contracts.py` defines `CapabilityDefinition`.

A capability can declare:

- stable id/version;
- human description;
- input/output schema;
- risk;
- permissions;
- approval policy;
- execution mode;
- timeout;
- integration provider;
- credential scopes;
- reversibility;
- effect class;
- data-egress class;
- category/display metadata;
- event capabilities;
- health check;
- network domains;
- configuration schema;
- plugin owner;
- tags and semantic operations.

Execution modes include:

- control-plane;
- external;
- isolated-runner.

Effect classes include:

- read;
- compute;
- write;
- external write;
- destructive.

Data egress can be none, same-scope, or external.

Those values are **policy metadata**, not authority by themselves.

## Capability registry and progressive exposure

Important files:

| File | Responsibility |
| --- | --- |
| `registry.py` | Canonical provider/capability registry |
| `defaults.py` | Boots first-party providers through PluginRuntime |
| `search_index.py` | Semantic capability lookup |
| `discovery_provider.py` | `capability.search` / describe-facing provider |
| `session_view.py` | Small permanent kernel + progressive schema exposure |
| `runtime_context.py` | Provider execution context |
| `surface_contract.py` | Surface-aware contracts |
| `validation.py` | Argument/schema validation |
| `firewall.py` | Canonical invocation boundary |
| `agent_harness.py` | Application-resolved model-facing harness |

The session view means the model does not need thousands of tokens of tool schemas on every turn. It can discover what it needs and then expose exact schemas.

## Built-in providers

`packages/capabilities/defaults.py` currently boots providers for:

- company state;
- research;
- analytics;
- Personal runtime operations;
- context;
- conversation history;
- Action lifecycle;
- websites;
- business entities/operations;
- CRM reads;
- workspaces;
- operations;
- software projects;
- software builds;
- relational data;
- canonical workspace entities;
- generated-app identity;
- reminders;
- universal tasks;
- public web reads;
- artifacts;
- file runtime;
- file authoring;
- Agent Computer;
- messaging;
- message curation;
- Solutions;
- model invocation;
- Discord;
- Gmail operations;
- Gmail reads;
- Gmail draft lifecycle;
- Gmail artifacts;
- Google Calendar;
- calendar semantics.

`CapabilityDiscoveryProvider` and `EventDiscoveryProvider` are added after the provider registry is built.

## PluginRuntime

`packages/plugins/runtime.py` composes plugin contributions.

A `PluginContribution` can contribute:

- manifest;
- capability provider;
- lifecycle;
- runtime plugins;
- model provider registrars;
- model discoverer registrars;
- task delivery adapters.

Registering a plugin **does not bypass execution security**. Capability execution still goes through the registry/firewall, models still go through `model_runtime`, and generated code still goes through runtime plugins + isolated runner.

Built-in capability providers are represented as plugin manifests. Discord also contributes lifecycle/task delivery. Platform-level capability and task runtimes are registered as plugins too.

## Actions

`packages/actions/service.py` owns the durable side-effect state machine:

```text
PROPOSED
  -> WAITING_APPROVAL
  -> APPROVED
  -> EXECUTING
  -> EXECUTED
  -> VERIFYING
  -> VERIFIED
```

Terminal alternatives include:

- REJECTED;
- FAILED;
- VERIFICATION_FAILED.

`policy.py` evaluates trusted action/capability effect metadata and application policy.

`lifecycle.py` and `planner.py` support lifecycle/planning behavior.

`attributed_service.py` adds explicit initiator/executor provenance without creating a second action implementation.

## Approval rule

A model can request an operation. It cannot grant itself authority or approve its own consequential work.

The approval result is persisted **before** Factory/workflow continuation, preventing continuation failure from causing the external action to be repeated.

---

# Context, memory, and federated history

OPERLY deliberately separates durable history from the model's current working prompt.

## Bounded conversation tail

`packages/business_brain/context_loader.py` loads only a bounded recent tail for the active conversation.

Older/large history remains durable and can be retrieved through governed context capabilities. It is not replayed just because it exists.

## Context records

`packages/context/service.py` loads structured human, tenant/workspace, and conversation context plus resolved principal/workspace/temporal information.

## ContextBroker

`packages/context/broker.py` is the local reference-first retrieval layer across OPERLY-owned sources such as:

- Personal/principal conversations;
- workspace messages;
- ContextRecords;
- business/company events.

It ranks only sources the current runtime is authorized to consider.

## FederatedHistoryService

`packages/context/federation.py` combines the local broker with provider-owned history through adapters.

Current provider history includes governed Google Gmail and Calendar paths.

The federation layer uses a registry rather than hardcoding every provider into one giant retrieval service.

## Provider-history adapters

`history_adapters.py` owns:

- provider account discovery;
- governed provider invocation;
- reference parsing;
- reauthorization during materialization.

`provider_history.py` provides the capability-backed Google history implementations.

## Retrieval engine

`packages/retrieval/semantic.py` is deliberately lightweight and deterministic. It provides local hashing-based semantic similarity and does **not** download embedding models, ONNX runtimes, Hugging Face models, or hidden ML dependencies.

Search callers still own authorization; retrieval only ranks the documents given to it.

## Critical invariant

```text
search permission != materialization permission
```

A returned reference is a locator. Authorization is checked again when the referenced data is materialized.

---

# Model runtime and adaptive routing

`packages/model_runtime` is the provider-neutral inference subsystem.

## Layers

| File | Responsibility |
| --- | --- |
| `contracts.py` | Model/inference contracts, traits, budgets, results/errors |
| `catalog.py` | ModelResource catalog and static/discovered metadata |
| `discovery.py` | Shared discovery registry/refresh |
| `provider_discovery.py` | Groq, Gemini, NVIDIA, Ollama catalog discovery |
| `openrouter_discovery.py` | OpenRouter catalog/pricing discovery |
| `providers.py` | Provider client factories and invocation gate |
| `ollama_client.py` | Ollama transport |
| `openrouter_client.py` | OpenRouter transport |
| `openai_compatible_client.py` | Groq/NVIDIA/Gemini-style compatible transport |
| `registry.py` | Model objects, adaptive selection and failover |
| `scoring.py` | Adaptive per-route scorer |
| `requirements.py` | Capability/context requirements |
| `task_routing.py` | Converts a turn into concrete model requirements |
| `routing_policy.py` | Route policy |
| `semantic_router.py` | Semantic task/routing support |
| `semantic_failover.py` | Semantic fallback behavior |
| `portfolio.py` | Role/provider route portfolios |
| `qualification.py` | Model qualification |
| `qualification_benchmark.py` | Qualification benchmark support |
| `free_policy.py` | Zero-cost eligibility |
| `provider_policy.py` | Hard provider activation switch |
| `conversation_policy.py` | Trivial-conversation handling |
| `trace_context.py` | Model/provider wire trace context |
| `trace_events.py` | Stable orchestration event names |
| `service.py` | Higher-level model service entrypoints |

## Live routing pipeline

```text
configured provider
  -> provider catalog discovery
  -> ModelResource index
  -> provider activation gate
  -> zero-cost eligibility gate
  -> concrete turn requirements
  -> adaptive score
  -> ranked route batch
  -> provider invocation
  -> latency/success/failure telemetry
  -> scorer update
```

Every provider/model pair is its own route. The same underlying model on different hosts can acquire different scores.

The scorer uses static priors plus live evidence such as:

- successes;
- failures;
- latency;
- cooldowns;
- task-specific performance;
- deterministic exploration.

Rate limiting can cool one route without taking the entire provider out of service.

## Current operational policy

The architecture supports Ollama, OpenRouter, Groq, Gemini, and NVIDIA discovery/transport.

Current policy is intentionally more restrictive:

- `OPERLY_ACTIVE_MODEL_PROVIDERS` defaults to `ollama`;
- `OPERLY_FREE_MODELS_ONLY` defaults on;
- unknown-cost routes fail closed;
- inactive providers are rejected even at the final client invocation boundary.

The currently allowlisted Ollama free routes are:

- `gpt-oss:20b`;
- `gemma4:31b`;
- `minimax-m3`.

The broader provider catalogs can still be known without being eligible to run.

---

# Tasks and workflow runtime

`packages/tasks` is the durable scheduled/event-driven execution system.

Files:

| File | Responsibility |
| --- | --- |
| `runtime.py` | Polling/lease dispatcher, scheduled execution, retries and delivery |
| `workflow.py` | Bounded workflow language/executor |
| `safe_workflow.py` | Workspace executor that treats waiting approval as blocked, not completed |
| `personal_workflow.py` | Personal-scope workflow execution |
| `events.py` | Event trigger matching and scheduling |
| `delivery.py` | Channel/provider output delivery |
| `__init__.py` | Public task package surface |

The workflow language is bounded by depth/node/foreach limits and supports node types such as:

- invoke;
- model;
- if;
- foreach;
- set;
- emit;
- stop.

Tasks can be triggered by plugin-declared durable events. Event declarations are metadata only; models cannot fabricate the event merely by naming it.

The task runtime is registered through PluginRuntime and can deliver back into OPERLY conversations or connector-specific adapters such as Discord.

---

# Business and company intelligence

This area is split intentionally.

## `packages/business`

A small core business service layer for operational/domain records.

## `packages/business_brain`

The composition layer used by Personal/Workspace AI.

Important files:

| File | Responsibility |
| --- | --- |
| `agent.py` | Workspace agent entrypoint, context, model/harness/Factory selection |
| `personal_agent.py` | Account-scoped Personal AI independent of selected workspace |
| `factory_runtime.py` | Workspace Factory binding/resume |
| `context_loader.py` | Bounded conversation/business context |
| `personal_capability_runtime.py` | Personal capability composition |
| `operations_service.py` | Operations-level service |
| `operations_tools.py` | Agent-facing operation tools |
| `conversation_artifacts.py` | Artifact handling in conversations |
| `attachments/` | Secure multimodal/document attachment processing |
| `security.py` | Input limits/rate limits/bounds |
| `registry.py` | Business tool/provider registration |
| `tools.py` | Tool helpers |
| `studio_tools.py` | Software/Studio-facing business tools |
| `types.py` | `AgentInput`, `ToolContext`, shared runtime types |
| `ollama_client.py` | Historical compatibility transport; not the architectural model boundary |

Personal AI is explicitly account-scoped. It may delegate into a workspace the human belongs to, but workspace execution still crosses the canonical harness, permissions, connector availability, approvals, and verification.

## `packages/company`

The persistent company-state/intelligence subsystem.

Subareas include:

- `state/` — company state representations/services;
- `events/` — durable Personal/Workspace business events;
- `context/` — company context projections;
- `attention.py` — attention/priority logic;
- `intelligence.py` — synthesized company intelligence/profile;
- `provenance.py` — scoped evidence, confidence, conflicts, owner confirmation;
- `research.py` — company research;
- `web_research.py` — web research support.

Business events preserve correlation/causation plus explicit initiator/executor/delegation and execution path.

Company evidence can retain source type/reference/URL, actor, conversation, action, research run, confidence, owner initiation/confirmation, staleness, and supersession so synthesized company state can be traced back to evidence.

---

# Software construction, Solutions, and Studio

The software stack is layered. Treating all of it as simply `Studio` hides important boundaries.

## Canonical product hierarchy

```text
Solution
  user-facing outcome/lifecycle
        │
        ▼
SoftwareProject
  canonical constructed-software identity
        │
        ▼
SourceVersion + runtime + service bindings
        │
        ▼
coding/planning/build orchestration
        │
        ▼
isolated runner
```

## SoftwareProject

`packages/software_projects` is the canonical project identity.

Files:

| File | Responsibility |
| --- | --- |
| `contracts.py` | `SoftwareProject`, `SourceVersion`, `StudioSession`, `ProjectState` |
| `service.py` | Canonical project persistence |
| `source_service.py` | Canonical source versions |
| `source_bundle.py` | Source bundle handling |
| `runner_adapter.py` | Project/runner adaptation |
| `delivery.py` | Delivery/output handling |
| `static_assets.py` | Static project asset support |
| `adapters.py` | Explicit compatibility conversions from old product generations |

`SoftwareProjectService` does not discover historical Studio/ManagedApplication/GeneratedProject rows as alternate project identities.

## Solutions

`packages/solutions` is the user-facing lifecycle above SoftwareProject.

Files:

| File | Responsibility |
| --- | --- |
| `service.py` | Canonical Solution registry/lifecycle |
| `manifest.py` | Solution manifest contracts |
| `generation_worker.py` | Durable generation worker |
| `traced_generation_worker.py` | Generation with trace integration |
| `completion.py` | Completion truth |
| `composer.py` | Solution composition |
| `production.py` | Production-state behavior |
| `deployment.py` | Deployment behavior |

`SolutionService` only accepts `software_project` as the supported product runtime. Historical runtime enum values are parseable only for old-row compatibility.

A Solution distinguishes:

- lifecycle status;
- current version;
- preview state/url;
- production state/url;
- visibility;
- generation status/evidence.

Source generation, preview readiness, and production deployment are therefore separate truths.

## Coding harness

`packages/coding_harness` is the persistent source-aware coding loop.

Files include:

- `ARCHITECTURE.md`;
- `contracts.py`;
- `engine.py`;
- `execution_loop.py`;
- `state_machine.py`;
- `context_window.py`;
- `contract_guidance.py`;
- `interaction_contracts.py`;
- `objective_audit.py`;
- `evaluation.py`;
- `model_client.py`;
- `model_resolution.py`;
- `runtime_resolution.py`;
- `source_jobs.py`;
- `source_service.py`;
- `build_service.py`;
- `studio_controller.py`;
- `opencode_agent.py`;
- `web_tools.py`.

It is responsible for source edits, coding-agent steps, objective/contract auditing, model/runtime resolution, build submission, and iterative repair rather than returning a one-shot code block.

## Custom-software planning/orchestration

`packages/custom_software` contains the larger planning and build orchestration lineage.

It includes:

- architecture selection;
- compiler/live/provider planning;
- recursive planning;
- graph planning and coverage;
- dependency orchestration/resolution;
- scope convergence;
- source synthesis/bundles;
- renderer/pack layers;
- generated engines;
- runtime profiles;
- runner contracts/adapters/service;
- sandbox job support;
- global repair;
- legacy/test subprocess execution.

Notable files:

`architectures.py`, `compiler_planning.py`, `construction.py`, `coverage.py`, `dependency_orchestrator.py`, `dependency_resolution.py`, `fullstack_subprocess_runner.py`, `generated_engines.py`, `global_repair.py`, `graph_coverage.py`, `graph_planning.py`, `live_planning.py`, `live_projection.py`, `model_planning_client.py`, `pack_renderer.py`, `packs.py`, `plan_service.py`, `planner.py`, `planning_orchestrator.py`, `planning_output_normalizer.py`, `provider_planning.py`, `recursive_planning.py`, `renderer.py`, `runner_adapters.py`, `runner_contracts.py`, `runner_service.py`, `runtime_profiles.py`, `sandbox.py`, `sandbox_jobs.py`, `schema.py`, `scope_convergence.py`, `service.py`, `source_bundles.py`, and `synthesis.py`.

The active planning architecture is provider-neutral. Historical Ollama-specific planning code still exists as a compatibility seam and should not regain architectural authority.

## Studio package

`packages/studio` is now primarily Studio hardening/policy/design support rather than the entire software generator.

Current files include:

- `context_hardening.py`;
- `design.py`;
- `hardening_policy.py`;
- `hardening_policy_v2.py`;
- `model_latency_policy.py`;
- `model_provenance.py`;
- `runtime_policy.py`;
- `terminal_recovery.py`;
- `tool_hardening.py`.

The source-aware coding controller itself lives in `packages/coding_harness`, canonical project state lives in `packages/software_projects`, and user-facing lifecycle lives in `packages/solutions`.

---

# Generated-runtime data, identity, and service bindings

Generated software must be useful without receiving OPERLY's backend secrets.

## Service bindings

`packages/service_bindings` provides project-scoped semantic bindings:

- `contracts.py` — binding/candidate/invocation contracts;
- `service.py` — binding service;
- `store.py` — persistence.

A binding maps a semantic need to an OPERLY capability and configuration.

Binding configuration is not authority. Runtime invocation still needs a valid delegated principal and crosses the capability firewall.

Raw passwords/API keys/provider credentials are not supposed to be embedded in project binding configuration.

## Relational data

`packages/relational_data` gives generated applications provider-neutral relational storage contracts.

Files:

- `contracts.py`;
- `bindings.py`;
- `store.py`;
- `tokens.py`.

Generated applications declare logical migrations and use scoped capability grants. They do not receive a database URL.

The relational schema uses strict logical identifiers and a bounded migration contract.

## App identity

`packages/app_identity` provides generated-application end-user identity.

Files:

- `contracts.py`;
- `bindings.py`;
- `crypto.py`;
- `store.py`.

These app users are deliberately **not OPERLY account users**.

A generated app can have its own:

- registrations;
- logins;
- sessions;
- invitations;
- roles;
- optional links to canonical workspace Employee/Customer entities.

## Workspace entities

`packages/workspace_entities` exposes canonical workspace-owned entity kinds shared across Solutions.

Files:

- `contracts.py`;
- `manifest.py`;
- `bindings.py`;
- `store.py`.

Current canonical entity kinds include:

- employee;
- customer;
- location.

Generated source declares the entities it consumes in `operly.entities.json` instead of quietly creating private duplicate employee/customer/location tables.

The source validator detects attempts to redefine those canonical entity tables.

---

# Artifacts, files, and Agent Computer

## Artifacts

`packages/artifacts` contains:

- `blob_store.py`;
- `service.py`;
- `delivery.py`.

Artifacts are durable outputs rather than transient model text.

They can represent generated files, archives, reports, code outputs, or other run deliverables. Delivery remains authorization-aware.

Factory promotion rules distinguish verified artifacts from failed-attempt evidence.

## Assets

`packages/assets/service.py` is the small managed asset service layer.

## File capabilities

File processing/authoring is exposed through capability providers rather than giving the model arbitrary filesystem access:

- `file_runtime_provider.py`;
- `file_authoring_provider.py`;
- `artifact_provider.py`.

## Agent Computer

`packages/agent_computer` contains:

- `session.py`;
- `runner_client.py`.

It manages bounded computer/runtime sessions through the sandbox boundary.

Personal execution permissions explicitly permit account-scoped file/computer operations, but that does not imply access to production credentials or unrestricted networking.

---

# Channels, Discord, Google, and MCP

## Channels

`packages/channels` is the provider-neutral channel/identity layer.

Files include:

- `envelope.py` — normalized channel envelope;
- `identity.py` — channel identity resolution;
- `linking.py` — identity linking;
- `guest_chat.py` — guest conversation behavior;
- `space_bindings.py` — channel/space-to-workspace bindings;
- `attachment_ingress.py` — attachment ingress;
- `presentation.py` — output presentation;
- `service.py` — channel service routing.

## Connectors

`packages/connectors` contains connector runtime and concrete integrations:

- `runtime.py`;
- `secrets.py`;
- `account_secrets.py`;
- `account_google.py`;
- `google_provider.py`;
- `google_scope.py`;
- `discord/`.

Connector credentials are encrypted/scoped outside model prompts.

### Discord

`packages/connectors/discord` contains:

- `bot.py`;
- `bot_harness.py`;
- `bot_shared.py`;
- `lifecycle.py`;
- `provider.py`;
- `runtime.py`;
- `secure_runtime.py`;
- `authority.py`;
- `slash_commands.py`;
- `transport.py`;
- `artifact_delivery.py`;
- `task_delivery.py`;
- `scheduled_tasks.py`;
- connector-specific `requirements.txt`.

Discord is also registered as a PluginRuntime lifecycle contribution and task-delivery adapter.

Guild/server traffic stays workspace-scoped. Linked external identity does not itself grant workspace permissions.

### Google

Google operations are exposed through capability providers and account/workspace connector resolution.

The repository contains separate concerns for:

- Google sign-in authentication;
- Google connector OAuth;
- account connector ownership;
- Gmail operations;
- Gmail read/draft/artifact flows;
- Calendar operations and semantics;
- federated Gmail/Calendar history retrieval.

## MCP

`packages/mcp` contains:

- `gateway.py`;
- `oauth.py`;
- `policy.py`.

MCP lets external AI clients access approved OPERLY capabilities. It is an interface to the same policy boundary, not privileged backend access.

Stored client grants bound what OAuth can expose; OAuth does not invent broader workspace scope.

---

# Tracing and AI Debug

OPERLY has durable model/runtime tracing instead of treating model calls as opaque.

## Model trace storage

`packages/database/model_trace.py` records runtime-visible request/response/provider evidence.

It deliberately:

- preserves the complete model-visible packet rather than silently truncating it;
- redacts credential-shaped values;
- redacts hidden-reasoning fields;
- stores a SHA-256 digest of the exact unredacted packet so packet identity is preserved without leaking secrets/reasoning.

`model_trace_models.py` stores append-only `ModelRuntimeTrace` rows keyed by conversation/run/attempt/provider/model/surface/component.

## Orchestration events

`packages/database/runtime_trace_events.py` persists non-model orchestration events into the same run/conversation trace when available.

Stable events in `packages/model_runtime/trace_events.py` include:

- route selected;
- model request/response/escalation;
- capability requested/rejected/searched/exposed;
- context searched/materialized;
- run compacted;
- plan created/revised;
- action created;
- approval requested/resolved;
- action resumed;
- connector request/response;
- delivery verified/failed;
- workflow completed.

Trace persistence is best-effort: a telemetry failure may not break the actual user action.

## API

`apps/api/runtime_trace_router.py` exposes authenticated trace/run browsing with Personal/Workspace ownership checks.

This is the basis for AI Debug: seeing what the runtime sent, which model/provider ran, what tools were exposed/called, what action/approval/connector steps occurred, and where a run failed or waited.

---

# Frontend architecture

The frontend is in migration, but canonical route ownership is already explicit.

## React/Vite is the canonical authenticated UI

`apps/web/src` owns `/channels/**`.

Important source areas:

```text
apps/web/src/
  main.tsx
  api.ts
  app/
    App.tsx
    routes.ts
    types.ts
    useRoute.ts
    useScope.ts
  account/
    PersonalHome.tsx
    AccountSettings.tsx
    ScopeRail.tsx
  workspace/
    WorkspaceShell.tsx
    WorkspaceHome.tsx
    WorkspaceOperly.tsx
    WorkspaceSettings.tsx
    AIDebugPage.tsx
    AccessPage.tsx
    ActivityPage.tsx
    DataPages.tsx
    MembersPage.tsx
    PluginsPage.tsx
    SolutionsPage.tsx
  ui/
    MessageContent.tsx
    OperlyMark.tsx
    LegalLinks.tsx
    artifactDownload.ts
    theme.ts
    CSS/tokens/surface styles
```

Canonical routes include:

- `/channels/@me` — Personal Operly;
- `/channels/:workspace` — workspace Home;
- `/channels/:workspace/operly` — Workspace Operly;
- `/channels/:workspace/crm`;
- `/channels/:workspace/operations`;
- `/channels/:workspace/activity`;
- `/channels/:workspace/presence`;
- `/channels/:workspace/solutions`;
- `/channels/:workspace/connections`;
- `/channels/:workspace/plugins`;
- `/channels/:workspace/members`;
- `/channels/:workspace/access`.

Scope switching is not cosmetic routing. The frontend asks the backend to enter Personal scope or switch workspace before the URL becomes authoritative.

## Static compatibility shell

`apps/web/static` remains for:

- public/authentication flows;
- compatibility code;
- protected legacy Studio surfaces not yet fully moved.

It is **not** the owner of normal authenticated `/channels/**` product development.

Do not add a third authenticated renderer or a new hidden-click/DOM-rewrite bridge.

See `apps/web/FRONTEND_MIGRATION.md`.

## Web build

`apps/web/package.json` currently uses React 18 and Vite.

Scripts include:

- `npm run dev`;
- `npm run build`;
- `npm run preview`;
- frontend contract tests;
- bundle contract tests.

The production Docker build produces `apps/web/dist`; FastAPI serves that build for canonical authenticated routes.

---

# FastAPI control plane

`apps/api/main.py` is the trusted application entrypoint.

It is responsible for:

- loading environment;
- validating runtime configuration;
- initializing database;
- bootstrapping admin membership;
- bootstrapping built-in plugins;
- bounded model-discovery warmup;
- starting/stopping PluginRuntime;
- security middleware;
- CORS/trusted-host policy;
- API router mounting;
- static/public shell serving;
- React build serving.

## Security middleware

Relevant files include:

- `csrf.py`;
- `request_safety.py`;
- `public_safety.py`;
- `security_headers.py`;
- `security.py`;
- `auth_cookies.py`;
- `session.py`;
- `dependencies.py`.

Production validates strong secrets/HTTPS assumptions and applies trusted-host/security/CORS boundaries.

## Active API areas

`main.py` currently mounts routers for:

- system;
- session/auth;
- analytics;
- admin;
- Personal agent;
- Personal connectors;
- workspace;
- access/client grants;
- MCP;
- approvals;
- integrations;
- business;
- agent;
- runtime traces;
- relational data;
- company;
- connectors;
- capability diagnostics;
- channel identity;
- operations;
- Studio source;
- software projects;
- Solutions;
- Solution generation;
- public Solution routes;
- workspace entities;
- app-identity runtime/admin.

The `apps/api` directory also contains router modules retained for compatibility or narrower internal paths. A file existing in `apps/api` does not automatically mean it is mounted by `main.py`.

Notable router files include:

- `access_router.py`;
- `admin_ai_usage.py`;
- `admin_router.py`;
- `agent_router.py`;
- `analytics_router.py`;
- `app_identity_router.py`;
- `approvals_router.py`;
- `artifact_router.py`;
- `business.py`;
- `capability_diagnostics_router.py`;
- `channel_identity_router.py`;
- `coding_harness_router.py`;
- `company_router.py`;
- `connectors_router.py`;
- `integrations_router.py`;
- `mcp_router.py`;
- `operations_router.py`;
- `personal_agent_router.py`;
- `personal_connectors_router.py`;
- `relational_data_router.py`;
- `runtime_trace_router.py`;
- `software_projects_router.py`;
- `solution_generation_router.py`;
- `solutions_router.py`;
- `studio_source_router.py`;
- `system_router.py`;
- `workspace_entities_router.py`;
- `workspace_router.py`.

---

# Production runner and sandbox runtime

Generated software must not execute in the API process.

## `apps/runner`

This is the production isolated execution service for the full-stack runtime contract.

Key files:

| File | Responsibility |
| --- | --- |
| `main.py` | Runner gateway/API |
| `docker_backend.py` | Per-job Docker isolation backend |
| `egress_proxy.py` | Trusted dependency-install egress proxy |
| `store.py` | Runner state |
| `dev_main.py` | Development sidecar with same control-plane protocol |
| `Dockerfile` | Runner image |
| `docker-compose.runner.yml` | Dedicated-host runner stack |
| `Caddyfile` | Runner TLS edge |
| `README.md` | Full security/deployment contract |

For each production build, the runner creates fresh job isolation.

Generated software receives:

- no Docker socket;
- no host bind mounts;
- no OPERLY database/session/model/connector credentials;
- no raw service-binding credentials;
- no shared job network;
- dropped Linux capabilities;
- `no-new-privileges`;
- CPU/memory/PID/fd limits;
- non-root UID;
- read-only root filesystem after build/test succeeds.

Dependency installation gets a temporary trusted CONNECT sidecar restricted to required registries. The sidecar is removed before generated build scripts/tests/workers/application runtime execute.

Preview is proxied through a separate credentialless sidecar and opaque token.

The production runner is intended for a dedicated Docker-capable Linux host. Railway is suitable for the control plane but not for this per-job Docker isolation contract.

## `apps/sandbox_runner`

This is a separate Node-based sandbox/computer implementation containing files such as:

- `server.mjs`;
- `core.mjs`;
- `launch.mjs`;
- `computer-endpoint.mjs`;
- `network-guard.mjs`;
- `lease-guard.mjs`;
- startup/full-stack smoke scripts and tests.

Do not confuse this with the dedicated production full-stack runner.

---

# Repository anatomy: every top-level package

There are currently **33 first-class package directories** under `packages/`.

## `packages/actions`

Durable governed side effects.

- `service.py` — mature action/approval/execution/verification state machine.
- `policy.py` — trusted action policy.
- `attributed_service.py` — initiator/executor/delegation attribution.
- `lifecycle.py` — lifecycle helpers.
- `planner.py` — action planning support.

## `packages/agent_computer`

Bounded computer-session abstraction.

- `session.py` — session state/contracts.
- `runner_client.py` — remote sandbox/runner client.

## `packages/agents`

Generic agent runtime plus long-horizon orchestration.

- `runtime.py` — model/tool micro-loop.
- `controller.py` — adaptive resumable controller.
- `planning.py` — planner.
- `run_state.py` — compact run state.
- `persistence.py` — checkpoints/resume.
- `verification.py` — objective verification.
- `capability_rescue.py` — missing-execution recovery.
- `compaction.py` — working-context compaction.
- `control_plane/` — Agent Factory DAG/stage orchestration.

## `packages/app_identity`

Generated-application end-user identity, separate from OPERLY account identity.

- strict identity/invitation/session contracts;
- encrypted token/session support;
- app-user store;
- runtime binding/grant support.

## `packages/artifacts`

Durable generated/user artifacts.

- blob storage;
- metadata/service;
- authorized delivery.

## `packages/assets`

Small managed asset service layer.

## `packages/business`

Core business-domain service.

## `packages/business_brain`

Personal/Workspace AI composition and business-agent runtime.

Contains the agent entrypoints, Factory binding, operation tools/services, attachment processing, context loading, Personal capability composition, security bounds, artifact integration, and compatibility helpers.

## `packages/capabilities`

The universal model-facing execution abstraction.

Contains contracts, registry, discovery, search, progressive session exposure, firewall, validation, runtime context, surface contracts, and all first-party providers.

This package is the main place to look when asking what tools are exposed to an agent.

## `packages/capability_sandbox`

Capability-resolution testing/benchmark utilities.

- `target_resolution.py`;
- `benchmarks.py`.

Used to test whether capability discovery and target selection stay bounded and correct.

## `packages/channels`

Provider-neutral channel/identity/conversation envelope layer.

Covers normalized envelopes, identity/linking, guest chat, attachments, presentation, space/workspace bindings, and service routing.

## `packages/coding_harness`

Persistent source-aware coding agent/build loop.

Owns source manipulation, coding-agent execution, context windows, model/runtime resolution, objective audit, evaluation, build submission, and Studio controller integration.

## `packages/company`

Company operating-state/event/intelligence/provenance/research subsystem.

## `packages/connectors`

External integration runtime.

Includes Google account/scoping/secrets and the full Discord connector.

## `packages/context`

Governed context loading and federated history.

- local ContextService;
- ContextBroker;
- provider adapter registry;
- Gmail/Calendar history capabilities;
- FederatedHistoryService.

## `packages/custom_software`

The broader planning, dependency, source-synthesis, runtime-profile, runner-orchestration, and repair stack used by software generation.

This contains both active provider-neutral architecture and compatibility implementations from earlier generations.

## `packages/database`

SQLAlchemy models, DB engine/session, migration helpers, backups, model tracing, and runtime-trace persistence.

Database model files are split by domain rather than one monolithic `models.py`.

## `packages/email`

Transactional email subsystem.

- `service.py`;
- `messages.py`;
- provider implementations;
- templates.

Supports application security/account emails and provider abstraction.

## `packages/harness`

**Deprecated compatibility bridge**, not the current core harness.

`plugins.py` explicitly forwards old imports to `packages.plugins.extensions` and says not to add behavior there.

New architecture should use `packages/capabilities`, `packages/agents`, and `packages/plugins`.

## `packages/mcp`

MCP gateway, OAuth, and policy layer for external AI clients.

## `packages/model_runtime`

Provider-neutral inference, discovery, qualification, adaptive scoring/routing, provider/cost activation policy, transport clients, and trace context.

## `packages/plugins`

Canonical plugin manifest/contribution/lifecycle system.

- `manifest.py`;
- `runtime.py`;
- `events.py`;
- `extensions.py`.

Plugins can contribute capabilities, events, lifecycles, model/runtime registration, and task delivery, but cannot bypass the canonical execution seams.

## `packages/relational_data`

Provider-neutral generated-application relational data, scoped bindings, store, and capability grants.

## `packages/retrieval`

Deterministic local text retrieval.

Currently small by design:

- `semantic.py`;
- package exports.

It performs dependency-free hashing similarity and owns no authorization.

## `packages/runtime_plugins`

Trusted generated-code runtime definitions.

Files include:

- `contracts.py`;
- `registry.py`;
- `builtins.py`;
- `fullstack_contract.py`;
- `fullstack_runtime.py`;
- `app_identity_source_validation.py`;
- `relational_source_validation.py`.

This registry is trusted application code. Models do not author arbitrary runtime definitions.

## `packages/security`

Principals, execution contexts, scope resolution, permissions, human identity, delegation, guest workspace authority, surfaces, temporal context, and workspace invitations.

This package is the authoritative source for who can do what, where, as whom.

## `packages/service_bindings`

Project-scoped semantic bindings from generated software to OPERLY capabilities.

## `packages/software_projects`

Canonical constructed-software identity and source/runtime state.

## `packages/solutions`

User-facing software outcome/lifecycle above SoftwareProject.

## `packages/studio`

Studio-specific hardening, design, model/runtime policy, provenance, tool hardening, and terminal recovery.

It is no longer accurate to describe this package alone as the whole software-building architecture.

## `packages/tasks`

Durable task scheduler, workflows, event triggers, approvals-aware execution, and delivery.

## `packages/workspace`

Workspace application service layer.

Currently intentionally small (`service.py`): higher-level domain behavior lives in capability/API/security packages rather than accumulating here.

## `packages/workspace_entities`

Canonical shared workspace entity contracts/storage/bindings for generated Solutions.

Current canonical kinds are employee, customer, and location.

---

# Database and migrations

## Database package

`packages/database` splits persistence by domain.

Important model modules include:

- `models.py` — core users/tenants/messages/tasks/auth primitives;
- `agent_models.py` — agent conversations/runs;
- `artifact_models.py`;
- `business_models.py`;
- `company_models.py`;
- `channel_models.py`;
- `connector_models.py`;
- `account_connector_models.py`;
- `principal_models.py`;
- `identity_graph_models.py`;
- `scope_models.py`;
- `workspace_security_models.py`;
- `software_project_models.py`;
- `product_models.py`;
- `custom_software_models.py`;
- `studio_models.py`;
- `studio_source_models.py`;
- `dashboard_studio_models.py`;
- `application_builder_models.py`;
- `architecture_pack_models.py`;
- `operations_models.py`;
- `model_trace_models.py`.

`application_builder_models.py` and old Studio/dashboard/custom-software records preserve historical schema/data compatibility; their existence does **not** mean there is a current first-class `packages/application_builder/` runtime.

Other important files:

- `db.py` — engine/session/base;
- `schema.py` — shared model registration and Alembic head;
- `migrate.py` — migration/check/backup/release commands;
- `backup.py` — backup support;
- `model_trace.py` — durable model/provider trace sink;
- `runtime_trace_events.py` — surrounding orchestration telemetry.

## Alembic

`alembic/versions` is the authoritative schema history.

The currently registered head is:

`0044_human_identity_workspace_invitations`

The migration history shows the product's architectural evolution: dashboard Studio, application builder, managed records, custom software, planning/runtime records, company OS, identity/security, universal software projects, artifacts, canonical source versions, and workspace invitations.

Do not infer current architectural authority merely from an old migration name. Migrations are history.

---

# Scripts

`scripts/` contains developer/operator utilities:

| Script | Purpose |
| --- | --- |
| `auth_browser_app.py` | Small browser/auth development helper |
| `benchmark_models.py` | Model benchmark utility |
| `check_ollama.py` | Ollama connectivity/model check |
| `create_dev_account.py` | Development account bootstrap |
| `run_capability_sandbox.py` | Capability sandbox runner |
| `run_web.py` | Local web launch helper |
| `studio_phase0_acceptance.py` | Studio acceptance/check utility |

Scripts are operational/dev helpers, not production architecture boundaries.

---

# CI workflows

`.github/workflows` currently contains focused contract suites rather than one giant CI job:

| Workflow | Main concern |
| --- | --- |
| `app-identity.yml` | Generated-app identity contracts |
| `application-flow.yml` | Application/Solution flow and startup |
| `assistant-first-routing.yml` | Assistant-first routing behavior |
| `capability-sandbox.yml` | Capability resolution/sandbox |
| `coding-harness-smoke.yml` | Coding/source/runtime architecture smoke |
| `model-qualification.yml` | Model qualification |
| `production-runner.yml` | Production runner contracts |
| `react-frontend-foundation.yml` | Canonical React UI/build contracts |
| `relational-data.yml` | Generated relational-data boundary |
| `runtime-hardening.yml` | Auth/scope/security/runtime hardening |
| `solution-architecture.yml` | Canonical Solution/software architecture |
| `task-workflow-contracts.yml` | Task/workflow contracts |
| `unified-agent-runtime.yml` | Shared agent/Factory/artifact/computer runtime |
| `workspace-entities.yml` | Canonical workspace entity contracts |

CI is part of the architecture: many migration boundaries are enforced by regression tests so compatibility code does not silently become canonical again.

---

# Tests

`tests/` is a large regression suite spanning almost every subsystem.

Representative families include:

## Identity and scope

- account scope resolution;
- Personal/workspace creation/switching;
- human identity graph;
- workspace invitations;
- external/provider identity binding;
- delegation;
- guest authority;
- scope-aware actions.

## Agent runtime / Factory

- agent runtime hardening;
- objective verification;
- Agent Computer sessions/storage/resume;
- Factory bindings;
- Factory control-plane contracts;
- context/artifact distribution;
- worker adapter;
- action correlation;
- exact-stage resume;
- terminal approval/action behavior.

## Capabilities/actions

- effect/egress policy;
- capability discovery;
- schema validation;
- progressive exposure;
- firewall behavior;
- action lifecycle;
- approvals;
- real-capability contracts.

## Models

- adaptive model scoring;
- provider discovery;
- free-route eligibility;
- routing/failover;
- qualification;
- telemetry/traces.

## Context/history

- account/workspace context boundaries;
- federated history;
- provider-history adapters;
- Gmail/Calendar federation;
- materialization reauthorization.

## Software

- coding-harness execution;
- source versions;
- objective audit;
- Studio source/controller;
- SoftwareProject identity;
- Solution generation/completion;
- runtime plugins;
- service bindings;
- relational data;
- app identity;
- workspace entities;
- production runner.

## Connectors/channels

- Discord artifact/task delivery;
- channel identity;
- Google scopes/connectors;
- connector credential boundaries.

## Tasks/workflows

- workflow validation;
- waiting approvals;
- delivery;
- event triggers;
- Personal vs workspace execution.

The exact list changes rapidly; use the focused workflow files to see which subsets gate which architectural area.

---

# Deployment files

## Root `Dockerfile`

Builds the OPERLY control-plane image, including the production web bundle.

## `docker-compose.yml`

Local composition for the main application/dependencies.

## `railway.toml`

Railway deployment configuration for the control plane.

## `deploy/`

Contains reverse-proxy deployment material:

- `deploy/caddy/`;
- `deploy/nginx/`.

## Runner deployment

The isolated runner has its own Dockerfile, Compose stack, Caddy edge, and deployment contract under `apps/runner`.

The runner should not share the API's secrets.

---

# Local development

Requirements:

- Python 3.11+;
- Node.js/npm for frontend development/builds;
- SQLite for simple local use or PostgreSQL for production-like use;
- `uv` is the repository's common Python command runner in examples.

## Backend

```powershell
Copy-Item .env.example .env

uv venv
uv pip install -r requirements.txt
uv run python -m packages.database.migrate upgrade
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open:

```text
http://localhost:8000
```

## Frontend dev server

```powershell
cd apps/web
npm install
npm run dev
```

## Tests

```powershell
uv run pytest -q
```

Frontend contracts:

```powershell
cd apps/web
npm run test:contracts
npm run test:bundle
```

## Discord

```powershell
uv run python -m packages.connectors.discord.bot_harness
```

## Capability sandbox

```powershell
uv run python scripts/run_capability_sandbox.py
```

## Local runner sidecar

For development, `apps.runner.dev_main` can expose the same signed runner protocol while using local/test execution underneath.

This is explicitly not equivalent to the production container-isolation guarantee.

See `apps/runner/README.md` for the required environment and safety conditions.

---

# Configuration

Start with `.env.example`.

## Core application

- `OPERLY_ENV`
- `PUBLIC_BASE_URL`
- `SESSION_SECRET`
- `AUTH_TOKEN_PEPPER`
- `DATABASE_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `DEFAULT_TENANT_NAME`
- `DEFAULT_TIMEZONE`

## Model providers

- `OLLAMA_*`
- `OPEN_ROUTER_*` / `OPENROUTER_*`
- `GROQ_*`
- `GEMINI_*`
- `NVIDIA_*`
- `OPERLY_MODEL_*`

Current code-level policy also recognizes:

- `OPERLY_ACTIVE_MODEL_PROVIDERS` — default `ollama`;
- `OPERLY_FREE_MODELS_ONLY` — default enabled.

Those are hard routing gates, not merely score preferences.

## Agent/coding runtime

- `OPERLY_CODING_AGENT_MAX_STEPS`
- `OPERLY_CODING_AGENT_MAX_SECONDS`
- `OPERLY_CODING_AGENT_MODEL_SLICE_SECONDS`
- `OPERLY_CODING_SOURCE_CONTRACT_REPAIRS`
- `OPERLY_CODING_CONTEXT_CHARS`
- `OPERLY_CODING_CONTEXT_RECENT_MESSAGES`
- `OPERLY_CODING_WEB_TOOLS`
- `OPERLY_PLANNING_MODE`
- `OPERLY_WORKSPACE_AGENT_FACTORY`

## Solution worker

- `OPERLY_SOLUTION_WORKER_ENABLED`
- `OPERLY_SOLUTION_WORKER_ID`
- `OPERLY_SOLUTION_WORKER_LEASE_SECONDS`
- `OPERLY_SOLUTION_WORKER_POLL_SECONDS`

## Isolated runner

- `OPERLY_SANDBOX_RUNNER_URL`
- `OPERLY_SANDBOX_RUNNER_TOKEN`
- `OPERLY_SANDBOX_RUNNER_HOSTS`
- `OPERLY_SANDBOX_PREVIEW_HOSTS`
- `OPERLY_RUNNER_POLL_INTERVAL_SECONDS`
- `OPERLY_RUNNER_POLL_TIMEOUT_SECONDS`

Development/test-only:

- `OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER`
- `OPERLY_ENABLE_LOCAL_RUNNER_SIDECAR`
- `OPERLY_LOCAL_RUNNER_PUBLIC_BASE_URL`
- `OPERLY_TEST_RUNNER_ALLOW_DEPENDENCY_INSTALL`

## Google

Authentication credentials and connector credentials are separate:

- `GOOGLE_AUTH_*` — OPERLY account sign-in;
- `GOOGLE_OAUTH_*` — Google Workspace connector.

## Email

- `MAIL_PROVIDER`
- `OPERLY_FROM_EMAIL`
- `OPERLY_FROM_NAME`
- `ZOHO_MAIL_*`
- `SMTP_*`

## Discord

- `DISCORD_BOT_TOKEN`
- `BOT_OWNER_IDS`

## Connector encryption

- `OPERLY_CONNECTOR_SECRET_KEY`

## MCP

- `MCP_OAUTH_SECRET`
- `CHATGPT_MCP_CLIENT_SECRET`
- `CHATGPT_MCP_REDIRECT_URIS`

---

# Compatibility and migration seams

OPERLY has evolved quickly. The repository intentionally contains compatibility code while canonical contracts converge.

The important rule is: **compatibility code may preserve old data/imports; it must not quietly regain architectural authority.**

Current seams include:

1. **`packages/harness` is deprecated.** It is an import bridge to the plugin extensions package.
2. **Static frontend files remain.** They support public/auth and protected legacy Studio paths, not normal authenticated `/channels/**` development.
3. **Historical database models remain.** Dashboard Studio, application builder, old Studio and generated-project tables are migration/data history.
4. **There is no current first-class `packages/application_builder/` package.** Do not document it as a current subsystem merely because historical models/migrations exist.
5. **`SoftwareProject` is canonical.** Compatibility adapters can convert old rows, but canonical project persistence does not discover old generations as peers.
6. **`SolutionService` supports SoftwareProject runtime.** Historical runtime enum values are not current product runtimes.
7. **Ollama-specific compatibility code remains in a few older paths.** New model architecture goes through `packages/model_runtime`.
8. **`custom_software` contains both active and compatibility planning/runtime code.** Do not use file age/name alone to infer the active path.
9. **Coding tools still retain some historical vocabulary/contracts.** The goal is convergence on universal session capabilities without breaking working source/sandbox semantics.
10. **The public/auth static shell is not yet fully deleted.** Frontend migration is incomplete even though route ownership is already canonical.
11. **Local/test subprocess execution is not production isolation.**
12. **Some API router files exist without being mounted by `apps/api/main.py`.** Presence in the tree is not the same as a live route.

---

# Architecture documents

`docs/` contains deeper design/history material.

Important documents include:

- `TARGET_ARCHITECTURE.md` — target universal architecture;
- `IMPLEMENTATION_STATUS.md` — implementation checkpoint, useful but may lag `main`;
- `MULTI_PROVIDER_MODELS.md` — model-provider architecture;
- `PLUGIN_HARNESS.md` — plugin/capability harness notes;
- `model-runtime.md` — model runtime;
- `model-qualification.md` — model qualification;
- `unified-agent-runtime.md` — shared agent runtime;
- `STUDIO_MODEL_FAILOVER_FIX.md` — Studio/model failover history;
- `STUDIO_PRODUCT_QUALITY.md` — Studio quality work;
- `OPERLY_10000_BUG_ARCHITECTURE_AUDIT.md` — broad architecture/bug audit;
- `legal/` — legal product content;
- `evidence/` — retained implementation/evidence material.

When README and an older architecture document conflict, inspect current code and current CI before assuming the older document is authoritative.

---

# Root-level repository map

```text
.
├── .github/                 CI workflows
├── .dockerignore
├── .env.example             primary configuration template
├── .env.mvp.example         smaller MVP/example environment
├── .gitignore
├── Dockerfile               control-plane production image
├── README.md                repository/product architecture
├── RELEASE_CHECKLIST.md     release gates
├── ROADMAP.md               roadmap
├── alembic.ini
├── alembic/
│   └── versions/            authoritative schema history
├── apps/
│   ├── api/                 trusted FastAPI control plane
│   ├── web/                 React UI + static compatibility shell
│   ├── runner/              production generated-code isolation service
│   └── sandbox_runner/      Agent Computer/sandbox runtime
├── deploy/
│   ├── caddy/
│   └── nginx/
├── docker-compose.yml
├── docs/
├── packages/                33 first-class Python package areas
├── railway.toml
├── requirements.txt
├── scripts/
└── tests/
```

The Python requirements cover FastAPI/SQLAlchemy/Alembic, async SQLite/Postgres, Discord, Google auth, cryptography, HTTP clients, scheduling, S3-compatible storage, and document/media authoring/parsing libraries such as PDF, DOCX, PPTX, XLSX, images, YAML, ReportLab, and ODF.

---

# Design invariants

These are the shortest summary of what the repository is trying to preserve.

1. **Models are replaceable.**
2. **The human and the executing principal are not the same concept.**
3. **Personal and workspace authority are explicit and different.**
4. **Identity linking never merges authorization.**
5. **Delegation can narrow authority, never widen it.**
6. **Discovery does not grant execution.**
7. **Schemas, surface policy, permissions, effect, egress, and approval are application-controlled.**
8. **Consequential model actions become durable Actions.**
9. **Provider success is not verified completion.**
10. **Events preserve initiator, executor, delegation, surface, causation, and correlation.**
11. **Context references are locators, not bearer credentials.**
12. **Durable history is not blindly replayed into prompts.**
13. **Agent workers receive minimum relevant context rather than inherited global transcripts.**
14. **Failed Factory artifacts never become trusted dependency inputs.**
15. **Approval/resume must not replay an already-resolved side effect.**
16. **SoftwareProject is the canonical constructed-software identity.**
17. **Solution is the user-facing lifecycle above SoftwareProject.**
18. **Generated code never executes inside the FastAPI control plane.**
19. **Generated applications do not receive provider/database credentials.**
20. **Canonical workspace entities should be shared, not duplicated privately by every generated app.**
21. **Plugin registration is composition, not an authorization bypass.**
22. **Runtime plugins are trusted application definitions, not model-authored shell access.**
23. **Tracing should explain the real model/runtime path without storing secrets or hidden reasoning.**
24. **Compatibility layers exist to migrate safely, not to create permanent parallel architectures.**
25. **One canonical route should have one renderer and one authority model.**

---

## One-sentence architecture

**OPERLY resolves a human and scope, gives an agent only the context and capabilities it is allowed to discover, executes authorized operations through a policy/action firewall, records durable provenance/evidence/traces, and runs generated software behind a separate isolated runtime boundary.**
