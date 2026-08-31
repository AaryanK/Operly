# Operly rebuild implementation status

Updated for **Kernel v3** after the intentional legacy-runtime demolition.

## Current production direction

The live mainline before this branch contains the hardened account shell and deterministic workspace business OS. AI/model/agent/MCP/Studio runtime routers are intentionally offline. Earlier architecture documents that describe the deleted runtime as already implemented are historical and must not be treated as current runtime truth.

This branch builds the deterministic Operly infrastructure first. AI is intentionally treated as a later interface/planner over that infrastructure, not as the runtime foundation.

## Implemented in Kernel v3

- One `TrustedIngress -> ExecutionContext` path that accepts identity/source facts but never request-provided roles or permissions.
- Existing Personal, full Workspace, and Guest Workspace authority semantics are reused rather than reimplemented.
- One namespaced `CapabilityRegistry` with input/output contracts, permissions, scopes, risk, approval policy, resource scope, version, provider, events, tags, and aliases.
- Separate capability discovery/visibility and effective execution authority.
- One `ProviderRegistry`; orchestration resolves provider by capability metadata rather than vendor-specific branches.
- One deterministic `CapabilityPolicyEngine` with `ALLOW / ASK / DENY` decisions.
- Deterministic contract validation on both capability input and provider output.
- A single 13-stage `OperlyKernelRuntime` matching the new architecture diagram.
- Minimum-context loading: role/surface/scope plus workspace identity/timezone only; resource data is fetched by the selected provider.
- Transactional execution: validated writes + trace + events commit together; invalid/failed writes roll back and a failure trace is persisted separately.
- Durable `kernel_runs`, `kernel_run_steps`, and `kernel_events` persistence via migration `0047_operly_kernel_v3`.
- Initial native capabilities for runtime status, workspace description, workspace modules, and personal task operations.
- The mature deterministic Workspace OS record registry is now projected into Kernel capabilities automatically through `WorkspaceOSProvider`.
- CRM, catalog, inventory, sales, finance, procurement, fulfillment, projects, operations, support, scheduling, tasks, team, documents, marketing, compliance, research, grants, and integration records receive standardized list/create/update/delete capability contracts from their existing record metadata instead of bespoke agent tools.
- Generated Workspace OS capability schemas reuse the actual SQLAlchemy field types, required fields, select options, module permissions, and mutability rules already present in the business OS.
- Workspace OS writes execute below the HTTP route layer so Kernel v3 still owns transaction commit/rollback, result validation, trace, and emitted events.
- Destructive generated record capabilities are marked `MEDIUM` risk and `approval_required=True`; they remain blocked until deterministic approval fulfillment is implemented.
- Resource-boundary enforcement remains inside the Workspace OS record helpers/queries in addition to capability permission checks.
- Authenticated web endpoints exist for workspace and Personal capability discovery/execution plus scope-filtered traces and workspace event audit.
- API health/rebuild status distinguishes `kernel_runtime_enabled=true` from `ai_runtime_enabled=false`.

## Deliberately still offline

- Model inference and model planning.
- Legacy business-agent router and its removed dependencies.
- MCP runtime.
- Studio/generated-application execution runtime.
- Discord/Slack/WhatsApp live agent loops until they are rewired as ingress adapters over the completed deterministic substrate.
- Durable workflow consumers/conditions on top of `kernel_events`.
- Approval fulfillment UI/runtime for capabilities returning `ASK`.

These are not separate architectures. Each must become an ingress adapter, provider, planner, event consumer, or client of Kernel v3.

## Infrastructure-first implementation order

1. Finish the deterministic Workspace OS capability substrate: add remaining non-record operations (module management, inventory adjustments, membership/role administration, workspace settings, connector lifecycle) without creating parallel tool implementations.
2. Add durable approval persistence, decision, immutable argument binding, and resume-after-approval for `ASK` decisions; wire Activity Center to this same state machine.
3. Add request idempotency/deduplication before allowing retried automated writes.
4. Add durable event subscriptions and workflow execution over `kernel_events`, with idempotent trigger delivery and deterministic authorization at every action.
5. Standardize provider/plugin lifecycle, capability health/availability, connector-account scope resolution, and resource binding so external systems plug into the same registry.
6. Route Discord, Slack, WhatsApp, MCP, web/mobile/API/SDK, and generated applications through the same ingress + capability boundary.
7. **Only after those infrastructure layers are stable**, rebuild model/provider selection and add AI as a planner/interface over stages 1/2/7. AI must not own authority, resource access, execution, validation, or event provenance.

See `docs/OPERLY_KERNEL_V3.md` for the normative rebuild mapping.
