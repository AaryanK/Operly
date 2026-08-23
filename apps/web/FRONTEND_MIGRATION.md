# Canonical frontend migration

Operly currently has two frontend source trees:

- `apps/web/src/` — Vite/React, now the canonical destination for authenticated product UI.
- `apps/web/static/` — the production legacy application shell. It remains temporarily reachable while routes are ported.

The migration rule is simple: **one route, one renderer, one visual system**. New product UI must not add another global stylesheet generation, hidden-click router, DOM mutation repair layer, or bridge that rewrites another renderer's output.

## Route ownership

The canonical routes are account-first:

- `/channels/@me` — Personal Operly.
- `/channels/:workspace` — workspace Home.
- `/channels/:workspace/operly` — workspace AI.
- `/channels/:workspace/crm` — CRM.
- `/channels/:workspace/operations` — Operations.
- `/channels/:workspace/activity` — Activity.
- `/channels/:workspace/presence` — Presence.
- `/channels/:workspace/solutions` — Solutions.
- `/channels/:workspace/connections` — Connections.
- `/channels/:workspace/plugins` — Plugins.
- `/channels/:workspace/members` — Members & roles.
- `/channels/:workspace/access` — AI & MCP access.

`apps/web/src/app/routes.ts` is the source of truth for route ownership. A route must not delegate to an alternate renderer through a synthetic click or DOM bridge.

## Retirement order

### Safe to retire now

The old unused React demo shell, its icon layer, and its stylesheet are replaced by the canonical account/workspace shell in this branch.

### Temporary compatibility only

The following legacy static layers remain until their route is ported and verified:

- `static/app.js`
- `static/personal.js`
- `static/workspace-shell.js`
- `static/simple-ui.js`
- `static/authenticated-ui.js`
- `static/ai-assistant-bridge.js`
- legacy authenticated CSS generations loaded from `static/index.html` / `static/regression.css`

No new product behavior should be added to these layers unless it is a production-critical fix that cannot wait for its route migration.

### Protected until managed-app creation work lands

`static/unified-solution-studio.js` is intentionally not deleted or substantially rewired in this foundation branch because the managed-app creation observability/failure work in PR #94 changes that surface. Port the final behavior after #94 lands, then retire the static Studio renderer.

## Cutover gate

FastAPI must continue serving the existing static production shell until the canonical React app has functional parity for the routes being cut over. The final serving switch happens only after:

1. Personal and workspace scope transitions are verified.
2. Every production route has a React owner.
3. Browser-level acceptance tests cover desktop and mobile navigation, contrast, overflow, composer behavior, and route isolation.
4. The legacy static application shell can be deleted without any hidden renderer dependency.

The build workflow in this branch makes the Vite app a required artifact while the production serving path remains unchanged.
