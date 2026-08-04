# OPERLY Shared AI Secure Patch

This patch creates one shared Ollama-powered business agent for:

- the OPERLY website
- Discord
- future channel adapters

## Security properties

- Ollama credentials remain server-side
- HttpOnly session cookies
- SameSite=Strict cookies
- CSRF protection for cookie-authenticated writes
- strict tenant and principal scoping
- conversation ownership checks
- rate limiting
- bounded input, output and tool results
- fixed tool allowlist
- validated arguments
- no arbitrary code, URL, file, shell or database tools
- no payment, refund, deletion or permission-changing tools
- prompt-injection boundaries around business records
- redacted tool audits
- disabled legacy token-returning login endpoint
- Discord mentions disabled in normal AI output

## Apply

Extract into:

```text
C:\MY_CODES\OPERLY
```

Then:

```powershell
cd C:\MY_CODES\OPERLY
uv run python .\patch_shared_ai.py
```

Start the website/API:

```powershell
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Start Discord with the shared agent:

```powershell
uv run python -m packages.connectors.discord.bot_shared
```

Open:

```text
http://localhost:8000
```

The same `.env` model settings power both channels:

```env
OLLAMA_API_KEY=...
OLLAMA_MODEL=gemma4:cloud
OLLAMA_URL=https://ollama.com/api/chat
```

## Important production settings

Use long, unique values:

```env
SESSION_SECRET=<at-least-32-random-bytes>
ADMIN_PASSWORD=<strong-unique-password>
PUBLIC_BASE_URL=https://overly.dragonzpyder.xyz
```

Do not commit `.env`.
