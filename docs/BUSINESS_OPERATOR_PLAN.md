# Operly business operator implementation plan

Status: proposed execution sequence, prepared from the deployed code and production traces on 2026-09-07.
Baseline: `main` at `37f78bab3c7590516df799c9a915062fdb3d6396`.
Working branch: `codex/business-operator-foundation`.

This plan records work to do. It does not claim these improvements have shipped.

## Product goal

Give an owner one place to understand the business, delegate work, see what happened, and intervene where their judgment is needed. Personal AI belongs to the person. Business work belongs to its authorized workspace. The same person can manage several businesses while private information and each business's data retain their existing boundaries.

The operating loop is:

1. Observe relevant business state and events.
2. Identify an actionable problem or opportunity.
3. Select an operation under the owner's existing permissions and operating rules.
4. Execute through the existing Kernel and capability providers.
5. Verify the result against the requested outcome.
6. Record what happened, continue durable work when necessary, and surface unresolved exceptions.

An online business needs dependable customer communication, lead handling, fulfillment coordination, invoices, content, software, and reporting. A physical store also needs reliable signals from stock, sales, suppliers, and staff. Staff or integrated store systems perform and confirm physical actions; Operly coordinates that work and keeps the remote owner informed. An internal record update must never be presented as proof that a payment settled, a parcel shipped, or a physical stock count changed.

The original brief's human-control principle remains: ordinary authorized work can proceed automatically; consequential actions follow the owner's explicit policy and approval boundaries.

## Preserve and extend the current architecture

| Responsibility | Existing owner to reuse |
| --- | --- |
| Human identity, Personal/Workspace scope, delegation | `packages/security` |
| Capability contracts, discovery, authorization, approval, idempotency, execution and audit | `packages/kernel` |
| Interactive objective interpretation and tool reasoning | `packages/agent_runtime` |
| Durable workflows, schedules, waits and semantic event dispatch | `packages/workflow` |
| Shared Personal and Workspace conversation storage | `packages/database/agent_chat_models.py` and `apps/api/agent_runtime_router.py` |
| Workspace records, attention signals and business transactions | `packages/workspace_modules/tools` |
| Account-owned and Workspace integrations | `packages/personal_modules`, `packages/connectors`, `packages/workspace_modules/integrations` |
| Installed capabilities, jobs, webhooks, credentials and runtime bindings | `packages/plugins` |
| Files, isolated computer tools and publishing | `packages/artifacts`, `packages/workspace_modules/agent_computer`, `packages/workspace_modules/studio` |
| Owner interface | Existing Personal, Workspace, Workflow, Capabilities and Activity React surfaces |

Before adding a capability, inspect the effective registry and its existing provider implementation. Improve a contract or implementation when it already covers the requested operation. A new tool needs a demonstrated capability gap, an existing package owner, scoped inputs, an outcome contract, and a focused regression. Business workflow configurations must not become phrase-specific routing branches in the generic agent.

The inference work already proposed in PR #327 and issue #318 must be reconciled with current main and reused where appropriate. Do not create a competing inference implementation.

## Evidence driving the order

- The current deployment serves Agent Runtime 1.0 using Groq's `openai/gpt-oss-120b`.
- A production Personal Gmail request on 2026-09-07 correctly selected `google.gmail.search`, received `runtime_unavailable`, exhausted useful discovery, and returned HTTP 500 after accessing expired ORM state in the chat router (`MissingGreenlet`).
- The actual reason behind `runtime_unavailable` still needs diagnosis. Do not assume it is an OAuth, provider, or model problem from that label alone.
- Workflow execution already preserves scalar lineage IDs across Kernel rollback. The chat path must respect the same transaction boundary.
- Web chat reads up to 18 recent messages and applies bounded context selection. Discord's live ingress currently passes `context_items=()`.
- Durable agent run/step storage and `DurableAgentOrchestrator` exist. The orchestrator is not yet connected to a production agent worker.
- Workflow schedules and event dispatch are booted; durable Workflow approvals have a dedicated regression gate. They must be reused.
- Personal chat returns an approval ID but lacks a complete user-facing checkpoint/resumption flow.
- Business tools include attention, customer snapshots, sales completion, invoices, payment recording, stock operations, and broad record CRUD. Their existence does not prove a complete live business process.
- The broad capability matrix substitutes provider implementations. It is useful contract coverage, not evidence of third-party service correctness.
- Several README/architecture claims predate the live runtime. Update those claims as the corresponding work is verified; keep implemented, tested, deployed and proposed states distinct.

