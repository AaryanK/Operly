from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.plugin_platform_models import PluginStorageNamespaceRecord
from packages.database.plugin_storage_models import PluginKVRecord


class PluginStorageError(RuntimeError):
    pass


class PluginStorageService:
    """Workspace/plugin-scoped storage; plugin code never receives DATABASE_URL."""

    async def _namespace(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        name: str,
    ) -> PluginStorageNamespaceRecord:
        row = await db.scalar(
            select(PluginStorageNamespaceRecord).where(
                PluginStorageNamespaceRecord.tenant_id == tenant_id,
                PluginStorageNamespaceRecord.installation_id == installation_id,
                PluginStorageNamespaceRecord.name == name,
            )
        )
        if row is None:
            raise LookupError("Plugin storage namespace not found")
        return row

    async def get_json(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        namespace: str,
        key: str,
    ):
        ns = await self._namespace(db, tenant_id=tenant_id, installation_id=installation_id, name=namespace)
        row = await db.scalar(
            select(PluginKVRecord).where(
                PluginKVRecord.tenant_id == tenant_id,
                PluginKVRecord.installation_id == installation_id,
                PluginKVRecord.namespace_id == ns.id,
                PluginKVRecord.key == key,
            )
        )
        if row is None:
            raise LookupError("Plugin storage key not found")
        return json.loads(row.value_json)

    async def put_json(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        namespace: str,
        key: str,
        value,
    ) -> PluginKVRecord:
        if not key or len(key.encode("utf-8")) > 500:
            raise PluginStorageError("Plugin storage key is invalid")
        ns = await self._namespace(db, tenant_id=tenant_id, installation_id=installation_id, name=namespace)
        if ns.storage_kind not in {"kv", "document"}:
            raise PluginStorageError("Namespace does not support JSON records")
        encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
        size = len(encoded.encode("utf-8"))
        if size > min(ns.quota_bytes, 2 * 1024 * 1024):
            raise PluginStorageError("Plugin storage value exceeds the namespace/item policy")
        row = await db.scalar(
            select(PluginKVRecord).where(PluginKVRecord.namespace_id == ns.id, PluginKVRecord.key == key)
        )
        previous = row.size_bytes if row is not None else 0
        projected = ns.used_bytes - previous + size
        if projected > ns.quota_bytes:
            raise PluginStorageError("Plugin storage quota exceeded")
        if row is None:
            row = PluginKVRecord(
                tenant_id=tenant_id,
                installation_id=installation_id,
                namespace_id=ns.id,
                key=key,
                value_json=encoded,
                size_bytes=size,
            )
            db.add(row)
        else:
            row.value_json = encoded
            row.size_bytes = size
            row.version += 1
        ns.used_bytes = projected
        await db.flush()
        return row

    async def delete(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        installation_id: str,
        namespace: str,
        key: str,
    ) -> bool:
        ns = await self._namespace(db, tenant_id=tenant_id, installation_id=installation_id, name=namespace)
        row = await db.scalar(
            select(PluginKVRecord).where(
                PluginKVRecord.namespace_id == ns.id,
                PluginKVRecord.key == key,
                PluginKVRecord.tenant_id == tenant_id,
                PluginKVRecord.installation_id == installation_id,
            )
        )
        if row is None:
            return False
        ns.used_bytes = max(0, ns.used_bytes - row.size_bytes)
        await db.delete(row)
        await db.flush()
        return True


plugin_storage = PluginStorageService()
