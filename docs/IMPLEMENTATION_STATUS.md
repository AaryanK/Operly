# Universal Studio Runtime implementation status

This branch implements the target architecture in dependency order while keeping the authorization redesign deferred.

## Implemented in the current pass

- `Model.infer()` provider-neutral contracts (`InferenceRequest`, `InferenceResult`, budgets, selectors, traits).
- `ModelRegistry`, configured `Model` objects, tag/capability selection, `ModelPool`, cross-provider failover, normalized attempt telemetry hooks.
- Coding harness and business-agent model boundaries migrated away from provider routes; Studio latency policy is provider-neutral.
- Capability placement and software planning migrated away from direct provider clients.
- Model-as-tool delegation invokes selected `Model` resources through `infer()`.
- Universal capability metadata expanded with plugin ownership, tags, semantic operations and discovery descriptors.
- `capability.search` / `capability.describe` kernel provider plus progressive `SessionCapabilityView`.
- Progressive capability exposure is active in `PluginAgentHarness`: sessions begin with the discovery/model kernel and a small general observation set, then exact schemas become visible after `capability.describe`.
- The business-agent prompt explicitly teaches discovery-before-use and no longer treats an incomplete initial tool list as proof that a capability is unavailable.
- Canonical `CapabilityFirewall` seam backed by existing `ActionService` permissions/approval lifecycle; authorization semantics intentionally unchanged.
- Agent capability invocation routed through the firewall seam.
- Canonical plugin manifest and `PluginRuntime` lifecycle/contribution registry.
- Canonical `SoftwareProject`, `SourceVersion`, `StudioSession` contracts plus compatibility adapters/service for Studio, ManagedApplication and GeneratedProject records.
- `RuntimePlugin` / `RuntimeRegistry` contracts. Existing `static-web-js` and `python-stdlib-web` execution profiles participate as runtime plugins.
- Coding runtime resolution and build-submission construction flow through runtime plugins while preserving current isolated-runner behavior.
- `ServiceBinding` contracts, semantic binding resolver, and project-scoped `CapabilityGateway` route through the firewall without exposing provider credentials.
- Architecture invariant tests and CI path coverage; architecture branches are included in the coding-harness smoke push trigger during this migration.

## Intentionally not changed yet

- Authorization policy/approval UX beyond the new firewall seam. This is reserved for the requested follow-up authorization design.
- Full replacement of legacy persisted project tables with canonical `SoftwareProject` persistence.
- Additional dependency-bearing runtimes such as Node package apps, React, Next.js, FastAPI, workers and multi-service projects. The registry is ready for these, but dependency/network/sandbox policies should be added deliberately.
- Persistent `ServiceBinding` database records and generated-runtime gateway HTTP transport.
- Connector lifecycle migration (Discord/Google) into `PluginRuntime`.
- Removal of compatibility `OllamaClient`/`ModelRoute`, legacy SiteSchema, ApplicationBuilder orchestration, or `packages/harness` until parity tests pass.

## Next safe implementation slices

1. Add persistent canonical `SoftwareProject` identity/source mapping without removing legacy records.
2. Add persistent `ServiceBindingRecord` and a scoped runtime gateway route.
3. Add the first dependency-bearing runtime plugin with explicit registry/network policy.
4. Migrate connector lifecycle registration into `PluginRuntime`.
5. Perform the separate authorization design/pass before broadening generated-runtime authority or high-risk bindings.
6. Retire compatibility layers only after end-to-end parity tests prove the canonical paths.
