# Operly local AI runtime retirement — gated execution cutover

**Architectural owner:** DragonZpyder Deep Agents / LangGraph / Hatchet.
**Business authority:** Operly Kernel, workspace memberships, MCP, data,
workflow records, human approvals and idempotency.

### Current functional behavior
- Personal plain-text chat can delegate to DragonZpyder via the existing
  session-bound Personal MCP token. Uploaded attachments remain local until
  signed artifact parity is proven.
- Workspace plain-text chat can delegate through the new exact private
  `/internal/agents/operly/workspace/run` endpoint, protected by the
  existing independently configured service identities and short-lived
  session+workspace-scoped MCP delegation. No browser credentials, roles,
  static resource grants or provider OAuth tokens go to the model.
- Every MCP call rechecks the live session, membership, current capability,
  approval and Kernel authority. Failed or ambiguous results do not fall back
  to another agent, which could duplicate external mutations.
- The existing Operly conversation and artifact tables remain readable,
  and support rollback. Attachments do not silently downgrade or reroute.

### Activation controls
Default local runtime is retained until parity testing:
`OPERLY_WORKSPACE_HARNESS_OWNER=dragonzpyder` +
`OPERLY_DRAGONZPYDER_WORKSPACE_CANARY_USER_IDS=<tested user IDs>`.
All-users requires both
`OPERLY_WORKSPACE_DRAGONZPYDER_ALL_USERS_ENABLED=1` and
`OPERLY_WORKSPACE_DRAGONZPYDER_ALL_USERS_APPROVED=1`.
Separately enable
`OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED=1`,
`OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL=https://api.dragonzpyder.xyz/internal/agents/operly/workspace/run`.
Mutation authority is independently gated by
`OPERLY_DRAGONZPYDER_WORKSPACE_MCP_MUTATIONS_ENABLED=1`; human approvals
still remain Operly's responsibility. Signed transport uses existing
`OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN` and
`OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET`. The matching private
DragonZpyder service variables must be set and verified.

After confirmed replacement across Personal and Workspace, set
`OPERLY_RETIRE_LOCAL_AGENT_EXECUTION=1` to remove the old
Runtime1Agent fallback from all chat routes. This switch is fail-closed.
DO NOT activate until attachment, Workspace guest-scope, recovery,
scheduled job and conversation-history parity have been proven with a
real browser/account.

This change does not delete the legacy DB, login credentials, workflow
engine, artifact data or approval history. Those are business/application
records, not a duplicate model runtime. Legacy source code can be deleted
only when the production parity suite and full API route inventory pass.
