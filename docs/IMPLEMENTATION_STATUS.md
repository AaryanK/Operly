# Operly rebuild implementation status

Updated for **Kernel v3** after the intentional legacy-runtime demolition.

## Current production direction

The live mainline before this branch contains the hardened account shell and deterministic workspace business OS. AI/model/agent/MCP/Studio runtime routers are intentionally offline. Earlier architecture documents that describe the deleted runtime as already implemented are historical and must not be treated as current runtime truth.

This branch builds the deterministic Operly infrastructure first. AI is intentionally a later interface/planner over that infrastructure, not the runtime foundation.

## Implemented in Kernel v3

- One `TrustedIngress -> ExecutionContext` path that accepts identity/source facts but never request-provided roles or permissions.
- Existing Personal, full Workspace, and Guest Workspace authority semantics are reused rather than reimplemented.
- One namespaced `CapabilityRegistry` with input/output contracts, permissions, scopes, risk, approval policy, resource scope, version, provider, events, tags, and aliases.
- Separate capability discovery/visibility and effective execution authority.
- One `ProviderRegistry`; orchestration resolves providers by capability metadata rather than vendor-specific branches.
- One deterministic `CapabilityPolicyEngine` with `ALLOW / ASK / DENY` decisions.
- Deterministic contract validation on both capability input and provider output.
- A single 13-stage `OperlyKernelRuntime` matching the architecture diagram.
- Minimum-context loading: role/surface/scope plus workspace identity/timezone only; resource data is fetched by the selected provider.
- Transactional execution: validated writes + trace + events commit together; invalid/failed writes roll back and failure provenance is persisted separately.
- Durable `kernel_runs`, `kernel_run_steps`, and `kernel_events` persistence via migration `0047_operly_kernel_v3`.
- Initial native capabilities for runtime status, workspace description, workspace modules, and personal task operations.
- The mature deterministic Workspace OS record registry is projected into Kernel capabilities automatically through `WorkspaceOSProvider`.
- CRM, catalog, inventory, sales, finance, procurement, fulfillment, projects, operations, support, scheduling, tasks, team, documents, marketing, compliance, research, grants, and integration records receive standardized list/create/update/delete capability contracts from existing business metadata instead of bespoke agent tools.
- Generated Workspace OS capability schemas reuse actual SQLAlchemy field types, required fields, select options, module permissions, and mutability rules already present in the business OS.
- Workspace OS writes execute below the HTTP route layer so Kernel v3 still owns commit/rollback, result validation, trace, and emitted events.
- Destructive generated record capabilities are `MEDIUM` risk and `approval_required=True`.
- Scoped request idempotency is persisted in `kernel_request_claims` via migration `0048_kernel_request_idempotency`; a repeated successful `request_id` replays its prior normalized response instead of repeating the business side effect, while reuse with a different deterministic request conflicts.
- Durable exact-invocation approvals are persisted in `kernel_approvals` via migration `0049_kernel_approvals`.
- An approval binds the scope, initiating principal, capability ID, and canonical argument hash. Approved execution rechecks current authority and the exact capability/arguments before running, then consumes the approval in the same transaction as execution.
- Workspace approval decisions require `actions:approve`; Personal approvals remain account-owner scoped. Approval decisions emit provenance events.
- Authenticated APIs expose workspace/Personal capability execution, approval listing/decision, run traces, and workspace event audit.
- API health/rebuild status distinguishes `kernel_runtime_enabled=true` from `ai_runtime_enabled=false`.

## Deliberately still offline

- Model inference and model planning.
- Legacy business-agent router and its removed dependencies.
- MCP runtime.
- Studio/generated-application execution runtime.
- Discord/Slack/WhatsApp live agent loops until they are rewired as ingress adapters over the deterministic substrate.
- Durable workflow consumers/conditions on top of `kernel_events`.

These are not separate architectures. Each must become an ingress adapter, provider, planner, event consumer, or client of Kernel v3.

## Infrastructure-first implementation order

1. Finish deterministic non-record Workspace OS capabilities: module/preset management, workspace settings, inventory adjustments, membership/role administration, and connector lifecycle.
2. Add durable event subscriptions/workflow execution over `kernel_events`, with idempotent delivery and fresh authorization for every action.
3. Standardize provider/plugin lifecycle, capability health/availability, connector-account scope resolution, and resource/service binding.
4. Route Discord, Slack, WhatsApp, MCP, web/mobile/API/SDK, and generated applications through the same ingress + capability boundary.
5. Add operational hardening: retention/cleanup for idempotency state, approval expiry/cancellation, workflow retry/dead-letter behavior, rate/usage quotas, and infrastructure observability.
6. **Only after those infrastructure layers are stable**, rebuild model/provider selection and add AI as a planner/interface over stages 1/2/7. AI must not own authority, resource access, execution, validation, approvals, idempotency, or event provenance.

See `docs/OPERLY_KERNEL_V3.md` for the normative rebuild mapping.
