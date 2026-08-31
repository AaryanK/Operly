# Operly Kernel v3

Status: **active rebuild architecture**

This document maps the current rebuild to the Operly architecture diagram built around one rule:

> Resolve identity → resolve scope → resolve permissions → load minimum context → reason → authorize → execute → validate → trace → emit events → repeat.

The legacy AI/runtime stack was intentionally demolished before this implementation. Kernel v3 therefore does **not** restore provider-specific agent code. It establishes the deterministic control plane first, so future model planners, Discord/Slack/WhatsApp adapters, MCP, Studio, workflows, and generated applications all enter through the same authority and capability boundary.

## 1. Ingress: any interface, one trusted context

`packages/kernel/ingress.py` defines `TrustedIngress`. An ingress adapter may supply authenticated identity facts, source channel/space metadata, and the requested scope. It cannot supply a role or permissions.

`resolve_ingress_context()` delegates to the existing security layer:

- personal requests → `resolve_personal_execution_context()`;
- full workspace requests → live workspace membership + role permissions;
- provisional external spaces → existing Guest Workspace authority resolution;
- source-platform permissions for Guest Workspaces remain trusted adapter metadata and are intersected with Operly's guest ceiling and admin policy.

The output is the canonical `ExecutionContext`. Everything after ingress consumes that context rather than request-provided authority.

## 2. Operly Universe: Personal and Workspace scopes

Kernel v3 preserves the existing explicit scope model:

- **Personal**: account-owned private authority. Personal resources must be queried by `owner_user_id` and never by a selected workspace merely because the UI is focused there.
- **Workspace**: workspace-owned authority. Full members resolve persisted role permissions. Guest Workspaces stay workspace-scoped but receive a narrower effective permission set.

A future Personal AI may acquire a separate workspace context when the user asks it to act in a workspace. A personal context itself never silently becomes workspace authority.

## 3. Capability Registry: the single source of truth

`packages/kernel/registry.py` owns the capability catalog. Every `CapabilitySpec` includes:

- stable namespaced ID and version;
- provider identity;
- input and output contracts;
- supported scopes;
- required permissions;
- risk and approval policy;
- resource scope;
- reversibility;
- aliases/tags for discovery;
- emitted event types.

Discovery and authority are different operations:

- `visible()` answers what the current surface/scope may discover;
- `effective()` answers what the current principal is presently allowed to use;
- `search()` may find a visible capability without granting it.

The web API exposes the effective registry through `/api/kernel/capabilities` and `/api/kernel/personal/capabilities`.

## 4. Capability Providers

`ProviderRegistry` maps provider IDs to execution implementations. The runtime only knows `CapabilitySpec.provider_id`; orchestration does not branch on vendor names.

The first native provider proves the end-to-end contract with real deterministic Operly resources:

- `system.runtime.status`;
- `workspace.describe`;
- `workspace.modules.list`;
- `tasks.list`;
- `tasks.create`;
- `tasks.update_status`.

Task providers enforce resource ownership again at the SQL query boundary, so a valid capability permission cannot be used to name a task from another workspace/personal scope.

New CRM, ERP, connector, Studio, MCP, model, deployment, and external-tool providers should register capabilities without changing the kernel loop.

## 5. Agent Runtime: the 13-stage loop

`OperlyKernelRuntime.execute()` records and enforces these stages in order:

1. Understand — normalize the inbound goal/request.
2. Classify & deduce task — resolve the requested capability.
3. Determine required scope — verify personal/workspace compatibility.
4. Resolve authorized capabilities — recompute the effective registry from trusted permissions.
5. Load minimum context — only role/surface/workspace identity needed for the invocation.
6. Expose tools — expose the current effective capability set, not the global registry.
7. Reason & plan — produce a capability plan and validate its arguments.
8. Authorize — deterministic fail-closed policy; model/planner output cannot grant authority.
9. Execute — invoke the registered provider.
10. Validate result — deterministic output-contract validation.
11. Record & trace — append the run/stage audit trail.
12. Emit events — create canonical event records such as `task.created`.
13. Respond / continue — return a normalized result and completion state.

The current planner is deliberately deterministic while the model runtime is offline. A model planner can replace stages 1/2/7 later without replacing stages 3/4/8/9/10/11/12.

## 6. Security and Policy Plane

`CapabilityPolicyEngine` is fail-closed and recomputes:

- scope compatibility;
- surface visibility;
- every required permission;
- explicit approval requirement.

A capability can return `ALLOW`, `ASK`, or `DENY`. No provider can upgrade a denied request. Guest Workspace restrictions flow through the same policy because their `ExecutionContext.permissions` are already the intersection of platform authority, Operly guest ceilings, and workspace-admin policy.

Input and output validation is deterministic and owned by the kernel. Providers receive already-authorized, contract-validated arguments.

## 7. Audit, provenance, and event plane

Migration `0047_operly_kernel_v3` adds:

- `kernel_runs` — one row per governed runtime invocation;
- `kernel_run_steps` — the ordered 13-stage trace;
- `kernel_events` — normalized event/provenance records.

Event records include scope, principal, human/system actor, initiator, executor, capability, resource type/id, payload, and timestamp. This provides the canonical `who did what?` chain required by workflows, integrations, FLOW/debugging, and compliance.

Workspace users with `actions:read` can inspect the current event stream through `/api/kernel/events`; run traces are scope-filtered through `/api/kernel/runs/{id}` and `/api/kernel/personal/runs/{id}`.

## 8. Transactional guarantee

Capability side effects, successful trace rows, and emitted event records commit together. If execution or result validation fails, the business mutation is rolled back and a separate failed run trace is persisted. This prevents a provider from leaving an unvalidated partial write while still preserving failure provenance.

## 9. What remains intentionally outside this slice

Kernel v3 is the control-plane foundation, not a restoration of the demolished legacy runtime. The next slices should converge on it in this order:

1. route Discord and future Slack/WhatsApp ingress through `TrustedIngress` + `ExecutionContext`;
2. register deterministic CRM/ERP actions as capabilities instead of adding parallel agent tools;
3. add approval fulfillment for `ASK` decisions and wire it to the Activity Center;
4. attach the durable workflow engine to `kernel_events`;
5. rebuild model selection/inference as capability providers and plug a model planner into stages 1/2/7;
6. expose MCP through the exact same registry/policy/runtime path;
7. rebuild Studio/generated-app bindings so generated software calls the kernel rather than provider credentials;
8. add connector account/scopes resolvers to minimum context only when the chosen capability requires them;
9. retire stale pre-demolition runtime documentation/imports after their remaining persistence models are migrated or removed.

No future surface should call a provider directly as an optimization. The kernel is the execution authority.
