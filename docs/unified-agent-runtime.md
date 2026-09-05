# Unified Agent Runtime

The core security invariant is unchanged: every human, workflow, generated solution, Studio surface, MCP client, and delegated agent uses the same governed capability fabric. Generated code never receives provider credentials, and temporary sandbox execution never becomes durable authority or storage.

## Runtime boundary

The new agent runtime is an orchestration layer above the Operly Kernel, not a second execution engine. An agent may decide *which* capability to request and *with what arguments*, but it never supplies its own workspace role, permissions, principal, provider credentials, or durable authority.

Every executable step is converted into a Kernel `RuntimeRequest`. Kernel then re-resolves the canonical capability, validates its schema, evaluates current `ExecutionContext` authority, applies approval policy, reserves mutating idempotency, executes the provider, validates output, emits events, and records audit trace.

`AgentRuntimeSettings` still defaults to disabled. The only environment switch recognized by this runtime is `OPERLY_AGENT_RUNTIME_ENABLED=1`; legacy agent flags do not enable it. No API route or production worker currently sets that switch, and the API continues to report `ai_runtime_enabled: false`.

## Runtime 1.0 front door

Runtime 1.0 starts by interpreting the human objective before exposing capabilities. `ObjectiveInterpreter` is a model-powered semantic boundary that classifies requests into a small finite representation rather than trying to enumerate every possible domain intent.

`ObjectiveIR` records a concise objective, a request kind, finite operations (`respond`, `retrieve`, `analyze`, `transform`, `act`, `wait`), semantic resource hints, whether external state/mutation/future waiting is required, and a bounded complexity class. From that representation the runtime derives four execution paths:

- `respond`: no capability discovery. Reason, explain, converse, or transform supplied content directly.
- `direct_capability`: one small external read or mutation can be satisfied through the governed capability layer.
- `agent_loop`: compound/open-ended work needs repeated reasoning, capability composition, observation and replanning.
- `wait`: completion depends on a future event or condition.

**No tool is a first-class successful outcome.** Runtime 1.0 does not teach the model that being an agent means calling a tool. Capability discovery begins only when the Objective IR says external state is genuinely required.

The interpreter receives trusted scope and surface *labels* so it can understand whether it is operating in a Personal or Workspace conversation, but it does not receive workspace IDs, user IDs, membership IDs, principals, roles, permissions, approvals, provider routes, or credentials. Model output cannot supply those fields. Meaning is model-derived; authority remains trusted runtime state.

The current Objective IR is a front-door contract, not durable authority and not an executable plan. Kernel still owns every authorization decision.

## Context engineering

Context is treated as a budgeted runtime resource, not a global prompt that is automatically forwarded everywhere.

`ContextAssembler` accepts candidate conversation fragments, memories, observations, artifacts, or user-provided content and produces a `ContextSlice` for one inference phase. Selection is relevance/priority/query-aware and is bounded by item count, per-item bytes, and total bytes. Oversized or irrelevant candidates are omitted instead of forwarding the entire candidate set.

Context does **not** implicitly inherit across runtime phases. The objective interpreter, planner, observation/replan loop, and final response should each request the smallest context slice required for their own decision. A tool/capability step must not receive the entire system conversation merely because earlier reasoning had access to it.

Internal context keys are retained server-side but omitted from model-facing prompt items. The Objective IR creates a compact capability-discovery query from the concise objective, external operations, and semantic resource hints; raw user history, memory payloads, and observation dumps do not automatically flow into capability discovery.

This is a foundation rather than the final retrieval system. Future memory and observation retrieval should add semantic/embedding or task-aware relevance upstream, while `ContextAssembler` remains the final hard byte/item gate before inference.

## One runtime, Studio-authored capabilities

Operly should keep one Agent Runtime for Personal AI, Workspaces, Studio-generated solutions, MCP/delegated clients, and future surfaces. Studio should not create a second agent engine.

Instead, Studio is the authoring/install surface for custom capabilities and capability packs. A Workspace-specific plugin can define narrowly scoped schemas, permissions, risk/approval requirements and provider bindings, then register through the same canonical Kernel capability fabric. The runtime discovers only capabilities that are currently effective for the trusted Personal/Workspace execution context.

This keeps reasoning, context engineering, loop control, budgets, memory boundaries, approvals and recovery semantics consistent everywhere while still allowing each Workspace to have custom tools. Customization happens in the **capability set and context**, not by forking the runtime.

## Authorization-aware planning

`GovernedAgentPlanner` adds a model-planning boundary without adding another execution path. It receives the canonical `CapabilityRegistry` selected by the caller and retrieves a small candidate set with `effective_only=True`, so scope, surface visibility, and current `ExecutionContext` permissions are applied before a capability can be shown to a planner. Production composition must pass the registry owned by the Kernel runtime that will execute the resulting plan; Kernel still independently re-resolves and re-authorizes every requested capability.

The planner never receives the full registry by default. Candidate count, description length, per-capability bytes, total prompt bytes, model-output bytes, plan steps, and mutations are bounded. Oversized capability contracts are omitted rather than truncated into a misleading schema; if no safe authorized candidate remains, planning fails closed.

