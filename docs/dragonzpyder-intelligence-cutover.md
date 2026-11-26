# Operly → DragonZpyder intelligence cutover

DragonZpyder owns every mounted Personal/Workspace chat's model execution,
agent planning and checkpoint. Operly owns Workspace membership, role policy,
business records, MCP authorization, idempotency, approvals, artifacts, and
deterministic business workflow operations.

The Operly API requires the existing DRAGONZPYDER Personal/Workspace first-party
endpoints and shared backend token/proof credentials. No browser secret is
forwarded. The bridge mints short-lived, scoped MCP tokens only after live
session/Workspace authorization and per-tool approval. An upstream failure
fails closed: **no local LLM retry**, which prevents duplicate mutations.

Deployment requirements (already supported by both codebases):
- OPERLY_DRAGONZPYDER_PERSONAL_RUN_URL
- OPERLY_DRAGONZPYDER_WORKSPACE_RUN_URL
- OPERLY_DRAGONZPYDER_PERSONAL_AGENT_TOKEN
- OPERLY_DRAGONZPYDER_MCP_SERVICE_SECRET
- OPERLY_DRAGONZPYDER_WORKSPACE_MCP_ENABLED=1
- DRAGONZPYDER_PERSONAL_MCP_HARNESS_ENABLED=1 (DragonZpyder)
- DRAGONZPYDER_OPERLY_WORKSPACE_HARNESS_ENABLED=1 (DragonZpyder)
- DragonZpyder shared backend credentials, checkpoint storage, MCP and gateway.

Old canary/owner flags cannot revive local Operly intelligence through chat.
The historical independent Runtime1 code/tests are still present for
dependency audit, but no mounted API chat request invokes them.
Physical deletion of the legacy modules requires cleaning their dedicated
tests/workflows without deleting Workspace permission or business runtime.

**Not yet live-verified:** remote attachment-handle resolution, Discord AI
ingress, old queued work, and cross-account approval/revocation scenarios.
Do not deploy to production until service flags/identity migration and
representative tests are confirmed on the current deployment.
