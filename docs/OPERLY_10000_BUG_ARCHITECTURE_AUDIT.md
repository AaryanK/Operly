# OPERLY 10,000-Bug / Architecture Audit

**Repository:** `AaryanK/Operly`  
**Audited branch:** `main`  
**Snapshot:** `7c73d1e4c33d0aca5bef06ac65b58a0321d02dd3`  
**Audit date:** 2026-08-23  
**Purpose:** identify major defects, architectural gaps, duplicated services, parallel generations of the same service, security/correctness hazards, frontend/backend drift, and migration debt.

> [!IMPORTANT]
> The request was to find **10,000 bugs**. This document does **not manufacture 10,000 fake defects** to satisfy a number. It records source-backed defects, known open defects, high-confidence architectural gaps, and systematic failure families. A single root cause here can fan out across thousands of runtime combinations. The final section defines a 10,000-cell audit matrix so future work can measure exhaustive coverage without pretending an unverified probe is a bug.

---

## 1. Executive summary

OPERLY has made substantial progress toward the target architecture in `docs/TARGET_ARCHITECTURE.md`, but the repository is currently in a **multi-generation migration state** where old and new architectures are simultaneously live. The most serious risk is not one isolated bug; it is **split authority**: more than one frontend, more than one Studio/runtime model, more than one connector representation, more than one tool/harness registry, more than one authorization path, and more than one migration strategy can answer the same product question differently.

### Stop-the-line findings

1. **External side effects are not exactly-once safe.** `BusinessActionRecord.idempotency_key` is indexed but not unique, approval transitions are not atomic, and external provider execution happens before the durable DB transaction commits.
2. **Concurrent approval can double-execute real actions.** Two requests can both observe `WAITING_APPROVAL` and call the provider.
3. **Any authenticated workspace member can reach the approval endpoints without an explicit `actions:read` / `actions:approve` gate.** Rejection is especially dangerous because `ActionService.reject()` does not resolve the target capability before rejecting it.
4. **Approval execution uses a different authorization source than the normal workspace runtime.** `approvals_router.py` uses hard-coded `ROLE_AUTHORITY` instead of `resolve_workspace_permissions()`.
5. **Production DB migration safety is contradictory.** Docker always runs raw `alembic upgrade head`; `railway.toml` has no pre-deploy migration command; the controlled migration utility is stale and recognizes revisions only through `0011` even though head is `0031`.
6. **The production frontend is not the React/Vite frontend.** Docker installs only Python and FastAPI serves `apps/web/static/index.html`; `apps/web/src/main.tsx` is a second frontend generation that production does not build.
7. **Studio has multiple live implementations sharing globals and runtime concepts.** `window.operlyStudio` is assigned by more than one implementation, and Unified Studio still branches among `studio`, `app`, and `generated` runtime generations.
8. **Account identity and workspace identity are structurally conflated.** Signup always creates `My workspace`, sessions require a non-null tenant, and login/verification fail when a user has no membership.
9. **Built-in role customization is misleading.** The API replaces permission rows, but runtime permission resolution unions hard-coded defaults back in, so apparent revocation may not revoke authority.
10. **Plugin lifecycle removal is incomplete.** Uninstalling a plugin does not deregister its contribution, manifest, runtime, model registrations, or capability registrations.
11. **Capability availability state is split.** `configured`/`healthy` metadata exists but `CapabilityRegistry.resolve()` does not fail closed on those states.
12. **The old `packages/harness/ToolRegistry` remains a second tool execution architecture without the canonical capability firewall in that registry.
13. **Connector state exists in multiple generations** (`Integration`, `DiscordGuild`, `ChannelInstallation`, `TenantConnector`), creating competing sources of truth.
14. **Company/profile context remains tenant-flat while Solutions are multi-product**, already causing cross-Solution semantic contamination.
15. **CI validates only curated subsets of a rapidly growing architecture** and misses the React source tree, many production static scripts, PostgreSQL production behavior, and actual browser-level script-order behavior.

### Recommended architectural rule

From this point forward, **no new feature should be added to a compatibility generation** unless the canonical layer cannot express it and the architectural gap is fixed first. Every new capability should enter through one of these canonical seams:

- `User/Principal` for identity;
- `WorkspaceMembership` for collaborative scope;
- `SoftwareProject/Solution` for product/project identity;
- `PluginRuntime` for installable capability ownership;
- `CapabilityRegistry + CapabilityFirewall` for tool discovery/execution;
- `ModelRegistry/ModelPool` for model selection;
- `RuntimePlugin` for generated software execution;
- one frontend application shell and one Studio API.

---

## 2. Severity and confidence

| Label | Meaning |
|---|---|
| **P0** | Can cause unauthorized action, duplicate external side effect, data loss/corruption, cross-scope exposure, or production release failure. |
| **P1** | Major correctness/security/architecture defect likely to create user-visible failures or make future changes unsafe. |
| **P2** | Important reliability, maintainability, performance, or migration debt. |
| **P3** | Lower-severity UX/observability/cleanup issue. |
| **CONFIRMED** | Directly visible in current source or an existing reproduced GitHub issue. |
| **HIGH** | Strongly implied by current source/DB semantics but should still receive a focused regression test. |
| **RISK** | Architecture allows the failure; reproduce before treating it as a production incident. |

---

# 3. Detailed findings

## A. External actions, approvals, idempotency, and firewall

### AUDIT-0001 — Idempotency key is not unique
**P0 · CONFIRMED** — `packages/database/company_models.py`, `packages/actions/service.py`

`BusinessActionRecord.idempotency_key` has only an index. `ActionService.propose()` performs a read-before-insert check. Concurrent callers can both see no row and create two actions with the same logical idempotency key.

**Fix:** add a tenant-scoped unique constraint, insert atomically, catch conflict, and return the winning existing action.

### AUDIT-0002 — Approval execution is not an atomic state transition
**P0 · CONFIRMED** — `packages/actions/service.py`

`approve()` reads the action, checks `WAITING_APPROVAL`, then executes. There is no conditional `UPDATE ... WHERE status='WAITING_APPROVAL'`, row lock, or compare-and-swap transition.

**Fix:** atomically claim the action for execution before any provider call.

### AUDIT-0003 — Approve-vs-approve race can duplicate external effects
**P0 · HIGH** — `packages/actions/service.py`

Two approval requests can both observe the same waiting state and both execute the provider.

### AUDIT-0004 — Approve-vs-reject race can produce contradictory durable state
**P0 · HIGH** — `packages/actions/service.py`

Approval and rejection can race without a database-enforced terminal transition rule.

### AUDIT-0005 — External side effect occurs before durable transaction commit
**P0 · CONFIRMED** — `packages/actions/service.py`, `packages/capabilities/firewall.py`

Provider execution happens inside an uncommitted DB transaction. A later exception can roll back the action record even after the remote system changed.

### AUDIT-0006 — Provider timeout does not prove the side effect did not happen
**P0 · CONFIRMED** — `packages/actions/service.py`

`asyncio.wait_for(..., timeout=30)` can time out after the remote provider accepted a request. Retrying can duplicate the action.

### AUDIT-0007 — Output schema validation happens after the external mutation
**P0 · CONFIRMED** — `packages/actions/service.py`

If the provider successfully mutates Gmail/Calendar/etc. but returns malformed evidence, OPERLY marks the action failed after the real side effect occurred.

### AUDIT-0008 — Verification has no timeout
**P0 · CONFIRMED** — `packages/actions/service.py`

`provider.verify()` can hang after a successful mutation and hold the request/transaction indefinitely.

### AUDIT-0009 — Verification exceptions are not normalized
**P0 · CONFIRMED** — `packages/actions/service.py`

Provider execution is wrapped in `try/except`; verification is not. A verifier exception can escape after the external action succeeded.

### AUDIT-0010 — Post-side-effect JSON serialization can fail
**P0 · HIGH** — `packages/actions/service.py`

`result.evidence` and verification evidence are serialized after execution. Non-JSON-compatible provider evidence can fail after the mutation.

### AUDIT-0011 — No durable outbox/command record protecting provider execution
**P1 · CONFIRMED ARCHITECTURAL GAP**