Planner-facing capability cards contain only the capability ID, display name, bounded description, input schema, risk, and approval requirement. Provider IDs, permission lists, scope internals, credentials, and principals are not part of the planner contract. Capability descriptions and schemas are explicitly treated as untrusted data, not instructions.

The provider-neutral `AgentPlannerModel` interface returns only structured planning data. This slice deliberately does not give that interface a capability execution method or provider registry. A concrete model transport can be attached later behind a new Kernel-v3 inference boundary without changing authority or execution semantics.

The pre-Kernel-v3 `packages/model_runtime` implementation was intentionally demolished during the runtime rebuild. It must not be restored wholesale merely to satisfy the planner interface. The new inference boundary is tracked separately in #318 and must be designed for the current Kernel-v3 authority model.

The *planner* output has exactly one allowed top-level field, `steps`. Each step has exactly `capability_id` and `arguments`. Model-supplied step IDs, approvals, permissions, principals, workspace IDs, scopes, or other authority-shaped fields are rejected. Operly assigns durable `step-001`, `step-002`, ... identities server-side. The planner is entered only on an external-capability path; the Runtime 1.0 front door can instead choose the no-tool `respond` path.

A selected capability must be in the exact retrieved candidate set. Arguments are validated against the canonical capability input schema before an `AgentPlan` is created. Mutation and step budgets are checked while constructing the plan and are checked again by the executor before any capability executes. Kernel independently re-authorizes every step at execution time, so planner visibility is never durable authority.

Malformed JSON, markdown-wrapped output, non-JSON data, oversized output, unsupported fields, invalid arguments, out-of-set capability selection, or budget violations all fail closed before Kernel is called.

## Plan and retry identity

An `AgentPlan` contains a stable `run_id`, a user-visible goal, bounded steps, and an explicit budget. Every step has a unique `step_id`, canonical capability ID, arguments, and optionally the Kernel approval UUID used to resume that exact step.

`stable_step_request_id(run_id, step_id)` produces one bounded deterministic request identity for the logical step. Approval resume or crash recovery reuses that exact ID. Kernel separately binds the ID to the canonical capability and exact planned arguments, so changing an operation while reusing a step identity fails closed as an idempotency/approval conflict.

The runtime never automatically retries a failed mutation with a fresh request ID.

## Budgets and cancellation

Before execution, the foundation preflights the complete plan against `max_steps` and `max_mutations`. A plan that exceeds either limit executes nothing. Cancellation is checked before every capability invocation and stops before the next side effect.

Queued or approval-waiting runs become terminal immediately when cancelled. A cancellation arriving while a capability is already executing cannot pretend that side effect did not occur: the current result is recorded and the run stops before any later step.

Planning adds prompt/candidate/model-output bounds. Objective interpretation now also has request/output/context bounds. Wall-clock, inference-token, monetary, and observation budgets are still future work. Those limits must remain enforcement controls rather than model suggestions.

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

## Runtime 1.0 evaluation

The dedicated runtime test suite includes a structural reference scorecard for the front-door paths: general/no-tool response, external retrieval, direct mutation, compound agent work, and future wait. It also adversarially verifies that unrelated/oversized context is omitted, raw context does not automatically flow into capability discovery, internal context keys/authority metadata are not exposed, authority-shaped model output fails closed, inconsistent objective flags fail closed, and malformed/oversized model output is rejected.

This scorecard measures runtime contracts and routing invariants with deterministic fake model outputs. It is **not yet a semantic accuracy score for Qwen/DeepSeek/Llama or another real model**. Real-model classification/tool-composition scoring belongs after the Kernel-v3 inference adapter in #318 exists.

## Remaining runtime roadmap

The next implementation slices should continue without weakening the Kernel boundary:

1. build the new narrow Kernel-v3 inference substrate tracked in #318; do not revive the deliberately demolished pre-v3 `packages/model_runtime` wholesale;
2. connect both `ObjectiveInterpreterModel` and `AgentPlannerModel` to that inference substrate with strict structured output plus inference-token, time, attempt, provider-failover, and monetary budgets;
3. compose the Runtime 1.0 dispatch paths so no-tool responses, direct capabilities, durable agent loops and waits share one entry point;
4. add observation/replan loops where every iteration receives its own bounded context slice rather than inheriting full prior context;
5. add scoped working context and memory retrieval/compression while keeping memory separate from authority;
6. add a real bounded worker service/loop while keeping the global runtime kill switch off in production;
7. run real open-model evaluation for objective classification, no-tool restraint, capability selection, multi-step recovery, context efficiency and adversarial tool-output resistance;
8. run a limited canary behind the global kill switch before `ai_runtime_enabled` can become true.

## Legacy agent and model code

The historical `/api/agent` and business-brain paths are not the new runtime and must not be re-mounted as part of this work. They predate the current TrustedIngress/ExecutionContext/Kernel authority model. Conversation history or model-routing utilities may be migrated selectively, but execution authority must stay in the Kernel.

Likewise, old model-runtime branches and the removed `packages/model_runtime` tree are reference material only. Any useful provider-neutral contracts or qualification ideas must be re-evaluated and reimplemented against Kernel-v3 rather than merged back as a legacy runtime.
