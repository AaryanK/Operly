# Operly — repository instructions

Read [the DragonZpyder platform boundary](docs/dragonzpyder-platform-boundary.md) and [canonical universal harness contract](https://github.com/AaryanK/DragonZpyder/blob/develop/docs/universal-harness-v4.md) before changing AI, identity, integrations, MCP, workflows or sandbox execution.

- Operly is the governed business execution application. DragonZpyder is the shared intelligence platform and owns the general-purpose agent harness, model routing, Personal identity/state and native computer execution target. Do not introduce a parallel intelligence stack.
- Retain Operly's Kernel, business tenancy, membership, policies, connectors, approvals, audit, idempotency and application-specific workflows. A DragonZpyder login or model assertion is not business authorization.
- Route intelligence through scoped DragonZpyder interfaces, including Discord and other channels; expose only permitted capabilities through the existing Kernel/MCP. Revalidate live scope, grants, approvals and schemas at invocation and resume.
- Keep untrusted execution outside the control plane; do not fake workspace identities, pass secrets to generated code or silently enable arbitrary shell/network access.
- Preserve compatibility until audited cutover; retire redundant AI services only after verified traffic, dependency, rollback and permission checks.
- Verify actual source, CI, deployment SHA and real outcome receipts. Document limitations, migration status and tests. No docs-only change authorizes production rollout or spending.
