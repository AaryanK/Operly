from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from packages.connectors.canva_provider import access_token, request_json
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.common import active_workspace_connectors, connector_scopes


PROVIDER_ID = "operly.canva.authoring"
DESIGN_META_READ = "design:meta:read"
DESIGN_CONTENT_READ = "design:content:read"
DESIGN_CONTENT_WRITE = "design:content:write"
BRAND_TEMPLATE_META_READ = "brandtemplate:meta:read"
BRAND_TEMPLATE_CONTENT_READ = "brandtemplate:content:read"

SCOPE_BY_CAPABILITY: dict[str, frozenset[str]] = {
    "canva.design.dataset": frozenset({DESIGN_CONTENT_READ}),
    "canva.brand_templates.list": frozenset({BRAND_TEMPLATE_META_READ}),
    "canva.brand_template.get": frozenset({BRAND_TEMPLATE_META_READ}),
    "canva.brand_template.dataset": frozenset({BRAND_TEMPLATE_CONTENT_READ}),
    "canva.autofill.create": frozenset({DESIGN_CONTENT_WRITE}),
    "canva.autofill.get": frozenset({DESIGN_META_READ}),
}


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    additional: bool = False,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": additional,
    }


def _capability(
    capability_id: str,
    name: str,
    description: str,
    *,
    permission: str,
    input_schema: dict[str, Any] | None = None,
    risk: CapabilityRisk = CapabilityRisk.READ_ONLY,
    approval: bool = False,
    reversible: bool = False,
    emits: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> CapabilitySpec:
    return CapabilitySpec(
        id=capability_id,
        version="1.0.0",
        display_name=name,
        description=description,
        provider_id=PROVIDER_ID,
        scopes=frozenset({"workspace"}),
        input_schema=input_schema or _object({}),
        output_schema=_object({}, additional=True),
        permissions=(permission,),
        risk=risk,
        approval_required=approval,
        reversible=reversible,
        emits=emits,
        tags=frozenset(("canva", "authoring", "external", *tags)),
        resource_scope="workspace",
    )


def workspace_canva_authoring_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        _capability(
            "canva.design.dataset",
            "Read Canva design autofill fields",
            "Read the named data fields configured for deterministic autofill in a Canva design.",
            permission="marketing:read",
            input_schema=_object(
                {"design_id": {"type": "string", "minLength": 1, "maxLength": 200}},
                required=["design_id"],
            ),
            tags=("design", "dataset", "autofill", "read"),
        ),
        _capability(
            "canva.brand_templates.list",
            "List Canva brand templates",
            "List brand templates available to the connected Canva account, optionally limited to autofill-enabled templates.",
            permission="marketing:read",
            input_schema=_object(
                {
                    "query": {"type": "string", "maxLength": 200},
                    "continuation": {"type": "string", "maxLength": 1000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "ownership": {"type": "string", "enum": ["any", "owned", "shared"]},
                    "sort_by": {
                        "type": "string",
                        "enum": [
                            "relevance",
                            "modified_descending",
                            "modified_ascending",
                            "title_ascending",
                            "title_descending",
                        ],
                    },
                    "dataset": {"type": "string", "enum": ["any", "non_empty"]},
                }
            ),
            tags=("brand-template", "list", "read"),
        ),
        _capability(
            "canva.brand_template.get",
            "Get Canva brand template",
            "Read metadata and Canva navigation URLs for one brand template.",
            permission="marketing:read",
            input_schema=_object(
                {"brand_template_id": {"type": "string", "minLength": 1, "maxLength": 200}},
                required=["brand_template_id"],
            ),
            tags=("brand-template", "read"),
        ),
        _capability(
            "canva.brand_template.dataset",
            "Read Canva brand-template autofill fields",
            "Read the data-field contract of an autofill-enabled Canva brand template.",
            permission="marketing:read",
            input_schema=_object(
                {"brand_template_id": {"type": "string", "minLength": 1, "maxLength": 200}},
                required=["brand_template_id"],
            ),
            tags=("brand-template", "dataset", "autofill", "read"),
        ),
        _capability(
            "canva.autofill.create",
            "Autofill Canva design",
            "Create a design from an autofill-enabled template/design, or update an autofill-enabled design in place using explicit named data fields.",
            permission="marketing:write",
            input_schema=_object(
                {
                    "type": {
                        "type": "string",
                        "enum": ["create_from_brand_template", "create_from_design", "update_design"],
                    },
                    "brand_template_id": {"type": "string", "maxLength": 200},
                    "design_id": {"type": "string", "maxLength": 200},
                    "title": {"type": "string", "maxLength": 255},
                    "data": _object({}, additional=True),
                },
                required=["type", "data"],
            ),
            risk=CapabilityRisk.HIGH,
            approval=True,
            reversible=False,
            emits=("canva.design.autofill.requested",),
            tags=("design", "autofill", "write"),
        ),
        _capability(
            "canva.autofill.get",
            "Get Canva autofill job",
            "Read the state and resulting design metadata for a Canva autofill job.",
            permission="marketing:read",
            input_schema=_object(
                {"job_id": {"type": "string", "minLength": 1, "maxLength": 200}},
                required=["job_id"],
            ),
            tags=("design", "autofill", "job", "read"),
        ),
    )


def _required_scope(capability_id: str) -> frozenset[str]:
    required = SCOPE_BY_CAPABILITY.get(capability_id)
    if required is None:
        raise LookupError(f"Canva authoring capability is not implemented: {capability_id}")
    return required


async def _connector(db: AsyncSession, workspace_id: str, capability_id: str):
    required = _required_scope(capability_id)
    rows = await active_workspace_connectors(db, workspace_id, "canva")
    for row in rows:
        if required.issubset(connector_scopes(row)):
            return row
    raise PermissionError(
        "Reconnect Canva and grant the provider scope required by this authoring tool"
    )


def _id(arguments: dict[str, Any], field: str) -> str:
    value = str(arguments.get(field) or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _validate_table(field_name: str, field_type: str, field: dict[str, Any]) -> dict[str, Any]:
    payload_key = "chart_data" if field_type == "chart" else "sheet_data"
    table = field.get(payload_key)
    if not isinstance(table, dict):
        raise ValueError(f"Autofill {field_type} field {field_name} requires {payload_key}")
    rows = table.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"Autofill {field_type} field {field_name} requires rows")
    if field_type == "chart" and len(rows) > 100:
        raise ValueError(f"Autofill chart field {field_name} cannot exceed 100 rows")
    expected_columns: int | None = None
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("cells"), list):
            raise ValueError(f"Autofill {field_type} field {field_name} contains an invalid row")
        cells = row["cells"]
        if field_type == "chart" and len(cells) > 20:
            raise ValueError(f"Autofill chart field {field_name} cannot exceed 20 columns")
        if expected_columns is None:
            expected_columns = len(cells)
        elif len(cells) != expected_columns:
            raise ValueError(f"Autofill {field_type} field {field_name} rows must have equal widths")
    column_configs = table.get("column_configs")
    if column_configs is not None:
        if not isinstance(column_configs, list):
            raise ValueError(f"Autofill {field_type} field {field_name} has invalid column_configs")
        if expected_columns is not None and len(column_configs) != expected_columns:
            raise ValueError(
                f"Autofill {field_type} field {field_name} column_configs must match row width"
            )
    return {"type": field_type, payload_key: table}


