# OPERLY Operations Phase

This phase adds:

- structured business induction
- imported business source material
- deterministic operational scanning
- AI-generated owner brief
- AI business health audit
- business health score
- editable visual operating plan
- approval gates inside plan nodes
- plan approval and versioning
- operational alert lifecycle
- shared-agent tools for audit, scans, briefs and plan generation

## Security design

- every query is tenant-scoped
- the browser never receives the Ollama API key
- AI receives bounded snapshots, not database access
- imported text is explicitly treated as untrusted data
- generated audit and plan JSON is validated before persistence
- AI cannot directly execute payments, refunds, deletion, credential changes or permission changes
- external or consequential steps are represented with approval nodes
- scans and AI generation are rate-limited
- shared-agent tool calls remain audited by the existing secure harness

## Apply

Extract into:

```text
C:\MY_CODES\OPERLY
```

Then:

```powershell
cd C:\MY_CODES\OPERLY
uv run python .\patch_operations_phase.py
```

Start the website:

```powershell
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Start Discord with the shared model:

```powershell
uv run python -m packages.connectors.discord.bot_shared
```

Open:

```text
http://localhost:8000
```

Use this order:

1. Complete **Induction**
2. Add business source material
3. Run **Operations**
4. Generate the **Audit**
5. Generate and approve an **Operating plan**
