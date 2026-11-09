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
