# Unified Agent Runtime

The core security invariant is unchanged: every human, workflow, generated solution,
Studio surface, MCP client, and delegated agent uses the same governed capability
fabric. Generated code never receives provider credentials, and temporary sandbox
execution never becomes durable authority or storage.

## Runtime boundary

The Agent Runtime is an orchestration layer above the Operly Kernel, not a second
execution engine. An agent may decide *which* capability to request and *with what
arguments*, but it never supplies its own workspace role, permissions, principal,
provider credentials, approval authority, or durable execution identity.

Every executable step becomes a Kernel `RuntimeRequest`. Kernel re-resolves the
canonical capability, validates schema, evaluates the current `ExecutionContext`,
applies approval policy, reserves mutating idempotency, executes the provider,
validates output, emits semantic events, and records audit trace.

`AgentRuntimeSettings` defaults disabled in code and recognizes only
`OPERLY_AGENT_RUNTIME_ENABLED=1`. The current production deployment explicitly opts
Runtime 1.0 in for the mounted Personal/Workspace chat surfaces and Discord. That is a
deployment choice, not a weaker code default. There is still no separate production
durable-agent worker service.

## Runtime 1.0 front door

Runtime 1.0 interprets the human objective before exposing capabilities.
`ObjectiveInterpreter` converts arbitrary requests into a small finite `ObjectiveIR`:

- operations: `respond`, `retrieve`, `analyze`, `transform`, `act`, `wait`;
- semantic resource hints;
- external-state, mutation and future-wait requirements;
- bounded complexity.

The IR drives four paths:

- `respond`: reason or transform supplied content without capability discovery;
- `direct_capability`: satisfy one small external read or mutation through Kernel;
- `agent_loop`: repeatedly reason, discover, call, observe and decide;
- `wait`: represent work that depends on a future condition/event.

**No tool is a first-class successful outcome.** The runtime does not teach the model
that being an agent means forcing a tool call. Capability discovery begins only when
external state is genuinely required.

The objective model receives trusted scope/surface *labels* but not workspace IDs,
user IDs, membership IDs, principals, roles, permissions, approvals, provider routes,
or credentials. Meaning may be model-derived; authority never is.

## Kernel-v3 inference substrate

The pre-v3 `packages/model_runtime` tree was deliberately removed and is not the
current Agent Runtime. Runtime 1.0 now has a narrow provider-neutral inference
substrate in `packages/agent_runtime/inference.py`:

- `KernelV3AgentModel` is the model facade used by Runtime 1.0;
- `AgentInferenceRuntime` owns inference budgets and explicit failover policy;
- `OpenAICompatibleTransport` owns one fixed-destination provider request at a time;
- `OpenAICompatibleAgentModel` remains only as a temporary compatibility class name.

Supported destinations are hardcoded for Groq, OpenRouter, Gemini OpenAI
compatibility, NVIDIA NIM, and local Ollama. User/model data and environment base-URL
overrides cannot change these destinations. Redirects and inherited proxy settings
are disabled at the transport boundary.

Cross-provider fallback is opt-in with
`OPERLY_AGENT_MODEL_FALLBACK_PROVIDERS`. Only retryable transport/provider
availability failures may advance to another provider. Invalid requests, bad
credentials, and invalid response shapes fail closed rather than being sent to
additional vendors.

Each inference call has bounded request bytes, output bytes, output tokens,
per-attempt timeout, total deadline, per-route attempts, global attempts, and provider
routes. Optional estimated-dollar admission is also supported; when a finite dollar
budget is enabled, routes without explicit operator price metadata are ineligible.
Inference retries never create or alter Kernel capability request IDs.

`scripts/benchmark_models.py` is the current inference-only qualification harness for
text, structured JSON and planner-shaped output. Durable empirical route admission is
still unfinished work under #318; a model-family name is not itself a capability
claim.

## Context engineering

Context is a budgeted runtime resource, not a global prompt forwarded everywhere.
`ContextAssembler` turns candidate conversation fragments, memories, observations,
artifacts, or user content into one bounded `ContextSlice` for one inference phase.
Selection is relevance/priority/query-aware and bounded by item count, per-item bytes,
and total bytes.

Context does **not** implicitly inherit across runtime phases. Objective
interpretation, next-move reasoning, planning, observation/replan, and final response
should each receive the smallest slice required for that decision. Internal context
keys stay server-side. Raw history or observation dumps do not automatically flow into
capability discovery.

Future memory retrieval may add semantic/embedding or task-aware ranking upstream;
`ContextAssembler` remains the final hard byte/item gate before inference.

## One runtime, Studio-authored capabilities

Operly should keep one Agent Runtime for Personal AI, Workspaces, Studio-generated
solutions, MCP/delegated clients, and future surfaces. Studio is an authoring/install
surface for custom capabilities and capability packs, not another agent engine.

Workspace-specific behavior therefore comes from scoped capabilities and context.
Reasoning, budgets, memory boundaries, approvals and recovery semantics stay common.

## Authorization-aware planning

`GovernedAgentPlanner` retrieves a small candidate set from the canonical
`CapabilityRegistry` with `effective_only=True`. Scope, surface visibility, and
current `ExecutionContext` authority are therefore applied before a capability can be
shown to a planner. Kernel independently repeats authorization at execution time.

