from __future__ import annotations

from typing import Any, Mapping

from packages.model_runtime.semantic_router import SemanticDecision, SemanticRouter


APPLICATION_BUILDER_DOMAIN = (
    "Requests to create, configure, or modify an OPERLY managed application, "
    "including its pages, data entities, forms, tables, authentication, theme, "
    "components, and bounded workflows."
)

# These routes are existing deterministic implementations. The model decides
# whether a request is fully covered by one of them; OPERLY still validates and
# executes the selected implementation.
BUILDER_ROUTES: dict[str, str] = {
    "secure_login": (
        "Add or enable OPERLY's standard secure login/authentication capability, "
        "including protected routes and normal role enforcement."
    ),
    "customer_notebook": (
        "Create the existing lightweight customer-and-purchase notebook with "
        "customer contact details, purchase records, forms, and tables."
    ),
    "customer_management": (
        "Create the existing customer-management application composition with "
        "secure login, a customer entity, customer form, and customer table."
    ),
    "application_theme": (
        "Apply the existing whole-application green or dark-green and cream theme "
        "tokens. Requests for other colors or broader visual synthesis are not covered."
    ),
    "component_orange": (
        "Apply the existing orange token override to explicitly selected components."
    ),
    "follow_up_task": (
        "Bind an explicitly selected component so its click creates the existing "
        "internal follow-up task workflow."
    ),
}


async def route_application_request(
    request: str,
    *,
    client=None,
    context: Mapping[str, Any] | None = None,
) -> SemanticDecision:
    return await SemanticRouter(client).decide(
        request=request,
        domain=APPLICATION_BUILDER_DOMAIN,
        routes=BUILDER_ROUTES,
        context=context,
    )
