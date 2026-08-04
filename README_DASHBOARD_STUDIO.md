# Operly Dashboard Studio

Dashboard Studio lets an authenticated workspace inspect and safely customize
the OPERLY dashboard itself. It is separate from the public-site builder in
`packages/studio` and shares the existing FastAPI, SQLAlchemy, cookie-session,
CSRF, Ollama, and vanilla JavaScript architecture.

## Architecture

Source-defined component defaults live in
`packages/dashboard_studio/registry.py`. Tenant-scoped overrides are stored
separately and merged by `DashboardStudioService`:

```text
registered source defaults + validated tenant overrides = effective screen
```

The browser never submits a tenant ID as authority. `AuthContext` supplies the
tenant and role for every API operation.

## Registering a component

Add a stable `ComponentDefinition` to `REGISTRY` with:

- `id`, `type`, `label`, `page_id`, and `region`
- source `editable_properties`
- an explicit `allowed_operations` list
- optional data source, action binding, visibility, and children

Then add one locator mapping in `dashboard-customize.js`. The reusable DOM
annotator supplies selection, Shift-selection, Escape clearing, outlines,
context chips, preview overlays, and role visibility. Individual pages should
not implement their own selection listeners.

Only these properties are currently accepted server-side: title/label, shown,
order, width, approved variant, role visibility, and a bounded action binding.
Unknown component IDs and properties are rejected again during apply.

## Screen context and chat

The persistent dashboard dock sends a strict context envelope containing the
authenticated workspace, current virtual route and screen, mode, selected
registered components, role, active configuration version, and viewport class.
The API verifies workspace and role against `AuthContext`. The validated
envelope is then included as application-controlled context in the existing
shared agent prompt and tool context. Page labels remain untrusted data.

## Change lifecycle

```text
proposed → previewing → applied
         ↘ rejected
applied version → rolled_back (when a later rollback supersedes it)
```

Preview returns a client-only overlay and does not write active overrides.
Apply revalidates registered IDs and property values, persists overrides, and
creates an immutable active configuration snapshot. On the first apply, a
baseline source-default version is also created. Rollback restores a selected
snapshot into a new version without deleting intervening history.

Every proposal, application, rejection, and rollback produces a tenant-scoped
audit record.

## Revisioned database migrations

Alembic owns production schema upgrades. Revision `0001_operly_core` establishes
the pre-Dashboard-Studio baseline without replacing existing tables. Revision
`0002_dashboard_studio` creates missing Studio tables or reconstructs earlier
SQLite Studio tables in batch mode to add required indexes, uniqueness rules,
user/workspace relationships, applied/origin/source-version relationships, and
operation ownership.

The upgrade supports empty, pre-Studio, first unversioned Studio, and already
versioned databases. It validates JSON, workspace ownership, users, operations,
statuses, and version numbers before constraints are applied. Multiple active
versions are normalized by retaining the highest numbered active version; unsafe
orphans and ambiguous data stop the migration.

```powershell
uv run python -m packages.database.migrate current
uv run python -m packages.database.migrate history
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run python -m packages.database.migrate backup
```

Use `--database-url sqlite+aiosqlite:///C:/path/to/copy.db` for SQLite rehearsal or
an explicitly disposable `TEST_POSTGRES_DATABASE_URL` for PostgreSQL rehearsal.
Production application startup checks for revision `0002_dashboard_studio` and
never calls `create_all`. Development and fresh unit-test schemas may still use
controlled `create_all`.

`deploy-upgrade --allow-production` detects the dialect. SQLite receives a verified
online backup. PostgreSQL requires either a successful `pg_dump --backup-dir ...`
or a release-scoped operator confirmation that a Railway manual backup has completed.
Set the current release in `OPERLY_RELEASE_ID` (Railway's `RAILWAY_DEPLOYMENT_ID` is
also accepted), set the matching value in `OPERLY_POSTGRES_BACKUP_RELEASE_ID`, and
set `OPERLY_POSTGRES_BACKUP_AT` to the timezone-aware ISO 8601 backup time. The gate
rejects mismatched releases, timestamps over 24 hours old, naive timestamps, and
timestamps more than five minutes in the future. The old reusable
`OPERLY_POSTGRES_BACKUP_CONFIRMED` boolean is ignored. The connection URL is never
printed or passed to `pg_dump` as a command-line argument.

