# OPERLY

OPERLY is an AI-native, tenant-isolated business operating system. Its intelligence
understands a company and composes tailored software, agents, workflows,
connectors, and actions around it. Its current interface focuses on four areas:

- **Home** accepts a business question or software request and shows work that
  needs attention.
- **Solutions** launches websites, digital presences, internal tools, customer
  portals, workflows, agents, or other arbitrary business software. Each Solution
  can move through planning, generation, preview, visual inspection, and editing.
- **Activity** combines tasks, approval decisions, and recent Discord messages.
- **Connectors** shows communication/event channels and workspace configuration.

## Product model

A **Solution** is the primary thing a business launches in OPERLY. It may contain
customer-facing surfaces, internal surfaces, backend capabilities, workflows,
agents, integrations, or any combination required by the business outcome.
Websites, managed applications, and generated projects are presented as one
Solution library even while their existing runtime implementations remain
separate internally.

Visual editing treats a rendered Solution as inspectable evidence: a user selects
what they can see, describes a change, and OPERLY maps that selection back to the
authoritative component or source artifact before producing a versioned preview.
The rendered DOM is never treated as unrestricted source authority.

A **Connector** such as Discord or WhatsApp is an event and action channel. It can
capture messages, set reminders, trigger workflows, request approvals, run bounded
backend agents, and publish controlled updates into a Solution. A connector does
not receive unrestricted authority to redesign or arbitrarily mutate Solution
frontends.

The current web application is the FastAPI service and the static browser client
under `apps/web/static`. The React/Vite client under `apps/web/src` is retained
source but is not the interface served by FastAPI.

## Software construction flow

```text
request
  -> requirements analysis and material clarification
  -> dependency-aware capability graph
  -> whole-graph review and owner approval
  -> persistent coding-agent tool loop
  -> immutable source snapshot
  -> isolated build, tests, start, and health check
  -> bounded repair loop
  -> preview
```

Planning uses a compact dynamic graph instead of recursively expanding every
node. Acceptance criteria and artifact identities are derived deterministically
where possible. The coding agent works through bounded file tools and cannot run
generated code inside the OPERLY API process.

Generated browser software is complete only when every visible interactive
control is covered by `operly.interactions.json`. That executable contract traces
the rendered control through its event handler and domain operation to
success/rejection behavior, state mutation, UI evidence, and reload-persistence
policy. Source-file presence, a rendered preview, or a generic smoke test is not
completion evidence; dead, placeholder, throwing, cosmetic-only, and untested
controls fail source and runner acceptance.

See [`packages/coding_harness/ARCHITECTURE.md`](packages/coding_harness/ARCHITECTURE.md)
for the detailed planning, coding, runner, repair, and visual-editing model.

## Supporting capabilities

The same tenant-scoped backend also provides:

- authenticated workspaces and workspace switching;
- AI chat with bounded business context;
- Discord message capture and recall;
- tasks, reminders, persistent business memory, and approval records;
- contacts, leads, products, inventory, orders, quotations, appointments, team
  members, documents, and activity history;
- business induction, operational scans, alerts, briefs, audits, and operating
  plans;
- schema-controlled websites and managed CRUD applications;
- quotation, inventory, and field-service runtimes.

These supporting APIs remain available, although the current navigation gives
priority to Home, Build, Activity, and Settings.

## Safety boundaries

- Authentication and every business-data query are tenant scoped.
- AI output is validated before persistence or execution.
- Generated source is stored as immutable, versioned snapshots.
- Consequential business actions can require explicit approval.
- Generated code does not execute in the FastAPI control plane.
- Production generation requires a separate isolated runner with bounded
  resources, deny-by-default networking, test execution, and preview isolation.
- If the production runner is missing, generation fails closed.
- The built-in subprocess runner is for local development and tests only.
- Generated-product deployment is not currently enabled; the supported endpoint
  of the construction lifecycle is a verified preview.

## Local setup

Requirements: Python 3.11 or newer. PostgreSQL is recommended for production;
SQLite is supported for local development.

