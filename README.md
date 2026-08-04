# OPERLY Discord Agent

Operly also includes an authenticated, manifest-driven application builder with blank applications, tested capability composition, structured canvas selection, theme tokens, generic managed records, safe workflow bindings, preview, atomic apply, and immutable rollback. See [README_APPLICATION_BUILDER.md](README_APPLICATION_BUILDER.md).

Implemented baseline capabilities:

1. Persistent tenant-scoped server memory
2. Context-aware response routing
3. Message search and recall
4. Task creation/listing
5. Reminder scheduling
6. Channel summaries
7. Image/OCR support
8. Permission-ready Discord integration
9. Business-tool extension point
10. Approval records for sensitive actions

## Run

From the repository root:

```powershell
Copy-Item .env.example .env
# Fill .env
uv venv
uv pip install -r requirements.txt
uv run python -m packages.connectors.discord.bot
```

Production uses the Railway PostgreSQL service reference and revisioned Alembic
migrations; local development may continue using SQLite. See
`README_DASHBOARD_STUDIO.md`. Useful commands:

```powershell
uv run python -m packages.database.migrate upgrade
uv run python -m packages.database.migrate check
uv run python -m packages.database.migrate backup
```

## Tenant isolation

Every guild is mapped to one tenant. All message, memory, task, and approval queries include `tenant_id`.