The action DB state and external side effect are not coordinated through a durable delivery protocol with provider-level idempotency.

### AUDIT-0012 — No compensation/reconciliation state for ambiguous outcomes
**P1 · CONFIRMED ARCHITECTURAL GAP**

`FAILED` and `VERIFICATION_FAILED` do not distinguish “definitely not executed” from “possibly executed externally.”

### AUDIT-0013 — Approval execution loses original runtime context
**P1 · CONFIRMED** — `packages/actions/service.py`

`propose()` receives runtime/channel/temporal metadata. `approve()` later calls `execute(action)` without the original runtime context, so approved execution and auto-execution can run with different provider context.

### AUDIT-0014 — Approval API has no explicit `actions:read` permission gate
**P0 · CONFIRMED** — `apps/api/approvals_router.py`

Listing approvals requires authentication/membership but not `actions:read`.

### AUDIT-0015 — Approval API has no explicit `actions:approve` permission gate
**P0 · CONFIRMED** — `apps/api/approvals_router.py`

Decision requests do not check `actions:approve` before entering `ActionService`.

### AUDIT-0016 — Any member can attempt to reject a business action
**P0 · CONFIRMED** — `apps/api/approvals_router.py`, `packages/actions/service.py`

`ActionService.reject()` does not resolve the capability or check an approval permission. A member who can reach the endpoint can reject a waiting action unless another unseen layer intervenes.

### AUDIT-0017 — Legacy approval rows without `business_action_id` can be changed directly
**P0 · CONFIRMED** — `apps/api/approvals_router.py`

For compatibility approval rows, the router assigns `row.status = payload.status` directly, again without an approval-specific permission gate.

### AUDIT-0018 — Approval authorization uses hard-coded role defaults
**P1 · CONFIRMED** — `apps/api/approvals_router.py`

The router constructs `ActionService` with `ROLE_AUTHORITY.get(auth.role)` rather than resolving workspace-customized permissions.

### AUDIT-0019 — Approval records do not record approver identity
**P1 · CONFIRMED** — `packages/database/models.py`

`Approval` has requester/status/created_at but no `approved_by`, `rejected_by`, `decided_at`, or decision reason.

### AUDIT-0020 — Approval records are weakly linked to the business action
**P1 · CONFIRMED**

`business_action_id` is stored inside `payload_json` instead of a typed foreign-key column on `Approval`.

### AUDIT-0021 — Firewall `evaluate()` is not the authority used by `invoke()`
**P1 · CONFIRMED** — `packages/capabilities/firewall.py`

`invoke()` goes through `ActionService`; it does not call `evaluate()` first.

### AUDIT-0022 — Firewall evaluation and ActionService policy can disagree
**P1 · CONFIRMED** — `packages/capabilities/firewall.py`, `packages/actions/policy.py`

The firewall checks permissions + `approval_policy`; ActionService also applies legacy capability-name/risk policy.

### AUDIT-0023 — `tenant_context` is ignored by policy
**P1 · CONFIRMED** — `packages/actions/policy.py`

`evaluate_action(action, tenant_context=None)` immediately discards `tenant_context`.

### AUDIT-0024 — Central action policy hard-codes capability names
**P1 · CONFIRMED** — `packages/actions/policy.py`

Old and namespaced capability IDs are explicitly enumerated, so plugin behavior can require editing central policy.

### AUDIT-0025 — Legacy and new capability names coexist in policy
**P2 · CONFIRMED**

Examples include `read_analytics` beside `analytics.query`, `publish_website` beside `website.edit`, and similar compatibility naming.

### AUDIT-0026 — `WAITING_APPROVAL` is returned with `ok=True`
**P1 · CONFIRMED** — `packages/capabilities/firewall.py`

Callers that treat `ok` as “effect happened” can incorrectly continue a workflow before approval/execution.

### AUDIT-0027 — `CapabilityInvocationResult.as_dict()` labels capability ID as `plugin`
**P3 · CONFIRMED** — `packages/capabilities/firewall.py`

This creates terminology/API confusion between plugin identity and capability identity.

### AUDIT-0028 — Action normalized event mapping is hard-coded
**P2 · CONFIRMED** — `packages/actions/service.py`

Central code maps selected `(capability,event)` pairs to product events. New plugins will not automatically participate.

### AUDIT-0029 — No explicit stuck-action recovery in ActionService
**P1 · RISK**

Statuses include `EXECUTING` and `VERIFYING`, but this service contains no lease/heartbeat/reclaim protocol for process death between transitions.

### AUDIT-0030 — Reusing a call ID can collide across capabilities
**P1 · CONFIRMED** — `packages/capabilities/firewall.py`

The idempotency key is `workspace_id:call_id`; it omits capability ID and principal ID.

---

## B. Authorization, roles, account identity, and workspace boundaries

### AUDIT-0031 — Signup always creates a workspace
**P1 · CONFIRMED** — `apps/api/session.py`

Every signup creates `Tenant(name="My workspace")` and owner membership.

### AUDIT-0032 — Auth sessions cannot exist without a tenant
**P1 · CONFIRMED** — `packages/database/models.py`

`AuthSession.tenant_id` is non-nullable.

### AUDIT-0033 — Verification fails when user has no workspace membership
**P1 · CONFIRMED** — `apps/api/session.py`

The new personal-account model cannot be represented.

### AUDIT-0034 — Login fails when user has no workspace membership
**P1 · CONFIRMED** — `apps/api/session.py`

A valid Operly account cannot log in independently of a workspace.

### AUDIT-0035 — Login picks the oldest membership, not an explicit/default personal choice
**P1 · CONFIRMED** — `apps/api/session.py`

`_first_membership()` orders by creation time and silently chooses the first workspace.

### AUDIT-0036 — Session identity and active workspace are fused
**P1 · CONFIRMED**

Changing workspace changes a security property of the authentication session rather than using account identity plus an explicit request scope.

### AUDIT-0037 — Personal connector ownership is not representable in `TenantConnector`
**P1 · CONFIRMED ARCHITECTURAL GAP** — `packages/database/connector_models.py`

Every connector requires `tenant_id`; there is no account-owned connector owner type.

### AUDIT-0038 — Personal-to-workspace delegation is not a first-class grant
**P1 · CONFIRMED ARCHITECTURAL GAP**

The desired one-time/persistent delegation semantics are not represented in the current connector model.

### AUDIT-0039 — Built-in role permission removal is ineffective
**P0 · CONFIRMED** — `apps/api/workspace_router.py`, `packages/security/permissions.py`

The API replaces stored permissions, but runtime resolution unions defaults back into system roles.

### AUDIT-0040 — The role API can report a permission set different from runtime authority
**P0 · CONFIRMED**

A user can believe a capability was removed while runtime still grants it.

### AUDIT-0041 — New hard-coded default permission silently escalates existing system roles
**P1 · CONFIRMED BY DESIGN** — `packages/security/permissions.py`

Existing workspaces inherit future default permissions automatically.

### AUDIT-0042 — Plugin-defined permissions cannot be assigned unless present in core defaults
**P1 · CONFIRMED** — `packages/security/permissions.py`

`KNOWN_PERMISSIONS` is derived from `DEFAULT_ROLE_AUTHORITY`; unknown permission strings are rejected by role editing.

### AUDIT-0043 — Owner cannot be constrained by permission rows
**P1 · CONFIRMED** — `packages/security/execution_context.py`, `apps/api/workspace_router.py`

Owner role short-circuits permission checks.

### AUDIT-0044 — Membership role is an unconstrained string
**P1 · CONFIRMED** — `packages/database/models.py`

`TenantMember.role` has no FK/check constraint tying it to a valid role.

### AUDIT-0045 — Last-owner protection is race-prone
**P0 · HIGH** — `apps/api/workspace_router.py`

The endpoint counts owners then updates the role without locking the membership/tenant. Concurrent demotions can both see more than one owner.

### AUDIT-0046 — Workspace timezone accepts arbitrary truncated text
**P2 · CONFIRMED** — `apps/api/workspace_router.py`

Workspace creation does not validate an IANA zone before storing it.

