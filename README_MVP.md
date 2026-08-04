# OPERLY MVP

## What is included

- Public OPERLY landing page
- Secure admin login
- Tenant-isolated dashboard
- Discord message inbox
- Tasks
- Business memory
- Approvals
- Integrations
- Business settings
- Shared PostgreSQL storage for the website and Discord bot
- Docker deployment
- Nginx and Caddy subdomain snippets

## Local development

Create `.env`:

```powershell
Copy-Item .env.mvp.example .env
```

For a quick local database, change `DATABASE_URL` to:

```env
DATABASE_URL=sqlite+aiosqlite:///./operly.db
```

Run the API:

```powershell
uv venv
uv pip install -r requirements.txt
uv run uvicorn apps.api.main:app --reload
```

Run the frontend in another terminal:

```powershell
cd apps/web
npm install
npm run dev
```

Open `http://localhost:5173`.

## Production with Docker

```bash
cp .env.mvp.example .env
# Fill all secrets and set POSTGRES_PASSWORD.
docker compose up -d --build
```

Then configure the supplied Nginx or Caddy snippet for
`overly.dragonzpyder.xyz`.

## Admin bootstrap

On first startup, OPERLY creates the admin account from:

- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`

If the Discord bot has already created one tenant, the admin is attached to
that tenant so the website immediately displays that server's data.