## First two work blocks

Treat approximately 25 minutes as a work budget. Finish a coherent, reviewable slice; carry unfinished work forward explicitly rather than expanding the scope to fill a label.

### Block 1: Reliable execution and failure handling

Business purpose: a request should either complete, report the actual blocker, or pause for a clear decision. It must not disappear into a server error.

Inspect and improve:
- `apps/api/agent_runtime_router.py`
- `packages/agent_runtime/interactive.py`
- `packages/agent_runtime/runtime.py`
- `packages/kernel/runtime_availability.py`
- the existing Personal Google provider and connector code implicated by the trace

Work:
1. Reproduce the recorded failure with a controlled provider failure and the real chat/Kernel transaction boundary.
2. Diagnose the original `runtime_unavailable` cause separately from the secondary ORM crash.
3. Preserve scalar conversation/message identities before Kernel execution and make subsequent database reads explicit and asynchronous.
4. Preserve a recoverable conversation and an accurate user-facing error even when a capability rolls back its transaction.
5. Stop redundant discovery when the same unavailable operation cannot make progress. Preserve useful alternatives and bounded retry of genuinely retryable failures.
6. Cover affected Personal/Workspace web flows and the shared Discord runtime behavior.

Acceptance:
- A failed capability cannot cause the observed expired-ORM HTTP 500.
- The user sees a specific, safe blocker and can continue the conversation.
- The recorded Gmail case either completes or reports a diagnosed blocker without repetitive tool discovery.
- Failed or uncertain mutations are never replayed with a newly invented execution identity.
- A focused transaction regression exercises rollback, not only mocked final model output.
- No automatic bulk live-model benchmarks are introduced.

Checkpoint: tested fix and exact remaining integration issue, if any. Do not claim live Gmail success unless a permitted live check actually demonstrates it.

### Block 2: A useful owner attention view

Business purpose: the owner can ask "What needs my attention today?" and act from the answer.

Inspect and reuse:
- `workspace.attention.list`, `workspace.search`, `workspace.customer.snapshot`
- existing overdue invoice, inventory, lead, support, task and appointment records
- the current Workspace home/assistant, Activity and Workflow interfaces

Work:
1. Verify which attention signals already exist and that their permissions, timestamps and destinations are accurate.
2. Present a compact, source-linked view of overdue invoices, low stock, pending follow-ups and other existing actionable signals.
3. Distinguish observed facts, suggested actions, and completed work.
4. Connect each item to an existing record, tool or approval destination. Repair broken links and missing result/error states along the route.
5. Provide a conversational explanation using the current agent when available; keep the underlying attention data inspectable.

Acceptance:
- A user can identify an issue, inspect its underlying record, and reach an existing authorized action.
- Values come from workspace records and preserve role/module visibility.
- No fabricated urgency, savings, revenue or completed actions.
- A narrow UI/API regression follows the actual attention-to-record/action chain.

Checkpoint: a usable owner view or one completed attention/action path, with a reproducible user test. Completing a new general-purpose dashboard engine is outside this block.

## Subsequent bounded slices

### 3. Shared conversation continuity

Use existing scoped conversation/message tables and context assembly. Persist and retrieve Discord history with the correct owner, workspace, channel, audience and principal checks. Personal web and Discord may share appropriate account-owned facts; public/server conversations must not inherit private history.

Acceptance: relevant follow-up references survive a later turn and process restart, while cross-account, cross-workspace and private-to-public context leakage are denied. Keep context bounded.

### 4. Complete approval and continuation

Extend the existing Kernel approval lifecycle, durable agent run/step storage, and existing React/Discord interfaces. Persist the exact paused action and present approve/deny controls. Resume the same logical operation with its original request identity and fresh authority. Do not add a separate approval service.