### AUDIT-0047 — HTTP and agent authorization paths are not guaranteed identical
**P1 · CONFIRMED**

Some HTTP routers call workspace permission resolution, some use `ROLE_AUTHORITY`, and some only require membership.

### AUDIT-0048 — Principal model and auth/user model overlap without one root identity seam
**P1 · CONFIRMED STRUCTURAL DUPLICATION** — `AppUser/AuthIdentity`, `Principal`, `ExternalIdentity`, `ExternalPrincipalBinding`

The repo contains multiple identity generations whose ownership relationships must be normalized.

### AUDIT-0049 — `AuthIdentity`, `ExternalIdentity`, and `ExternalPrincipalBinding` duplicate provider identity concepts
**P2 · CONFIRMED STRUCTURAL DUPLICATION**

Different subsystems can disagree about whether an external identity is linked/verified.

### AUDIT-0050 — Personal client grants with `tenant_id=NULL` are not actually unique in PostgreSQL
**P0 · HIGH** — `packages/database/principal_models.py`

`UNIQUE(principal_id, tenant_id, client_id)` does not collapse multiple rows where `tenant_id` is NULL under normal PostgreSQL NULL uniqueness semantics.

### AUDIT-0051 — Status/kind fields for principals are unconstrained strings
**P2 · CONFIRMED**

Invalid lifecycle states can enter the DB through non-API code paths.

### AUDIT-0052 — Context visibility/scope combinations are not DB constrained
**P1 · CONFIRMED** — `packages/database/channel_models.py`

`ContextRecord` allows combinations of `scope_type`, `visibility`, `tenant_id`, and `owner_user_id` that may be semantically invalid.

### AUDIT-0053 — Tenant/member FKs use inconsistent delete semantics
**P2 · CONFIRMED** — `packages/database/models.py`

Some identity relationships declare cascades; legacy `TenantMember`, Message/Memory/Task/etc. FKs do not consistently specify them.

### AUDIT-0054 — Bootstrap admin can join the only existing tenant automatically
**P0 · CONFIRMED** — `apps/api/main.py`

If the configured admin user has no membership and exactly one tenant exists, bootstrap logic adds the admin as owner to that tenant. This is dangerous in a customer-hosted/multi-tenant production environment.

### AUDIT-0055 — Bootstrap administrative behavior is coupled to application startup
**P1 · CONFIRMED** — `apps/api/main.py`

Operational ownership mutation occurs automatically during web-process startup instead of an explicit administrative provisioning flow.

---

## C. Capability registry, plugin runtime, and harness convergence

### AUDIT-0056 — `configured=False` does not block `CapabilityRegistry.resolve()`
**P1 · CONFIRMED** — `packages/capabilities/registry.py`

Execution resolution checks enabled + permissions, not configured state.

### AUDIT-0057 — `healthy=False` does not block `CapabilityRegistry.resolve()`
**P1 · CONFIRMED**

Capability health is metadata rather than an execution gate at this layer.

### AUDIT-0058 — Capability search can surface configured/unhealthy tools without a precise unavailable reason
**P1 · CONFIRMED**

This contributes directly to capability-availability ambiguity.

### AUDIT-0059 — Capability version exists in metadata but registration key is only capability ID
**P1 · CONFIRMED**

Two versions of the same capability ID cannot coexist or be explicitly negotiated.

### AUDIT-0060 — Provider version compatibility is not expressed in the registry key
**P2 · CONFIRMED ARCHITECTURAL GAP**

Version skew is represented as a field, not an enforced compatibility contract.

### AUDIT-0061 — Capability “semantic” search is lexical token overlap
**P2 · CONFIRMED** — `packages/capabilities/registry.py`

The target architecture calls for semantic discovery; current search tokenizes and scores overlap/phrases.

### AUDIT-0062 — Capability tag filtering has case-normalization asymmetry
**P3 · CONFIRMED**

Requested tags are lower-cased; stored `definition.tags` are compared as-is.

### AUDIT-0063 — Phrase bonus compares lower-cased query against non-normalized discovery text
**P3 · CONFIRMED**

Search ranking can vary with capitalization.

### AUDIT-0064 — Capability registry has no unregister path
**P1 · CONFIRMED**

Dynamic uninstall cannot remove provider/capability authority cleanly.

### AUDIT-0065 — Plugin uninstall does not remove its contribution
**P1 · CONFIRMED** — `packages/plugins/runtime.py`

The plugin remains in `_contributions` after lifecycle `uninstall()`.

### AUDIT-0066 — Plugin uninstall does not remove the manifest
**P1 · CONFIRMED**

Manifest registry state survives lifecycle uninstall.

### AUDIT-0067 — Plugin uninstall does not deregister runtime plugins
**P1 · CONFIRMED**

`RuntimeRegistry` has no unregister method.

### AUDIT-0068 — Plugin uninstall cannot undo model registrar side effects
**P1 · CONFIRMED ARCHITECTURAL GAP**

Registrars are invoked imperatively on register with no corresponding deregistration contract.

### AUDIT-0069 — Replacing an already-started plugin can leave the new lifecycle unstarted
**P0 · HIGH** — `packages/plugins/runtime.py`

`_started` tracks only plugin ID. Replacing the contribution while that ID is in `_started` means `start()` skips the new lifecycle.

### AUDIT-0070 — Replacing a plugin does not stop the old lifecycle first
**P1 · CONFIRMED**

`register(..., replace=True)` overwrites contribution metadata without lifecycle handoff.

### AUDIT-0071 — Plugin shutdown order is not deterministic reverse-start order
**P1 · CONFIRMED**

`_started` is a set; reversing a tuple made from a set does not preserve startup ordering.

### AUDIT-0072 — Plugin registration is not transactional
**P1 · CONFIRMED**

Manifest/contribution is stored before model/runtime registrar side effects finish. A registrar exception can leave partial registration.

### AUDIT-0073 — One plugin lifecycle startup failure can abort the full app lifespan
**P1 · CONFIRMED**

`PluginRuntime.start()` sequentially awaits lifecycles without isolation/degraded startup policy.

### AUDIT-0074 — Runtime registry ignores `requirements`
**P1 · CONFIRMED** — `packages/runtime_plugins/registry.py`

`resolve(..., requirements)` immediately deletes the requirements parameter.

### AUDIT-0075 — Runtime resolver picks a winner before validating it
**P1 · CONFIRMED**

If the highest-scoring runtime detects the source but fails validation, resolution raises instead of trying the next valid match.

### AUDIT-0076 — One runtime plugin `detect()` exception can break all runtime resolution
**P1 · CONFIRMED**

Plugin detection is not isolated per candidate.

### AUDIT-0077 — Runtime registry has no unregister path
**P1 · CONFIRMED**

Install/uninstall lifecycle cannot safely remove runtime authority.

### AUDIT-0078 — Legacy `ToolRegistry` remains an independent execution path
**P1 · CONFIRMED** — `packages/harness/registry.py`

It directly invokes handlers and does not inherently cross the canonical capability firewall.

### AUDIT-0079 — Coding harness keeps an independent tool vocabulary and registry model
**P1 · CONFIRMED MIGRATION GAP** — `packages/coding_harness/opencode_agent.py`, `docs/IMPLEMENTATION_STATUS.md`

The project itself identifies this as the main second-registry bridge still to migrate.

### AUDIT-0080 — Agent harness still contains vendor-specific Gmail capability policy
**P1 · CONFIRMED** — `packages/capabilities/agent_harness.py`

`_PRIVATE_CONNECTOR_AUTHORITY` enumerates Gmail capability IDs centrally.

### AUDIT-0081 — Agent harness still contains vendor-specific Discord capability policy
**P1 · CONFIRMED**

`_DISCORD_CURRENT_CONTEXT` enumerates Discord capability IDs centrally.

### AUDIT-0082 — Agent harness imports Google scopes and maps them to capabilities
**P1 · CONFIRMED**

`registry_for()` knows Google OAuth scope constants and manually enables Gmail/Calendar capability IDs, violating the target “harness consumes capabilities, not vendors” rule.

