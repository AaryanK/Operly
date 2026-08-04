# OPERLY General Business Pack

Adds:

- Contacts and CRM
- Lead pipeline
- Products and services
- Inventory adjustments and low-stock detection
- Orders
- Quotations
- Appointments
- Team directory
- Business documents
- Activity log
- Business summary

Every table and every API query is scoped by `tenant_id`.

## Install

Extract into `C:\MY_CODES\OPERLY`, replacing files when requested.

Then run:

```powershell
cd C:\MY_CODES\OPERLY
uv run python .\patch_general_business.py
uv run uvicorn apps.api.main:app --reload --env-file .env
```

Open:

```text
http://localhost:8000
```
