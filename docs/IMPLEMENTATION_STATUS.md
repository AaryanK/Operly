# Operly rebuild implementation status

Updated for **Kernel v3** after the intentional legacy-runtime demolition.

## Current production direction

The live mainline before this branch contains the hardened account shell and deterministic workspace business OS. AI/model/agent/MCP/Studio runtime routers are intentionally offline. Earlier architecture documents that describe the deleted runtime as already implemented are historical and must not be treated as current runtime truth.

This branch begins the new runtime from the security boundary outward instead of restoring the deleted parallel registries/provider clients.

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
- Initial real native capabilities for runtime status, workspace description, workspace modules, and personal/workspace task list/create/status update.
- Resource-boundary enforcement inside task SQL queries in addition to capability permission checks.
- Authenticated web endpoints for workspace and Personal capability discovery/execution plus scope-filtered traces and workspace event audit.
- API health/rebuild status now distinguishes `kernel_runtime_enabled=true` from `ai_runtime_enabled=false`.

## Deliberately still offline

- Model inference and model planning.
- Legacy business-agent router and its removed dependencies.
- MCP runtime.
- Studio/generated-application execution runtime.
- Discord/Slack/WhatsApp live agent loops until they are rewired to `TrustedIngress`.
- Durable workflow consumers/conditions on top of `kernel_events`.
- Approval fulfillment UI/runtime for capabilities returning `ASK`.

These are not separate architectures. Each must be rebuilt as an ingress adapter, capability/provider, planner, or event consumer around Kernel v3.

## Next implementation order

1. Wire current external-channel identity/Guest Workspace adapters into `TrustedIngress` and prove Discord end-to-end.
2. Move deterministic Workspace OS CRUD/actions behind generated capability specs so the existing CRM/ERP suite becomes agent-addressable without duplicate domain code.
3. Implement approval persistence/fulfillment for `ASK`, including Activity Center lifecycle and resume-after-approval.
4. Add durable workflow subscriptions to `kernel_events` with idempotent trigger execution.
5. Rebuild model runtime/provider discovery, then replace only the deterministic planner while keeping deterministic authorization/execution/validation unchanged.
6. Expose MCP and generated applications through the same registry and policy boundary.

See `docs/OPERLY_KERNEL_V3.md` for the normative rebuild mapping.