### AUDIT-0083 — `handles()` overclaims unknown dotted capabilities
**P2 · CONFIRMED** — `packages/capabilities/agent_harness.py`

When no registry is injected, any name containing exactly one dot is considered handled.

### AUDIT-0084 — Session capability views are process-local
**P1 · CONFIRMED**

`PluginAgentHarness._session_views` is in-memory; multi-replica requests can have different progressive exposure state.

### AUDIT-0085 — Session capability view cache has no eviction
**P2 · CONFIRMED**

Long-lived processes can accumulate conversation/session keys indefinitely.

### AUDIT-0086 — Missing conversation ID collapses state into an `ephemeral` key
**P1 · CONFIRMED**

Same principal/workspace/channel operations without a conversation ID share one capability exposure state.

### AUDIT-0087 — Malformed tool-call JSON silently becomes `{}`
**P2 · CONFIRMED** — `packages/capabilities/agent_harness.py`

This hides the actual parse failure and shifts the error downstream.

### AUDIT-0088 — Capability-unavailable errors collapse to generic “unknown or unauthorized”
**P2 · CONFIRMED**

Installed, disabled, missing scope, denied, and hidden states are not explainable here.

### AUDIT-0089 — Fixed execution-step policy exists in multiple layers
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

`PluginAgentHarness.run_session()` defaults to 8 while the business-agent issue tracker documents another fixed ceiling. Safety budgets do not have one authority.

### AUDIT-0090 — Compatibility `OllamaClient` still owns provider-env branching
**P2 · CONFIRMED** — `packages/business_brain/ollama_client.py`

Although it delegates into model runtime, a vendor-named facade still decides provider defaults from environment state.

---

## D. Frontend architecture, duplicated shells, and version skew

### AUDIT-0091 — Production does not build the React/Vite app
**P1 · CONFIRMED** — `Dockerfile`, `apps/api/main.py`

The container installs only Python. FastAPI serves `apps/web/static/index.html`.

### AUDIT-0092 — `apps/web/src/main.tsx` is a second frontend implementation
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

It implements overlapping pages/data flows but is not the production source of truth.

### AUDIT-0093 — Two HTML application entrypoints exist
**P2 · CONFIRMED** — `apps/web/index.html`, `apps/web/static/index.html`

The repo does not have one unambiguous frontend entrypoint.

### AUDIT-0094 — Static frontend loads many global scripts in order
**P1 · CONFIRMED** — `apps/web/static/index.html`

The production shell relies on globals and sequential script ordering rather than modules/bundling.

### AUDIT-0095 — Production page carries mixed asset-version query strings
**P1 · CONFIRMED**

Different scripts/CSS files use date/version suffixes from multiple generations in the same page.

### AUDIT-0096 — Cache revision rewriting is brittle exact-string replacement
**P2 · CONFIRMED** — `apps/api/main.py`

`frontend_shell()` rewrites selected literal asset strings. If index markup changes, replacement can silently stop applying.

### AUDIT-0097 — Only a small subset of static assets receives runtime revision rewriting
**P2 · CONFIRMED**

Other scripts keep their own hard-coded version values.

### AUDIT-0098 — Legacy `app.js` and `simple-ui.js` both own navigation/product screens
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

Both implement home/activity/tasks/memory/approvals-style product surfaces in the same static application.

### AUDIT-0099 — Global functions are shared across frontend generations
**P1 · CONFIRMED**

Examples include `api`, `$`, `$$`, Studio globals, and exported `window.operly*` functions.

### AUDIT-0100 — Unified and legacy Studio collide on `window.operlyStudio`
**P1 · CONFIRMED** — existing issue #64, `unified-solution-studio.js`

Load order determines which implementation owns the global.

### AUDIT-0101 — `simple-ui.openBuild()` calls the ambiguous global
**P1 · CONFIRMED** — `apps/web/static/simple-ui.js`

The caller cannot express which Studio generation it wants.

### AUDIT-0102 — `openBuild()` then searches for a legacy selector
**P1 · CONFIRMED**

It calls the global Studio then looks for `#studio-software-prompt`, coupling modern Home to legacy DOM.

### AUDIT-0103 — Unified Studio is still website-first
**P1 · CONFIRMED** — `apps/web/static/unified-solution-studio.js`

Its list CTA says “Create website” and calls `createWebsite()` instead of composing arbitrary Solution intent.

### AUDIT-0104 — Unified Studio runtime selection is hard-coded to three generations
**P1 · CONFIRMED**

The UI branches on `studio`, `app`, and `generated`.

### AUDIT-0105 — Unified Studio infers runtime type from redirect URL regexes
**P1 · CONFIRMED**

Adding/changing runtimes can require frontend path parsing changes, bypassing plugin-driven runtime abstraction.

### AUDIT-0106 — Source-state fetch failure silently downgrades to legacy behavior
**P1 · CONFIRMED**

`refreshSourceState()` catches all errors and sets `S.source=null`; auth/network/server failures can be interpreted as “legacy website.”

### AUDIT-0107 — Studio trace can render `[object Object]`
**P2 · CONFIRMED** — existing issue #82

Nested event detail is coerced through generic string compaction.

### AUDIT-0108 — Legacy approvals UI can render nested objects as `[object Object]`
**P2 · CONFIRMED** — `apps/web/static/app.js`

`Object.values(details).join(...)` coerces objects.

### AUDIT-0109 — Home queries three legacy project APIs and manually merges them
**P1 · CONFIRMED** — `apps/web/static/simple-ui.js`

It loads custom software, Studio projects, and ApplicationBuilder applications rather than one canonical Solution/SoftwareProject collection.

### AUDIT-0110 — Legacy Studio repeats the same three-generation aggregation
**P1 · CONFIRMED** — `apps/web/static/studio.js`

The duplication exists in more than one frontend layer.

### AUDIT-0111 — Logical Solution duplication is possible in merged arrays
**P1 · HIGH**

Compatibility records representing one canonical project can appear as separate cards because the merge has no canonical deduplication key.

### AUDIT-0112 — `Promise.allSettled()` turns backend failures into empty product state
**P1 · CONFIRMED** — `simple-ui.js`

A failed tasks/projects/profile request is commonly converted to `[]` or `{}`.

### AUDIT-0113 — Company-profile outage can look like “new business, no profile”
**P1 · CONFIRMED**

This can invite the user to run company discovery and create more persistent facts during a transient backend failure.

### AUDIT-0114 — Connector/identity outage can look disconnected
**P1 · CONFIRMED** — `app.js`

Settings uses `Promise.allSettled()` and empty fallbacks.

### AUDIT-0115 — Approval UI exposes both approve/reject controls without a shared decision lock
**P1 · HIGH** — `simple-ui.js`

Only the clicked button is disabled; a rapid opposing click can race the backend.

### AUDIT-0116 — Native browser `prompt/confirm/alert` remains in production creation/editor paths
**P2 · CONFIRMED** — `studio.js`, issue #81

These bypass application state, structured intent, accessibility patterns, and telemetry.

### AUDIT-0117 — Custom-software client idempotency key includes `Date.now()`
**P1 · CONFIRMED** — `studio.js`

Every retry/double click generates a different key, defeating logical client-side deduplication.

### AUDIT-0118 — Multiple planner UI generations coexist in `studio.js`
**P2 · CONFIRMED**

`drawSoftwarePlan()` and `drawSynthesizedSoftwarePlan()` are parallel planner generations.

### AUDIT-0119 — `studio.js` contains SiteSchema Studio, ApplicationBuilder, and CustomSoftware editor logic together
**P1 · CONFIRMED**

One global file owns three runtime generations and their state machines.

### AUDIT-0120 — Unified Studio editor removes iframe sandbox for non-source runtimes
**P0 · RISK** — `unified-solution-studio.js`

Source-first Studio gets `sandbox="allow-scripts"`; other runtime previews remove the sandbox attribute. If any preview is same-origin and contains untrusted generated code, this violates the intended isolation boundary. Reproduce and verify response origin/CSP immediately.

### AUDIT-0121 — Preview isolation policy is inconsistent across card/editor paths
**P1 · CONFIRMED**

