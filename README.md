# OPERLY

OPERLY is a tenant-isolated business workspace and AI-assisted software builder.
Its current interface focuses on four areas:

- **Home** accepts a business question or software request and shows work that
  needs attention.
- **Build** turns a software request into a reviewed capability graph, generated
  source, an isolated build-and-test cycle, and a preview.
- **Activity** combines tasks, approval decisions, and recent Discord messages.
- **Settings** manages the current workspace.

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
| `OLLAMA_URL`, `OLLAMA_API_KEY`, `OLLAMA_MODEL` | Planning and coding model |
| `OPERLY_PLANNING_MODE` | Selects the planning implementation |
| `OPERLY_SANDBOX_RUNNER_URL`, `OPERLY_SANDBOX_RUNNER_TOKEN` | External isolated runner |
| `OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER` | Development/test runner; never production |
| `DISCORD_BOT_TOKEN` | Optional Discord connector |

Production additionally requires HTTPS, strong unique secrets, PostgreSQL,
completed migrations, a verified backup, and an independently isolated runner if
software builds are enabled.

## Database and tests

Alembic revisions are the authoritative database history. Do not apply the old
root-level patch scripts to a current database.

```powershell
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run pytest -q
```

Before releasing, follow [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

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
