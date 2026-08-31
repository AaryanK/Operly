from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import AuthContext
from apps.api.workspace_os_router import (
    ENTITY_REGISTRY,
    _activity,
    _after_record_mutation,
    _module_enabled,
    _payload_values,
    _record_for_workspace,
    _recalculate_parent,
    _searchable_clause,
    _serialize_record,
    _sync_invoice_payment_state,
    _validate_references,
)
from packages.database.models import AppUser, Task, Tenant
from packages.kernel.contracts import CapabilityExecutionResult, CapabilityRisk, CapabilitySpec
from packages.security.execution_context import ExecutionContext
from packages.workspace_modules.catalog import ENTITY_CATALOG, MODULE_CATALOG


PROVIDER_ID = "operly.workspace_os"


@dataclass(frozen=True, slots=True)
class WorkspaceRecordOperation:
    entity: str
    operation: str


def _normalized(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def capability_id(module: str, entity: str, operation: str) -> str:
    return f"workspace_os.{_normalized(module)}.{_normalized(entity)}.{_normalized(operation)}"


def _field_manifest(module: str, entity: str) -> dict[str, dict[str, Any]]:
    for entry in ENTITY_CATALOG.get(module, []):
        if entry.get("entity") == entity:
            return {
                str(field.get("key")): dict(field)
                for field in entry.get("fields", [])
                if field.get("key")
            }
    return {}


def _json_type(model: type, field: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    column = model.__table__.columns[field]
    column_type = column.type
    if isinstance(column_type, Boolean):
        kind = "boolean"
    elif isinstance(column_type, Integer):
        kind = "integer"
    elif isinstance(column_type, Float):
        kind = "number"
    elif isinstance(column_type, DateTime):
        kind = "string"
    else:
        kind = "string"

    schema: dict[str, Any] = {"type": [kind, "null"] if column.nullable else kind}
    if isinstance(column_type, String) and column_type.length:
        schema["maxLength"] = int(column_type.length)

    options = list((manifest or {}).get("options") or [])
    if options:
        schema["enum"] = [*options, None] if column.nullable else options
    return schema


def _record_schema(config, field_manifest: dict[str, dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, Any] = {"id": {"type": "string"}}
    for field in config.fields:
        properties[field] = _json_type(config.model, field, field_manifest.get(field))
    for field in ("created_at", "updated_at", "opened_at", "stage_changed_at"):
        if hasattr(config.model, field) and field not in properties:
            properties[field] = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": properties,
        "required": ["id"],
        "additionalProperties": False,
    }


def _write_schema(config, field_manifest: dict[str, dict[str, Any]], *, partial: bool) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field: _json_type(config.model, field, field_manifest.get(field))
            for field in config.fields
        },
        "required": [] if partial else list(config.required),
        "additionalProperties": False,
    }