```powershell
Copy-Item .env.example .env
# Configure SESSION_SECRET, ADMIN_PASSWORD, and the model settings.

uv venv
uv pip install -r requirements.txt
uv run python -m packages.database.migrate upgrade
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open `http://localhost:8000`. FastAPI serves the current browser client directly;
an npm/Vite process is not required for this interface.

To run the Discord connector separately:

```powershell
uv run python -m packages.connectors.discord.bot_harness
```

## Configuration

Start from [`.env.example`](.env.example). Important settings include:

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLite development database or PostgreSQL connection |
| `SESSION_SECRET` | Signs authenticated sessions; required |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Bootstrap owner account |
| `PUBLIC_BASE_URL` | Canonical origin and production host policy |
| `MAIL_PROVIDER` | Transactional email transport: `zoho_mail_api` on Railway Hobby, or `smtp` where SMTP egress is available |
| `ZOHO_MAIL_ACCOUNT_ID`, `ZOHO_MAIL_CLIENT_ID`, `ZOHO_MAIL_CLIENT_SECRET`, `ZOHO_MAIL_REFRESH_TOKEN` | Zoho Mail REST API sender and OAuth credentials |
| `ZOHO_ACCOUNTS_BASE_URL`, `ZOHO_MAIL_API_BASE_URL` | Regional Zoho OAuth and Mail API endpoints |
| `OLLAMA_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL` | Ollama endpoint and default compatibility model |
| `OPERLY_MODEL_<ROLE>`, `OPERLY_MODEL_<ROLE>_FALLBACKS` | Provider-neutral role portfolio for planning, validation, coding, repair, placement, agents, and bounded tasks |
| `OPERLY_PLANNING_MODE` | Selects the planning implementation |
| `OPERLY_SANDBOX_RUNNER_URL`, `OPERLY_SANDBOX_RUNNER_TOKEN` | External isolated runner |
| `OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER` | Development/test runner; never production |
| `DISCORD_BOT_TOKEN` | Optional Discord connector |
| `OPERLY_CONNECTOR_SECRET_KEY` | Encrypts tenant OAuth credentials at rest |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | Google Workspace OAuth application |
| `GOOGLE_OAUTH_REDIRECT_URI` | Exact registered Google OAuth callback |

Production additionally requires HTTPS, strong unique secrets, PostgreSQL,
completed migrations, a verified backup, and an independently isolated runner if
software builds are enabled.

The default Ollama configuration uses `gemma4:31b` for every role with no
automatic model fallback. This keeps interactive behavior, latency, and usage
measurable while the orchestration loop is optimized. Role routing remains
expressed as provider plus model, so individual roles can later be overridden
or moved to OpenRouter or another provider without changing planner or coding
agent contracts.

## Database and tests

Alembic revisions are the authoritative database history. Do not apply the old
root-level patch scripts to a current database.

```powershell
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run pytest -q
```

Before releasing, follow [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
For the north-star gap analysis and delivery sequence, see
[`ROADMAP.md`](ROADMAP.md).

## Repository map

| Path | Responsibility |
| --- | --- |
| `apps/api` | FastAPI routes, sessions, security, and browser delivery |
| `apps/web/static` | Current browser interface |
| `packages/coding_harness` | Coding-agent loop and source lifecycle |
| `packages/custom_software` | Planning, project, runner, preview, and repair orchestration |
| `packages/business_brain` | Shared business agent and operational tools |
| `packages/database` | Tenant-scoped persistence and migration helpers |
| `packages/connectors/discord` | Discord integration |
| `alembic/versions` | Authoritative schema revisions |
| `tests` | Unit, integration, planning, isolation, and harness tests |

## Current limitations

- The repository still contains older backend surfaces and patch scripts for
  compatibility and historical development; they are not the recommended setup
  path.
- The static client is the current UI, while the React/Vite client is not wired to
  the FastAPI catch-all route.
- Successful source generation does not imply production deployment.
- Production-quality execution depends on an externally operated sandbox runner.
