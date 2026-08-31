# Operly rebuild implementation status

Updated for **Kernel v3** after the intentional legacy-runtime demolition.

## Current production direction

The live mainline before this branch contains the hardened account shell and deterministic workspace business OS. AI/model/agent/MCP/Studio runtime routers are intentionally offline. Earlier architecture documents that describe the deleted runtime as already implemented are historical and must not be treated as current runtime truth.

This branch builds the deterministic Operly infrastructure first. AI is intentionally a later interface/planner over that infrastructure, not the runtime foundation.

The implementation rule is now explicit:

> If an important action should eventually be available to an AI agent, first make it a deterministic Operly capability that a human, API client, workflow, or other interface can execute without AI.

## Implemented in Kernel v3

### Governed execution substrate

- One `TrustedIngress -> ExecutionContext` path that accepts identity/source facts but never request-provided roles or permissions.
- Existing Personal, full Workspace, and Guest Workspace authority semantics are reused rather than reimplemented.
- One namespaced `CapabilityRegistry` with input/output contracts, permissions, scopes, risk, approval policy, resource scope, version, provider, events, tags, and aliases.
- Separate capability discovery/visibility and effective execution authority.
- One `ProviderRegistry`; orchestration resolves providers by capability metadata rather than vendor-specific branches.
- One deterministic `CapabilityPolicyEngine` with `ALLOW / ASK / DENY` decisions.
- Deterministic contract validation on both capability input and provider output.
- A single 13-stage `OperlyKernelRuntime` matching the architecture diagram.
- Minimum-context loading: role/surface/scope plus workspace identity/timezone only; resource data is fetched by the selected provider.
- Transactional execution: validated database writes + trace + events commit together; invalid/failed writes roll back and failure provenance is persisted separately.
- Durable `kernel_runs`, `kernel_run_steps`, and `kernel_events` persistence via migration `0047_operly_kernel_v3`.
- Scoped request idempotency in `kernel_request_claims` via migration `0048_kernel_request_idempotency`; a repeated successful `request_id` replays its prior normalized response instead of repeating the business side effect, while conflicting reuse is rejected. Current authority is recomputed before replay.
- Durable exact-invocation approvals in `kernel_approvals` via migration `0049_kernel_approvals`.
- An approval binds the scope, initiating principal, capability ID, and canonical argument hash. Approved execution rechecks current authority and the exact invocation before running, then consumes the approval with successful execution.
- Workspace approval decisions require `actions:approve`; Personal approvals remain account-owner scoped. Approval decisions emit provenance events.

### Deterministic tool fabric

The registry is intentionally layered so future agents can prefer business outcomes while lower-level deterministic primitives still exist.

**Native/core capabilities**

- runtime status;
- workspace description and module visibility;
- personal/workspace task list/create/status update.

**Workspace record capabilities**

- The mature deterministic Workspace OS record registry is projected automatically through `WorkspaceOSProvider`.
- CRM, catalog, inventory, sales, finance, procurement, fulfillment, projects, operations, support, scheduling, tasks, team, documents, marketing, compliance, research, grants, and integration records receive standardized list/create/update/delete contracts from existing business metadata instead of bespoke agent tools.
- Generated schemas reuse real SQLAlchemy types, required fields, select options, module permissions, references, mutability rules, and existing business mutation hooks.
- Destructive record capabilities are approval-gated.

**Workspace control capabilities**

`WorkspaceControlProvider` adds deterministic operations that do not fit generic CRUD:

- workspace operating summary and activity feed;
- workspace settings updates;
- module enable/disable with dependency protection;
- workspace preset listing/application;
- member listing/addition/role changes/removal with last-owner invariants;
- role/permission listing and updates;
- invitation listing/creation/revocation;
- inventory movement history and stock adjustments.

Access-changing operations are approval-gated and continue to enforce owner/role invariants inside the provider rather than trusting a caller.

**Agent-grade business outcomes**

`WorkspaceBusinessProvider` adds higher-level deterministic tools so a future planner does not have to orchestrate fragile row-by-row mutations itself:

