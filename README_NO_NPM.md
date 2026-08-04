# No-npm frontend

From `C:\MY_CODES\OPERLY` after copying this patch:

```powershell
uv run python .\patch_main.py
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open:

```text
http://localhost:8000
```

No Node, npm, Vite, or Docker is required.
