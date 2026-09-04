# Unified Agent Runtime

The core security invariant is unchanged: every human, workflow, generated solution, Studio surface, MCP client, and delegated agent uses the same governed capability fabric. Generated code never receives provider credentials, and temporary sandbox execution never becomes durable authority or storage.

## Runtime boundary

The new agent runtime is an orchestration layer above the Operly Kernel, not a second execution engine. An agent may decide *which* capability to request and *with what arguments*, but it never supplies its own workspace role, permissions, principal, provider credentials, approval identity, request identity, or durable authority.

Every executable step is converted into a Kernel `RuntimeRequest`. Kernel then re-resolves the canonical capability, validates its schema, evaluates current `ExecutionContext` authority, applies approval policy, reserves mutating idempotency, executes the provider, validates output, emits events, and records audit trace.

`AgentRuntimeSettings` still defaults to disabled. The only environment switch recognized by this runtime is `OPERLY_AGENT_RUNTIME_ENABLED=1`; legacy agent flags do not enable it. No new API route or production agent worker is introduced by the planner slice, and production enablement remains a separate gate.

## Plan and retry identity

An `AgentPlan` contains a stable `run_id`, a user-visible goal, bounded steps, and an explicit budget. Every step has a unique `step_id`, canonical capability ID, arguments, and optionally the Kernel approval UUID used to resume that exact step.

`stable_step_request_id(run_id, step_id)` produces one bounded deterministic request identity for the logical step. Approval resume or crash recovery reuses that exact ID. Kernel separately binds the ID to the canonical capability and exact planned arguments, so changing an operation while reusing a step identity fails closed as an idempotency/approval conflict.

The runtime never automatically retries a failed mutation with a fresh request ID.

## Budgets and cancellation

Before execution, the foundation preflights the complete plan against `max_steps` and `max_mutations`. A plan that exceeds either limit executes nothing. Cancellation is checked before every capability invocation and stops before the next side effect.

Queued or approval-waiting runs become terminal immediately when cancelled. A cancellation arriving while a capability is already executing cannot pretend that side effect did not occur: the current result is recorded and the run stops before any later step.

The planner adds separate hard limits for capability candidates, steps per planning turn, replans, model-output bytes, argument bytes, capability-descriptor bytes, and observation size/depth. These values are runtime policy, not model suggestions. Wall-clock, inference-token, and monetary budgets remain future enforcement work.

## Approval behavior

If Kernel returns `approval_required`, the agent run stops immediately with `waiting_approval`. No later step executes. Resumption uses the same run ID, same step ID, same capability and arguments, the same deterministic Kernel request identity, and a 36-character Kernel approval ID. Kernel remains the authority that validates and claims that approval.

The planner is never allowed to emit an approval ID or request ID. Those fields are rejected before an `AgentPlan` can be accepted.

## Durable run state

Revision `0057_agent_runtime_foundation` introduces separate durable runtime tables rather than overloading legacy conversation history:

- `agent_runtime_runs` records the stable run ID, goal/plan/budget, state, cancellation, lease/recovery fields, authority provenance, and the trusted source channel/surface needed to reconstruct the same audience boundary during recovery.
- `agent_runtime_steps` records one durable logical step and its stable Kernel request ID.
- `agent_runtime_step_attempts` records immutable attempt history, including approval waits and later completion using the same request identity.

A durable run stores `scope_kind`, workspace or personal ownership, `authority_user_id`, `principal_id`, `source_channel`, and `source_surface`, but **does not store role or permission snapshots**. Those are not durable authority.

Initial durable creation is restricted to trusted Personal-private and full-Workspace web-style surfaces. Guest Workspace, MCP, plugin runtime, solution runtime, Discord and other delegated/external surfaces are rejected until the corresponding trusted delegation/install provenance can be stored and revalidated. This prevents recovery from silently widening a delegated client or external installation into ordinary user authority.

## Durable orchestration and current authority