Solution cards use `sandbox=""`; editor runtime previews may be unsandboxed.

### AUDIT-0122 — Frontend search requests can render out of order
**P3 · HIGH** — `app.js`

Debounced `inbox()` calls are not cancelled or sequence-checked; a slower older query can replace newer results.

### AUDIT-0123 — Frontend state is primarily page-global mutable state
**P2 · CONFIRMED**

`state`, `studioState`, `appBuilderState`, `customSoftwareState`, and Unified Studio `S` can drift independently.

### AUDIT-0124 — Capture-phase handlers can suppress other frontend generations
**P2 · CONFIRMED** — `simple-ui.js`

`stopImmediatePropagation()` in a document-level capture handler makes script-order/interoperability brittle.

### AUDIT-0125 — Duplicate large logo asset exists in multiple frontend asset trees
**P3 · CONFIRMED STRUCTURAL DUPLICATION**

`public` and `static` contain duplicated logo media, another sign of two frontend build roots.

---

## E. Frontend build and CI gaps

### AUDIT-0126 — Root `package.json` is empty
**P2 · CONFIRMED**

There is no root frontend/monorepo command contract.

### AUDIT-0127 — Vite package has no lint script
**P2 · CONFIRMED** — `apps/web/package.json`

### AUDIT-0128 — Vite package has no test script
**P2 · CONFIRMED**

### AUDIT-0129 — Vite package has no explicit typecheck script
**P2 · CONFIRMED**

### AUDIT-0130 — Main application-flow CI does not watch `apps/web/src/**`
**P1 · CONFIRMED** — `.github/workflows/application-flow.yml`

React source can change without triggering the primary application-flow workflow.

### AUDIT-0131 — Main application-flow CI does not build the Vite app
**P1 · CONFIRMED**

### AUDIT-0132 — Main application-flow CI syntax-checks only a subset of production scripts
**P1 · CONFIRMED**

Several scripts directly loaded by static `index.html` are absent from the `node --check` list.

### AUDIT-0133 — No browser-level test validates global script order
**P1 · CONFIRMED GAP**

Syntax checking cannot detect `window.operlyStudio` collisions or handler interception.

### AUDIT-0134 — No browser-level test validates the actual production shell
**P1 · CONFIRMED GAP**

The smoke test exercises HTTP health, not rendered UI behavior.

### AUDIT-0135 — CI uses manually curated Python compile lists
**P2 · CONFIRMED**

New files can be omitted as the architecture grows.

### AUDIT-0136 — CI uses manually curated pytest lists instead of an authoritative test suite selection
**P2 · CONFIRMED**

A new regression test can exist but never run in the workflow unless explicitly added.

### AUDIT-0137 — Production smoke uses SQLite rather than PostgreSQL
**P1 · CONFIRMED**

Concurrency, NULL uniqueness, locking, FK, and migration behavior differ from production PostgreSQL.

### AUDIT-0138 — CI migration path does not reproduce the Docker startup command
**P1 · CONFIRMED**

The workflow manually migrates, then starts Uvicorn; production Docker performs migration and web startup in one shell command.

### AUDIT-0139 — Coding-harness workflow duplicates path filters for push and PR
**P3 · CONFIRMED**

Two copies of a long list can drift.

### AUDIT-0140 — Coding-harness workflow does not directly watch migration `0031`
**P2 · CONFIRMED**

Its migration path list explicitly names `0030`; future migration-specific architecture tests can fail to trigger if shared watched files do not also change.

### AUDIT-0141 — No dependency/security scanning workflow is evident in the checked workflow set
**P2 · RISK**

Repository workflow inventory should add dependency audit, secret scanning, SAST, and container scanning if GitHub/platform policy does not already provide them externally.

---

## F. Database, migrations, release safety, and timestamps

### AUDIT-0142 — Docker always runs `alembic upgrade head` on web startup
**P0 · CONFIRMED** — `Dockerfile`

### AUDIT-0143 — Railway config does not define the claimed controlled pre-deploy migration
**P0 · CONFIRMED** — `railway.toml`

### AUDIT-0144 — Runtime comments claim a deployment behavior that is not configured
**P1 · CONFIRMED** — `packages/database/db.py`, `railway.toml`

### AUDIT-0145 — Multiple replicas can contend on startup migration
**P0 · HIGH**

Every web replica executes the migration command before Uvicorn.

### AUDIT-0146 — Controlled migration whitelist stops at revision `0011`
**P0 · CONFIRMED** — `packages/database/migrate.py`

Current repository head is `0031_studio_model_trace`.

### AUDIT-0147 — A DB at revisions `0012`–`0031` is rejected by `inspect_supported_schema()`
**P0 · CONFIRMED**

The safer deploy-upgrade path is stale relative to current schema history.

### AUDIT-0148 — `ALEMBIC_HEAD` is manually duplicated in Python
**P1 · CONFIRMED** — `packages/database/schema.py`

It can drift from Alembic `ScriptDirectory` head.

### AUDIT-0149 — Manual schema validator covers only selected older table families
**P1 · CONFIRMED** — `packages/database/migrate.py`

Newer principal/channel/software-project/model-trace tables are not comprehensively validated there.

### AUDIT-0150 — Production backup/release gate is bypassed by Docker startup migration
**P0 · CONFIRMED**

The strong logic in `deploy-upgrade` is not the command production actually runs.

### AUDIT-0151 — `upgrade --allow-production` itself does not require verified backup metadata
**P0 · CONFIRMED**

Only the `deploy-upgrade`/release-gate path enforces the stronger protocol.

### AUDIT-0152 — Fresh PostgreSQL `deploy-upgrade` may ALTER `alembic_version` before it exists
**P1 · HIGH** — `packages/database/migrate.py`

`ensure_alembic_version_capacity()` is invoked before `command.upgrade()` and unconditionally alters the table for PostgreSQL.

### AUDIT-0153 — Development uses `Base.metadata.create_all()` instead of migrations
**P1 · CONFIRMED** — `packages/database/db.py`

Developer/test schema can differ from migration-produced production schema.

### AUDIT-0154 — Default development DB is SQLite while production is PostgreSQL
**P1 · CONFIRMED**

Important lock, constraint, NULL uniqueness, and concurrency bugs can remain invisible.

### AUDIT-0155 — Model registration for dev schema is a manual import list
**P2 · CONFIRMED** — `packages/database/schema.py`

A new ORM model omitted from `import_all_models()` can silently disappear from create-all metadata initialization.

### AUDIT-0156 — Many timestamps use naive `datetime.utcnow()`
**P1 · CONFIRMED**

Auth, actions, connector, channel, principal, and legacy business tables store timezone-naive datetimes, increasing ambiguity around timezone conversion and mixed-driver semantics.

### AUDIT-0157 — Timezone correctness is split between stored naive UTC and user/workspace timezone layers
**P1 · CONFIRMED ARCHITECTURAL GAP**

Issue #59 demonstrates this boundary is already user-visible.

### AUDIT-0158 — Python dependencies are ranged, not fully locked
**P1 · CONFIRMED** — `requirements.txt`

A rebuild can install different dependency versions without source changes.

### AUDIT-0159 — Production image installs directly from ranged requirements
**P1 · CONFIRMED** — `Dockerfile`

There is no lock/hash enforcement in the image build.

---

## G. Connector and channel duplication

### AUDIT-0160 — `Integration` and `TenantConnector` represent overlapping connection state
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

### AUDIT-0161 — `DiscordGuild` and `ChannelInstallation` represent overlapping Discord installation state
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

### AUDIT-0162 — `/integrations` explicitly merges legacy and generic connector projections
**P1 · CONFIRMED** — `apps/api/integrations_router.py`

This is a live compatibility source-of-truth merge, not dead code.

### AUDIT-0163 — Connector health has multiple representations
**P2 · CONFIRMED**

`Integration.status`, `ChannelInstallation.status`, `TenantConnector.status`, `enabled`, and `health_status` can diverge.

### AUDIT-0164 — `TenantConnector` uniqueness is weak when `provider_account_id` is NULL
**P1 · HIGH** — `packages/database/connector_models.py`

