# Unified Agent Runtime

The core security invariant is unchanged: every human, workflow, generated solution, Studio surface, MCP client, and delegated agent uses the same governed capability fabric. Generated code never receives provider credentials, and temporary sandbox execution never becomes durable authority or storage.

## Runtime boundary

The new agent runtime is an orchestration layer above the Operly Kernel, not a second execution engine. An agent may decide *which* capability to request and *with what arguments*, but it never supplies its own workspace role, permissions, principal, provider credentials, or durable authority.

Every executable step is converted into a Kernel `RuntimeRequest`. Kernel then re-resolves the canonical capability, validates its schema, evaluates current `ExecutionContext` authority, applies approval policy, reserves mutating idempotency, executes the provider, validates output, emits events, and records audit trace.

`AgentRuntimeSettings` still defaults to disabled. The only environment switch recognized by this runtime is `OPERLY_AGENT_RUNTIME_ENABLED=1`; legacy agent flags do not enable it. No API route or production worker currently sets that switch, and the API continues to report `ai_runtime_enabled: false`.

## Authorization-aware planning

`GovernedAgentPlanner` adds a model-planning boundary without adding another execution path. It receives the canonical `CapabilityRegistry` selected by the caller and retrieves a small candidate set with `effective_only=True`, so scope, surface visibility, and current `ExecutionContext` permissions are applied before a capability can be shown to a planner. Production composition must pass the registry owned by the Kernel runtime that will execute the resulting plan; Kernel still independently re-resolves and re-authorizes every requested capability.

The planner never receives the full registry by default. Candidate count, description length, per-capability bytes, total prompt bytes, model-output bytes, plan steps, and mutations are bounded. Oversized capability contracts are omitted rather than truncated into a misleading schema; if no safe authorized candidate remains, planning fails closed.

Planner-facing capability cards contain only the capability ID, display name, bounded description, input schema, risk, and approval requirement. Provider IDs, permission lists, scope internals, credentials, and principals are not part of the planner contract. Capability descriptions and schemas are explicitly treated as untrusted data, not instructions.

The provider-neutral `AgentPlannerModel` interface returns only structured planning data. This slice deliberately does not give that interface a capability execution method or provider registry. A concrete model transport can be attached later behind a new Kernel-v3 inference boundary without changing authority or execution semantics.

The pre-Kernel-v3 `packages/model_runtime` implementation was intentionally demolished during the runtime rebuild. It must not be restored wholesale merely to satisfy the planner interface. The new inference boundary is tracked separately in #318 and must be designed for the current Kernel-v3 authority model.

Model output has exactly one allowed top-level field, `steps`. Each step has exactly `capability_id` and `arguments`. Model-supplied step IDs, approvals, permissions, principals, workspace IDs, scopes, or other authority-shaped fields are rejected. Operly assigns durable `step-001`, `step-002`, ... identities server-side.

A selected capability must be in the exact retrieved candidate set. Arguments are validated against the canonical capability input schema before an `AgentPlan` is created. Mutation and step budgets are checked while constructing the plan and are checked again by the executor before any capability executes. Kernel independently re-authorizes every step at execution time, so planner visibility is never durable authority.

Malformed JSON, markdown-wrapped output, non-JSON data, oversized output, unsupported fields, invalid arguments, out-of-set capability selection, or budget violations all fail closed before Kernel is called.

## Plan and retry identity

An `AgentPlan` contains a stable `run_id`, a user-visible goal, bounded steps, and an explicit budget. Every step has a unique `step_id`, canonical capability ID, arguments, and optionally the Kernel approval UUID used to resume that exact step.

`stable_step_request_id(run_id, step_id)` produces one bounded deterministic request identity for the logical step. Approval resume or crash recovery reuses that exact ID. Kernel separately binds the ID to the canonical capability and exact planned arguments, so changing an operation while reusing a step identity fails closed as an idempotency/approval conflict.

The runtime never automatically retries a failed mutation with a fresh request ID.

## Budgets and cancellation

