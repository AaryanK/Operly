# OPERLY release checklist

Do not deploy unless every applicable item below is complete and recorded for the
candidate commit.

## Source and automated verification

- [ ] Record the candidate commit SHA and confirm `/api/health` reports it.
- [ ] Review the working tree and exclude local databases, secrets, generated
      previews, and test artifacts.
- [ ] Run the complete automated test suite and record totals and warnings.
- [ ] Run authenticated browser acceptance for Home, Build, Activity, Settings,
      login, logout, and workspace switching.
- [ ] Inspect desktop, tablet, and mobile layouts.

## Database

- [ ] Confirm the Alembic head matches the latest file in `alembic/versions`
      (currently `0012_live_recursive_planning`).
- [ ] Create a timestamped PostgreSQL backup and test restoration.
- [ ] Rehearse a fresh migration and an upgrade from the deployed revision against
      disposable PostgreSQL databases.
- [ ] Run migration and integrity checks twice to prove idempotent startup.
- [ ] Confirm production `DATABASE_URL` points to persistent PostgreSQL rather than
      SQLite or a test database.

## Authentication and isolation

- [ ] Use HTTPS, a strong unique `SESSION_SECRET`, secure cookies, trusted hosts,
      and the intended CORS origin.
- [ ] Confirm bootstrap or temporary credentials have been removed or rotated.
- [ ] Verify users cannot read or mutate another workspace's conversations, tasks,
      approvals, plans, source snapshots, builds, projects, records, or previews.
- [ ] Verify workspace switching requires an existing membership.
- [ ] Verify CSRF protection on authenticated mutations and throttling on public
      and login endpoints.

## Planning and coding harness

- [ ] Exercise a request that proceeds without clarification and one that requires
      a genuine owner decision.
- [ ] Verify capability dependencies, acceptance criteria, whole-graph review, and
      approved-plan version binding.
- [ ] Verify coding context compaction, bounded tool output, permission modes, and
      repeated-tool-call termination.
- [ ] Verify source edits create immutable snapshots and cannot escape the project
      source boundary.
- [ ] Verify build failure evidence reaches the bounded repair loop.
- [ ] Verify stale writes, duplicate build requests, cancellation, cleanup, and
      preview deletion behave safely.

## Runner and generated previews

- [ ] Keep `OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER` disabled in production.
- [ ] Configure the external runner URL and token if builds are enabled.
- [ ] Confirm the runner uses ephemeral filesystems, resource limits,
      deny-by-default networking, dependency controls, and preview-scoped secrets.
- [ ] Review build logs, tests, artifact inventory, immutable digest, resource
      usage, startup result, and health check.
- [ ] Confirm generated applications cannot execute inside the FastAPI process.
- [ ] Confirm generation fails closed when the isolated runner is unavailable.
- [ ] Confirm preview URLs are isolated and expire or clean up as intended.
- [ ] Do not represent preview generation as production deployment.

## Supporting product surfaces

- [ ] Verify Discord ingestion and tenant-scoped message recall if the connector is
      enabled.
- [ ] Verify task completion and approval decisions are audited.
- [ ] Verify public website and field-service endpoints enforce rate limits,
      validation, tenant boundaries, and framing policy.
- [ ] Verify managed application records, workflows, versions, and rollback remain
      application- and workspace-scoped.

## Deployment and rollback

- [ ] Record the current production revision and its rollback procedure.
- [ ] Deploy migrations before or atomically with compatible application code.
- [ ] Verify health, landing, login, authenticated navigation, one planning request,
      and one non-destructive preview flow in production.
- [ ] Monitor API errors, runner failures, database connections, and authentication
      failures after deployment.
- [ ] Retain the previous application image and verified database backup until the
      release observation window closes.
