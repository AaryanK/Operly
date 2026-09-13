# Runtime Adapter Registry — 2026-09-13

## Scope and base

Started from fetched `origin/main` at `d7840a5f`. That revision did not include the preceding runtime-support change (`7e37b563`), so this branch carries it as prerequisite commit `77b86720`. The registry refactor is a separate subsequent commit. Compare against the prerequisite to review only the registry change; a PR against this main revision includes both.

Inspection found no equivalent registered implementation abstraction. `PluginRuntimeController` is a protocol for build/start/status/stop boundaries, not a registry of concrete production adapters. Previously, `_IMPLEMENTED` and compatibility maps determined support, while reconciliation and the sandbox provider independently selected transports by execution mode.

## Registry design

`packages/plugins/runtime_registry.py` defines an abstract `RuntimeAdapter` and a frozen `RuntimeAdapterRegistry` backed by a read-only mapping. Default registration contains exactly:

| Mode | Concrete adapter | Compatible profile / kind | Implementation ID |
| --- | --- | --- | --- |
| `remote_http` | `RemoteHttpRuntimeAdapter` | `remote-http` / `remote` | `operly.remote_http` |
| `sandbox_job` | `SandboxJobRuntimeAdapter` | `sandbox-job` / `job` | `operly.sandbox_job` |

Registration requires concrete asynchronous execution and reconciliation methods, an implementation identity, and nonempty compatibility pairs. Duplicate registrations and reserved `platform_native` registration are rejected. There is no mutable registration API or dynamic third-party adapter loading.

`resolve(mode)` returns an adapter or `None`. `adapter.check_support(profile=..., kind=...)` checks the exact pair. The registry produces public support metadata, including stable `unsupported_runtime_mode`, `runtime_profile_mode_mismatch`, and `platform_native_reserved` reason codes. `runtime_support.py` remains a small compatibility facade; `require_supported_runtime` now returns the resolved adapter.

Adapters delegate to existing reconciliation and execution methods. No API-layer types or execution services are imported when the registry is loaded. The provider lazily resolves the existing sandbox backend; the Workspace provider returns itself so its injected runner and execution semaphore are preserved. Remote endpoint fallback lookup moved from the mode dispatcher into the remote implementation. Remote HTTP authority/identity/egress handling and sandbox artifact/network/credential/binding checks remain in their existing backends.

The adapter also declares artifact validation requirements and the existing runtime-specific health policy. These influence validation/readiness behavior only after support resolution. `supported` itself remains independent of health, credentials, configuration, authority, endpoint validity, or runner availability.

## Enforcement audit

| Boundary | Registry-backed enforcing path |
| --- | --- |
| Publication | `PluginPlatformService.publish_workspace_version` resolves support before artifacts, package/version records or jobs; artifact requirement comes from adapter. |
| Internal validation | `record_validation(passed=True)` resolves support before marking passed. |
| Internal runtime recording | `record_runtime_instance` resolves support before creating a runtime row. |
| Validation jobs | `_require_supported_version` runs before validation/build logic and before the already-passed fast path; stable permanent failures are committed by the worker. |
| Installation | `install_version` resolves support before creating installation/storage/event records. |
| Activation | `_assert_activation_ready` resolves support independently of stored healthy runtime evidence. |
| Reconciliation API | `reconcile_runtime` returns structured HTTP 422 before queueing absent/incompatible adapters. |
| Reconciler and worker | `_context` checks registry support; `reconcile` delegates to the resolved adapter. Direct transport methods also use `_context`. Permanent unsupported errors become failed jobs, not retry loops. |
| Agent discovery | `InstalledPluginCapabilitySource.list` omits unsupported manifests. |
| Provider resolution/execution | `_resolve` independently checks support; `execute` invokes the adapter, whose backend resolves again before transport execution. |
| Foundation/status/profiles | Support metadata comes through registry-backed helpers; declared enum vocabulary remains separately visible. Profile deployment/preview/binding flags cannot override absent support. |

Historical manifests, enum values, runtime instance states and static ZIP serving are not rewritten. No migration, hosting adapter, scheduler change or new authorization path is introduced. Existing transaction/idempotency handling is unchanged; this refactor does not add a locking claim.

## Duplicate knowledge audit

Searched `packages` and `apps` for `_IMPLEMENTED`, `MODE_BY_RUNTIME_KIND`, combined supported-mode lists, and execution-mode comparisons. No separate implementation-availability set remains. Three local mode comparisons remain deliberately: the remote execution backend and the two concrete reconciliation backends reject direct calls for the wrong transport **after registry support enforcement**. They do not advertise implementation availability.

The enum, profile catalog, and explicitly labelled historical vocabulary tables still name profiles/modes. They describe syntax and historical inspection; only registered adapters supply support and compatible pairs. Publication and worker artifact/build decisions now use adapter requirements rather than remote-versus-sandbox mode lists.

## Validation and limits

Commands run with `DATABASE_URL=sqlite+aiosqlite:///:memory:`:

- `python -m unittest discover -s tests -p 'test_plugin*.py' -q` — 34 passed, including platform, support, sandbox reconciliation, registry, provider execution, discovery and worker tests.
- `python -m unittest discover -s tests -p 'test_pre_agent_runtime*.py' -q` — 18 passed.
- `python -m unittest discover -s tests -p test_security_hardening.py -q` — 21 passed.

Seven new registry tests cover complete registration, immutability/import dependencies, pair compatibility, registration removal across all boundaries, remote HTTP execution/identity revocation, sandbox execution with injected runner/network isolation/semaphore release, and transient readiness failure remaining distinct from support. The existing nine support tests and three sandbox reconciler tests retain historical/unsupported and successful reconciliation coverage. CI explicitly runs the new registry suite.

SQLite tests use scoped plugin rows and exercise durable worker commits. HTTP, artifact storage, runtime identity issuance/revocation and runner infrastructure are mocked where applicable; this is not a real infrastructure deployment test. Existing datetime deprecation warnings are unrelated to this change. No PostgreSQL locking changes are made or claimed.

The change is suitable for merge after normal CI/review. The main review consideration is the unmerged prerequisite described above; the only intended behavior changes are canonical registry-derived support/dispatch and consistent profile support metadata. Future hosting adapters remain separate work and must register concrete implementations rather than modify the manifest enum or a support allowlist.

## Files in the registry refactor

- `packages/plugins/runtime_registry.py`: concrete adapters and immutable registry.
- `packages/plugins/runtime_support.py`: registry-backed compatibility facade.
- `packages/plugins/runtime_profiles.py`: exact-pair support and guarded public flags.
- `packages/plugins/runtime_provider.py`, `sandbox_job_runtime.py`: common registry dispatch, existing backend bodies and runner injection retained.
- `packages/plugins/runtime_reconciler.py`: adapter dispatch and remote-specific endpoint fallback.
- `packages/plugins/service.py`, `worker.py`: use resolved adapter requirements.
- `tests/test_plugin_runtime_registry.py`: seven new behavioral tests.
- `.github/workflows/workflow-engine.yml`: registry regression CI coverage.
- This document: design, enforcement and validation record.