Before execution, the foundation preflights the complete plan against `max_steps` and `max_mutations`. A plan that exceeds either limit executes nothing. Cancellation is checked before every capability invocation and stops before the next side effect.

Queued or approval-waiting runs become terminal immediately when cancelled. A cancellation arriving while a capability is already executing cannot pretend that side effect did not occur: the current result is recorded and the run stops before any later step.

Planning adds prompt/candidate/model-output bounds, but wall-clock, inference-token, monetary, and observation budgets are still future work. Those limits must remain enforcement controls rather than model suggestions.

## Approval behavior

If Kernel returns `approval_required`, the agent run stops immediately with `waiting_approval`. No later step executes. Resumption uses the same run ID, same step ID, same capability and arguments, the same deterministic Kernel request identity, and a 36-character Kernel approval ID. Kernel remains the authority that validates and claims that approval.

## Durable run state

Revision `0057_agent_runtime_foundation` introduces separate durable runtime tables rather than overloading legacy conversation history:

- `agent_runtime_runs` records the stable run ID, goal/plan/budget, state, cancellation, lease/recovery fields, authority provenance, and the trusted source channel/surface needed to reconstruct the same audience boundary during recovery.
- `agent_runtime_steps` records one durable logical step and its stable Kernel request ID.
- `agent_runtime_step_attempts` records immutable attempt history, including approval waits and later completion using the same request identity.

A durable run stores `scope_kind`, workspace or personal ownership, `authority_user_id`, `principal_id`, `source_channel`, and `source_surface`, but **does not store role or permission snapshots**. Those are not durable authority.

Initial durable creation is restricted to trusted Personal-private and full-Workspace web-style surfaces. Guest Workspace, MCP, plugin runtime, solution runtime, Discord and other delegated/external surfaces are rejected until the corresponding trusted delegation/install provenance can be stored and revalidated. This prevents recovery from silently widening a delegated client or external installation into ordinary user authority.

## Durable orchestration and current authority

`DurableAgentOrchestrator` is an internal orchestration component, not an HTTP endpoint or background service. It claims a run with a bounded lease and renews/verifies that lease around every Kernel step.

Before **every pending capability**, it reconstructs a fresh `ExecutionContext` from current Operly state using the stored user/workspace plus trusted source channel/surface. Current user activity, membership, role and permissions are therefore re-evaluated between steps. A role downgrade affects the next capability. Membership removal stops the run before the next capability. The reconstructed principal, scope and surface must still match the durable provenance.

Long-running capability execution is protected by an independent heartbeat that uses a separate database session from the execution session. Losing the heartbeat/lease fails closed before durable step completion is recorded. Kernel's stable request identity remains the at-most-once boundary for a mutating step; a recovering worker reuses the same logical step identity rather than inventing another write.

## Remaining runtime roadmap

The next implementation slices should continue without weakening the Kernel boundary:

1. build the new narrow Kernel-v3 inference substrate tracked in #318; do not revive the deliberately demolished pre-v3 `packages/model_runtime` wholesale;
2. connect `AgentPlannerModel` to that inference substrate with strict structured output plus inference-token, time, attempt, provider-failover, and monetary budgets;
3. add observation/replan loops with bounded observations and explicit loop budgets;
4. keep scoped working context and retrieved memory separate from authority;
5. add a real bounded worker service/loop while keeping the global runtime kill switch off in production;
6. expand adversarial evaluation for malicious tool outputs, runaway replanning, context poisoning, restart recovery, and model/provider failure;
7. run a limited canary behind the global kill switch before `ai_runtime_enabled` can become true.

## Legacy agent and model code

The historical `/api/agent` and business-brain paths are not the new runtime and must not be re-mounted as part of this work. They predate the current TrustedIngress/ExecutionContext/Kernel authority model. Conversation history or model-routing utilities may be migrated selectively, but execution authority must stay in the Kernel.

Likewise, old model-runtime branches and the removed `packages/model_runtime` tree are reference material only. Any useful provider-neutral contracts or qualification ideas must be re-evaluated and reimplemented against Kernel-v3 rather than merged back as a legacy runtime.