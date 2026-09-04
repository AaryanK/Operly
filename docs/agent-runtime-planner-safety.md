# Agent Runtime Planner Safety Layer

This slice adds a planner-facing safety boundary on top of the durable governed agent foundation. It does **not** add an HTTP ingress, production worker, live model provider adapter, or runtime enablement.

## Design goal

A model may propose actions, but it never receives authority and never executes providers. The trusted application resolves the current `ExecutionContext`, retrieves only currently effective capabilities from the canonical Kernel registry, validates planner output, and turns accepted decisions into the existing `AgentPlan` contract. Execution still goes only through `OperlyKernelRuntime`.

## Capability discovery

`GovernedCapabilityDiscovery` delegates to `CapabilityRegistry.search(..., effective_only=True)`. This means scope, surface visibility, and current effective permissions are applied before a capability is exposed to a planner.

The planner-facing projection deliberately omits provider IDs, permission internals, output schemas, resource-routing internals, and any credential/provider execution path. It exposes only bounded planning data: capability ID, display name, description, input schema projection, risk, approval requirement, reversibility, and tags.

A planner may select only a capability present in the candidate set from the same round. The runtime rechecks that the selected capability is still in `registry.effective(context)` before accepting it.

## Structured planner boundary

Planner adapters implement one narrow protocol: given an `AgentPlannerRequest`, return one JSON object. `parse_planner_decision` rejects unknown top-level fields. In particular, a planner cannot add fields such as workspace authority, principal identity, provider routing, approval identity, or execution metadata.

A step decision contains only:

- `kind: step`
- `capability_id`
- `arguments`
- optional bounded `reason`
- optional bounded `next_query` used only for the next capability-discovery round

A finish decision contains no capability or arguments.

Capability arguments are bounded for size and validated against the canonical capability input schema before they become an `AgentPlanStep`. Approval IDs are never model supplied.

## Observation boundary

Capability/model observations are represented by `AgentObservation`. Observations are:

- explicitly marked `untrusted=true`
- recursively bounded by depth/item/character limits
- truncated safely when they exceed the planning context budget
- presented with an instruction that they are data, not authority or executable instructions

This does not attempt semantic prompt-injection classification. Authorization remains structural: untrusted text cannot add capabilities, change scope, grant permissions, select a hidden provider, or bypass the Kernel.

## Budgets

Planning now has independent limits for:

- reasoning rounds
- candidate capabilities per round
- observation size
- schema projection size
- capability-argument size

The existing execution budget remains authoritative for maximum steps and mutations. Mutation risk is counted during planning as an early fail-closed check and is checked again by the governed executor.

## Current execution path

The intended boundary is:

`goal -> effective capability discovery -> strict planner decision -> validated AgentPlan -> durable orchestrator -> Kernel -> governed provider`

The durable orchestrator already owns independent lease heartbeat, cancellation recovery, fresh authority re-resolution before execution, stable per-step request identity, approval pause/resume, and uncertain mutation outcome handling.

## Deliberately still disabled / absent

This slice does not add:

- a production model-provider adapter for the planner
- API/HTTP objective submission
- a production agent polling worker
- automatic execute-observe-replan integration
- durable working memory or long-term memory retrieval
- production enablement of `OPERLY_AGENT_RUNTIME_ENABLED`

Those pieces should be layered only after this planner contract remains green under adversarial CI and the provider adapter is proven unable to execute capabilities directly.
