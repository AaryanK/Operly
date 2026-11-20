# Operly remote MCP — multi-client rollout plan (staging first)

Operly already exposes an MCP JSON-RPC resource at **`POST /mcp`** and
authorization discovery at `/.well-known/oauth-protected-resource/mcp` and
`/.well-known/oauth-authorization-server`. It projects existing authorized
**Workspace** Kernel capabilities through `McpGateway`; it is NOT the same as
the private DragonZpyder broker at `/api/internal/agent-tools/*`.

This change adds explicit **pre-registered OAuth clients**, not anonymous
access, automatic credential sharing, arbitrary remote client registration, or
personal-conversation access. The existing ChatGPT OAuth configuration remains
backward compatible.

## One MCP resource, independent client grants

Clients: ChatGPT, Codex, Claude, DragonZpyder, or any operator-pre-registered
agent. Each client has its own `client_id`, exact redirect allowlist, optional
secret, one-time S256 PKCE code, OAuth access/refresh tokens, and independently
revocable **workspace-owner** consent record. Tokens are resource-bound to
the configured Operly `/mcp` URL.

The operator sets `OPERLY_MCP_CLIENTS_JSON` to a JSON object (NOT a JSON
list). For example:

```json
{
  "claude": {
    "name": "Claude",
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]
  },
  "codex": {
    "name": "Codex",
    "redirect_uris": ["https://YOUR-VERIFIED-CODEX-CALLBACK.example/callback"]
  },
  "dragonzpyder": {
    "name": "DragonZpyder",
    "redirect_uris": ["https://YOUR-DRAGONZPYDER-OAUTH-CALLBACK.example/callback"]
  }
}
```

**Replace the illustrative Codex/DragonZpyder callback URLs** with the exact
callbacks actually configured by those OAuth clients. Do not deploy placeholders
or guess client callback endpoints. A locally running Codex client can instead
use an explicitly approved `http://127.0.0.1:<port>/callback`; ports are exact,
not wildcarded. Claude's callback is documented by Anthropic, but verify it
against the client used in your environment.

For a confidential DragonZpyder OAuth client, provision the separate
`OPERLY_MCP_DRAGONZPYDER_SECRET` securely via Railway, not source code.
An unset secret disables that confidential client. Public clients must use
PKCE S256 and an exact registered callback. Keep existing
`CHATGPT_MCP_REDIRECT_URIS` and optional `CHATGPT_MCP_CLIENT_SECRET`
for the legacy ChatGPT client.

`PUBLIC_BASE_URL` must identify the **actual externally reachable HTTPS
origin** used to host Operly's OAuth issuer and MCP resource. Changing this
origin affects token audiences; plan a reauthorization rather than sharing
tokens across domains.

### Enable a client for one workspace

1. A workspace owner logs into Operly and calls
   `GET /api/access/external-clients` to see configured clients.
2. The owner inspects `GET /api/access/mcp-catalog` and uses
   `PUT /api/access/tool-exposure` to hide capabilities not intended for
   external AI access.
3. The owner explicitly creates a *narrow* client grant using
   `POST /api/access/client-grants` with the exact `client_id` and selected
   scopes. Avoid `workspace:*` for initial testing.
4. The MCP client discovers the protected resource metadata, uses PKCE S256
   authorization code OAuth, and sends a bearer token on every MCP HTTP request.
5. Operly rechecks principal, workspace membership, live capability availability,
   scope narrowing, and approval policy at tool-call time.
6. The owner can revoke the grant with `DELETE /api/access/client-grants/{grant_id}`.
   Do not reuse one client's access/refresh tokens in another client.

## Client connection

After a verified HTTPS deployment, use the **same** MCP resource URL:
`https://<VERIFIED_OPERLY_MCP_ORIGIN>/mcp`.

- ChatGPT: add a custom remote MCP server/plugin where the current plan and
  workspace support it. Choose OAuth and authorize the named workspace.
- Codex: configure a remote streamable-HTTP MCP server with that URL, then
  authenticate using the registered OAuth client credentials/callback.
- Claude or Claude Code: add a custom remote HTTP MCP connector using that URL
  and authorize the separate Claude grant.
- DragonZpyder: use existing `langchain-mcp-adapters`/MCP client support with
  **per-run delegated user authority** and secure server-side token storage.
  Never put OAuth bearer or refresh tokens inside LLM messages, LangGraph
  checkpoints, or generated code. Its existing internal Operly broker path can
  remain as an independent read-only integration during migration.

Existing workspace MCP exposure does not imply access to Personal sessions,
conversation history, private memory, or user models. Those would need
separate explicit user grants and resource/visibility policy.

## Safe launch checklist

- [ ] Confirm the public HTTPS hostname, `PUBLIC_BASE_URL`, ingress routing,
      issuer metadata, and `/mcp` are consistent.
- [ ] Set the exact callback URI for each real OAuth client. Confirm no wildcard
      redirects, no unpublished placeholder callbacks, and no token in a URL.
- [ ] Set distinct signing secrets and `OPERLY_MCP_CLIENTS_JSON` on **staging**.
- [ ] Run `python -m unittest tests.test_mcp_multiclient -v` plus existing
      `tests.test_mcp_request_safety` and MCP gateway/security regressions.
- [ ] Run official MCP SDK/Inspector conformance tests on the deployed server
      including `tools/list`, `tools/call`, protocol version negotiation,
      JSON responses, auth challenges, and client registration/PKCE.
- [ ] Test each client in isolation; verify one workspace cannot read another,
      revoked membership immediately disables execution, and mutations require
      appropriate owner approval.
- [ ] Start with read-only scopes and one isolated demo workspace. Review
      rate limits, audit trails, payload bounds, concurrent calls and DDoS.
- [ ] Promote to production only after all authenticated integration tests and
      client-specific OAuth flows pass. No silent fallback to global credentials.

## Compatibility limits

This revision uses *explicit pre-registration*, which is allowed by the
2026-07-28 MCP authorization specification. It does **not** yet implement
Client ID Metadata Documents (CIMD) or Dynamic Client Registration. Clients
that mandate one of those registration mechanisms need additional work.
The existing MCP router is a custom JSON-RPC implementation and should be
interoperability-tested against official SDK clients before public launch.

The starting point is the existing `/mcp` endpoint; **do not deploy a duplicate
MCP server in front of the same Kernel**.