def _validate_autofill_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("data must be an object keyed by Canva autofill field name")
    if not value:
        raise ValueError("At least one Canva autofill field is required")
    if len(value) > 100:
        raise ValueError("A single Canva autofill request is limited to 100 named fields")
    encoded_size = len(
        json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    if encoded_size > 250_000:
        raise ValueError("Canva autofill data is too large")

    result: dict[str, Any] = {}
    for raw_name, raw_field in value.items():
        name = str(raw_name).strip()
        if not name or len(name) > 200:
            raise ValueError("Each Canva autofill field needs a bounded non-empty name")
        if not isinstance(raw_field, dict):
            raise ValueError(f"Autofill field {name} must be an object")
        field_type = str(raw_field.get("type") or "").strip()
        if field_type not in {"text", "image", "video", "chart", "sheet"}:
            raise ValueError(f"Autofill field {name} has an unsupported type")

        if field_type == "text":
            content = str(raw_field.get("text") or "")
            if len(content) > 50_000:
                raise ValueError(f"Autofill text field {name} is too long")
            result[name] = {"type": "text", "text": content}
            continue

        if field_type in {"image", "video"}:
            asset_id = str(raw_field.get("asset_id") or "").strip()
            if not asset_id or len(asset_id) > 200:
                raise ValueError(f"Autofill {field_type} field {name} requires a valid asset_id")
            result[name] = {"type": field_type, "asset_id": asset_id}
            continue

        result[name] = _validate_table(name, field_type, raw_field)

    return result


class AvailableWorkspaceCanvaAuthoringProvider:
    async def is_available(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
    ) -> bool:
        if not context.workspace_id:
            return False
        required = SCOPE_BY_CAPABILITY.get(capability.id)
        if required is None:
            return False
        rows = await active_workspace_connectors(db, context.workspace_id, "canva")
        return any(required.issubset(connector_scopes(row)) for row in rows)

    async def execute(
        self,
        db: AsyncSession,
        *,
        context: ExecutionContext,
        capability: CapabilitySpec,
        arguments: dict[str, Any],
        minimum_context: dict[str, Any],
    ) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Canva authoring requires workspace authority")
        connector = await _connector(db, context.workspace_id, capability.id)
        token = await access_token(db, connector)
        capability_id = capability.id

        if capability_id == "canva.design.dataset":
            design_id = _id(arguments, "design_id")
            body = await request_json(
                "GET", f"/designs/{quote(design_id, safe='')}/dataset", token
            )
            return CapabilityExecutionResult(
                value=body,
                resource_type="canva_design",
                resource_id=design_id,
            )

        if capability_id == "canva.brand_templates.list":
            params = {
                key: arguments[key]
                for key in ("query", "continuation", "ownership", "sort_by", "dataset")
                if arguments.get(key) not in (None, "")
            }
            params["limit"] = max(1, min(int(arguments.get("limit") or 25), 100))
            return CapabilityExecutionResult(
                value=await request_json("GET", "/brand-templates", token, params=params),
                resource_type="canva_brand_template_collection",
            )

        if capability_id == "canva.brand_template.get":
            template_id = _id(arguments, "brand_template_id")
            body = await request_json(
                "GET", f"/brand-templates/{quote(template_id, safe='')}", token
            )
            return CapabilityExecutionResult(
                value=body,
                resource_type="canva_brand_template",
                resource_id=template_id,
            )

        if capability_id == "canva.brand_template.dataset":
            template_id = _id(arguments, "brand_template_id")
            body = await request_json(
                "GET",
                f"/brand-templates/{quote(template_id, safe='')}/dataset",
                token,
            )
            return CapabilityExecutionResult(
                value=body,
                resource_type="canva_brand_template",
                resource_id=template_id,
            )

        if capability_id == "canva.autofill.create":
            mode = str(arguments.get("type") or "").strip()
            data = _validate_autofill_data(arguments.get("data"))
            payload: dict[str, Any] = {"type": mode, "data": data}
            if mode == "create_from_brand_template":
                payload["brand_template_id"] = _id(arguments, "brand_template_id")
            elif mode in {"create_from_design", "update_design"}:
                payload["design_id"] = _id(arguments, "design_id")
            else:
                raise ValueError("Unsupported Canva autofill type")
            if mode != "update_design" and arguments.get("title"):
                payload["title"] = str(arguments["title"]).strip()[:255]
            body = await request_json("POST", "/autofills", token, payload=payload)
            job = body.get("job") if isinstance(body.get("job"), dict) else {}
            job_id = str(job.get("id") or "") or None
            return CapabilityExecutionResult(
                value=body,
                resource_type="canva_autofill_job",
                resource_id=job_id,
                event_payload={"job_id": job_id, "mode": mode},
            )

        if capability_id == "canva.autofill.get":
            job_id = _id(arguments, "job_id")
            body = await request_json(
                "GET", f"/autofills/{quote(job_id, safe='')}", token
            )
            return CapabilityExecutionResult(
                value=body,
                resource_type="canva_autofill_job",
                resource_id=job_id,
            )

        raise LookupError(f"Canva authoring capability is not implemented: {capability_id}")
