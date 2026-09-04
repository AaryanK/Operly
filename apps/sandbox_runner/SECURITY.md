# Sandbox Runner security boundary

The Sandbox Runner authenticates control-plane requests with a Bearer credential plus a separate HMAC signing key. Signed requests include a timestamp and random nonce; the Runner rejects stale timestamps and repeated nonces within the replay window.

## Replica constraint

The current nonce replay cache is intentionally process-local. **Run exactly one Sandbox Runner service replica while this implementation is in use.** A captured, still-fresh signed request routed to a different Runner process would not be present in that second process's nonce cache.

Before horizontally scaling the Runner, replace the local replay cache with one of the following equivalent cluster-wide boundaries:

- an atomic shared nonce/request-ID store with expiry (for example Redis `SET NX EX`), or
- a durable request-idempotency layer that atomically claims every signed Runner mutation across replicas before the sandbox side effect.

The shared claim must cover session creation, session destruction, and native tool execution, not only tool calls. It must expire after a bounded period and fail closed if the shared replay store is unavailable.

This constraint does not change the per-session Sandbox isolation model: agent code still runs inside a Railway Sandbox VM and does not receive Operly control-plane credentials.