def workspace_record_capabilities() -> tuple[CapabilitySpec, ...]:
    specs: list[CapabilitySpec] = []
    for entity, config in sorted(ENTITY_REGISTRY.items()):
        module = str(config.module)
        manifest = _field_manifest(module, entity)
        record_schema = _record_schema(config, manifest)
        entity_label = entity.replace("-", " ")
        module_name = str(MODULE_CATALOG.get(module, {}).get("name") or module)

        list_id = capability_id(module, entity, "list")
        specs.append(
            CapabilitySpec(
                id=list_id,
                version="1.0.0",
                display_name=f"List {entity_label}",
                description=f"List {entity_label} records from the {module_name} module in the authorized workspace.",
                provider_id=PROVIDER_ID,
                scopes=frozenset({"workspace"}),
                input_schema={
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "maxLength": 200},
                        "status": {"type": "string", "maxLength": 80},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "offset": {"type": "integer", "minimum": 0},
                        "sort": {"type": "string", "maxLength": 80},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": record_schema},
                        "total": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                    },
                    "required": ["items", "total", "limit", "offset"],
                    "additionalProperties": False,
                },
                permissions=(config.read_permission,),
                aliases=(f"list {entity_label}", f"show {entity_label}", f"search {entity_label}"),
                tags=frozenset({"workspace_os", module, entity, "record", "read"}),
                resource_scope="workspace",
            )
        )

        if not config.mutable:
            continue

        create_id = capability_id(module, entity, "create")
        specs.append(
            CapabilitySpec(
                id=create_id,
                version="1.0.0",
                display_name=f"Create {entity_label}",
                description=f"Create one {entity_label} record in the {module_name} module.",
                provider_id=PROVIDER_ID,
                scopes=frozenset({"workspace"}),
                input_schema=_write_schema(config, manifest, partial=False),
                output_schema=record_schema,
                permissions=(config.write_permission,),
                risk=CapabilityRisk.LOW,
                reversible=True,
                aliases=(f"create {entity_label}", f"add {entity_label}", f"new {entity_label}"),
                emits=(f"{module}.{_normalized(entity)}.created",),
                tags=frozenset({"workspace_os", module, entity, "record", "write", "create"}),
                resource_scope="workspace",
            )
        )

        update_id = capability_id(module, entity, "update")
        specs.append(
            CapabilitySpec(
                id=update_id,
                version="1.0.0",
                display_name=f"Update {entity_label}",
                description=f"Update one existing {entity_label} record in the authorized workspace.",
                provider_id=PROVIDER_ID,
                scopes=frozenset({"workspace"}),
                input_schema={
                    "type": "object",
                    "properties": {
                        "record_id": {"type": "string", "minLength": 1, "maxLength": 80},
                        "changes": _write_schema(config, manifest, partial=True),
                    },
                    "required": ["record_id", "changes"],
                    "additionalProperties": False,
                },
                output_schema=record_schema,
                permissions=(config.write_permission,),
                risk=CapabilityRisk.LOW,
                reversible=True,
                aliases=(f"update {entity_label}", f"edit {entity_label}"),
                emits=(f"{module}.{_normalized(entity)}.updated",),
                tags=frozenset({"workspace_os", module, entity, "record", "write", "update"}),
                resource_scope="workspace",
            )
        )

        delete_id = capability_id(module, entity, "delete")
        specs.append(
            CapabilitySpec(
                id=delete_id,
                version="1.0.0",
                display_name=f"Delete {entity_label}",
                description=f"Delete one {entity_label} record from the authorized workspace.",
                provider_id=PROVIDER_ID,
                scopes=frozenset({"workspace"}),
                input_schema={
                    "type": "object",
                    "properties": {"record_id": {"type": "string", "minLength": 1, "maxLength": 80}},
                    "required": ["record_id"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}, "record_id": {"type": "string"}},
                    "required": ["ok", "record_id"],
                    "additionalProperties": False,
                },
                permissions=(config.write_permission,),
                risk=CapabilityRisk.MEDIUM,
                approval_required=True,
                reversible=False,
                aliases=(f"delete {entity_label}", f"remove {entity_label}"),
                emits=(f"{module}.{_normalized(entity)}.deleted",),
                tags=frozenset({"workspace_os", module, entity, "record", "write", "delete"}),
                resource_scope="workspace",
            )
        )
    return tuple(specs)


def workspace_record_operations() -> dict[str, WorkspaceRecordOperation]:
    operations: dict[str, WorkspaceRecordOperation] = {}
    for entity, config in ENTITY_REGISTRY.items():
        operations[capability_id(config.module, entity, "list")] = WorkspaceRecordOperation(entity, "list")
        if config.mutable:
            operations[capability_id(config.module, entity, "create")] = WorkspaceRecordOperation(entity, "create")
            operations[capability_id(config.module, entity, "update")] = WorkspaceRecordOperation(entity, "update")
            operations[capability_id(config.module, entity, "delete")] = WorkspaceRecordOperation(entity, "delete")
    return operations


