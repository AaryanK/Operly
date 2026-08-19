"""Isolated browser-QA application with a non-networked email outbox.

This module refuses to load outside OPERLY_ENV=test and is never referenced by
the production process command.
"""

import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from packages.email.providers.memory import MemoryEmailProvider
from packages.email.service import EmailService, set_email_service_for_tests


if os.getenv("OPERLY_ENV", "").lower() != "test":
    raise RuntimeError("The authentication browser-QA app is test-only")

email_provider = MemoryEmailProvider()
set_email_service_for_tests(EmailService(email_provider))
request_diagnostic: dict = {}

from apps.api.main import app as operly_app  # noqa: E402
from apps.api.dependencies import AuthContext, get_auth_context  # noqa: E402

app = FastAPI()


@app.middleware("http")
async def record_auth_request_shape(request, call_next):
    if request.url.path.startswith("/api/auth/"):
        request_diagnostic.clear()
        request_diagnostic.update(
            {
                "path": request.url.path,
                "method": request.method,
                "cookie_names": sorted(request.cookies),
                "has_csrf_header": bool(request.headers.get("x-csrf-token")),
            }
        )
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/"):
        request_diagnostic["status"] = response.status_code
    return response


@app.get("/__test__/email-outbox", include_in_schema=False)
async def email_outbox():
    if os.getenv("OPERLY_ENV", "").lower() != "test":
        raise HTTPException(status_code=404)
    return [
        {
            "to": message.to_email,
            "subject": message.subject,
            "text": message.text_body,
        }
        for message in email_provider.messages
    ]


@app.get("/__test__/request-diagnostic", include_in_schema=False)
async def request_shape():
    return request_diagnostic


@app.get("/__test__/private-probe", response_class=HTMLResponse, include_in_schema=False)
async def private_probe(auth: AuthContext = Depends(get_auth_context)):
    return "<!doctype html><title>Private probe</title><h1>Authenticated</h1>"


app.mount("/", operly_app)