Acceptance: approve, deny, expiry, refresh, restart, permission revocation and duplicate clicks have explicit behavior. A paused multi-step request continues without replaying previous mutations.

### 5. Persistent personal and business memory

After continuity works, define the minimum canonical memory contract: scoped fact/preference/policy, source, author, timestamps, correction/supersession state, and retrieval relevance. Reuse current identity, data and artifact storage. Inspect retained schema and existing memory-related records before adding storage.

Begin with explicit user-provided facts and corrections. Model-proposed memories require validation and scope assignment. Memory can inform reasoning; it cannot grant permissions or override policy.

Acceptance: a fact can be stored, retrieved in the right scope, corrected and forgotten, with provenance. Switching inference models preserves the same application-owned memory. Unrelated memories do not inflate every prompt.

### 6. Durable goals and event-driven agent work

Connect the existing durable agent orchestrator to an appropriately bounded worker and ingress path. Workflows remain the durable schedule/wait/dependency mechanism. Agent reasoning may select or propose the next operation; Kernel remains the executor.

Implement one demonstrated goal continuation before broadening: event arrives, authorized run wakes, retrieves fresh context, acts or requests approval, verifies a postcondition, and records its outcome.

Acceptance: restart, cancellation, permission change, provider failure and uncertain external effect have tested behavior. Inference attempts, elapsed time, output, actions and spending have enforceable applicable budgets. Resolve/reuse PR #327 rather than duplicating its work.

### 7. One complete business pilot

Use ANHITRA/AHT's inquiry and quotation workflow as the first candidate from the original brief. Confirm the actual connected channels, business rules and available data before enabling outbound effects.

Proposed process:
- capture an inquiry through an existing connected channel;
- identify the customer and missing requirements;
- update the existing lead/customer record;
- prepare a quotation from approved business data;
- route the proposed response for approval where required;
- schedule a follow-up;
- track invoice/payment status using trustworthy sources;
- report unresolved exceptions and completed work to the owner.

A quotation must not invent availability, prices or commitments. If a necessary external operation has no capability, add the narrow missing integration through the existing plugin/provider boundary.

Acceptance: repeat the process with real permitted inputs and integration evidence, including missing data, duplicate requests, provider failure, customer changes and human intervention.

### 8. Expand by proven business outcomes

| Business type | Useful next process | Existing foundation |
| --- | --- | --- |
| Digital/service business | Inquiry to follow-up; appointments; overdue invoice reminders | CRM, Gmail/Calendar, tasks, invoices, workflows |
| Online store | Customer support, order exceptions and fulfillment status | Customer/order records, support, integrations, events |
| Physical shop | Low-stock review, supplier order proposal, staff stock confirmation | Inventory, procurement, tasks, permissions |
| Multi-location owner | Location-scoped attention and accountable staff assignments | Workspace scopes, roles, activity, workflow history |
| Digital presence | Content proposal, approved publishing and observed results | Canva/authoring, Studio, Agent Computer, artifacts |

Store systems, payment processors and fulfillment services must be connected before their state is treated as observed. Choose additions from the pilot's demonstrated needs rather than adding every possible connector.

## Definition of operational progress

For each supported business job, capture:
- eligible requests and verified completions;
- runtime/provider failures and recoveries;
- unresolved or uncertain outcomes;
- human interventions and approval waits;
- time to completion;
- inference/tool cost where measurable;
- duplicate or unauthorized effects;
- the business-specific result, such as a qualified lead, acknowledged follow-up, confirmed stock change or reconciled invoice.

A model saying "done", a successful tool HTTP response, a schema-valid record, and a verified business outcome are distinct observations. The interface should show the strongest evidence actually obtained.

## Working practice

- Use small commits on the working branch and open reviewable PRs for coherent slices.
- Preserve existing work and reconcile overlapping PRs before implementing the same concern.
- Trace each change through ingress, runtime, provider/database, result, UI and tests.
- Keep focused deterministic regressions. Use explicit, bounded live checks only where appropriate to the operation and existing authorization.
- Record each checkpoint: commit, changed behavior, verification, unresolved risks and the next concrete task.
- Deliver the first business benefit before attempting a universal autonomous company.
