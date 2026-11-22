# Operly local-agent retirement: DragonZpyder owns all AI

## Immutable split

- **DragonZpyder** owns general AI execution, agent planning, model routing,
  LangGraph persistence, MCP broker, user Personal sessions and Hatchet AI goals.
- **Operly** remains the business service that owns tenant/workspace membership,
  business data, connector permissions, capability exposure, MCP OAuth, and
  human approvals. It must not grant these capabilities merely because the
  caller has a DragonZpyder personal identity.
- Operly's MCP server remains a supported optional provider. DragonZpyder must
  connect using an individual user's scoped OAuth grant and revalidate the
  workspace and provider tools before every execution.

## Implementation in this PR

`OPERLY_AI_EXECUTION_OWNER=dragonzpyder` (OFF by default) forces new
Personal agent messages into the signed DragonZpyder Personal delegate, for
**all** Personal users irrespective of the old UUID canary list.

Workspace messages use the existing session-bound, separately signed Workspace
MCP delegation in `packages/agent_runtime/dragonzpyder_workspace.py`.
Operly resolves current membership and selected tool permissions, issues a
short-lived Workspace-specific MCP token, and delegates to DragonZpyder's
existing `/internal/agents/operly/workspace/run`. Business tool calls still
pass through the Operly Kernel and approval checks. These capabilities are
**not** available to a user merely because DragonZpyder authenticated them.

`OPERLY_IDENTITY_OWNER=dragonzpyder` (OFF by default) blocks new local
Operly password/Google/Discord identities and local administrative account
bootstrap. DragonZpyder OIDC login remains the account entry path. Existing
`AppUser` rows remain local Workspace membership foreign-key projections,
linked to DragonZpyder issuer subject by an audited one-to-one mapping.

`Runtime1Agent.run` refuses to invoke Operly's old interactive model loop
when strict mode is enabled. No automatic return to legacy inference occurs
on destination error. Attachment-bearing chat fails closed until signed
artifact transport exists. The flags are not set in production.

## Existing signed Workspace configuration (DO NOT ACTIVATE YET)

Operly uses the already implemented `OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL`
endpoint and the existing `OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN` and
`OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET` service credentials. It also
requires `OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED=1` for issuing
current-membership Workspace MCP grants.

DragonZpyder accepts those same backend proofs using
`DRAGONZPYDER_OPERLY_PERSONAL_AGENT_TOKEN` and
`DRAGONZPYDER_OPERLY_MCP_SERVICE_SECRET` and independently enables the
Workspace path with `DRAGONZPYDER_OPERLY_WORKSPACE_HARNESS_ENABLED=1`.
The existing model gateway, budget and runtime gates apply. Do not use
invented alternate Workspace secret variable names.

Retirement further requires `OPERLY_AI_EXECUTION_OWNER=dragonzpyder`,
`OPERLY_PERSONAL_HARNESS_OWNER=dragonzpyder`,
`OPERLY_WORKSPACE_HARNESS_OWNER=dragonzpyder`, and separately
`OPERLY_IDENTITY_OWNER=dragonzpyder`, only after identity mappings,
artifacts, approvals, scheduled goals, and client paths are proven.

No browser may receive these service credentials. A guest principal never
inherits another member's Workspace grants.

## Proper retirement sequence

1. Complete DragonZpyder public identity migration (including verified
   signup, reset, Google/Discord and historic account mapping) and
   authenticated Personal chat and history.
2. Complete user/workspace OAuth MCP connection, provider revocation,
   per-tool approvals, and scheduled authority renewal.
3. Migrate every active conversation/message, file byte/checksum, memory,
   approval request, and scheduled AI objective with readback receipts.
4. Compare both real Workspace and Personal tool effects, idempotency,
   failures and user-scoped isolation across 2+ accounts.
5. Flip DragonZpyder-first Web/Android/Operly routing, check security and
   ability to rollback without dangling credentials or lost work.
6. Drain legacy active jobs and confirm no execution/usage traces originate
   from `Runtime1Agent`, Operly business-brain model services or old model
   schedulers, then remove those independent agent implementations and
   credentials in a separate deletion PR.

**DO NOT delete Operly's Kernel, Workspace auth, MCP server, database,
approval/audit system, or business endpoints.** They are not competing AI
harnesses; they are the business capabilities the single DragonZpyder harness
needs to call.

**DO NOT turn on strict owner now:** The Workspace MCP delegate exists and is tool-capable, but complete account migration, attachment transport, Discord channel authority, workflow execution, and end-to-end parity are still unverified. Enabling strict mode early can interrupt legitimate work.
