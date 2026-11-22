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

Workspace messages use a separate signed service-only delegate via
`packages/agent_runtime/dragonzpyder_workspace.py`; it runs DragonZpyder
Deep Agents with NO workspace tools until the independently permissioned
Workspace MCP grant is delivered.

`Runtime1Agent.run` refuses to invoke Operly's old interactive model loop
when strict mode is enabled. No automatic return to legacy inference occurs
on destination error. Attachment-bearing chat fails closed until signed
artifact transport exists. The flags are not set in production.

## New Workspace env pairing (DO NOT ACTIVATE YET)

Operly:
- `OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL` = exact URL ending in
  `/internal/agents/operly/workspace/run`
- `OPERLY_DRAGONZPYDER_WORKSPACE_AGENT_TOKEN` = separate random 40+ char secret
- `OPERLY_DRAGONZPYDER_WORKSPACE_SERVICE_SECRET` = distinct random 40+ char proof

DragonZpyder API:
- `DRAGONZPYDER_OPERLY_WORKSPACE_AGENT_TOKEN` = same TOKEN through sealed
  server-only Railway reference
- `DRAGONZPYDER_OPERLY_WORKSPACE_SERVICE_SECRET` = same PROOF
- `DRAGONZPYDER_WORKSPACE_DELEGATION_ENABLED=1` only after integration tests
- Existing agent gateway, production-mode checks and budget controls must pass

No browser should obtain the service secrets. No shared Personal/Workspace
delegations are allowed. Guest principals are rejected until an independent
guest-scope contract has been reviewed.

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

**DO NOT turn on strict owner now:** Workspace MCP execution and artifact
transport are not yet integrated; the new Workspace delegate is intentionally
tool-free. In this state, enabling strict mode on existing business users
would break legitimate actions even though inference would route correctly.