## Local development and Railway

```powershell
uv run uvicorn apps.api.main:app --reload --env-file .env
uv run python -m unittest discover -s tests -v
```

Railway continues to run Uvicorn on `0.0.0.0` and `${PORT:-8000}` through the
existing Dockerfile. No additional environment variables are required.

## Current limitations

- The first registry covers the Overview metric/panel components and global
  navigation. More screens can be registered incrementally.
- Layout editing uses safe order and width variants, not drag-and-drop geometry.
- Workflow bindings are metadata only; arbitrary workflow execution is not
  introduced in this milestone.
- Real-time collaboration and conflict resolution are not yet implemented.

## Safe authenticated local acceptance

Never reuse production credentials. Back up `operly.db`, copy it to an isolated
location, point `DATABASE_URL` at the copy, and create a disposable owner with:

```powershell
$env:OPERLY_ENV='development'
$env:PUBLIC_BASE_URL='http://localhost:8010'
$env:DATABASE_URL='sqlite+aiosqlite:///C:/tmp/operly-dashboard-acceptance.db'
$env:OPERLY_DEV_EMAIL='acceptance-owner@operly.local'
$env:OPERLY_DEV_PASSWORD='<a temporary password of at least 12 characters>'
uv run python -m scripts.create_dev_account
```

The command refuses production/HTTPS environments and never prints the password.
Start the isolated server with matching `DATABASE_URL`, a disposable
`SESSION_SECRET`, and port 8010. Delete the copied database when acceptance is
complete.

Verify sign-in, selection IDs and chips, proposal Before/After, preview/cancel,
Apply, reload and restart persistence, Shift multi-select, Escape, role visibility,
rejection, history, and rollback. Record the desktop, tablet, and mobile widths
actually exercised.

## Migration and Railway release gate

Run the migration twice against a timestamped database copy, compare existing
record counts, and run integrity and foreign-key checks. Run `release-check` with
the verified backup path before release. Downgrades intentionally fail because
dropping Studio relationships is unsafe; recovery restores a verified backup.

For Railway, confirm `OPERLY_ENV=production`, the HTTPS `PUBLIC_BASE_URL`, strong
`SESSION_SECRET` and `ADMIN_PASSWORD`, persistent `DATABASE_URL`, an available
backup, and a recorded rollback revision. Verify `/api/health` before using an
owner-supplied production test account. Never create a production account solely
for acceptance.

Railway runs the controlled PostgreSQL migration through `railway.toml` before
starting Uvicorn. `DATABASE_URL` must remain the `${{Postgres.DATABASE_URL}}`
service reference. Studio assets remain separately mounted at
`/app/studio_assets`; PostgreSQL data must never be moved into that asset volume.

PostgreSQL recovery uses Railway Postgres Backups restoration or a verified custom
format dump restored with `pg_restore` into an isolated database before cutover.
Stop writes, preserve the failed database, restore, run `check`, restart, and verify
health plus authentication. SQLite recovery continues to use the verified online
backup procedure.

Future model changes require a new immutable file in `alembic/versions`; modifying
an old revision or relying on model changes to alter production is prohibited.

## Troubleshooting and extension

- A stale-version response means a newer version became active; reload and create
  a fresh proposal.
- Preview never changes customization rows or the active version. Cancel it or
  reload to restore the active configuration.
- Invalid stored JSON is logged and ignored in favor of registered defaults.
- Supported deterministic requests can produce proposals while AI is unavailable;
  unsupported or ambiguous requests produce no ChangeSet.

Roles are `owner`, `manager`, and `employee`; only owners and managers may mutate
Studio configuration. Register another editable component in
`packages/dashboard_studio/registry.py` with a stable ID, bounded properties,
allowed operations, and one DOM locator mapping.
