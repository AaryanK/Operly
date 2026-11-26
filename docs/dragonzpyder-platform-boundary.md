# Operly and DragonZpyder — platform boundary

Status: accepted target architecture, 2026-11-25; not proof of deployed cutover.

[DragonZpyder's universal harness contract](https://github.com/AaryanK/DragonZpyder/blob/develop/docs/universal-harness-v4.md) is the canonical intelligence architecture. Operly is a governed business execution application powered by DragonZpyder, not a competing AI harness.

## Responsibilities

DragonZpyder owns shared agent reasoning, model routing, media, native computer execution, Personal identity/conversations/files/memory, generic durable agent objectives, and tool discovery/orchestration. Web, mobile, voice, and external clients should use one shared agent engine (Deep Agents + LangGraph). Operly owns business tenants, workspace membership, data, policy, approvals, connectors, application-specific workflows, Kernel capability execution, audit and idempotency. A signed-in DragonZpyder user does not automatically have business authority.

Operly's Kernel remains authoritative for workspace effects. A DragonZpyder agent discovers authorized MCP capabilities, invokes scoped delegates, and receives verifiable receipts. Operly must independently revalidate live membership, scope, schemas, approvals, credentials and idempotency. Neither an LLM nor an MCP grant can create authority.

## Migration discipline

- Retain business capabilities and application workflow execution; do not confuse these with redundant model/harness infrastructure.
- Route agent requests through the shared DragonZpyder execution API, preserving business context and policy. Voice, Discord, web and other ingress should not spawn independent intelligence stacks.
- Evaluate existing sandbox runner isolation before reuse or transfer to DragonZpyder; separate genuine Personal authorization from Workspace grants. Never fabricate workspace IDs or expose host shell/credentials.
- Preserve old routes only for verified transition/rollback. Retire redundant AI services and credentials after usage/dependency audit, cutover evidence, rollback checks and approval. Do not delete business data or authority.
- New integration changes require scope and revocation tests, replay/approval tests, real receipt-backed outcomes, versioned schemas and deployment evidence. Docs do not establish a cutover or authorize enabling flags.