Planner cards contain only capability ID, display name, bounded description, input
schema, risk and approval requirement. Provider IDs, permission lists, credentials,
principals and scope internals are excluded. Capability descriptions and schemas are
untrusted data, not instructions.

Planner output has one top-level field, `steps`; each step has exactly
`capability_id` and `arguments`. Model-supplied step IDs, approvals, permissions,
principals, workspace IDs, scopes, provider routes, or other authority-shaped fields
are rejected. Operly assigns durable step identities server-side.

A selected capability must be in the exact retrieved candidate set. Arguments are
validated against the canonical input schema before an `AgentPlan` is accepted.
Mutation and step budgets are checked during planning and again before execution.

## Interactive execution

The currently mounted Personal and Workspace chat routes use `Runtime1Agent`. The
interactive loop:

1. interprets the objective;
2. selects a bounded reasoning context;
3. skips discovery entirely for no-tool work;
4. otherwise asks the current Kernel runtime for effective capabilities;
5. gives the model a bounded candidate set;
6. validates the model's next move and capability arguments;
7. executes only through `GovernedAgentRuntime -> Kernel`;
8. converts the result into a bounded observation for the next cycle.

Loop repetitions, discovery count, mutations, failures, observations and context are
all bounded. The same model/capability decision repeating more than the allowed count
terminates as a loop rather than running forever.

## Plan, retry and approval identity

`stable_step_request_id(run_id, step_id)` creates one deterministic Kernel request
identity for a logical durable step. Approval resume or crash recovery reuses that
same identity. Kernel separately binds it to the canonical capability and exact
arguments, so changing an operation while reusing the ID fails closed.

The runtime never automatically retries a failed mutation with a fresh request ID.

If Kernel returns `approval_required`, the agent stops immediately. Interactive
Runtime 1.0 reports the approval ID; durable execution enters `waiting_approval` and
executes no later step. Durable resume uses the same run ID, step ID, capability,
arguments, deterministic request ID, and Kernel approval ID.

The retired `/approvals/personal` router is not part of this architecture. A current
Personal human-control UI/API must bind to Kernel/Agent Runtime approval state rather
than remount the pre-Kernel `business_brain.runtime_v2_resume` path.

## Durable run state and current authority

Migration `0057_agent_runtime_foundation` introduces:

- `agent_runtime_runs` for plan/budget/state/cancellation/lease and trusted provenance;
- `agent_runtime_steps` for logical steps and stable Kernel request IDs;
- `agent_runtime_step_attempts` for immutable attempt history, approval waits and
  later completion.

Durable rows store trusted scope/provenance but do **not** store role or permission
snapshots as authority.

`DurableAgentOrchestrator` claims a run with a bounded lease. Before every pending
capability it reconstructs a fresh `ExecutionContext` from current Operly state, so
membership removal or role downgrade affects the next capability. A separate-session
heartbeat protects long capability execution; lease loss fails closed before durable
step completion is recorded.

Guest Workspace and delegated/external surfaces remain fail-closed for durable resume
until their trusted delegation/install provenance can be durably represented and
revalidated.

## Budgets and uncertainty

Execution budgets cap steps and mutations. Interactive reasoning also caps cycles,
discoveries, failures and observations. Inference now adds time/token/byte/attempt/
provider-route ceilings plus optional monetary admission.

Cancellation never pretends an in-flight external side effect did not happen. If a
mutation crosses the durable reservation boundary but completion cannot be proven, the
runtime enters `execution_uncertain`; cancellation or retry cannot hide the required
reconciliation.

## Evaluation

The Agent Runtime Foundation suite exercises no-tool behavior, objective validation,
candidate-set enforcement, authority-field rejection, context limits, planning limits,
approval identity, durable leases/races, uncertainty handling, fixed inference
destinations and inference failover/budget behavior.

The structural scorecard is not a semantic-quality score for a concrete open model.
The new qualification harness makes real-route testing possible, but #318 should stay
open until route qualification/admission evidence is durable and reviewed.

## Remaining runtime roadmap

Continue without weakening Kernel authority:

1. finish #318 with durable empirical route qualification/admission rather than
   trusting model-family metadata;
2. migrate remaining Runtime 1.0 callers to the `KernelV3AgentModel` name and remove
   the transport-shaped compatibility name after the cutover is complete;
3. connect the human-control frontend/API directly to canonical Kernel/Agent Runtime
   approval state, with exact run/step/request identity on resume;
4. make compound interactive work durably hand off to the durable orchestrator when
   restart/recovery semantics are required rather than keeping long work in one HTTP
   request;
5. add richer observation/replan policy while preserving per-phase context selection;
6. add scoped working context plus long-term memory retrieval/compression, keeping
   memory separate from authority;
7. add a bounded production worker loop for durable runs;
8. run real-model evaluations for no-tool restraint, capability selection,
   multi-step recovery, context efficiency and adversarial tool-output resistance;
9. canary new durable/approval paths behind deployment controls before broad enablement.

## Legacy code

Historical business-brain, Runtime v2, pre-Kernel approvals, and removed
`packages/model_runtime` branches are reference material only. Useful ideas must be
reimplemented against current `ExecutionContext`, Kernel idempotency, approval and
audit contracts; they must not be re-mounted as parallel execution authority.
