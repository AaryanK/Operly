# Universal Studio Runtime implementation status

This branch implements the target architecture in dependency order while keeping the authorization redesign deferred.

## Implemented in the current pass

- `Model.infer()` provider-neutral contracts (`InferenceRequest`, `InferenceResult`, budgets, selectors, traits).
- `ModelRegistry`, configured `Model` objects, tag/capability selection, `ModelPool`, cross-provider failover, normalized attempt telemetry hooks.
- Coding harness model boundary migrated away from provider routes; Studio latency policy is now provider-neutral.
- Capability placement and software planning migrated away from direct provider clients.
- Model-as-tool delegation now invokes selected `Model` resources through `infer()`.
- Universal capability metadata expanded with plugin ownership, tags, semantic operations and discovery descriptors.
- `capability.search` / `capability.describe` kernel provider plus progressive `SessionCapabilityView` contract.
- Canonical `CapabilityFirewall` seam backed by existing `ActionService` permissions/approval lifecycle; authorization semantics intentionally unchanged.
- Agent capability invocation routed through the firewall seam.
- Canonical plugin manifest and `PluginRuntime` lifecycle/contribution registry.
- Canonical `SoftwareProject`, `SourceVersion`, `StudioSession` contracts plus compatibility adapters/service for Studio, ManagedApplication and GeneratedProject records.
- `RuntimePlugin` / `RuntimeRegistry` contracts. Existing `static-web-js` and `python-stdlib-web` execution profiles now participate as runtime plugins.
- Coding runtime resolution and build-submission construction now flow through runtime plugins while preserving current isolated-runner behavior.
- `ServiceBinding` contracts, semantic binding resolver, and project-scoped `CapabilityGateway` that routes through the firewall without exposing provider credentials.
- Architecture invariant tests and CI path coverage.

## Intentionally not changed yet

- Authorization policy/approval UX beyond the new firewall seam. This is reserved for the requested follow-up authorization design.
- Full replacement of legacy persisted project tables with a new `SoftwareProject` table.
- Full progressive-capability exposure as the default business-agent mode; the session view exists but should be enabled after compatibility CI proves the discovery journey.
- Additional dependency-bearing runtimes such as Node package apps, React, Next.js, FastAPI, workers and multi-service projects. The registry is now ready for these, but their dependency/network/sandbox policies should be added deliberately.
- Persistent `ServiceBinding` database records and generated-runtime gateway HTTP transport.
- Connector lifecycle migration (Discord/Google) into `PluginRuntime`.
- Removal of compatibility `OllamaClient`/`ModelRoute`, legacy SiteSchema, ApplicationBuilder orchestration, or `packages/harness` until parity tests pass.

## Next safe implementation slices

1. Make progressive capability discovery the default agent view and update agent prompts/step budgets.
2. Add persistent `SoftwareProject` + source compatibility mapping without removing legacy records.
3. Add persistent `ServiceBindingRecord` and a scoped runtime gateway route.
4. Add the first dependency-bearing runtime plugin with explicit registry/network policy.
5. Migrate connector lifecycle registration.
6. Perform the separate authorization design/pass before broadening runtime authority.