- `workspace.search` — permission/module-aware search across customers, organizations, opportunities, products, invoices, projects, tasks, appointments, suppliers, and research projects;
- `workspace.attention.list` — deterministic overdue invoice, low-stock, due-task, appointment, follow-up, and urgent-support signals;
- `workspace.customer.snapshot` — customer context with opportunities, orders, invoices, interactions, lifetime sales, and outstanding balance;
- `workspace.sales.complete` — one governed transaction for order + lines + inventory movements + payment or invoice;
- `workspace.finance.invoice.create_simple` — atomic invoice + line creation;
- `workspace.finance.payment.record` — payment recording plus deterministic invoice-status synchronization.

The financial/sales outcome tools are approval-gated and executed through Kernel transaction, idempotency, validation, trace, and event semantics.

**Kernel-native Google workspace tools**

A new `WorkspaceGoogleProvider` avoids reviving the demolished legacy capability runtime. It resolves the existing workspace OAuth record and granted scopes directly, never provider credentials or scopes supplied by the caller.

Available contracts include:

- Google connection status/scopes/health (credential-free metadata only);
- Gmail search and message read;
- Gmail draft creation;
- Gmail send after exact-invocation approval;
- Gmail label modification after approval;
- Calendar list-calendars, list-events, and free/busy;
- Calendar create/update/delete after approval.

The connector provider uses bounded network timeouts/retries for reads and avoids automatic retry of externally mutating requests inside the provider. Kernel request idempotency still protects ordinary retries, but distributed exactly-once guarantees for external providers remain a separate infrastructure hardening item because a process can fail after a provider accepts an external mutation but before local commit.

### Workspace interface

- The normal Workspace navigation now includes **Capabilities** under Extend.
- `CapabilitiesPage` is a human/debug client of `/api/kernel/capabilities` and `/api/kernel/execute`.
- It shows only the current principal's effective capabilities, with provider, permissions, risk, approval requirement, resource scope, reversibility, and input/output contracts.
- Users can search/filter the capability surface, enter deterministic JSON arguments, execute a capability, inspect the validated result and Kernel trace, and fulfill an exact approval before resuming the same invocation.
- The console generates a stable `request_id` for an invocation so UI retries participate in Kernel idempotency.

This is deliberately the same surface future AI planners will discover. There is no separate AI-only tool registry.

### APIs and runtime truth

- Authenticated APIs expose workspace/Personal capability discovery and execution, approval listing/decision, run traces, and workspace event audit.
- API health/rebuild status distinguishes `kernel_runtime_enabled=true` from `ai_runtime_enabled=false`.

## Deliberately still offline

- Model inference and model planning.
- Legacy business-agent router and its removed dependencies.
- MCP execution surface.
- Studio/generated-application execution runtime.
- Discord/Slack/WhatsApp live agent loops until they are rewired as ingress adapters over the deterministic substrate.
- Durable workflow consumers/conditions on top of `kernel_events`.

These are not separate architectures. Each must become an ingress adapter, provider, planner, event consumer, or client of Kernel v3.

## Remaining deterministic tool families

Before adding AI, continue converging these areas onto the same capability boundary:

1. Files/artifacts and knowledge retrieval/creation with workspace ownership, size/type contracts, and deterministic parsing boundaries.
2. Durable workflow definitions/subscriptions over `kernel_events`, including idempotent action delivery, fresh authorization, retries, dead letters, loop prevention, and execution budgets.
3. Additional connector providers (Canva and later Slack/WhatsApp/other integrations) using the same scoped-provider pattern as Google.
4. Website/Solutions/Studio/deployment operations as project-scoped capabilities rather than direct runtime shortcuts.
5. Channel/member/message operations needed by normal workspace collaboration interfaces.
6. Provider/plugin lifecycle, capability health/availability, connector-account scope resolution, secrets references, and resource/service bindings.
7. Operational hardening: retention/cleanup for idempotency and approval state, approval expiry/cancellation, external-effect reconciliation, rate/usage quotas, and infrastructure observability.
8. Route Discord, Slack, WhatsApp, MCP, web/mobile/API/SDK, and generated applications through the same ingress + capability boundary.
9. **Only after these infrastructure layers are stable**, rebuild model/provider selection and add AI over the reasoning/planning stages. AI must not own authority, resource access, execution, validation, approvals, idempotency, transactions, or provenance.

See `docs/OPERLY_KERNEL_V3.md` for the normative rebuild mapping.
