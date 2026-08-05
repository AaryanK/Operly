# Operly release checklist

## Architecture-first generation gate

- [ ] Back up the target database with the release-scoped backup confirmation.
- [ ] Rehearse Alembic head `0009_sandbox_job_lifecycle` against disposable SQLite and PostgreSQL.
- [ ] Review plan and architecture-pack migrations; never run browser acceptance against `operly.db`.
- [ ] Confirm every generated implementation is bound to an explicitly approved plan version.
- [ ] Confirm unsupported plans fail closed when the isolated runner is absent.
- [ ] Verify quotation, inventory, and field-service tenant boundaries and stale-write rejection.
- [ ] Record the deployed commit SHA from `/api/health`; deploy atomically and retain the previous image for rollback.

## Managed application builder and custom software

- [ ] Run SQLite upgrade to `0009_sandbox_job_lifecycle` twice.
- [ ] Rehearse PostgreSQL fresh, legacy, current, and unsafe states with the release-scoped backup gate.
- [ ] Execute authenticated login, theme, component override, customer app, workflow, and selection scenarios.
- [ ] Verify desktop, tablet, and mobile canvas viewports.
- [ ] Confirm cross-workspace application, ChangeSet, version, preview, and record access is denied.
- [ ] Generate a field-service product from Studio and verify public intake, signed customer status, dispatch transitions, and artifact selection.
- [ ] Preview and apply a selected visual change; verify unrelated backend artifact hashes are preserved.
- [ ] Confirm public and dispatcher pages deny framing while authenticated Studio previews allow same-origin framing.
- [ ] Confirm agentic requests fail closed unless the isolated runner URL and token are configured.
- [ ] Review the sandbox runner's dependency scan, test report, artifact graph, immutable digest, resource usage, and preview URL before promotion.
- [ ] Do not deploy before backup verification and authenticated deployment testing.

- [ ] Full automated suite passes; totals and warnings recorded.
- [ ] Timestamped database backup exists and restoration was tested.
- [ ] Migration succeeds twice on a copy; record and integrity checks pass.
- [ ] `python -m packages.database.migrate release-check --backup-path ...` passes.
- [ ] `DATABASE_URL` is the `${{Postgres.DATABASE_URL}}` Railway service reference.
- [ ] A Railway PostgreSQL manual backup or verified `pg_dump` exists.
- [ ] PostgreSQL rehearsal tests ran against an isolated disposable database.
- [ ] Exposed PostgreSQL credentials were rotated and web/Discord references updated.
- [ ] Railway pre-deploy migration targets PostgreSQL; assets remain at `/app/studio_assets`.
- [ ] Authenticated local Studio acceptance passes after a server restart.
- [ ] Desktop, tablet, and mobile layouts were actually inspected.
- [ ] Isolation, authorization, stale proposals, duplicate Apply, invalid JSON,
      rejection, history, and rollback were verified.
- [ ] Production uses HTTPS, scoped trusted hosts/CORS, strong secrets, and
      persistent database storage.
- [ ] Current and rollback Railway revisions are recorded.
- [ ] Health, landing, login, and TLS checks pass.
- [ ] Authenticated production smoke uses an owner-supplied account.
- [ ] Temporary acceptance credentials and data are removed.

Do not deploy unless PostgreSQL rehearsal, credential rotation, Railway backup,
service reference, and current deployment revision are independently confirmed.
