from __future__ import annotations

from typing import Any
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from packages.connectors.canva_provider import access_token, get_identity, request_json
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.integrations.common import (
    active_workspace_connectors,
    connector_public_json,
    connector_scopes,
)


PROVIDER_ID = "operly.canva"

PROFILE_READ = "profile:read"
DESIGN_META_READ = "design:meta:read"
DESIGN_CONTENT_READ = "design:content:read"
DESIGN_CONTENT_WRITE = "design:content:write"
FOLDER_READ = "folder:read"

CANVA_SCOPE_BY_CAPABILITY: dict[str, frozenset[str]] = {
    "canva.profile.read": frozenset({PROFILE_READ}),
    "canva.designs.list": frozenset({DESIGN_META_READ}),
    "canva.design.get": frozenset({DESIGN_META_READ}),
    "canva.design.create": frozenset({DESIGN_CONTENT_WRITE}),
    "canva.design.export_formats": frozenset({DESIGN_CONTENT_READ}),
    "canva.design.export.create": frozenset({DESIGN_CONTENT_READ}),
    "canva.design.export.get": frozenset({DESIGN_CONTENT_READ}),
    "canva.folder.items.list": frozenset({FOLDER_READ}),
}


class CanvaConnectorRequired(LookupError):
    pass


def _object(properties: dict[str, Any], *, required: list[str] | None = None, additional: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": additional}


def _array(item: dict[str, Any], *, max_items: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "array", "items": item}
    if max_items is not None:
        result["maxItems"] = max_items
    return result


def _capability(capability_id: str, display_name: str, description: str, *, permission: str, input_schema: dict[str, Any] | None = None, output_schema: dict[str, Any] | None = None, risk: CapabilityRisk = CapabilityRisk.READ_ONLY, approval: bool = False, reversible: bool = False, emits: tuple[str, ...] = (), tags: tuple[str, ...] = ()) -> CapabilitySpec:
    return CapabilitySpec(id=capability_id, version="1.0.0", display_name=display_name, description=description, provider_id=PROVIDER_ID, scopes=frozenset({"workspace"}), input_schema=input_schema or _object({}), output_schema=output_schema or _object({}, additional=True), permissions=(permission,), risk=risk, approval_required=approval, reversible=reversible, emits=emits, tags=frozenset(("canva", "connector", "external", *tags)), resource_scope="workspace")


def workspace_canva_capabilities() -> tuple[CapabilitySpec, ...]:
    return (
        _capability("canva.connection.status", "Read Canva connection status", "Inspect connected workspace Canva accounts and granted provider scopes without exposing credentials.", permission="integrations:read", output_schema=_object({"connections": _array(_object({}, additional=True))}, required=["connections"]), tags=("status", "read")),
        _capability("canva.profile.read", "Read Canva profile", "Read the connected Canva user/team identity.", permission="integrations:read", output_schema=_object({"profile": _object({}, additional=True)}, required=["profile"]), tags=("profile", "read")),
        _capability("canva.designs.list", "List Canva designs", "List a page of designs visible to the connected Canva workspace account.", permission="marketing:read", input_schema=_object({"query": {"type": "string", "maxLength": 200}, "continuation": {"type": "string", "maxLength": 1000}, "ownership": {"type": "string", "enum": ["any", "owned", "shared"]}, "sort_by": {"type": "string", "enum": ["relevance", "modified_descending", "modified_ascending", "title_ascending", "title_descending"]}}), output_schema=_object({}, additional=True), tags=("design", "list", "read")),
        _capability("canva.design.get", "Get Canva design", "Read metadata and temporary view/edit URLs for one Canva design.", permission="marketing:read", input_schema=_object({"design_id": {"type": "string", "minLength": 1, "maxLength": 200}}, required=["design_id"]), output_schema=_object({}, additional=True), tags=("design", "read")),
        _capability("canva.design.create", "Create Canva design", "Create a blank Canva design using an approved preset or bounded custom pixel dimensions.", permission="marketing:write", input_schema=_object({"title": {"type": "string", "minLength": 1, "maxLength": 255}, "preset": {"type": "string", "enum": ["doc", "email", "presentation", "whiteboard"]}, "width": {"type": "integer", "minimum": 40, "maximum": 8000}, "height": {"type": "integer", "minimum": 40, "maximum": 8000}, "asset_id": {"type": "string", "maxLength": 200}}, required=["title"]), output_schema=_object({}, additional=True), risk=CapabilityRisk.MEDIUM, approval=True, reversible=False, emits=("canva.design.created",), tags=("design", "create", "write")),
        _capability("canva.design.export_formats", "Get Canva export formats", "Read the file formats Canva supports for a design.", permission="marketing:read", input_schema=_object({"design_id": {"type": "string", "minLength": 1, "maxLength": 200}}, required=["design_id"]), output_schema=_object({}, additional=True), tags=("design", "export", "read")),
        _capability("canva.design.export.create", "Create Canva export job", "Create transient export output without modifying the Canva design.", permission="marketing:read", input_schema=_object({"design_id": {"type": "string", "minLength": 1, "maxLength": 200}, "format": {"type": "string", "enum": ["pdf", "jpg", "png", "gif", "pptx", "mp4"]}, "pages": _array({"type": "integer"}, max_items=100), "export_quality": {"type": "string", "enum": ["regular", "pro"]}}, required=["design_id", "format"]), output_schema=_object({}, additional=True), risk=CapabilityRisk.LOW, emits=("canva.export.created",), tags=("design", "export", "job")),
        _capability("canva.design.export.get", "Get Canva export job", "Read the state and result URLs of a Canva export job.", permission="marketing:read", input_schema=_object({"export_id": {"type": "string", "minLength": 1, "maxLength": 200}}, required=["export_id"]), output_schema=_object({}, additional=True), tags=("design", "export", "read")),
        _capability("canva.folder.items.list", "List Canva folder items", "List designs, folders, image assets, or brand templates in a Canva folder.", permission="marketing:read", input_schema=_object({"folder_id": {"type": "string", "minLength": 1, "maxLength": 100}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "continuation": {"type": "string", "maxLength": 1000}, "item_types": _array({"type": "string", "enum": ["design", "folder", "image", "brand_template"]}, max_items=4)}, required=["folder_id"]), output_schema=_object({}, additional=True), tags=("folder", "asset", "read")),
    )


async def _canva_connectors(db: AsyncSession, workspace_id: str):
    return await active_workspace_connectors(db, workspace_id, "canva")


async def _connector(db: AsyncSession, workspace_id: str, capability_id: str):
    required = CANVA_SCOPE_BY_CAPABILITY.get(capability_id, frozenset())
    for row in await _canva_connectors(db, workspace_id):
        if required.issubset(connector_scopes(row)):
            return row
    raise CanvaConnectorRequired("Connect Canva and grant the exact provider scope required by this workspace tool")


def _clean_id(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


class WorkspaceCanvaProvider:
    async def execute(self, db: AsyncSession, *, context: ExecutionContext, capability: CapabilitySpec, arguments: dict[str, Any], minimum_context: dict[str, Any]) -> CapabilityExecutionResult:
        del minimum_context
        if not context.workspace_id:
            raise PermissionError("Canva workspace capability requires workspace authority")
        workspace_id = context.workspace_id
        capability_id = capability.id

        if capability_id == "canva.connection.status":
            rows = await _canva_connectors(db, workspace_id)
            return CapabilityExecutionResult(value={"connections": [connector_public_json(row) for row in rows]}, resource_type="canva_connection", resource_id=rows[0].id if len(rows) == 1 else None)

        connector = await _connector(db, workspace_id, capability_id)
        token = await access_token(db, connector)

        if capability_id == "canva.profile.read":
            profile = await get_identity(token)
            return CapabilityExecutionResult(value={"profile": profile}, resource_type="canva_user", resource_id=str(profile.get("user_id") or "") or None)
        if capability_id == "canva.designs.list":
            params = {key: arguments[key] for key in ("query", "continuation", "ownership", "sort_by") if arguments.get(key) not in (None, "")}
            return CapabilityExecutionResult(value=await request_json("GET", "/designs", token, params=params))
        if capability_id == "canva.design.get":
            design_id = _clean_id(arguments.get("design_id"), "design_id")
            body = await request_json("GET", f"/designs/{quote(design_id, safe='')}", token)
            return CapabilityExecutionResult(value=body, resource_type="canva_design", resource_id=design_id)
        if capability_id == "canva.design.create":
            title = str(arguments.get("title") or "").strip()
            if not title:
                raise ValueError("title is required")
            preset = str(arguments.get("preset") or "").strip()
            width, height = arguments.get("width"), arguments.get("height")
            if preset:
                design_type: dict[str, Any] = {"type": "preset", "name": preset}
            else:
                if width is None or height is None:
                    raise ValueError("Supply either preset or both width and height")
                width_i, height_i = int(width), int(height)
                if width_i * height_i > 25_000_000:
                    raise ValueError("Custom Canva designs cannot exceed 25,000,000 pixels")
                design_type = {"type": "custom", "width": width_i, "height": height_i}
            payload: dict[str, Any] = {"type": "type_and_asset", "design_type": design_type, "title": title[:255]}
            if arguments.get("asset_id"):
                payload["asset_id"] = str(arguments["asset_id"]).strip()
            body = await request_json("POST", "/designs", token, payload=payload)
            design = body.get("design") if isinstance(body.get("design"), dict) else {}
            design_id = str(design.get("id") or body.get("id") or "") or None
            return CapabilityExecutionResult(value=body, resource_type="canva_design", resource_id=design_id, event_payload={"design_id": design_id})
        if capability_id == "canva.design.export_formats":
            design_id = _clean_id(arguments.get("design_id"), "design_id")
            body = await request_json("GET", f"/designs/{quote(design_id, safe='')}/export-formats", token)
            return CapabilityExecutionResult(value=body, resource_type="canva_design", resource_id=design_id)
        if capability_id == "canva.design.export.create":
            design_id = _clean_id(arguments.get("design_id"), "design_id")
            export_format: dict[str, Any] = {"type": str(arguments["format"]).lower()}
            if arguments.get("pages"):
                export_format["pages"] = [int(item) for item in arguments["pages"]]
            if arguments.get("export_quality"):
                export_format["export_quality"] = arguments["export_quality"]
            if export_format["type"] == "jpg":
                export_format["quality"] = 90
            if export_format["type"] == "mp4":
                export_format["quality"] = "horizontal_1080p"
            body = await request_json("POST", "/exports", token, payload={"design_id": design_id, "format": export_format})
            job = body.get("job") if isinstance(body.get("job"), dict) else {}
            export_id = str(job.get("id") or body.get("id") or "") or None
            return CapabilityExecutionResult(value=body, resource_type="canva_export", resource_id=export_id, event_payload={"export_id": export_id, "design_id": design_id})
        if capability_id == "canva.design.export.get":
            export_id = _clean_id(arguments.get("export_id"), "export_id")
            body = await request_json("GET", f"/exports/{quote(export_id, safe='')}", token)
            return CapabilityExecutionResult(value=body, resource_type="canva_export", resource_id=export_id)
        if capability_id == "canva.folder.items.list":
            folder_id = _clean_id(arguments.get("folder_id"), "folder_id")
            params: dict[str, Any] = {"limit": max(1, min(int(arguments.get("limit") or 50), 100))}
            if arguments.get("continuation"):
                params["continuation"] = str(arguments["continuation"])
            if arguments.get("item_types"):
                params["item_types"] = ",".join(str(item) for item in arguments["item_types"])
            body = await request_json("GET", f"/folders/{quote(folder_id, safe='')}/items", token, params=params)
            return CapabilityExecutionResult(value=body, resource_type="canva_folder", resource_id=folder_id)
        raise LookupError(f"Canva capability is not implemented: {capability_id}")
