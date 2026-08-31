# Operly implementation status

Updated for the deterministic Workspace tool fabric built over the shared governed execution substrate.

## Architectural rule

If an important action should eventually be available to an AI agent, first make it a deterministic Operly tool that a human, frontend, API client, workflow, SDK, or other interface can execute without AI.

Workspace business logic belongs to the Workspace package. The generic `packages/kernel` package is only shared execution infrastructure; it must not become a second Workspace OS package.

## Package ownership

### Shared execution substrate — `packages/kernel`

Owns only cross-scope mechanics:

- trusted ingress/context resolution;
- capability contracts and registry primitives;
- deterministic input/output validation;
- `ALLOW / ASK / DENY` policy;
- provider registry abstraction;
- approval binding;
- request idempotency;
- minimum-context loading;
- 13-stage governed execution;
- transaction/rollback coordination;
- trace and event persistence;
- Personal/platform primitives.

It does **not** own Workspace CRM/ERP/provider implementations, Workspace HTTP endpoints, Workspace module administration, or Workspace connector actions.

### Workspace OS tools — `packages/workspace_modules/tools`

This package owns the Workspace deterministic tool surface:

- `system.py` — workspace identity/module visibility;
- `records.py` — generated Workspace OS record list/create/update/delete tools;
- `controls.py` — settings, presets, modules, members, roles, invitations, inventory controls;
- `business.py` — search, attention, customer snapshot, sales/invoice/payment business outcomes;
- `google.py` — Gmail and Calendar tools over durable Workspace Google connections;
- `availability.py` — module/provider/OAuth availability preflight;
- `runtime.py` — Workspace composition root over the shared execution substrate;
- `router.py` — authenticated Workspace tool HTTP API.

The old `packages/kernel/workspace_*_provider.py` and `packages/kernel/provider_availability.py` files are removed.

## Workspace tool API

Workspace tools have a first-class API boundary:

- `GET /api/workspace-tools` — current authorized + actually available tools;
- `GET /api/workspace-tools/{capability_id}` — one tool contract;
- `POST /api/workspace-tools/{capability_id}/execute` — execute that exact tool;
- `GET /api/workspace-tools/approvals`;
- `POST /api/workspace-tools/approvals/{approval_id}/decision`;
- `GET /api/workspace-tools/events`;
- `GET /api/workspace-tools/runs/{run_id}`.

Discovery returns the exact HTTP method and endpoint for each tool. Execution re-resolves trusted Workspace authority and provider availability before entering policy/approval/idempotency/execution/validation/trace/event handling.

The generic `/api/kernel` router is now Personal-only. Workspace frontend code does not call `/api/kernel/capabilities` or `/api/kernel/execute`.

## Workspace UI

The Workspace navigation includes **Capabilities** under Extend.

`CapabilitiesPage` is an E2E client of the Workspace tool API. It:

- loads `/api/workspace-tools`;
- shows only current permission-authorized and provider-available tools;
- displays the real endpoint for every tool;
- displays provider, permissions, risk, approval policy, resource scope, reversibility, and input/output contracts;
- invokes the backend-advertised `selected.endpoint` directly;
- uses stable request IDs for idempotent retries;
- handles exact-invocation approval and resume through `/workspace-tools/approvals/...`;
- displays validated results and execution trace.

So the operational path is:

**Workspace frontend → exact `/api/workspace-tools/{id}/execute` endpoint → trusted Workspace context → availability → deterministic authorization/approval/idempotency → Workspace provider → database/external provider → output validation → trace/events → frontend result.**

There is no separate AI-only tool registry or Workspace-only Kernel runtime implementation.

## Deterministic Workspace tool families currently implemented

### Workspace records

The mature Workspace OS entity registry is projected into more than 100 standardized list/create/update/delete tool contracts across CRM, catalog, inventory, sales, finance, procurement, fulfillment, projects, operations, support, scheduling, tasks, team, documents, marketing, compliance, research, grants, and integrations.

Generated contracts reuse real field types, required fields, select options, permissions, references, mutability rules, and business mutation hooks. Destructive record operations are approval-gated.

### Workspace controls

Includes operating summary/activity, settings, module state, presets, members, role/permission administration, invitations, inventory movements and stock adjustments. Access-changing operations preserve ownership/role invariants and are approval-gated.

### Business outcomes

Includes `workspace.search`, `workspace.attention.list`, `workspace.customer.snapshot`, `workspace.sales.complete`, `workspace.finance.invoice.create_simple`, and `workspace.finance.payment.record`.

Outcome tools deliberately encapsulate multi-row business transactions so a future planner does not need to coordinate fragile low-level mutations.

### Google

Includes Workspace Google connection status, Gmail search/read/draft/send/label modification and Calendar list/freebusy/create/update/delete. OAuth authority is resolved from durable Workspace connector state. External writes are approval-gated where appropriate.

## Reliability infrastructure

Implemented:

- fresh authority resolution;
- permission + surface filtering;
- provider/module/OAuth availability preflight;
- deterministic input/output contracts;
- exact-invocation approval;
- scoped request idempotency;
- database transaction rollback on failure;
- durable run/step/event provenance.

External providers cannot yet be truthfully claimed as exactly-once across a process crash. External-effect reconciliation/outbox infrastructure remains a hardening item.

## Tests / regression contracts

Python contract tests cover the shared execution substrate and Workspace-owned tool contracts.

`apps/web/scripts/workspace-tools-contract.mjs` guards the frontend-to-endpoint architecture: it fails if the Workspace UI falls back to generic Kernel routes, if Workspace provider code returns to `packages/kernel`, if the Workspace API/router leaves `workspace_modules`, or if the frontend stops invoking backend-advertised per-tool endpoints.

This is a structural E2E contract. The repository does not currently include Playwright/browser E2E infrastructure, so browser automation is not claimed here.

## Still deliberately offline / incomplete

- model inference and AI planning/agent loop;
- MCP execution surface;
- Discord/Slack/WhatsApp agent loops;
- Studio/generated-app execution bindings;
- durable workflow consumers over `kernel_events`;
- files/artifacts/knowledge tools on this Workspace boundary;
- Canva and additional connector providers;
- external-effect reconciliation/outbox;
- quotas/rate limits, approval expiry/cleanup, dead letters and operational observability.

AI should be added only after these deterministic surfaces are sufficiently complete. AI may plan/select Workspace tools, but it must not own authority, approvals, resource access, execution, validation, idempotency, transactions, or provenance.
