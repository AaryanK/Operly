# Canonical frontend migration

Operly now has one canonical authenticated product UI:

- `apps/web/src/` — Vite/React, owner of `/channels/**`.
- `apps/web/static/` — temporary compatibility shell for public/auth flows and the protected legacy Studio editor while their in-flight contracts settle.

The migration rule is **one route, one renderer, one visual system**. New product UI must not add another global stylesheet generation, hidden-click router, DOM mutation repair layer, or bridge that rewrites another renderer's output.

## Canonical route ownership

`apps/web/src/app/routes.ts` owns the authenticated account/workspace routes:

- `/channels/@me` — Personal Operly, conversation history, private attachments.
- `/channels/:workspace` — workspace Home / operating overview.
- `/channels/:workspace/operly` — workspace Operly chat, history, attachments.
- `/channels/:workspace/crm` — CRM.
- `/channels/:workspace/operations` — Operations.
- `/channels/:workspace/activity` — Activity, approvals, tasks, recent messages.
- `/channels/:workspace/presence` — digital presence state and verified publish/rollback actions.
- `/channels/:workspace/solutions` — Solution library and truthful creation entrypoint.
- `/channels/:workspace/connections` — workspace connector health and management.
- `/channels/:workspace/plugins` — capability/plugin composition.
- `/channels/:workspace/members` — members and human-readable roles/permissions.
- `/channels/:workspace/access` — external client grants and MCP tool exposure.

Every route above has a direct React owner. No canonical route delegates through a synthetic click or legacy DOM bridge.

## Scope truthfulness

Changing the rail is not a cosmetic URL change. The canonical scope controller calls:

- `POST /api/auth/personal-scope` before returning to Personal Operly;
- `POST /api/auth/switch-workspace` before entering another workspace.

The account/workspace URL therefore follows the authenticated backend session instead of getting ahead of it.

## Attachment boundary

Personal attachments are account-private. `/api/personal-agent/chat-with-attachments` uses the same secure multimodal processor as workspace chat without manufacturing a workspace binding. Extracted attachment content is explicitly marked untrusted before it reaches Personal Operly. Personal web conversations remain separate transcripts from Discord; shared account memory/context may still be retrieved through authorized capabilities.

## Production serving

The production Docker image now builds `apps/web/dist` in a Node build stage. FastAPI serves the built React application for `/channels/**` and its generated assets. Public/auth URLs continue to use the static compatibility shell for now, avoiding overlap with the separate account-authentication PR.

This split is intentional and merge-safe: authenticated product rendering can move forward without rewriting `static/auth.js` while another branch owns it.

## Retired in this PR

The obsolete, unused Vite demo frontend (Overview / Inbox / Tasks / Business brain / old Settings), its icon layer, and its stylesheet have been removed. The canonical React application replaced that dead source tree rather than adding a third frontend.

## Temporary compatibility only

These files remain because the public/auth shell still loads them or because an in-flight PR owns their final behavior:

- `static/app.js`
- `static/auth.js`
- `static/personal.js`
- `static/workspace-shell.js`
- `static/simple-ui.js`
- `static/authenticated-ui.js`
- `static/ai-assistant-bridge.js`
- legacy authenticated CSS loaded by `static/index.html`

They are no longer route owners for direct `/channels/**` requests. Do not add normal product development to them.

## Protected Studio boundary

`static/unified-solution-studio.js` remains protected because PR #94 changes managed-app creation observability and truthful failure semantics there. This PR intentionally does not edit that file. Once #94 lands, rebase/update this branch, port the final Studio contract, and then delete the protected static Studio renderer together with any static-only authenticated dependencies that become unreachable.

## Final cleanup gate

Before deleting the static compatibility shell entirely:

1. PR #94's final managed-app creation/Studio behavior must be present in the canonical UI.
2. The separate authentication work must either merge or be superseded by a canonical auth implementation.
3. Production/browser acceptance must cover account ↔ workspace switching, desktop/mobile navigation, attachments, approval actions, connector management, contrast/overflow, and direct-route refreshes.
4. A final reachability check must show no public/auth path still imports the legacy authenticated renderer stack.

Until those dependencies settle, keeping compatibility files is safer than deleting files that an open PR still edits. The key rule is that they no longer own the authenticated `/channels/**` product routes.
