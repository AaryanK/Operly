# Unified Agent Runtime

Operly is the business operating system: the agent and the interface are two views of the same governed runtime. Chat, Studio, workflows, generated software, Discord, MCP, scheduled work, and future surfaces must not create parallel authority or execution systems.

## Root invariant

Every human or delegated run starts from one application-resolved `ExecutionContext`:

```text
authenticated user
    -> selected workspace/scope
    -> current membership
    -> effective permissions
    -> surface/channel
    -> AgentRuntime
    -> governed capabilities
```

The application chooses identity, workspace, scope, permissions, surface, and delegation. Models never choose, widen, or switch them.

Every capability invocation still crosses the canonical capability firewall and action lifecycle. Data, connectors, generated source, browser observations, documents, messages, and model output are context/evidence, not authority.

## Business OS context fabric

The agent should be able to reach everything the current principal is legitimately allowed to use in Operly, including workspace context, files, CRM/business data, workflows, solutions, software projects, connectors, events, messages, web/browser capabilities, and delegated agents.

Reachability is always the intersection of:

```text
registered capability
AND configured/healthy integration
AND allowed surface
AND current user's effective permissions
AND delegated capability allowlist (when delegated)
```

Something existing in the workspace does not make it executable. Something being mentioned by a model does not grant authority.

## Delegation

Workflows, software projects, production runtimes, and child agents inherit an already-authorized workspace context and may only narrow it:

```text
child authority
    subset of parent delegated authority
    subset of authenticated user's workspace authority
```

A child agent is never a new user and never gets a separate workspace-selection mechanism. Delegated principals remain accountable to the human/workspace that created them and are re-authorized at execution time.

## Runtime strategies

`AgentRuntime` remains the canonical reason/act/observe substrate. Long-horizon orchestration sits above it and may choose specialized execution strategies without creating separate security fabrics.

- General business work: progressive capability discovery, governed actions, approvals, context retrieval, and bounded replanning.
- Workflows: durable graph/event/wake/wait semantics plus the same capability firewall and delegated authority.
- Software projects: canonical `SoftwareProject` lifecycle with requirements, coding, isolated build/test/start/health/acceptance, bounded repair, immutable source versions, and Operly-managed preview/hosting.

The strategies may differ in execution mechanics; authority does not.

## Software completion contract

A generated software project is not complete because a model wrote files or because a process started. Completion requires verified evidence for the requested behavior and Operly runtime compatibility.

At minimum, a hosted web application must prove:

```text
canonical source exists
build succeeds
server starts in the isolated runner
runner-owned host/port contract is honored
health endpoint succeeds
browser entry/preview succeeds
mandatory user behavior has executable acceptance evidence
```

If one criterion fails, repair should target the failed criterion while preserving passing behavior. The model must not substitute simulations, comments, placeholders, mocks, or cosmetic UI for mandatory behavior.

Generated software may use workspace capabilities only through governed bindings/gateways; provider credentials are never embedded in generated source.

## Capability discovery and recovery

Progressive exposure is a context-efficiency mechanism, not an authority mechanism. A denied/unexposed/invalid capability attempt is not proof that execution happened and must not suppress capability rescue. Discovery metadata is not execution evidence.

When a non-trivial run is about to terminate without real operational evidence, the controller may perform one bounded governed search/describe rescue. The rescue can reveal authorized schemas; it cannot grant authority or execute the business operation itself.

## Durable truth

Operly should report lifecycle truth rather than model confidence:

- `WAITING_APPROVAL`: action is gated and not complete.
- `PENDING_EVIDENCE`: durable external work is still running; do not replan or claim completion.
- `VERIFIED`: provider/runtime evidence confirms completion.
- `FAILED` / `UNVERIFIED`: do not present success; preserve evidence for targeted repair or resume.

Sandboxes are disposable compute. Backend canonical state, source versions, action history, workflow state, permissions, and workspace ownership remain durable authority.