class WorkspaceOSProvider:
    """Deterministic adapter from the mature Workspace OS into Kernel v3.

    The current Workspace OS record registry remains the compatibility source of truth
    while the business-domain service layer is extracted from the FastAPI module.  This
    provider deliberately calls the lower-level record helpers rather than HTTP route
    functions so the Kernel still owns commit/rollback, validation, tracing, and events.
    """

    def __init__(self) -> None:
        self._operations = workspace_record_operations()

    async def _auth(self, db: AsyncSession, context: ExecutionContext) -> AuthContext:
        if not context.workspace_id or not context.user_id:
            raise PermissionError("Workspace record operations require an authenticated workspace member")
        tenant = await db.get(Tenant, context.workspace_id)
        user = await db.get(AppUser, context.user_id)
        if tenant is None or user is None:
            raise PermissionError("Workspace authority is unavailable")
        return AuthContext(user=user, tenant=tenant, role=context.role)

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
        operation = self._operations.get(capability.id)
        if operation is None:
            raise LookupError(f"Workspace OS capability is not implemented: {capability.id}")
        config = ENTITY_REGISTRY[operation.entity]
        if not await _module_enabled(db, context.workspace_id or "", config.module):
            raise PermissionError(f"Workspace module is disabled: {config.module}")
        auth = await self._auth(db, context)
        if operation.operation == "list":
            return await self._list(db, auth, operation.entity, config, arguments)
        if operation.operation == "create":
            return await self._create(db, auth, operation.entity, config, arguments)
        if operation.operation == "update":
            return await self._update(db, auth, operation.entity, config, arguments)
        if operation.operation == "delete":
            return await self._delete(db, auth, operation.entity, config, arguments)
        raise LookupError(f"Unsupported Workspace OS operation: {operation.operation}")

    async def _list(self, db, auth, entity, config, arguments) -> CapabilityExecutionResult:
        limit = max(1, min(int(arguments.get("limit") or 50), 200))
        offset = max(0, int(arguments.get("offset") or 0))
        criteria = [config.model.tenant_id == auth.tenant.id]
        q = str(arguments.get("q") or "").strip()
        if q and config.search_fields:
            clause = _searchable_clause(config, q)
            if clause is not None:
                criteria.append(clause)
        status = str(arguments.get("status") or "").strip()
        if status and hasattr(config.model, "status"):
            criteria.append(config.model.status == status)
        sort_key = str(arguments.get("sort") or config.default_sort)
        if not hasattr(config.model, sort_key):
            raise ValueError("Unsupported sort field")
        direction = str(arguments.get("direction") or "desc")
        column = getattr(config.model, sort_key)
        rows = (
            await db.scalars(
                select(config.model)
                .where(*criteria)
                .order_by(column.asc() if direction == "asc" else column.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
        total = int(await db.scalar(select(func.count(config.model.id)).where(*criteria)) or 0)
        return CapabilityExecutionResult(
            value={
                "items": [_serialize_record(row, config) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            },
            resource_type=entity,
        )

    async def _create(self, db, auth, entity, config, arguments) -> CapabilityExecutionResult:
        values = _payload_values(config, arguments, partial=False)
        await _validate_references(db, auth, config, values)
        values["tenant_id"] = auth.tenant.id
        if config.model is Task:
            values["owner_user_id"] = None
        row = config.model(**values)
        db.add(row)
        try:
            await db.flush()
            await _after_record_mutation(db, auth, config.model, row)
            _activity(db, auth, "created", entity, row.id, f"Created {entity.replace('-', ' ')} record")
        except IntegrityError as error:
            raise ValueError("A record with that unique identifier already exists") from error
        return CapabilityExecutionResult(
            value=_serialize_record(row, config),
            resource_type=entity,
            resource_id=row.id,
            event_payload={"record_id": row.id, "entity": entity, "operation": "create"},
        )

    async def _update(self, db, auth, entity, config, arguments) -> CapabilityExecutionResult:
        record_id = str(arguments.get("record_id") or "").strip()
        changes = dict(arguments.get("changes") or {})
        if not changes:
            raise ValueError("At least one field must be changed")
        try:
            row = await _record_for_workspace(db, auth, config, record_id)
        except Exception as error:
            raise ValueError("Record not found in the authorized workspace") from error
        parent_field = {
            "order-items": "order_id",
            "quote-items": "quote_id",
            "purchase-order-items": "purchase_order_id",
            "invoice-items": "invoice_id",
            "payments": "invoice_id",
        }.get(entity)
        old_parent = getattr(row, parent_field, None) if parent_field else None
        values = _payload_values(config, changes, partial=True)
        await _validate_references(db, auth, config, values)
        for key, value in values.items():
            setattr(row, key, value)
        if "stage" in values and hasattr(row, "stage_changed_at"):
            row.stage_changed_at = datetime.utcnow()
        try:
            await db.flush()
            await _after_record_mutation(db, auth, config.model, row, old_parent)
            _activity(db, auth, "updated", entity, record_id, f"Updated {entity.replace('-', ' ')} record")
        except IntegrityError as error:
            raise ValueError("Update conflicts with an existing record") from error
        return CapabilityExecutionResult(
            value=_serialize_record(row, config),
            resource_type=entity,
            resource_id=record_id,
            event_payload={"record_id": record_id, "entity": entity, "operation": "update"},
        )

    async def _delete(self, db, auth, entity, config, arguments) -> CapabilityExecutionResult:
        record_id = str(arguments.get("record_id") or "").strip()
        try:
            row = await _record_for_workspace(db, auth, config, record_id)
        except Exception as error:
            raise ValueError("Record not found in the authorized workspace") from error
        parent_field = {
            "order-items": "order_id",
            "quote-items": "quote_id",
            "purchase-order-items": "purchase_order_id",
            "invoice-items": "invoice_id",
            "payments": "invoice_id",
        }.get(entity)
        old_parent = getattr(row, parent_field, None) if parent_field else None
        await db.delete(row)
        try:
            await db.flush()
            if entity in {"order-items", "quote-items", "purchase-order-items", "invoice-items"}:
                await _recalculate_parent(db, auth.tenant.id, config.model, old_parent)
            elif entity == "payments":
                await _sync_invoice_payment_state(db, auth.tenant.id, old_parent)
            _activity(db, auth, "deleted", entity, record_id, f"Deleted {entity.replace('-', ' ')} record")
        except IntegrityError as error:
            raise ValueError("This record is still referenced by other workspace data") from error
        return CapabilityExecutionResult(
            value={"ok": True, "record_id": record_id},
            resource_type=entity,
            resource_id=record_id,
            event_payload={"record_id": record_id, "entity": entity, "operation": "delete"},
        )