`DurableAgentOrchestrator` is an internal orchestration component, not an HTTP endpoint. It claims a run with a bounded lease and renews/verifies that lease around every Kernel step.

Before **every pending capability**, it reconstructs a fresh `ExecutionContext` from current Operly state using the stored user/workspace plus trusted source channel/surface. Current user activity, membership, role and permissions are therefore re-evaluated between steps. A role downgrade affects the next capability. Membership removal stops the run before the next capability. The reconstructed principal, scope and surface must still match the durable provenance.

Long-running capability execution is protected by an independent heartbeat using a separate database session. If heartbeat renewal or the explicit post-step ownership check fails, the orchestrator stops before recording durable completion. Kernel's stable request identity remains the at-most-once boundary for a mutating step; a recovering worker reuses the same logical step identity rather than inventing another write.

## Governed model planning

`GovernedAgentPlanner` is a provider-neutral planning boundary. It receives an injected `AgentPlannerModel`; concrete model/provider routing belongs below that interface. The planner itself has no provider registry and no capability execution method.

Capability discovery reuses the Kernel `CapabilityRegistry.search(..., effective_only=True)` path with the current `ExecutionContext` and a small candidate limit. The model therefore receives only capabilities that are currently visible on the trusted surface and currently permitted for that principal. It does **not** receive the full registry. Model-visible descriptors omit provider identity and permission/authority metadata.

A model may return only:

- `done`, with a bounded textual summary and no executable steps; or
- bounded steps containing exactly a freshly offered capability ID plus JSON arguments.

Step IDs are generated by Operly, not by the model. Provider IDs, request IDs, approval IDs, model-authored step IDs, and other unsupported fields are rejected. Proposed arguments are validated against the canonical Kernel capability input schema before the plan is accepted. Mutation and remaining-step budgets are recomputed by Operly and cannot be reset by model output.

## Observations and replanning

Capability results supplied to a future planning turn are explicitly labeled `untrusted_capability_output`. Their strings, object keys, list lengths, nesting depth, and total result size are bounded before they cross the model boundary. Oversized results collapse to size plus digest instead of becoming gigantic prompts.

This is not a claim that text sanitization can make arbitrary tool output trustworthy. The real safety boundary is structural: observations are data, never authority; every replan re-runs effective-only capability discovery using the **current** `ExecutionContext`; the model may select only the freshly offered set; and Kernel still reauthorizes every actual execution. A malicious observation that asks the model to call a privileged capability cannot make that capability available.

Replanning is itself capped. Consumed step and mutation counts are passed in as runtime-owned counters, remaining budgets only decrease, and the model cannot override them.

The current planner slice validates and produces plans, but does not yet connect model planning/replanning to the durable worker loop. That integration must preserve stable durable step identity, approval resume behavior, cancellation, lease ownership, and uncertain-mutation handling already enforced by the foundation.

## Remaining runtime roadmap

The next implementation slices should continue without weakening the Kernel boundary:

1. connect the provider-neutral planner contract to the actual shared model-runtime adapter once that runtime package is present/verified in the active tree;
2. integrate initial planning plus observation-driven replanning into durable orchestration while preserving immutable attempt history and stable mutation identity;
3. add explicit inference-token, wall-clock, and monetary budgets;
4. add scoped working context and retrieved memory as data, never as authority;
5. add a bounded production worker loop/canary path behind the global kill switch, with no public ingress until recovery behavior is exercised under failure;
6. expand adversarial evaluation for prompt/tool injection, malicious observations, capability-catalog poisoning, stale authority, runaway replans, provider failures, token exhaustion, restart recovery, and cross-scope access;
7. only after those gates, add carefully scoped ingress and consider production enablement.

## Legacy agent code

The historical `/api/agent` and business-brain paths are not the new runtime and must not be re-mounted as part of this work. They predate the current TrustedIngress/ExecutionContext/Kernel authority model. Conversation history or model-routing utilities may be migrated selectively, but execution authority must stay in the Kernel.
