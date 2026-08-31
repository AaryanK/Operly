# Operly governed execution substrate

Status: active deterministic infrastructure; AI planning remains offline.

The package historically named `packages/kernel` is the shared governed execution substrate. It is intentionally **not** the owner of Workspace business logic. Workspace tools are owned and composed by `packages/workspace_modules/tools`.

Core rule:

> Resolve identity → resolve scope → resolve permissions → resolve available tools → load minimum context → authorize → execute → validate → trace → emit events → respond.

## 1. Shared substrate

`packages/kernel` owns reusable cross-scope mechanics only:

- `ingress.py` — trusted ingress facts → `ExecutionContext`;
- `contracts.py` — capability/request/result contracts;
- `registry.py` — generic capability registry primitive;
- `policy.py` — deterministic `ALLOW / ASK / DENY`;
- `schema_validation.py` — deterministic input/output validation;
- `providers.py` — generic provider registry and Personal/platform primitives;
- `approvals.py` — exact invocation approval binding;
- `idempotency.py` — scoped request claims/replay rules;
- `runtime.py` — 13-stage governed execution;
- `runtime_availability.py` — provider availability-aware discovery;
- `audit.py` — trace/event persistence.

`packages/kernel/bootstrap.py` composes only generic/Personal primitives. It does not import `packages.workspace_modules`.

## 2. Workspace ownership

All Workspace deterministic tool implementations live under `packages/workspace_modules/tools`:

- `system.py` — Workspace identity and module visibility;
- `records.py` — generated Workspace OS CRUD contracts/providers;
- `controls.py` — Workspace settings/modules/presets/access/inventory controls;
- `business.py` — higher-level deterministic business outcomes;
- `google.py` — Workspace Google/Gmail/Calendar provider;
- `availability.py` — Workspace-specific provider/module/OAuth preflight;
- `runtime.py` — composes Workspace capabilities/providers over the shared substrate;
- `router.py` — exposes the Workspace HTTP tool API.

This direction is deliberate: the execution substrate must never become a second Workspace package.

## 3. Scope and authority

`TrustedIngress` accepts authenticated identity/source facts but never caller-supplied roles or permissions. `resolve_ingress_context()` resolves Personal/full Workspace/Guest Workspace authority through the existing security layer.

A Workspace request therefore enters with a freshly resolved `ExecutionContext`. The tool registry then applies scope/surface/permission checks, and Workspace provider availability checks module state or connector authority before the tool is exposed.

Discovery is not authority. Execution rechecks its own invariants.

## 4. Workspace E2E API

Workspace tools are not exposed through a generic Workspace Kernel route. The Workspace package owns:

- `GET /api/workspace-tools`;
- `GET /api/workspace-tools/{capability_id}`;
- `POST /api/workspace-tools/{capability_id}/execute`;
- Workspace approval, event and run endpoints beneath `/api/workspace-tools`.

Each discovered tool advertises its exact `method`, `endpoint`, and contract metadata. This allows the React UI, workflows, SDKs, MCP adapters, external channel adapters, and future AI planners to consume the same deterministic interface.

The generic `/api/kernel` router remains Personal-only.

## 5. Workspace frontend

Workspace → Extend → **Capabilities** is a human/debug client of this API.

It discovers tools from `/api/workspace-tools` and invokes the exact endpoint returned by the backend. It does not call `/api/kernel/capabilities` or `/api/kernel/execute`.

The frontend path is therefore:

**CapabilitiesPage → tool.endpoint → Workspace tool router → trusted context → availability → policy/approval/idempotency → Workspace provider → validation → trace/events → result.**

This makes the human UI a real consumer of the same interface future agents will receive.

## 6. Governed execution stages

The shared runtime still enforces the same 13 stages:

1. Understand.
2. Classify/deduce task.
3. Resolve required scope.
4. Resolve authorized capabilities.
5. Load minimum context.
6. Expose allowed tools.
7. Plan invocation.
8. Authorize deterministically.
9. Execute provider.
10. Validate result.
11. Record/trace.
12. Emit events.
13. Respond/continue.

The current planner is deterministic. A future model may replace reasoning/planning stages, but not scope resolution, authority, approval, execution, validation, idempotency or provenance.

## 7. Reliability

Database mutations, successful trace rows and emitted events commit together. Failed execution/result validation rolls back the business mutation and preserves failure provenance separately.

Request idempotency prevents ordinary duplicate database effects and exact-invocation approvals bind capability + canonical arguments + scope/principal. Current authority is rechecked before execution/replay.

External APIs still require a dedicated outbox/reconciliation mechanism for truthful crash-safe exactly-once behavior; this is not overstated as solved.

## 8. Future interfaces

Discord, Slack, WhatsApp, MCP, generated applications, workflows and AI agents should become clients/adapters over the same Workspace tool surface. No future interface should receive special provider credentials or bypass Workspace tool authorization as an optimization.
