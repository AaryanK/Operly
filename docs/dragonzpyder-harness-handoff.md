# Operly as DragonZpyder's Workspace/Business wrapper

This branch begins replacing Operly's bespoke LLM planning loop with a client
for DragonZpyder's LangGraph/Deep Agents harness. Nothing has been deployed.

## Runtime ownership

- **DragonZpyder** decides how to reason, plan, delegate and continue agent jobs.
- **Operly** continues to own business/workspace data, membership, policy,
  governed capabilities, third-party credentials, approval, execution and audit.
- Operly's custom Runtime1 remains as an **opt-out rollback path** until
  DragonZpyder passes matching real-world functional and security tests.
- Operly's business workflow scheduler stays available as a domain capability,
  but its **reasoning engine** is expected to become DragonZpyder-owned.
- Operly Personal connectors and stored account state remain here only as
  a **transitional dependency**. Moving these requires user identity mapping,
  OAuth reauthorization, data migration and retention/deletion controls.

## First feature-flagged request path

`/api/personal-agent/chat` and `/api/agent/chat` keep their authenticated,
scope-checked entrypoints and conversation storage. With
`OPERLY_AGENT_HARNESS_OWNER=dragonzpyder`, they call the DragonZpyder service
instead of constructing `Runtime1Agent`. A signed, 240-second read-only lease
is created from the current Kernel's available capabilities. The service may
invoke only lease-listed, still-permitted **read-only** capabilities through
`/api/internal/agent-tools/search` and `/api/internal/agent-tools/execute`.

The broker never trusts model-authored user IDs/workspace IDs, and always
re-resolves the person, membership and capability. Any credentials stay inside
Operly. No automatic fallback after a remote failure (avoids duplicate work).
This is intentionally a restricted canary, not general-purpose autonomy.

## Required configuration

- `OPERLY_AGENT_HARNESS_OWNER=dragonzpyder`
- `OPERLY_DRAGONZPYDER_AGENT_URL=https://api.dragonzpyder.xyz/internal/agents/operly/run`
  or an approved Railway-private equivalent.
- `OPERLY_DRAGONZPYDER_AGENT_TOKEN` (40+ chars, matches DragonZpyder inbound token)
- `OPERLY_DRAGONZPYDER_AGENT_BROKER_ENABLED=1`
- `OPERLY_DRAGONZPYDER_BROKER_TOKEN` (40+ chars, matches DragonZpyder outbound token)
- `OPERLY_DRAGONZPYDER_DELEGATION_KEY` (40+ chars, never shared with DragonZpyder)

Keep everything off until staging validation and global model budget enforcement.
Never log/copy the tokens into user-visible traces or code.

## Not yet migrated

Personal OAuth/data/attachments/conversation history, workflow ownership,
scheduled personal goals, full external MCP lifecycle, non-read-only actions,
approval-resume and end-to-end independently verified outcomes.

The **Operly Kernel is not obsolete**: deleting it would remove the security
boundary between user intent and consequential real-world actions.


## Workflow-wrapper migration in this PR

- Generic workflow language and schedule/cron calculations have been moved
  to `DragonZpyder/api/agent_platform/workflows/spec.py`.
- DragonZpyder now also owns a bounded LangGraph read-only workflow runner,
  capability hub, and operator-reviewed MCP adapter.
- Operly's `WorkflowProvider._schedule_preview` calls DragonZpyder's
  authenticated `/internal/workflows/preview` when
  `OPERLY_WORKFLOW_HARNESS_OWNER=dragonzpyder`; default remains `operly`.
- `packages/workflow/dragonzpyder_client.py` is the authenticated wrapper;
  no browser credentials, session cookies, OAuth secrets, workspace database
  connections or arbitrary endpoint URLs are forwarded.
- DragonZpyder's `DRAGONZPYDER_WORKFLOW_HUB_ENABLED=1` flag is required;
  its service token must match `OPERLY_DRAGONZPYDER_AGENT_TOKEN`.
- All actual, legacy Operly workflow creation, due-time dispatch, event
  triggers, approvals and stored runs remain local until DragonZpyder's
  **durable definition registry + Hatchet wakeup + fresh run-time delegation**
  have passed end-to-end. Do not advertise a successful schedule after a
  mere preview. Do not delete workflow records or migrations.


## Wrapper context and persistent goals

Operly `/api/internal/agent-tools/search` now also returns
`source=operly`, `scope_kind` and a minimal current
`workspace_context` (name, timezone, membership role and workspace mode).
The context is resolved from Operly's live identity and membership tables
and is injected by DragonZpyder's harness, not by the browser. This response
is untrusted model **data**, never an authorization token. A revoked user
or removed Workspace member can no longer retrieve it.

`packages/workflow/dragonzpyder_goals.py` delegates `register`, `get`
and `cancel` to DragonZpyder's service-only encrypted PostgreSQL goal store,
using the authenticated Operly `ExecutionContext`. The adapter has no
local agent planner, queue or cron. For now this is only an internal API
integration: registering a goal does not schedule or execute it.
Pending Hatchet wakeups and renewed authorization, do not replace Operly's
working workflow creation with this partial implementation or remove its
business data models.

**Destination architecture:** Operly remains an OS-like business/workspace
provider; DragonZpyder owns the reusable intelligence, Personal connectors,
MCP hub, personal computer broker, memory, schedules, jobs and workflows.
NaySchool can be another app wrapper with its own independently authenticated
learner context, never borrowed Operly authority.
