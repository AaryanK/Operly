# Canonical React frontend

Operly has one frontend implementation:

- `apps/web/src/` — Vite/React, owner of public, authentication, legal, admin, Personal Operly, and workspace routes.
- `apps/web/public/` — static assets copied by Vite into the production build. It is not a second renderer.

The legacy `apps/web/static/` compatibility frontend has been retired and removed.

## Route ownership

React owns:

- `/` — public landing page.
- `/login`, `/signup`, `/join` — authentication and workspace-invite entry.
- `/verify-email`, `/forgot-password`, `/reset-password`, `/onboarding` — account lifecycle.
- `/privacy`, `/terms` — legal pages.
- `/admin` — platform administration.
- `/channels/@me` — Personal Operly.
- `/channels/:workspace/**` — workspace product surfaces.
- unknown frontend routes — React 404 surface.

FastAPI serves API routes first and otherwise returns the built React shell. Vite-generated files under `apps/web/dist` are served directly when requested.

## Architecture rule

**One route, one renderer, one visual system.**

Do not add a second static application, DOM mutation bridge, hidden-click router, or parallel authenticated shell. New frontend work belongs under `apps/web/src/`.

## Authentication parity preserved in React

The React public surface preserves the previous authentication contracts:

- password sign-in and sign-up;
- Google sign-in;
- Discord sign-in;
- email verification by code or link;
- verification resend;
- forgot/reset password by code or link;
- workspace invitation inspection and acceptance;
- Personal vs workspace post-authentication routing;
- onboarding handoff.

## Scope truthfulness

Authenticated account/workspace navigation remains coupled to backend scope changes:

- `POST /api/auth/personal-scope` before entering Personal Operly when required;
- `POST /api/auth/switch-workspace` before entering another workspace.

## Production serving

The Docker build compiles `apps/web/dist` in a Node build stage. FastAPI serves that single build for every frontend route. There is no `/static` compatibility mount and no runtime dependency on source frontend files.

The repository logo source lives in `apps/web/public/operly-logo.png`; the production image optimizes it into the built bundle.