PostgreSQL allows multiple NULL values in an ordinary unique constraint, so multiple `(tenant, provider, NULL)` connectors can coexist.

### AUDIT-0165 — Connector secret lifecycle is not strongly owned by one connector row
**P1 · CONFIRMED ARCHITECTURAL GAP**

`ConnectorSecret` belongs only to tenant; `TenantConnector` points to it. The model itself does not ensure removal/rotation/orphan cleanup when connector lifecycle changes.

### AUDIT-0166 — Legacy channel data models are Discord-shaped
**P2 · CONFIRMED** — `Message`, `Memory`, `Task`, `Approval`, `ScheduledJob`, `ToolAudit`

They contain Discord `guild_id/channel_id` integer fields rather than generic channel/surface identity.

### AUDIT-0167 — Generic and legacy conversation state coexist
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

`ChannelConversationState`, `PrincipalConversation`, and legacy `Message`/guild-channel state all encode conversation identity differently.

### AUDIT-0168 — Connector availability is reconstructed in the agent harness from Google scopes
**P1 · CONFIRMED**

Connector runtime and capability runtime are not fully self-describing.

---

## H. Studio, Solution, SoftwareProject, and generated-runtime generations

### AUDIT-0169 — Three product/runtime generations remain first-class in UI
**P1 · CONFIRMED**

Studio websites, managed applications, and generated custom software are all independently queried/edited.

### AUDIT-0170 — Canonical `SoftwareProject` exists but UI still branches by legacy runtime identity
**P1 · CONFIRMED** — `docs/IMPLEMENTATION_STATUS.md`

### AUDIT-0171 — `SolutionService` is still a compatibility normalizer across runtime generations
**P1 · CONFIRMED MIGRATION GAP**

### AUDIT-0172 — Runtime identity leaks into product UI behavior
**P1 · CONFIRMED**

Frontend logic asks “studio/app/generated?” instead of consuming one canonical SoftwareProject/Solution contract.

### AUDIT-0173 — Legacy `SiteSchema` remains available
**P2 · CONFIRMED MIGRATION GAP**

It must not receive new authority while source-first Studio is canonical.

### AUDIT-0174 — ApplicationBuilder remains a separate orchestration generation
**P2 · CONFIRMED MIGRATION GAP**

### AUDIT-0175 — CustomSoftware planning has multiple planner implementations
**P2 · CONFIRMED**

The repo retains legacy planning clients and newer model-runtime planning paths.

### AUDIT-0176 — Old `OllamaPlanningClient` remains import-compatible
**P2 · CONFIRMED** — `docs/IMPLEMENTATION_STATUS.md`

### AUDIT-0177 — Runtime plugin registry is ready but production runner does not provide all advertised future runtimes
**P1 · CONFIRMED ARCHITECTURAL GAP** — `docs/IMPLEMENTATION_STATUS.md`

### AUDIT-0178 — Generated-runtime HTTP capability gateway is not production-exposed yet
**P1 · CONFIRMED ARCHITECTURAL GAP**

### AUDIT-0179 — Studio source range editing is not revision-aware
**P1 · CONFIRMED** — GitHub issue #65

### AUDIT-0180 — Studio success can occur without syntax/build/runtime validity
**P0 · CONFIRMED** — GitHub issue #66

### AUDIT-0181 — Generated web software lacks governed dependency management
**P1 · CONFIRMED** — GitHub issue #67

### AUDIT-0182 — Studio run model provenance collapses multi-model attempts
**P2 · CONFIRMED** — GitHub issue #68

### AUDIT-0183 — Studio source run history lacks a collection endpoint/view
**P2 · CONFIRMED** — GitHub issue #69

### AUDIT-0184 — Initial creation intent can be dropped before source generation
**P0 · CONFIRMED** — GitHub issue #72

### AUDIT-0185 — Contradictory Studio context is not fail-closed
**P0 · CONFIRMED** — GitHub issue #73

### AUDIT-0186 — Studio terminal `succeeded` does not prove semantic objective fulfillment
**P0 · CONFIRMED** — GitHub issue #74

### AUDIT-0187 — Grounding validation misses unsupported dates/factual metadata
**P1 · CONFIRMED** — GitHub issue #75

### AUDIT-0188 — Studio spends model/tool rounds rediscovering known-empty workspaces
**P2 · CONFIRMED** — GitHub issue #76

### AUDIT-0189 — Modern Solution creation still uses browser-native dialogs in compatibility flows
**P2 · CONFIRMED** — GitHub issue #81

---

## I. Context, company intelligence, memory, and task continuity

### AUDIT-0190 — Tenant-wide CompanyProfile is treated as one business/product identity
**P0 · CONFIRMED** — GitHub issues #71/#77

### AUDIT-0191 — Multiple unrelated Solutions compete for the same canonical company fields
**P0 · CONFIRMED**

### AUDIT-0192 — Studio can inject unrelated tenant CompanyProfile as authoritative project context
**P0 · CONFIRMED** — issue #71

### AUDIT-0193 — Company research can promote a research seed to owner-confirmed canonical evidence
**P0 · CONFIRMED** — issue #78

### AUDIT-0194 — `SolutionRecord.context_json` is not consistently the source-agent authority
**P1 · CONFIRMED** — issue #79

### AUDIT-0195 — CompanyEvidence lacks subject/entity provenance
**P0 · CONFIRMED** — issue #80

### AUDIT-0196 — Profile synthesis can pick a value despite unresolved conflict
**P0 · CONFIRMED** — issue #80

### AUDIT-0197 — Downstream Studio can discard conflict metadata and consume only the chosen value
**P0 · CONFIRMED** — issue #80

### AUDIT-0198 — Account/human, workspace, Solution, project, conversation, and source context scopes are not yet one explicit hierarchy
**P1 · CONFIRMED ARCHITECTURAL GAP**

### AUDIT-0199 — Legacy `Memory` table and generic `ContextRecord` overlap
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

### AUDIT-0200 — Context implementation spans several service layers
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

Relevant families include `packages/context`, company intelligence, `business_brain/context_loader`, capability context providers, legacy Memory, and channel context.

### AUDIT-0201 — Discord attachment/derived-artifact continuity is not durable across follow-up turns
**P1 · CONFIRMED** — GitHub issue #70

### AUDIT-0202 — Follow-up intent such as “send it” lacks a universally durable task/artifact reference model
**P1 · CONFIRMED BY ISSUE #70**

### AUDIT-0203 — Capability unavailability can cause the model to make generic false claims
**P1 · CONFIRMED** — issues #61/#63/#70

---

## J. Agent/model routing and observability

### AUDIT-0204 — Request-level task routing does not precede role routing
**P1 · CONFIRMED** — GitHub issue #83

### AUDIT-0205 — Trivial and complex requests can begin on the same business-agent profile
**P2 · CONFIRMED**

### AUDIT-0206 — Missing-capability recovery still depends partly on model prompt compliance
**P1 · CONFIRMED** — issue #61

### AUDIT-0207 — Main business-agent execution budget is an arbitrary fixed ceiling
**P1 · CONFIRMED** — issue #62

### AUDIT-0208 — Different harnesses own different step/loop bounds
**P1 · CONFIRMED STRUCTURAL DUPLICATION**

### AUDIT-0209 — Capability availability cannot explain every failed gate
**P1 · CONFIRMED** — issue #63

### AUDIT-0210 — UI/API operation → agent capability parity has no enforced manifest
**P1 · CONFIRMED** — issue #60

### AUDIT-0211 — Model provenance does not fully persist every provider/model attempt
**P2 · CONFIRMED** — issue #68

### AUDIT-0212 — Provider identity remains visible in compatibility model surfaces
**P2 · CONFIRMED MIGRATION DEBT**

ModelRuntime has provider-neutral contracts, but compatibility `ModelRoute`, `OllamaClient`, and provider-local fallback configuration remain reachable.

---

## K. Existing open defect registry crosswalk

The existing GitHub issues are part of this audit, not separate from it. They should be kept as reproducible defect tickets while this document tracks architecture/root cause.

