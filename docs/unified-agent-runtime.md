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

## Durable execution roadmap

The next implementation slices should add durable run/step state and recovery without weakening the Kernel boundary:

1. database-backed `AgentRun` / `AgentStep` state with explicit state-transition rules;
2. durable cancellation and lease/recovery semantics for interrupted orchestration;
3. authorization-aware capability discovery for a planner;
4. a model planner that emits bounded plans but has no provider execution access;
5. observation/replan loops with explicit model/token/time budgets;
6. scoped working context and retrieved memory kept separate from authority;
7. adversarial evaluation for approval bypass, duplicate mutation, cross-scope access, prompt/tool injection, malicious tool outputs, runaway loops, restart recovery, and provider failure;
8. limited canary behind the global kill switch before `ai_runtime_enabled` can become true.

## Legacy agent code

The historical `/api/agent` and business-brain paths are not the new runtime and must not be re-mounted as part of this work. They predate the current TrustedIngress/ExecutionContext/Kernel authority model. Conversation history or model-routing utilities may be migrated selectively, but execution authority must stay in the Kernel.
