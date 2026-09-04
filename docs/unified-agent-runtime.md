# Unified Agent Runtime

The core security invariant is unchanged: every human, workflow, generated solution, Studio surface, MCP client, and delegated agent uses the same governed capability fabric. Generated code never receives provider credentials, and temporary sandbox execution never becomes durable authority or storage.

## Runtime boundary

The new agent runtime is an orchestration layer above the Operly Kernel, not a second execution engine. An agent may decide *which* capability to request and *with what arguments*, but it never supplies its own workspace role, permissions, principal, provider credentials, or durable authority.

Every executable step is converted into a Kernel `RuntimeRequest`. Kernel then re-resolves the canonical capability, validates its schema, evaluates current `ExecutionContext` authority, applies approval policy, reserves mutating idempotency, executes the provider, validates output, emits events, and records audit trace.

The initial implementation is deliberately non-model and non-routable. `GovernedAgentRuntime` executes only an already-built `AgentPlan`, and `AgentRuntimeSettings` defaults to disabled. The only environment switch recognized by this runtime is `OPERLY_AGENT_RUNTIME_ENABLED=1`; legacy agent flags do not enable it. The API continues to report `ai_runtime_enabled: false` until a later canary explicitly wires the runtime into a trusted ingress.

## Plan and retry identity

An `AgentPlan` contains a stable `run_id`, a user-visible goal, bounded steps, and an explicit budget. Every step has a unique `step_id`, canonical capability ID, arguments, and optionally the approval ID used to resume that exact step.

`stable_step_request_id(run_id, step_id)` produces one bounded deterministic request identity for the logical step. Approval resume or transport retry reuses that exact ID. Kernel separately binds the ID to the canonical capability and exact planned arguments, so changing an operation while reusing a step identity fails closed as an idempotency/approval conflict.

The runtime never automatically retries a failed mutation with a fresh request ID.

## Budgets and cancellation

Before execution, the foundation preflights the complete plan against `max_steps` and `max_mutations`. A plan that exceeds either limit executes nothing. Cancellation is checked before every capability invocation and stops before the next side effect.

Future slices may add wall-clock, inference-token, monetary, and observation budgets, but these limits must remain enforcement controls rather than model suggestions.

## Approval behavior

If Kernel returns `approval_required`, the agent run stops immediately with `waiting_approval`. No later step executes. Resumption must use the same run ID, same step ID, same capability and arguments, and the approved invocation ID. Kernel remains the authority that validates and claims the approval.

## Durable run state

Revision `0057_agent_runtime_foundation` introduces separate durable runtime tables rather than overloading legacy conversation history:

- `agent_runtime_runs` records the stable run ID, goal/plan/budget, state, cancellation, lease/recovery fields and authority provenance.
- `agent_runtime_steps` records one durable logical step and its stable Kernel request ID.
- `agent_runtime_step_attempts` records immutable attempt history, including approval waits and later completion using the same request identity.

A durable run stores `scope_kind`, workspace or personal ownership, `authority_user_id`, and `principal_id`, but **does not store role or permission snapshots**. Those are not durable authority. A future worker must reconstruct a fresh `ExecutionContext` from current application state before every execution boundary.

Run lookup is scope + principal bound. Personal runs cannot acquire a workspace ID. Workspace runs require a workspace ID. The database has a check constraint enforcing this ownership split.

Worker claims use a bounded lease. One unexpired lease prevents another worker from claiming the run; an expired lease can be reclaimed for restart recovery. Cancellation is stored durably and blocks new claims.

## Remaining runtime roadmap

The next implementation slices should continue without weakening the Kernel boundary:

1. connect the durable store to a worker/orchestrator that re-resolves current authority and persists each executor result transactionally;
2. add authorization-aware capability discovery for a planner;
3. add a model planner that emits bounded plans but has no provider execution access;
4. add observation/replan loops with explicit model/token/time budgets;
5. keep scoped working context and retrieved memory separate from authority;
6. add adversarial evaluation for approval bypass, duplicate mutation, cross-scope access, prompt/tool injection, malicious tool outputs, runaway loops, restart recovery, and provider failure;
7. run a limited canary behind the global kill switch before `ai_runtime_enabled` can become true.

## Legacy agent code

The historical `/api/agent` and business-brain paths are not the new runtime and must not be re-mounted as part of this work. They predate the current TrustedIngress/ExecutionContext/Kernel authority model. Conversation history or model-routing utilities may be migrated selectively, but execution authority must stay in the Kernel.