| Issue | Current defect/gap | Audit linkage |
|---:|---|---|
| #59 | Repeated timezone preference 422 | AUDIT-0156/0157 |
| #60 | UI/API ↔ agent capability parity | AUDIT-0210 |
| #61 | Harness-driven capability discovery | AUDIT-0206 |
| #62 | Fixed agent step ceiling | AUDIT-0089/0207 |
| #63 | Capability availability diagnostics | AUDIT-0056–0058/0209 |
| #64 | Dual Studio entry point / intent routing | AUDIT-0100–0105 |
| #65 | Revision-unsafe range edits | AUDIT-0179 |
| #66 | Missing technical validation before success | AUDIT-0180 |
| #67 | Governed dependencies missing | AUDIT-0181 |
| #68 | Multi-model provenance incomplete | AUDIT-0182/0211 |
| #69 | Studio run history collection missing | AUDIT-0183 |
| #70 | Discord attachment continuity | AUDIT-0201/0202 |
| #71 | Tenant profile contaminates project | AUDIT-0190–0192 |
| #72 | Owner creation intent dropped | AUDIT-0184 |
| #73 | Contradictory context not fail-closed | AUDIT-0185 |
| #74 | No semantic fulfillment gate | AUDIT-0186 |
| #75 | Grounding validator factual gaps | AUDIT-0187 |
| #76 | Redundant known-empty inspection | AUDIT-0188 |
| #77 | Flat tenant CompanyProfile | AUDIT-0190/0191 |
| #78 | Research silently creates canonical fact | AUDIT-0193 |
| #79 | SolutionRecord context not authoritative | AUDIT-0194 |
| #80 | CompanyEvidence provenance/conflict | AUDIT-0195–0197 |
| #81 | Native creation prompts | AUDIT-0116/0189 |
| #82 | `[object Object]` trace detail | AUDIT-0107/0108 |
| #83 | Request-level task routing missing | AUDIT-0204/0205 |
| #84 | Account/workspace/personal delegation model | AUDIT-0031–0038 |

---

# 4. Duplication / version-generation map

This is the core architectural debt map. Each row represents a place where a bug fix can be applied to one generation while another generation remains live.

| Domain | Older / compatibility generation | Newer / target generation | Current risk |
|---|---|---|---|
| Frontend shell | `apps/web/static/*` globals | `apps/web/src/*` React/Vite | Production runs static; React can drift independently. |
| Navigation/Home | `app.js` pages | `simple-ui.js` + modern scripts | Multiple event/render authorities in one DOM. |
| Studio entry | `studio.js` | `unified-solution-studio.js` | Shared `window.operlyStudio` global. |
| Website artifact | `SiteSchema` | source-first Studio | Old schema can remain authoritative on compatibility path. |
| Business app | ApplicationBuilder | canonical SoftwareProject/runtime plugins | App generation branches remain in UI/service. |
| Custom software | custom-software project/planner generations | SoftwareProject + RuntimePlugin | Planner/build/runtime duplication. |
| Product identity | StudioProject / ManagedApplication / GeneratedProject | `SoftwareProject` / `SolutionRecord` | Synchronization can drift. |
| Tool registry | `packages/harness/ToolRegistry` | CapabilityRegistry + Firewall | Possible execution bypass / policy duplication. |
| Coding tools | CodingTool registry | universal capabilities | Second tool vocabulary/security model. |
| Plugin terminology | `PluginRegistry = CapabilityRegistry` compatibility | PluginRuntime + CapabilityRegistry | “plugin” and “capability” authority blurred. |
| Model clients | legacy `OllamaClient`, ModelRoute, local clients | ModelRegistry/ModelPool/Model.infer | Provider-specific configuration still leaks. |
| Connector state | `Integration`, `DiscordGuild` | ChannelInstallation / TenantConnector | Conflicting connection/health state. |
| Identity | AuthIdentity / ExternalIdentity | Principal / ExternalPrincipalBinding | Link/verification truth can diverge. |
| Conversation | legacy Message/guild/channel | ChannelConversationState / PrincipalConversation | Context ownership and continuity drift. |
| Memory/context | `Memory` | `ContextRecord` + scoped context services | Personal/workspace semantics inconsistent. |
| Company context | tenant CompanyProfile | entity/Solution-scoped profile target | Cross-product contamination. |
| Authorization | hard-coded role maps + action policy | ExecutionContext + future policy engine | Different surfaces can authorize differently. |
| Migrations | `create_all`, raw Alembic startup, manual validator | controlled release migration | Three migration authorities. |
| Runtime selection | legacy runtime kinds/URL parsing | RuntimeRegistry plugins | UI still knows runtime generations. |
| CI | hand-curated file/test lists | desired graph-aware authoritative CI | New code can evade relevant checks. |

## Duplication rule for future PRs

A PR that adds behavior to any cell in the “older / compatibility” column should be blocked unless it is either:

1. a parity/security fix required to keep current users safe while migration is in progress; or
2. a deletion/migration adapter that moves authority into the canonical layer.

No new product capability should be born in a compatibility layer.

---

# 5. Recommended fix order

## Phase 0 — Stop duplicate/unauthorized side effects

1. Add DB-enforced action idempotency uniqueness.
2. Implement atomic action claim/approval transitions.
3. Add explicit `actions:read` / `actions:approve` gates to approval routes.
4. Use `resolve_execution_context()` / workspace permissions in approval execution.
5. Add provider-level idempotency token contract.
6. Separate action outcome into `not_executed`, `executed_unverified`, `verified`, `ambiguous`.
7. Add verification timeout/error normalization and reconciliation worker.
8. Preserve immutable original execution context through approval.

## Phase 1 — Repair production release safety

1. Remove `alembic upgrade head` from web-process startup.
2. Add one Railway pre-deploy/release migration command.
3. Make backup-gated `deploy-upgrade` the only production schema mutation path.
4. Remove stale revision whitelist or derive supported migration graph from Alembic.
5. Run production migration CI against PostgreSQL.
6. Add migration-from-recent-production-snapshot test.

## Phase 2 — One authorization authority

1. Make every HTTP/agent/MCP/Studio action resolve one `ExecutionContext`.
2. Remove direct `ROLE_AUTHORITY` use from routers.
3. Fix built-in role revocation semantics.
4. Move permission declaration into plugin manifests/registry rather than a closed hard-coded `KNOWN_PERMISSIONS` set.
5. Add explicit grant/deny/resource constraints.
6. Add personal-to-workspace delegation grants.

## Phase 3 — Separate account from workspace

1. Make account session tenant-optional.
2. Introduce account/personal scope as first-class root.
3. Keep active workspace as request/conversation scope, not login identity.
4. Make connectors owned by `user` or `workspace` explicitly.
5. Add delegation records for personal capabilities/data used by a workspace.
6. Migrate auto-created `My workspace` tenants safely.

## Phase 4 — Converge frontend

1. Choose exactly one production frontend build root.
2. If React/Vite is canonical, build it in Docker and serve built assets.
3. Move legacy static pages behind temporary adapters or delete them.
4. Remove globals such as `window.operlyStudio`.
5. Use ES modules/component boundaries.
6. Add Playwright/browser regression tests for login, workspace switch, Solutions, Studio, approvals, connector settings, logout.
7. Delete mixed date-based asset version strings; use content hashes from one build pipeline.

## Phase 5 — Converge Solution/Studio/runtime

1. Make canonical SoftwareProject/Solution IDs the only public UI identity.
2. Resolve runtime from server-provided canonical metadata, never URL regexes.
3. Route creation from owner intent before selecting runtime.
4. Finish source-first Studio validation and semantic fulfillment gates.
5. Migrate ApplicationBuilder/custom-software compatibility implementations behind runtime plugins.
6. Remove legacy SiteSchema authority after parity tests.

## Phase 6 — Converge plugins/capabilities

1. Add unregister/lifecycle transaction semantics.
2. Make plugin install/uninstall durable and versioned.
3. Make capability versions first-class in resolution.
4. Fail closed on not-configured/unhealthy capabilities where execution requires health.
5. Move Google/Discord vendor scope logic out of the central agent harness into plugin contributions.
6. Migrate CodingToolRegistry and old ToolRegistry into session-scoped capabilities.
7. Add availability `explain()` with every gate.

