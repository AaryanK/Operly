# Universal Studio Runtime implementation status

This branch implements `docs/TARGET_ARCHITECTURE.md` in dependency order while deliberately keeping the authorization redesign as a separate follow-up.

## Implemented in this branch

### Model runtime

- Provider-neutral `Model.infer()` contract with `InferenceRequest`, `InferenceResult`, budgets, capabilities, tags and traits.
- `ModelRegistry`, `ModelSelector`, configured `Model` objects and `ModelPool`.
- Cross-provider model candidates/failover and normalized model-attempt telemetry hooks.
- Coding harness, Studio latency policy, business agent, capability placement and active software planning paths consume the shared model runtime instead of concrete provider clients.
- Studio no longer contains `OpenRouterClient`/provider-specific fallback mutation.
- Active live software planning now uses `ModelPlanningClient -> model_for_role(role) -> Model.infer()`; requirements analysis, planning and validation are no longer tied to Ollama transport.
- `model.invoke` remains a normal capability for bounded specialist delegation.

### Capability / agent runtime

- Universal capability metadata includes plugin ownership, schemas, risk, permissions, execution mode, tags and semantic operations.
- Semantic `capability.search` and exact `capability.describe` are real read-only capabilities.
- `SessionCapabilityView` gives each model session a small permanent discovery kernel and progressively exposes exact schemas after description.
- Discovery and authorization are separate states; search/describe can report a capability without granting execution authority.
- Progressive exposure is live in `PluginAgentHarness`, with discovery state preserved across connector/registry refreshes while current authority is rechecked each turn.
- `CapabilityFirewall` is the canonical invocation seam backed by the current `ActionService` approval/verification lifecycle.
- Generic `AgentRuntime` owns the provider-neutral model -> capability -> observation loop. The business agent now uses it directly.

### Plugin runtime

- Canonical `PluginManifest`, permission specs, lifecycle specs and `PluginRuntime` contribution registry.
- First-party capability providers are bootstrapped through `PluginRuntime` rather than a separate agent-only registry path.
- Runtime plugins and model provider/discoverer registrars can be contributed through plugin registration.
- FastAPI lifespan starts/stops the plugin runtime.
- Discord lifecycle is registered as a plugin lifecycle contribution rather than a vendor-specific application startup branch.

### Studio / software projects

- Canonical persisted `software_projects` and `service_bindings` tables plus migration `0030_universal_software_projects`.
- `SoftwareProject` contracts and `SoftwareProjectService` create canonical-only projects and synchronize stable compatibility identities for Studio, ManagedApplication and GeneratedProject records.
- Canonical project records track current source/runtime state from real Studio source versions and generated source bundles.
- A dedicated `/api/software-projects` facade exists for canonical project and binding management while legacy APIs remain compatible.
- AI-facing `StudioProvider` is source-first: `studio.generate_site` edits real source through `studio.source_agent` instead of generating a separate `SiteSchema` representation.
- Studio version listing includes source-first and legacy versions; publishing uses the unified production service.
- Canonical software project and binding operations are also normal discoverable capabilities (`software.project.*`, `software.binding.*`).

### Runtime / runner boundary

- `RuntimePlugin`, `RuntimePluginSpec` and `RuntimeRegistry` are real trusted execution contracts.
- Existing `static-web-js` and `python-stdlib-web` profiles participate through runtime plugins.
- Coding runtime detection/validation and build-submission construction use runtime plugins while preserving the existing isolated runner contract.
- Generated code still never executes inside the Operly control plane.

### Service bindings

- Durable project-scoped `ServiceBindingRecord` persistence.
- Semantic binding discovery through the workspace capability registry.
- Binding configuration rejects raw credential/token/password/API-key fields; credential aliases/references may point to secrets owned elsewhere.
- `CapabilityGateway` invokes a configured binding handle through the same `CapabilityFirewall` used by agents; generated code does not receive provider credentials.
- Binding configuration may restrict allowed argument fields and supply non-secret defaults.
- Creating a binding does not grant runtime authority and does not invoke the target capability.

### Tests / guardrails

- Architecture invariant tests cover model failover, tag-based selection, discovery-vs-authority, progressive exposure, firewall decisions, plugin ownership, runtime registration, source-first Studio, software-project persistence and service-binding secrecy.
- A planning-boundary test verifies the structured planner uses `Model.infer()` and that live `plan_service.py` contains no concrete provider transport dependency.
- The coding-harness smoke workflow includes architecture branches and the new architecture/persistence/planning tests.

## Compatibility bridges intentionally still present

These are allowed during migration but must not receive new architectural authority:

- `packages/custom_software/live_planning.py` still contains the old `OllamaPlanningClient` implementation for legacy imports/tests; the active `plan_service` path no longer uses it.
- `ModelRoute`, concrete OpenRouter/Ollama adapters and compatibility chat adapters remain inside/below `model_runtime` for existing callers.
- The coding agent still exposes the historical short tool vocabulary (`read`, `write`, `edit`, `finish`, etc.) through `CodingToolRegistry`. Its workspace/sandbox semantics are valuable, but this remains the main second-registry compatibility bridge to migrate into universal session capabilities.
- `SolutionService` and the Studio web UI still normalize/branch across Studio, managed-app and generated-project runtime generations while callers migrate to canonical `SoftwareProject` IDs.
- Legacy `SiteSchema`, ApplicationBuilder orchestration and `packages/harness` remain available for parity/rollback but are no longer the desired source of new architecture.

## Deliberately deferred / externally blocked

### Authorization redesign

Authorization/approval UX and principal/delegation semantics are intentionally unchanged beyond the stable `CapabilityFirewall` seam. The planned Discord-style authorization design must be specified in the next pass before broadening generated-runtime authority or high-risk service bindings.

### Additional full-stack runtimes

The control plane is ready for installable runtime plugins, but this repository does not contain the production external runner implementation that advertises `/v1/capabilities`. New Node/React/Next/FastAPI/worker profiles must be implemented and advertised by that isolated runner as well as registered here. This branch must not claim a runtime the runner cannot execute.

### Generated-runtime HTTP gateway

The `CapabilityGateway` service contract exists, but a production HTTP/runtime identity transport is intentionally not exposed yet. Doing that safely depends on the next authorization/principal design so generated applications cannot mint broader authority than their project binding grants.

## Next implementation slices

1. Design and implement the requested authorization/principal model behind `CapabilityFirewall`, including channel/runtime identities and approval behavior.
2. Migrate coding workspace/preview/runner tools from `CodingToolRegistry` into universal session-scoped capability providers while retaining current model tool aliases during compatibility.
3. Move Studio/solution UI and API orchestration from runtime-generation branching to canonical `SoftwareProject` as the primary project identity.
4. Pair the first dependency-bearing `RuntimePlugin` with real support in the production isolated runner, then add it end-to-end.
5. Add the generated-runtime gateway transport only after the authorization pass defines project/runtime principals and binding grants.
6. Retire the legacy planning client, SiteSchema-primary paths, ApplicationBuilder authority, `ModelRoute` compatibility and `packages/harness` only after parity tests pass.

## Current architectural checkpoint

A business can now have models, connectors and business operations registered as plugins; an agent starts with a bounded tool set and discovers additional capabilities semantically; model selection/failover can cross providers; Studio edits real source; canonical software projects persist independently of legacy runtime generation; and projects can persist semantic bindings to real workspace capabilities without storing provider credentials.

The remaining work is no longer a provider/model refactor. It is primarily the authorization pass, coding-tool capability convergence, Studio UI/project convergence, and adding real runner-supported full-stack runtimes.