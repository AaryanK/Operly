# Operly → DragonZpyder scoped inference (staged, no production cutover)

This change gives Operly its own first-party app credential and a dedicated
**chat-only** inference path at DragonZpyder's existing central provider gateway.
It does **not** move Operly's business Kernel, tenant data, capability execution,
MCP grants, workflow approvals, or agent reasoning loop to the gateway.

## Boundary

- Operly resolves user/workspace authority, assembles its bounded agent request,
  and remains the only executor of business capabilities via Operly Kernel.
- The DragonZpyder gateway authenticates the dedicated `operly` app credential,
  restricts its provider operations to POST chat completions, forwards inference
  to OpenRouter, and attributes its usage and traces to `operly`.
- The gateway must never interpret the app token as an Operly user's authorization.
  Never send provider keys, workspace permission snapshots, or business secrets.

## Staged deployment (do not enable until gateway branch is deployed and tested)

1. On DragonZpyder API, add a randomly generated >=32-character credential
   under `"operly"` in `DRAGONZPYDER_APP_KEYS_JSON`. Retain existing keys.
2. Verify the DragonZpyder gateway deploy supports `operly` on
   `/internal/provider/chat/completions` and rejects `operly` on image,
   speech, video, and catalogue routes. Confirm its model and accounting
   configuration is ready.
3. On Operly API, set:
   - `OPERLY_AGENT_MODEL_PROVIDER=dragonzpyder`
   - `DRAGONZPYDER_OPERLY_GATEWAY_TOKEN=<matching operly secret>`
   - `DRAGONZPYDER_INFERENCE_BASE_URL=http://dragonzpyder-api.railway.internal/internal/provider`
   - `OPERLY_AGENT_MODEL_ID=<model allowed by the upstream route>`
4. Canary with a no-tool answer, a read-only capability, and an approval-required
   mutation. Verify that only Operly's Kernel authorizes and executes actions.
   Verify one central gateway trace/usage event per inference call.
5. Keep `OPERLY_AGENT_RUNTIME_ENABLED` under the existing deployment controls;
   do not enable the runtime solely because inference routing works.

The dedicated gateway credential and scoped path must be changed **together**.
The client rejects a dedicated credential pointed at legacy `/internal/v1`,
as well as unapproved gateway origins.

## Rollback

Unset the dedicated gateway credential and restore the earlier
`DRAGONZPYDER_INFERENCE_BASE_URL` and `OPERLY_AGENT_MODEL_PROVIDER`
configuration. Legacy `DRAGONZPYDER_RUNTIME_TOKEN` remains supported for
the old inference-only path during the migration. Revoking the dedicated
credential must not revoke another product's app key.

## Future, separate migration

A full agent-runtime extraction is **not** performed here. Compare LangGraph
with Operly's `Runtime1Agent` in isolated tests; keep the Kernel as the
authoritative business capability executor. Design signed short-lived
delegation and resumable execution before moving generic planning or durable
agents across services. Keep raw business data out of cross-app traces.

## Checks

```sh
python -m unittest tests.test_dragonzpyder_inference_provider -v
```