## Phase 7 — Entity-scoped context

1. Replace tenant-flat CompanyProfile with subject-scoped profiles/evidence.
2. Make Solution context authoritative for Studio.
3. Preserve owner creation intent and artifact references durably.
4. Add conflict-resolution semantics before downstream consumption.
5. Unify legacy Memory and generic ContextRecord under explicit scope/provenance contracts.

## Phase 8 — CI and deletion budget

1. Run complete Python suite or declare authoritative test shards generated from ownership metadata.
2. Build/test/lint/typecheck frontend.
3. Add PostgreSQL integration tests.
4. Add concurrency tests for approval/idempotency/last-owner boundaries.
5. Add chaos tests around provider timeout after side effect.
6. Add architecture tests that fail when new compatibility imports or globals are introduced.
7. Create a deletion ledger and require every migration PR to remove or quarantine an old path.

---

# 6. Architecture invariants that should become tests

The following should be executable, repository-wide guardrails:

1. There is exactly one production web entrypoint.
2. No production frontend writes `window.operlyStudio` or another shared feature global.
3. No frontend derives runtime kind from URL structure.
4. No new API route calls a capability provider directly.
5. No new code imports `packages.harness` outside a compatibility allowlist.
6. No new code imports vendor-specific connector constants inside the generic agent harness.
7. Every consequential capability resolves through one firewall.
8. Every approval decision requires `actions:approve`.
9. Every read of approval data requires `actions:read`.
10. Every external mutation has a DB-unique logical idempotency key.
11. Every external provider mutation receives a provider idempotency token where the provider supports it.
12. Every action transition is atomic and monotonic.
13. Every external-effect state distinguishes unknown/ambiguous outcome.
14. Every verifier has timeout/error normalization.
15. Every plugin can be installed, started, stopped, uninstalled, and then disappear completely from discovery.
16. Replacing a running plugin performs deterministic lifecycle handoff.
17. Runtime resolution tries the next candidate when a detected candidate fails validation.
18. Runtime requirements influence selection.
19. A disabled/unconfigured/unhealthy capability produces a specific availability reason.
20. New plugin permission strings can be assigned without editing a core permission enum/map.
21. Removing a role permission actually removes it.
22. A user account can exist with zero workspaces.
23. A workspace cannot read personal memory/connectors unless explicitly delegated.
24. A personal AI may only traverse workspaces the user is authorized to access.
25. A connector has exactly one explicit owner scope.
26. A personal grant with NULL workspace remains unique.
27. A workspace always has at least one owner under concurrent role changes.
28. Company/profile facts have an explicit subject.
29. Conflicted high-identity facts cannot become authoritative silently.
30. Studio creation preserves the exact owner intent.
31. Studio success requires syntax/build/runtime validation.
32. Studio success requires semantic objective fulfillment.
33. Source range edits require revision/hash preconditions.
34. Generated source cannot receive raw provider credentials.
35. Generated code does not execute in the control-plane process.
36. Untrusted preview code cannot execute with Operly app-origin authority.
37. Solution runtime metadata comes from the backend canonical contract.
38. One logical Solution cannot appear as three cards through compatibility projections.
39. Failed backend reads render an error/degraded state, not fake empty state.
40. Browser CI loads the exact production shell and checks console errors/global collisions.
41. Production migration is never run by normal web startup.
42. Release migration requires a verified backup in production.
43. Migration validation derives current Alembic head rather than a stale constant.
44. CI tests PostgreSQL semantics.
45. Dependency versions are reproducible.
46. No new model/provider role logic is hard-coded into Studio/business features.
47. Request/task routing is surface-neutral.
48. Model failover provenance persists all attempts.
49. Attachment/artifact references survive follow-up turns.
50. Every compatibility layer has an owner, sunset condition, and deletion test.

---

# 7. 10,000-cell audit matrix

Instead of inventing 10,000 defects, use **10,000 deterministic audit cells** and mark each cell `PASS`, `FAIL`, `NOT_APPLICABLE`, or `NOT_TESTED`.

A practical matrix is:

- **20 subsystem families**
  1. auth/session
  2. principals
  3. workspace membership/RBAC
  4. context/memory
  5. company intelligence
  6. connectors
  7. channels
  8. capability registry
  9. firewall/actions
  10. approvals
  11. model runtime
  12. business agent
  13. coding harness
  14. Studio
  15. Solutions/SoftwareProject
  16. runtime plugins/runner
  17. frontend shell
  18. database/migrations
  19. CI/deployment
  20. observability/audit

- **10 scope identities**
  1. anonymous
  2. personal user
  3. workspace owner
  4. workspace manager
  5. workspace agent
  6. workspace employee
  7. external principal
  8. MCP client
  9. channel/Discord surface
  10. generated runtime principal

- **10 operation classes**
  1. discover
  2. read
  3. create
  4. update
  5. delete
  6. approve/reject
  7. execute external side effect
  8. retry/idempotency
  9. failover/recovery
  10. revoke/uninstall

- **5 lifecycle/concurrency states**
  1. normal
  2. duplicate/concurrent
  3. timeout/partial failure
  4. stale/revoked context
  5. restart/multi-replica

`20 × 10 × 10 × 5 = 10,000` audit cells.

Each cell should receive an ID such as:

```text
AUDITCELL-<subsystem>-<scope>-<operation>-<state>
```

Example:

```text
AUDITCELL-actions-owner-external_effect-duplicate
Expected: exactly one provider mutation and one durable VERIFIED/AMBIGUOUS action.
Current status: FAIL because DB idempotency is not unique and approval claim is not atomic.
```

This gives OPERLY the exhaustive **10,000-point bug-hunting framework** requested while preserving engineering integrity: only failing/reproduced cells become defect tickets.

---

# 8. Definition of “fixed” for this audit

Do not close a root-cause item merely because one reproduction stops failing. A root issue is fixed only when:

1. the canonical architecture owns the behavior;
2. the compatibility path cannot independently reintroduce it;
3. a regression test demonstrates the invariant;
4. concurrency/retry behavior is tested where relevant;
5. cross-workspace/personal scope is tested where relevant;
6. production deployment/runtime semantics are tested, not only SQLite/local behavior;
7. user-visible error/degraded state is deterministic;
8. observability can explain the decision without private chain-of-thought;
9. old code/path is deleted, quarantined, or placed on a dated removal ledger.

---

# 9. Highest-value next PRs

Suggested sequence:

1. **Exactly-once action boundary** — unique idempotency + atomic claim + ambiguous outcome + verifier timeout.
2. **Approval authorization repair** — explicit `actions:*` gates + unified workspace permission resolution + approver audit.
3. **Production migration gate** — remove startup migration, add Railway pre-deploy controlled migration, PostgreSQL CI.
4. **RBAC correctness** — make role revocation real; move plugin permissions out of closed hard-coded permission universe.
5. **Account/workspace split foundation** — tenant-optional account session + explicit workspace scope.
6. **Frontend source-of-truth decision** — make Vite/React canonical or remove it; stop carrying two complete frontends.
7. **Studio global/runtime convergence** — one entry point; intent-first creation; canonical Solution runtime metadata.
8. **Plugin uninstall/version semantics** — reversible lifecycle and version-aware capability/runtime registrations.
9. **Entity-scoped business context** — fix CompanyProfile/CompanyEvidence subject model.
10. **10,000-cell regression harness** — generate the matrix into parametrized integration/architecture tests and burn down every `FAIL`/`NOT_TESTED` cell by risk.

---

# 10. Audit conclusion

OPERLY's target architecture is directionally strong, but the repository is paying the cost of several architecture generations being live at once. The priority should not be to keep adding compatibility bridges. It should be to **collapse authority**:

- one account identity model;
- one workspace authorization model;
- one capability execution boundary;
- one plugin lifecycle;
- one project/Solution identity;
- one Studio/runtime selection contract;
- one frontend build;
- one database migration path;
- one source of truth per connector/context/resource.

Until that convergence happens, a fix in one layer can be invalidated by another still-live version of the same service. That is the root cause family most likely to produce the next thousands of bugs.
