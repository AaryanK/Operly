# OPERLY Studio

Studio is the tenant-scoped, component-schema website builder built into the
existing FastAPI and vanilla JavaScript application.

## Run

```powershell
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open `http://localhost:8000`, sign in, choose a workspace in the sidebar, and
select **Studio**. Public deployments are served at `/sites/{public_slug}`.

Studio uses the existing `DATABASE_URL`, `PUBLIC_BASE_URL`, `SESSION_SECRET`,
`OLLAMA_URL`, `OLLAMA_MODEL`, and `OLLAMA_API_KEY` settings. The optional
`STUDIO_ASSET_DIR` selects an image storage root; it defaults to
`./studio_assets`. No AI credential is sent to the browser.

Database startup uses SQLAlchemy `create_all` only for new Studio tables. It
does not delete, recreate, or modify existing tables or `operly.db`.

## Security model

- authenticated identity and tenant come only from `AuthContext`
- workspace switching requires an existing `TenantMember`
- all AI output passes the strict Site Schema validator
- published versions are immutable; rollback creates a new draft
- public routes resolve active deployments and published versions only
- the renderer escapes copy and never executes user HTML, CSS, or JavaScript
- assets are magic-byte checked JPEG, PNG, or WebP files with randomized paths
- public forms are bounded, honeypot-protected, rate-limited, and CRM-scoped
- CSV exports prefix spreadsheet-formula cells
